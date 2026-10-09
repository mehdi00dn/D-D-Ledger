"""Rules reference (spells, monsters, items) from the public D&D 5e API, 2024 edition (https://www.dnd5eapi.co/api/2024).

The browser never talks to the API: the server fetches, normalises and caches it, so the Content-Security-Policy stays
strict, the data is shaped once here, and a flaky API only ever costs the reference pages (never a campaign).
Everything that comes back is plain data for the caller to escape or insert as text.
"""
import os
import re
import threading
import time
from html import escape

import requests

import dnd5e

VERSION = '2024'
BASE = os.environ.get('SRD_API_BASE', 'https://www.dnd5eapi.co').rstrip('/')      # overridable so tests can use a local stand-in
API = f'{BASE}/api/{VERSION}'
TIMEOUT = 8
LIST_TTL = 24 * 3600
DETAIL_TTL = 24 * 3600
MAX_RESULTS = 60
INDEX_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,80}$')

# kind -> the API collections it is served from (items are two collections on the API)
KINDS = {'spells': ['spells'], 'monsters': ['monsters'], 'items': ['equipment', 'magic-items']}


class SrdError(Exception):
    """The API could not be reached or answered with something unusable."""


_lock = threading.Lock()
_cache = {}


def _get_json(path):
    now = time.time()
    with _lock:
        hit = _cache.get(path)
        if hit and hit[0] > now:
            return hit[1]
    try:
        r = requests.get(API + path, timeout=TIMEOUT, headers={'Accept': 'application/json'})
        if r.status_code == 404:
            raise SrdError('not found')
        r.raise_for_status()
        data = r.json()
    except SrdError:
        raise
    except (requests.RequestException, ValueError) as e:
        raise SrdError('The rules reference is unavailable right now.') from e
    with _lock:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[path] = (now + (LIST_TTL if path.count('/') <= 1 else DETAIL_TTL), data)
    return data


def clear_cache():
    with _lock:
        _cache.clear()


def valid_index(value):
    return isinstance(value, str) and bool(INDEX_RE.match(value))


def search(kind, query='', limit=MAX_RESULTS):
    """[{'id': 'monsters/aboleth', 'name': 'Aboleth', 'sub': ...}] -- names containing the query, best matches first."""
    if kind not in KINDS:
        raise SrdError('bad request')
    q = (query or '').strip().lower()[:60]
    out = []
    for coll in KINDS[kind]:
        data = _get_json('/' + coll)
        for row in data.get('results', []):
            name = str(row.get('name', ''))
            index = str(row.get('index', ''))
            if not name or not valid_index(index):
                continue
            low = name.lower()
            if q and q not in low:
                continue
            entry = {'id': f'{coll}/{index}', 'name': name}
            if row.get('level') is not None:
                entry['sub'] = 'Cantrip' if row['level'] == 0 else f'Level {row["level"]}'
            elif coll == 'magic-items':
                entry['sub'] = 'Magic item'
            out.append((0 if low.startswith(q) else 1, low, entry))
    out.sort(key=lambda t: (t[0], t[1]))
    return [e for _a, _b, e in out[:limit]]


def _text(value):
    if isinstance(value, list):
        return '\n\n'.join(str(v) for v in value if v)
    return str(value) if value else ''


def _named(items):
    return [str(i.get('name')) for i in (items or []) if isinstance(i, dict) and i.get('name')]


def _cr(value):
    return {0.125: '1/8', 0.25: '1/4', 0.5: '1/2'}.get(value, str(int(value)) if isinstance(value, (int, float)) and value == int(value) else str(value))


def _speed_ft(raw):
    m = re.search(r'\d+', str(raw or ''))
    return int(m.group()) if m else dnd5e.DEFAULT_SPEED


def _level_for_prof(pb):
    try:
        pb = int(pb)
    except (TypeError, ValueError):
        return 1
    return max(1, min(20, (pb - 2) * 4 + 1))


def _abilities(data):
    return {k: int(data.get(name) or 10) for k, name in
            (('str', 'strength'), ('dex', 'dexterity'), ('con', 'constitution'),
             ('int', 'intelligence'), ('wis', 'wisdom'), ('cha', 'charisma'))}


def _monster_prefill(d):
    scores = _abilities(d)
    pb = d.get('proficiency_bonus') or 2
    level = _level_for_prof(pb)
    prof = dnd5e.proficiency_bonus(level)
    saves, skills = [], {}
    for p in d.get('proficiencies') or []:
        idx = str((p.get('proficiency') or {}).get('index', ''))
        value = p.get('value')
        if idx.startswith('saving-throw-') and idx[13:] in dnd5e.ABILITY_KEYS:
            saves.append(idx[13:])
        elif idx.startswith('skill-') and idx[6:] in dnd5e.SKILL_KEYS and isinstance(value, int):
            key = idx[6:]
            ability = next(a for k, _l, a in dnd5e.SKILLS if k == key)
            skills[key] = 2 if value >= dnd5e.modifier(scores[ability]) + 2 * prof else 1
    ac = d.get('armor_class')
    ac = ac[0].get('value') if isinstance(ac, list) and ac and isinstance(ac[0], dict) else ac
    speed = d.get('speed') if isinstance(d.get('speed'), dict) else {}
    return {
        'name': d.get('name', ''), 'level': level, 'is_npc': True,
        'max_hp': int(d.get('hit_points') or 1), 'armor_class': int(ac or 10),
        'speed': dnd5e.clean_speed(_speed_ft(speed.get('walk'))),
        **{f'{k}_score': v for k, v in scores.items()},
        'save_prof': saves, 'skill_prof': skills,
    }


def _block(title, entries):
    rows = [f'<li><b>{escape(str(e.get("name", "")))}.</b> {escape(str(e.get("desc", "")))}</li>' for e in entries or [] if isinstance(e, dict) and e.get('name')]
    return f'<b>{escape(title)}</b><ul>{"".join(rows)}</ul>' if rows else ''


def _monster_notes(d):
    """The stat block's text as note boxes (b/i/ul/li/br only -- the same whitelist the notes editor allows)."""
    kind = ' '.join(x for x in (d.get('size'), d.get('type')) if x)
    lines = [f'<b>{escape(kind)}, {escape(str(d.get("alignment") or "unaligned"))}</b>',
             escape(f'Challenge {_cr(d.get("challenge_rating", 0))} ({int(d.get("xp") or 0):,} XP) - HP {d.get("hit_points_roll") or d.get("hit_dice") or ""}')]
    speed = d.get('speed') if isinstance(d.get('speed'), dict) else {}
    plain = []
    if speed:
        plain.append('Speed: ' + ', '.join(f'{k} {v}' for k, v in speed.items()))
    senses = d.get('senses') if isinstance(d.get('senses'), dict) else {}
    if senses:
        plain.append('Senses: ' + ', '.join(f'{k.replace("_", " ")} {v}' for k, v in senses.items()))
    if d.get('languages'):
        plain.append('Languages: ' + str(d['languages']))
    for label, key in (('Vulnerabilities', 'damage_vulnerabilities'), ('Resistances', 'damage_resistances'),
                       ('Damage immunities', 'damage_immunities')):
        if d.get(key):
            plain.append(f'{label}: ' + ', '.join(str(x) for x in d[key]))
    if d.get('condition_immunities'):
        plain.append('Condition immunities: ' + ', '.join(_named(d['condition_immunities'])))
    head = '<br>'.join(lines + [escape(x) for x in plain])
    boxes = [head]
    for title, key in (('Traits', 'special_abilities'), ('Actions', 'actions'), ('Bonus actions', 'bonus_actions'),
                       ('Reactions', 'reactions'), ('Legendary actions', 'legendary_actions')):
        b = _block(title, d.get(key))
        if b:
            boxes.append(b)
    return boxes


def _spell(d):
    comps = ', '.join(d.get('components') or [])
    if d.get('material'):
        comps += f' ({d["material"]})'
    level = d.get('level', 0)
    school = (d.get('school') or {}).get('name', '')
    sub = f'{school} cantrip' if level == 0 else f'Level {level} {school.lower()}'
    if d.get('ritual'):
        sub += ' (ritual)'
    facts = [('Casting time', d.get('casting_time')), ('Range', d.get('range')), ('Components', comps),
             ('Duration', ('Concentration, ' if d.get('concentration') else '') + str(d.get('duration') or '')),
             ('Classes', ', '.join(_named(d.get('classes'))))]
    body = _text(d.get('description') or d.get('desc'))
    if d.get('higher_level'):
        body += '\n\nUsing a higher-level spell slot: ' + _text(d['higher_level'])
    return {'title': d.get('name', ''), 'subtitle': sub, 'facts': [(k, v) for k, v in facts if v], 'body': body}


def _item(coll, d):
    facts = []
    if coll == 'magic-items':
        sub = (d.get('equipment_category') or {}).get('name', 'Magic item')
        if (d.get('rarity') or {}).get('name'):
            facts.append(('Rarity', d['rarity']['name']))
        if d.get('attunement'):
            facts.append(('Attunement', 'Required'))
    else:
        cats = _named(d.get('equipment_categories'))
        sub = cats[-1] if cats else 'Equipment'
        cost = d.get('cost')
        if isinstance(cost, dict) and cost.get('quantity') is not None:
            facts.append(('Cost', f'{cost["quantity"]} {cost.get("unit", "")}'.strip()))
        if d.get('weight'):
            facts.append(('Weight', f'{d["weight"]} lb'))
        dmg = d.get('damage')
        if isinstance(dmg, dict) and dmg.get('damage_dice'):
            facts.append(('Damage', f'{dmg["damage_dice"]} {(dmg.get("damage_type") or {}).get("name", "")}'.strip()))
        if d.get('armor_class') and isinstance(d['armor_class'], dict):
            ac = d['armor_class']
            facts.append(('Armor Class', str(ac.get('base', '')) + (' + Dex' if ac.get('dex_bonus') else '')))
        if d.get('properties'):
            facts.append(('Properties', ', '.join(_named(d['properties']))))
        if (d.get('mastery') or {}).get('name'):
            facts.append(('Mastery', d['mastery']['name']))
    body = _text(d.get('description') or d.get('desc'))
    if coll == 'magic-items' and body.split('\n\n', 1)[0].lower().startswith(('wondrous', 'weapon', 'armor', 'ring', 'potion', 'rod', 'staff', 'wand', 'scroll')):
        body = body.split('\n\n', 1)[1] if '\n\n' in body else ''
    return {'title': d.get('name', ''), 'subtitle': sub, 'facts': facts, 'body': body}


def detail(kind, ref):
    """One entry ('spells/fireball', 'monsters/aboleth', 'equipment/longsword', 'magic-items/bag-of-holding')."""
    if kind not in KINDS or not isinstance(ref, str) or '/' not in ref:
        raise SrdError('bad request')
    coll, index = ref.split('/', 1)
    if coll not in KINDS[kind] or not valid_index(index):
        raise SrdError('bad request')
    d = _get_json(f'/{coll}/{index}')
    if not isinstance(d, dict) or not d.get('name'):
        raise SrdError('not found')
    if kind == 'spells':
        return _spell(d)
    if kind == 'items':
        return _item(coll, d)
    return {'title': d['name'], 'subtitle': ' '.join(x for x in (d.get('size'), d.get('type')) if x),
            'facts': [('Challenge', _cr(d.get('challenge_rating', 0))), ('Armor Class', str(_monster_prefill(d)['armor_class'])),
                      ('Hit Points', str(d.get('hit_points', ''))), ('Speed', ', '.join(f'{k} {v}' for k, v in (d.get('speed') or {}).items()))],
            'body': '', 'prefill': _monster_prefill(d), 'notes': _monster_notes(d)}
