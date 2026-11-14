"""Whole-campaign export and import (DL-183).

A campaign export is one ZIP: `manifest.json` plus `images/<avatars|sheets|maps>/<name>.<ext>`.  It holds
everything the DM built:

  * the campaign itself (name, description, setting, status, "hide NPCs", notes, banner)
  * factions, and characters with NPCs, notes, attacks, level history, sheets and faction memberships
  * maps with their grid settings, fog, pins (tokens and props) and drawings
  * quests, the session journal and the treasury (every entry, with the purse it belongs to)

and leaves out people and history: members, invitations, the activity feed, dice rolls, notifications,
session RSVPs, the live battle and the short-lived map measures.

Things that point at each other inside the file (a character in a faction, a token on a map, a drawing
stuck to a token, a purse entry for a character) use `ref` numbers that only mean something inside this
one file.  On import every ref becomes a brand-new row, so nothing can reach into a different campaign.

This module has no Flask or storage code.  `build_manifest` reads rows through the db handle it is given;
`normalize_manifest` turns an untrusted manifest into clean, typed, bounded data (the same rules as
importer.py: types coerced, strings capped, NUL bytes removed, colours checked, image names whitelisted,
rich text passed through the caller's sanitisers).  Writing the rows and the images is app.py's job.
"""
import json
import math
import re
from datetime import date, datetime

import dnd5e
import fog
from importer import ImportRejected, _clean, _flag, _int      # the same coercion helpers the old importer uses

class WrongFile(ImportRejected):
    """A valid export, but of something else than a whole campaign (the old characters and factions kind)."""


FORMAT = 'oaken-campaign'
VERSION = 2

MANIFEST_LIMIT = 24 * 1024 * 1024          # a campaign manifest is bigger than a characters one (drawings, journal, treasury)

MAX_FACTIONS = 200
MAX_CHARACTERS = 1000
MAX_SHEETS_PER_CHARACTER = 20
MAX_LEVELS_PER_CHARACTER = 30
MAX_MAPS = 100
MAX_PINS_PER_MAP = 2000
MAX_DRAWINGS_PER_MAP = 3000
MAX_PEN_POINTS = 20000
MAX_IMAGES = 400                           # images read from one file; more are ignored, not fatal (each one costs CPU to re-encode)

_IMAGE_REF = re.compile(r'^(avatars|sheets|maps)/[A-Za-z0-9_\-]{1,80}\.(png|jpe?g|gif|webp)$', re.IGNORECASE)
_COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')
_BATCH = re.compile(r'^[A-Za-z0-9_\-]{1,40}$')
_GRID_COLORS = ('gray', 'white', 'black')
_DRAWING_KINDS = ('line', 'rect', 'oval', 'pen', 'angle')
_REF_MAX = 2_147_483_647
_COORD = 1_000_000.0
_COINS = ('cp', 'sp', 'ep', 'gp', 'pp')


# ---------------------------------------------------------------------------------------------- export

def _ts(value):
    """A database timestamp as 'YYYY-MM-DD HH:MM:SS' text (what the file stores)."""
    return value.strftime('%Y-%m-%d %H:%M:%S') if hasattr(value, 'strftime') else (str(value) if value else None)


def build_manifest(db, campaign_id, exported_at):
    """The whole manifest for one campaign.  The caller has already checked the person may export it (a DM)."""
    camp = db.execute('SELECT name, description, avatar_path, setting, status, hide_npcs, notes FROM campaigns WHERE id = ?', (campaign_id,)).fetchone()
    if camp is None:
        raise LookupError('campaign')

    factions = db.execute('SELECT id, name, bio, color, avatar_path FROM groups WHERE campaign_id = ? ORDER BY id', (campaign_id,)).fetchall()
    faction_ids = {f['id'] for f in factions}

    characters = db.execute('SELECT * FROM characters WHERE campaign_id = ? AND is_temp_familiar = 0 ORDER BY id', (campaign_id,)).fetchall()
    char_ids = {c['id'] for c in characters}

    memberships = {}
    for r in db.execute('SELECT cg.character_id, cg.group_id FROM character_groups cg JOIN characters c ON c.id = cg.character_id '
                        'WHERE c.campaign_id = ? ORDER BY cg.character_id, cg.position, cg.group_id', (campaign_id,)).fetchall():
        if r['group_id'] in faction_ids:
            memberships.setdefault(r['character_id'], []).append(r['group_id'])

    attacks = {}
    for a in db.execute('SELECT a.character_id, a.name, a.attack_bonus, a.damage, a.damage_type FROM character_attacks a '
                        'JOIN characters c ON c.id = a.character_id WHERE c.campaign_id = ? ORDER BY a.character_id, a.position, a.id', (campaign_id,)).fetchall():
        attacks.setdefault(a['character_id'], []).append(
            {'name': a['name'], 'attack_bonus': a['attack_bonus'], 'damage': a['damage'], 'damage_type': a['damage_type']})

    levels = {}
    for r in db.execute('SELECT l.character_id, l.level, l.max_hp, l.armor_class, l.str_score, l.dex_score, l.con_score, l.int_score, l.wis_score, '
                        'l.cha_score, l.speed, l.skill_prof, l.save_prof FROM character_levels l JOIN characters c ON c.id = l.character_id '
                        'WHERE c.campaign_id = ? ORDER BY l.character_id, l.level', (campaign_id,)).fetchall():
        levels.setdefault(r['character_id'], []).append({
            'level': r['level'], 'max_hp': r['max_hp'], 'armor_class': r['armor_class'],
            'str_score': r['str_score'], 'dex_score': r['dex_score'], 'con_score': r['con_score'],
            'int_score': r['int_score'], 'wis_score': r['wis_score'], 'cha_score': r['cha_score'],
            'speed': dnd5e.clean_speed(r['speed']),
            'skill_prof': dnd5e.clean_skill_prof(r['skill_prof']), 'save_prof': dnd5e.clean_save_prof(r['save_prof'])})

    sheets = {}
    for s in db.execute('SELECT s.character_id, s.image_path FROM character_sheets s JOIN characters c ON c.id = s.character_id '
                        'WHERE c.campaign_id = ? ORDER BY s.character_id, s.sort_order, s.id', (campaign_id,)).fetchall():
        sheets.setdefault(s['character_id'], []).append(s['image_path'])

    pins_by_map, drawings_by_map = {}, {}
    for p in db.execute('SELECT p.* FROM map_pins p JOIN maps m ON m.id = p.map_id WHERE m.campaign_id = ? ORDER BY p.map_id, p.id', (campaign_id,)).fetchall():
        if p['pin_type'] == 'character' and p['character_id'] not in char_ids:
            continue                                    # a token for someone who is not in the file would point at nothing
        pins_by_map.setdefault(p['map_id'], []).append({
            'ref': p['id'], 'pin_type': p['pin_type'], 'icon_key': p['icon_key'], 'custom_name': p['custom_name'],
            'x': p['x'], 'y': p['y'], 'scale': p['scale'], 'rotation': p['rotation'], 'locked': p['locked'], 'dm_only': p['dm_only'],
            'character_ref': p['character_id'] if p['pin_type'] == 'character' else None})
    for d in db.execute('SELECT d.* FROM map_drawings d JOIN maps m ON m.id = d.map_id WHERE m.campaign_id = ? ORDER BY d.map_id, d.sort_order, d.id', (campaign_id,)).fetchall():
        try:
            geometry = json.loads(d['data'])
        except (TypeError, ValueError):
            geometry = {}
        drawings_by_map.setdefault(d['map_id'], []).append({
            'kind': d['kind'], 'data': geometry, 'cx': d['cx'], 'cy': d['cy'], 'w': d['w'], 'h': d['h'], 'rotation': d['rotation'],
            'color': d['color'], 'fill': d['fill'], 'fill_opacity': d['fill_opacity'], 'locked': d['locked'], 'dm_only': d['dm_only'],
            'attached_pin_ref': d['attached_pin_id']})

    maps = []
    for m in db.execute('SELECT * FROM maps WHERE campaign_id = ? ORDER BY id', (campaign_id,)).fetchall():
        maps.append({
            'ref': m['id'], 'name': m['name'], 'image_file': m['image_path'], 'image_width': m['image_width'], 'image_height': m['image_height'],
            'grid_size': m['grid_size'], 'grid_offset_x': m['grid_offset_x'], 'grid_offset_y': m['grid_offset_y'], 'grid_color': m['grid_color'],
            'grid_visible': m['grid_visible'], 'grid_setup_done': m['grid_setup_done'], 'snap_to_grid': m['snap_to_grid'],
            'locked_for_players': m['locked_for_players'], 'feet_per_square': m['feet_per_square'],
            'fog': m['fog_mask'] or None,
            'pins': pins_by_map.get(m['id'], []), 'drawings': drawings_by_map.get(m['id'], [])})

    quests = [{'title': q['title'], 'details': q['details'], 'done': q['done'], 'hidden': q['hidden']}
              for q in db.execute('SELECT title, details, done, hidden FROM quests WHERE campaign_id = ? ORDER BY id', (campaign_id,)).fetchall()]
    journal = [{'title': j['title'], 'played_on': j['played_on'].isoformat() if hasattr(j['played_on'], 'isoformat') else str(j['played_on']), 'body': j['body']}
               for j in db.execute('SELECT title, played_on, body FROM session_journal WHERE campaign_id = ? ORDER BY played_on, id', (campaign_id,)).fetchall()]
    treasury = [{'holder_ref': t['character_id'] if t['character_id'] in char_ids else None,
                 'cp': t['cp'], 'sp': t['sp'], 'ep': t['ep'], 'gp': t['gp'], 'pp': t['pp'],
                 'note': t['note'], 'created_at': _ts(t['created_at']), 'batch': t['batch']}
                for t in db.execute('SELECT character_id, cp, sp, ep, gp, pp, note, created_at, batch FROM treasury_entries '
                                    'WHERE campaign_id = ? ORDER BY id', (campaign_id,)).fetchall()
                if t['character_id'] is None or t['character_id'] in char_ids]       # a purse of a temporary familiar is not part of the campaign

    return {
        'format': FORMAT,
        'version': VERSION,
        'exported_at': exported_at,
        'campaign': {'name': camp['name'], 'description': camp['description'], 'setting': camp['setting'], 'status': camp['status'],
                     'hide_npcs': 1 if camp['hide_npcs'] else 0, 'notes': camp['notes'], 'avatar_file': camp['avatar_path']},
        'factions': [{'ref': f['id'], 'name': f['name'], 'bio': f['bio'], 'color': f['color'], 'avatar_file': f['avatar_path']} for f in factions],
        'characters': [{
            'ref': c['id'], 'name': c['name'], 'is_npc': c['is_npc'], 'is_ally': c['is_ally'], 'level': c['level'], 'max_hp': c['max_hp'],
            'str_score': c['str_score'], 'dex_score': c['dex_score'], 'con_score': c['con_score'],
            'int_score': c['int_score'], 'wis_score': c['wis_score'], 'cha_score': c['cha_score'],
            'armor_class': c['armor_class'], 'class_key': c['class_key'], 'speed': dnd5e.clean_speed(c['speed']),
            'skill_prof': dnd5e.clean_skill_prof(c['skill_prof']), 'save_prof': dnd5e.clean_save_prof(c['save_prof']),
            'attacks': attacks.get(c['id'], []), 'levels': levels.get(c['id'], []),
            'notes': c['notes'],                          # a DM's export carries the hidden notes too
            'faction_refs': memberships.get(c['id'], []),
            'main_faction_ref': c['group_id'] if c['group_id'] in faction_ids else None,
            'avatar_file': c['avatar_path'], 'sheet_files': sheets.get(c['id'], [])} for c in characters],
        'maps': maps,
        'quests': quests,
        'journal': journal,
        'treasury': treasury,
    }


def image_refs(manifest):
    """Every stored image key a manifest points at (the export packs each one under images/)."""
    refs = set()

    def add(value):
        if isinstance(value, str) and value:
            refs.add(value)
    add(manifest['campaign'].get('avatar_file'))
    for f in manifest['factions']:
        add(f.get('avatar_file'))
    for c in manifest['characters']:
        add(c.get('avatar_file'))
        for s in c.get('sheet_files') or []:
            add(s)
    for m in manifest['maps']:
        add(m.get('image_file'))
    return refs


# ---------------------------------------------------------------------------------------------- import

def _num(value, default, lo, hi):
    """A finite float in [lo, hi]; `default` when the value is not a usable number."""
    if isinstance(value, bool) or value is None:
        return default
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(n):
        return default
    return max(lo, min(hi, n))


def _ref(value):
    n = _int(value, 0, 0, _REF_MAX)
    return n or None


def _text(value, limit):
    return _clean(value, limit).strip()


def _line(value, limit):
    """One line of plain text: whitespace collapsed, no control characters."""
    return ' '.join(''.join(ch for ch in _clean(value, limit * 4) if ch.isprintable() or ch in ' \t\n').split())[:limit]


def _list(value, limit):
    return value[:limit] if isinstance(value, list) else []


def _geometry(raw):
    """The shape-specific part of a drawing: only the keys the map editor writes, numbers only."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key in ('x1n', 'y1n', 'x2n', 'y2n', 'sweepDeg'):
        v = _num(raw.get(key), None, -_COORD, _COORD)
        if v is not None:
            out[key] = v
    if isinstance(raw.get('points'), list):
        pts = []
        for p in raw['points'][:MAX_PEN_POINTS]:
            if isinstance(p, (list, tuple)) and len(p) == 2:
                x, y = _num(p[0], None, -_COORD, _COORD), _num(p[1], None, -_COORD, _COORD)
                if x is not None and y is not None:
                    pts.append([x, y])
        out['points'] = pts
    return out


def _stamp(value):
    """'YYYY-MM-DD HH:MM:SS' (or the date part alone) -> that text; None when it is not a date."""
    if not isinstance(value, str):
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(value.strip()[:19], fmt).strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
    return None


def normalize_manifest(raw, *, sanitize_notes, sanitize_bio, icon_keys, class_keys, statuses, limits):
    """Clean data for every part of a campaign export.  Raises ImportRejected when the file is not one.

    sanitize_notes / sanitize_bio: the app's rich-text sanitisers.  icon_keys: the known map icons.
    class_keys: the known character classes.  statuses: the campaign statuses.  limits: the app's own
    limits for quests, the journal and the treasury (so the import can never exceed what the forms allow)."""
    if not isinstance(raw, dict) or raw.get('format') != FORMAT:
        if isinstance(raw, dict) and ('characters' in raw or 'groups' in raw) and 'format' not in raw:
            raise WrongFile('This file holds characters and factions, not a whole campaign.')
        raise ImportRejected('This is not a campaign export.')
    if _int(raw.get('version'), 0, 0, 10 ** 6) > VERSION:
        raise ImportRejected('This export was made by a newer version of Oaken and cannot be read here.')

    budget = {'images': MAX_IMAGES}

    def image(value, folder):
        """A whitelisted image name under `folder`, while the file's image budget lasts."""
        if not (isinstance(value, str) and _IMAGE_REF.match(value) and value.split('/')[0].lower() == folder) or budget['images'] <= 0:
            return None
        budget['images'] -= 1
        return value

    camp = raw.get('campaign') if isinstance(raw.get('campaign'), dict) else {}
    status = _text(camp.get('status'), 20)
    notes = sanitize_notes(_clean(camp.get('notes'), 200000))
    campaign = {
        'name': _text(camp.get('name'), 120) or 'Imported campaign',
        'description': sanitize_bio(_clean(camp.get('description'), 100000)),
        'setting': _text(camp.get('setting'), 80) or None,
        'status': status if status in statuses else 'active',
        'hide_npcs': bool(_flag(camp.get('hide_npcs'))),
        'notes': notes if notes not in ('', '[]') else None,
        'avatar_file': image(camp.get('avatar_file'), 'avatars'),
    }

    factions, seen = [], set()
    for f in _list(raw.get('factions'), MAX_FACTIONS):
        ref = _ref(f.get('ref')) if isinstance(f, dict) else None
        if ref is None or ref in seen:
            continue
        seen.add(ref)
        color = f.get('color')
        factions.append({
            'ref': ref,
            'name': _text(f.get('name'), 120) or 'Unnamed Group',
            'bio': _clean(f.get('bio'), 20000),
            'color': color if isinstance(color, str) and _COLOR.match(color) else '#c9a24b',
            'avatar_file': image(f.get('avatar_file'), 'avatars')})
    faction_refs = seen

    characters, seen = [], set()
    for c in _list(raw.get('characters'), MAX_CHARACTERS):
        ref = _ref(c.get('ref')) if isinstance(c, dict) else None
        if ref is None or ref in seen:
            continue
        seen.add(ref)
        is_npc = 1 if _flag(c.get('is_npc')) else 0
        member_of = []
        for r in _list(c.get('faction_refs'), 20):
            r = _ref(r)
            if r in faction_refs and r not in member_of:
                member_of.append(r)
        main = _ref(c.get('main_faction_ref'))
        if main in member_of:                             # the main faction goes first, like the app keeps it
            member_of.remove(main)
            member_of.insert(0, main)
        levels, seen_levels = [], set()
        for lv in _list(c.get('levels'), MAX_LEVELS_PER_CHARACTER):
            if not isinstance(lv, dict):
                continue
            n = _int(lv.get('level'), 0, 0, 10 ** 6)
            if not 1 <= n <= 30 or n in seen_levels:             # a level that does not exist is dropped, not bent into one that does
                continue
            seen_levels.add(n)
            levels.append({
                'level': n, 'max_hp': _int(lv.get('max_hp'), 10, 0, 100000), 'armor_class': _int(lv.get('armor_class'), 10, 0, 100),
                'scores': tuple(_int(lv.get(k), 10, 0, 100) for k in ('str_score', 'dex_score', 'con_score', 'int_score', 'wis_score', 'cha_score')),
                'speed': dnd5e.clean_speed(lv.get('speed')),
                'skill_prof': dnd5e.clean_skill_prof(lv.get('skill_prof')), 'save_prof': dnd5e.clean_save_prof(lv.get('save_prof'))})
        class_key = _text(c.get('class_key'), 40).lower()
        characters.append({
            'ref': ref,
            'name': _text(c.get('name'), 120) or 'Unnamed',
            'is_npc': is_npc,
            'is_ally': 1 if is_npc and _flag(c.get('is_ally')) else 0,
            'level': _int(c.get('level'), 1, 1, 30),
            'max_hp': _int(c.get('max_hp'), 10, 0, 100000),
            'str_score': _int(c.get('str_score'), 10, 0, 100), 'dex_score': _int(c.get('dex_score'), 10, 0, 100),
            'con_score': _int(c.get('con_score'), 10, 0, 100), 'int_score': _int(c.get('int_score'), 10, 0, 100),
            'wis_score': _int(c.get('wis_score'), 10, 0, 100), 'cha_score': _int(c.get('cha_score'), 10, 0, 100),
            'armor_class': _int(c.get('armor_class'), 10, 0, 100),
            'class_key': class_key if class_key in class_keys else None,
            'speed': dnd5e.clean_speed(c.get('speed')),
            'skill_prof': dnd5e.clean_skill_prof(c.get('skill_prof')),
            'save_prof': dnd5e.clean_save_prof(c.get('save_prof')),
            'attacks': dnd5e.clean_attacks(c.get('attacks') if isinstance(c.get('attacks'), list) else []),
            'levels': levels,
            'notes': sanitize_notes(_clean(c.get('notes'), 200000)),
            'faction_refs': member_of,
            'avatar_file': image(c.get('avatar_file'), 'avatars'),
            'sheet_files': [s for s in (image(s, 'sheets') for s in _list(c.get('sheet_files'), MAX_SHEETS_PER_CHARACTER)) if s]})
    character_refs = seen

    maps, seen = [], set()
    for m in _list(raw.get('maps'), MAX_MAPS):
        ref = _ref(m.get('ref')) if isinstance(m, dict) else None
        if ref is None or ref in seen:
            continue
        seen.add(ref)
        pins, pin_refs = [], set()
        for p in _list(m.get('pins'), MAX_PINS_PER_MAP):
            pref = _ref(p.get('ref')) if isinstance(p, dict) else None
            if pref is None or pref in pin_refs:
                continue
            kind = p.get('pin_type')
            icon = p.get('icon_key')
            char_ref = _ref(p.get('character_ref'))
            if kind == 'character':
                if char_ref not in character_refs:
                    continue
                icon, name = None, None
            elif kind == 'prop':
                if icon is not None and icon not in icon_keys:
                    continue
                char_ref, name = None, (_line(p.get('custom_name'), 80) or None)
            else:
                continue
            pin_refs.add(pref)
            pins.append({
                'ref': pref, 'pin_type': kind, 'icon_key': icon, 'custom_name': name, 'character_ref': char_ref,
                'x': _num(p.get('x'), 0.0, -_COORD, _COORD), 'y': _num(p.get('y'), 0.0, -_COORD, _COORD),
                'scale': _num(p.get('scale'), 1.0, 0.05, 20.0), 'rotation': _num(p.get('rotation'), 0.0, -3600.0, 3600.0),
                'locked': 1 if _flag(p.get('locked')) else 0, 'dm_only': 1 if _flag(p.get('dm_only')) else 0})
        drawings = []
        for d in _list(m.get('drawings'), MAX_DRAWINGS_PER_MAP):
            if not isinstance(d, dict) or d.get('kind') not in _DRAWING_KINDS:
                continue
            color = d.get('color')
            attached = _ref(d.get('attached_pin_ref'))
            drawings.append({
                'kind': d['kind'], 'data': _geometry(d.get('data')),
                'cx': _num(d.get('cx'), 0.0, -_COORD, _COORD), 'cy': _num(d.get('cy'), 0.0, -_COORD, _COORD),
                'w': _num(d.get('w'), 0.0, 0.0, _COORD), 'h': _num(d.get('h'), 0.0, 0.0, _COORD),
                'rotation': _num(d.get('rotation'), 0.0, -3600.0, 3600.0),
                'color': color if isinstance(color, str) and _COLOR.match(color) else '#c9a24b',
                'fill': 1 if _flag(d.get('fill')) else 0, 'fill_opacity': _num(d.get('fill_opacity'), 0.4, 0.0, 1.0),
                'locked': 1 if _flag(d.get('locked')) else 0, 'dm_only': 1 if _flag(d.get('dm_only')) else 0,
                'attached_pin_ref': attached if attached in pin_refs else None})
        fog_mask = m.get('fog')
        grid_color = m.get('grid_color')
        maps.append({
            'ref': ref,
            'name': _line(m.get('name'), 120) or 'Untitled Map',
            'image_file': image(m.get('image_file'), 'maps'),
            'image_width': _int(m.get('image_width'), 0, 0, 16000), 'image_height': _int(m.get('image_height'), 0, 0, 16000),
            'grid_size': _int(m.get('grid_size'), 50, 25, 100), 'grid_offset_x': _int(m.get('grid_offset_x'), 0, -100000, 100000),
            'grid_offset_y': _int(m.get('grid_offset_y'), 0, -100000, 100000),
            'grid_color': grid_color if grid_color in _GRID_COLORS else 'gray',
            'grid_visible': 1 if _flag(m.get('grid_visible', 1)) else 0, 'grid_setup_done': 1 if _flag(m.get('grid_setup_done')) else 0,
            'snap_to_grid': 1 if _flag(m.get('snap_to_grid')) else 0, 'locked_for_players': 1 if _flag(m.get('locked_for_players')) else 0,
            'feet_per_square': _int(m.get('feet_per_square'), 5, 1, 100),
            'fog': fog_mask if isinstance(fog_mask, str) and 0 < len(fog_mask) <= 400000 else None,    # checked against the image size on import
            'pins': pins, 'drawings': drawings})

    quests = []
    for q in _list(raw.get('quests'), limits['quests']):
        title = _line(q.get('title'), limits['quest_title']) if isinstance(q, dict) else ''
        if not title:
            continue
        quests.append({'title': title, 'details': _text(q.get('details'), limits['quest_details']) or None,
                       'done': bool(_flag(q.get('done'))), 'hidden': bool(_flag(q.get('hidden')))})

    journal = []
    for j in _list(raw.get('journal'), limits['journal']):
        if not isinstance(j, dict):
            continue
        title = _line(_clean(j.get('title'), limits['journal_title'] * 2).replace('<', '').replace('>', ''), limits['journal_title'])
        body = sanitize_notes(_clean(j.get('body'), limits['journal_body'] + 1)[:limits['journal_body']])
        if not title or body in ('', '[]'):
            continue
        try:
            played_on = datetime.strptime(_text(j.get('played_on'), 10), '%Y-%m-%d').date()
        except ValueError:
            played_on = date.today()
        journal.append({'title': title, 'played_on': played_on, 'body': body})

    treasury = []
    coin = limits['coin']
    for t in _list(raw.get('treasury'), limits['treasury']):
        if not isinstance(t, dict):
            continue
        coins = {c: _int(t.get(c), 0, -coin, coin) for c in _COINS}
        if not any(coins.values()):
            continue
        holder = _ref(t.get('holder_ref'))
        batch = t.get('batch')
        treasury.append({
            'holder_ref': holder if holder in character_refs else None, 'coins': coins,
            'note': _line(t.get('note'), limits['treasury_note']) or 'Entry',
            'created_at': _stamp(t.get('created_at')),
            'batch': batch if isinstance(batch, str) and _BATCH.match(batch) else None})

    return {'campaign': campaign, 'factions': factions, 'characters': characters, 'maps': maps,
            'quests': quests, 'journal': journal, 'treasury': treasury}


def fog_for(stored, width, height):
    """(mask text, cols, rows) when `stored` is a valid fog mask for an image of this size and really hides something, else None."""
    dims = fog.grid_dims(width, height)
    if not dims or not stored:
        return None
    raw = fog.from_storage(stored, *dims)
    if raw is None or not fog.any_fogged(raw):
        return None
    return fog.to_storage(raw), dims[0], dims[1]
