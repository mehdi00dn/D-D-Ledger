"""Rules reference (spells, monsters, items, conditions, classes, species, backgrounds, feats and the basic rules) from the public D&D 5e API, 2024 edition (https://www.dnd5eapi.co/api/2024) or, on request, the 2014 edition (https://www.dnd5eapi.co/api/2014).

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

VERSION = DEFAULT_EDITION = '2024'
BASE = os.environ.get('SRD_API_BASE', 'https://www.dnd5eapi.co').rstrip('/')      # overridable so tests can use a local stand-in
API = f'{BASE}/api/{VERSION}'            # the default edition's address (kept for callers and tests)
TIMEOUT = 8
LIST_TTL = 24 * 3600
DETAIL_TTL = 24 * 3600
MAX_RESULTS = 120
INDEX_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,80}$')

# kind -> the API collections it is served from (items, classes and rules are several collections on the API)
KINDS = {
    'spells': ['spells'], 'monsters': ['monsters'], 'items': ['equipment', 'magic-items'], 'conditions': ['conditions'],
    'classes': ['classes', 'subclasses'], 'species': ['species'], 'backgrounds': ['backgrounds'], 'feats': ['feats'],
    'rules': ['skills', 'ability-scores', 'damage-types', 'weapon-properties', 'weapon-mastery-properties', 'magic-schools',
              'languages', 'alignments', 'poisons'],
}
# what the list shows under a name when it is not a spell level (so mixed lists such as Rules read clearly)
COLL_LABEL = {
    'magic-items': 'Magic item', 'classes': 'Class', 'subclasses': 'Subclass', 'species': 'Species', 'backgrounds': 'Background',
    'feats': 'Feat', 'skills': 'Skill', 'ability-scores': 'Ability score', 'damage-types': 'Damage type',
    'weapon-properties': 'Weapon property', 'weapon-mastery-properties': 'Weapon mastery', 'magic-schools': 'School of magic',
    'languages': 'Language', 'alignments': 'Alignment', 'poisons': 'Poison',
}
MAX_TRAITS = 14
# The 2014 API names things differently: races (and subraces) instead of species, no weapon mastery or poisons, plus rules and rule sections.
KINDS_2014 = {
    'spells': ['spells'], 'monsters': ['monsters'], 'items': ['equipment', 'magic-items'], 'conditions': ['conditions'],
    'classes': ['classes', 'subclasses'], 'species': ['races', 'subraces'], 'backgrounds': ['backgrounds'], 'feats': ['feats'],
    'rules': ['skills', 'ability-scores', 'damage-types', 'weapon-properties', 'magic-schools', 'languages', 'alignments',
              'rules', 'rule-sections'],
}
COLL_LABEL.update({'races': 'Race', 'subraces': 'Subrace', 'rules': 'Rule', 'rule-sections': 'Rule'})
# edition -> what the page needs to know: its kinds, the tab for "species", and the licence line
EDITIONS = {
    '2024': {'kinds': KINDS, 'species_tab': 'Species', 'label': '2024 rules', 'srd': 'System Reference Document 5.2.1'},
    '2014': {'kinds': KINDS_2014, 'species_tab': 'Races', 'label': '2014 rules', 'srd': 'System Reference Document 5.1'},
}


def edition_of(value):
    """The edition asked for ('2014' or '2024'); anything else, or nothing, is the default.  Raises nothing: callers that must
    refuse a bad value (the API) use check_edition."""
    return value if value in EDITIONS else DEFAULT_EDITION


def check_edition(value):
    if value in (None, ''):
        return DEFAULT_EDITION
    if value not in EDITIONS:
        raise SrdError('bad request')
    return value


class SrdError(Exception):
    """The API could not be reached or answered with something unusable."""


_lock = threading.Lock()
_cache = {}


def _get_json(path, edition=DEFAULT_EDITION):
    key = (edition, path)
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    try:
        r = requests.get(f'{BASE}/api/{edition}{path}', timeout=TIMEOUT, headers={'Accept': 'application/json'})
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
        _cache[key] = (now + (LIST_TTL if path.count('/') <= 1 else DETAIL_TTL), data)
    return data


def clear_cache():
    with _lock:
        _cache.clear()


def valid_index(value):
    return isinstance(value, str) and bool(INDEX_RE.match(value))


def search(kind, query='', limit=MAX_RESULTS, edition=DEFAULT_EDITION):
    """[{'id': 'monsters/aboleth', 'name': 'Aboleth', 'sub': ...}] -- names containing the query, best matches first."""
    ed = check_edition(edition)
    kinds = EDITIONS[ed]['kinds']
    if kind not in kinds:
        raise SrdError('bad request')
    q = (query or '').strip().lower()[:60]
    out = []
    for coll in kinds[kind]:
        try:
            data = _get_json('/' + coll, ed)
        except SrdError as e:
            if str(e) == 'not found' and ed != DEFAULT_EDITION:
                continue          # a collection the other edition does not have costs that part of the list only
            raise
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
            elif coll in COLL_LABEL:
                entry['sub'] = COLL_LABEL[coll]
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


def _monster_attacks(d):
    """The stat block's attack rolls as attacks for the Attacks card: the actions that carry a to-hit bonus, each with its
    first damage entry (the card holds one kind of die plus a flat bonus; riders such as "+1d4 with advantage" stay in the notes)."""
    out = []
    for act in d.get('actions') or []:
        if not isinstance(act, dict) or not act.get('name') or not isinstance(act.get('attack_bonus'), (int, float)):
            continue
        dmg = next((x for x in act.get('damage') or [] if isinstance(x, dict) and x.get('damage_dice')), None)
        kind = ((dmg or {}).get('damage_type') or {}).get('name', '') if isinstance((dmg or {}).get('damage_type'), dict) else ''
        out.append({'name': act['name'], 'attack_bonus': int(act['attack_bonus']),
                    'damage': (dmg or {}).get('damage_dice', ''), 'damage_type': str(kind).lower()})
    return dnd5e.clean_attacks(out)


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
        'hp_roll': str(d.get('hit_points_roll') or ''),
        'attacks': _monster_attacks(d),
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
        first = str((d.get('desc') or [''])[0] if isinstance(d.get('desc'), list) and d.get('desc') else '').lower()
        if d.get('attunement') or 'requires attunement' in first:          # 2014 notes it in the first line of the text
            facts.append(('Attunement', 'Required'))
    else:
        cats = _named(d.get('equipment_categories') or [d.get('equipment_category')])
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
        if d.get('category_range'):                                        # 2014 weapons: "Martial Melee"
            facts.append(('Category', str(d['category_range'])))
        if d.get('properties'):
            facts.append(('Properties', ', '.join(_named(d['properties']))))
        if (d.get('mastery') or {}).get('name'):
            facts.append(('Mastery', d['mastery']['name']))
    body = _text(d.get('description') or d.get('desc'))
    if coll == 'magic-items' and body.split('\n\n', 1)[0].lower().startswith(('wondrous', 'weapon', 'armor', 'ring', 'potion', 'rod', 'staff', 'wand', 'scroll')):
        body = body.split('\n\n', 1)[1] if '\n\n' in body else ''
    return {'title': d.get('name', ''), 'subtitle': sub, 'facts': facts, 'body': body}


def _condition(d):
    """A condition's rules text.  The API marks bold with ** which the page shows as plain text, so those are dropped."""
    body = _text(d.get('description') or d.get('desc')).replace('**', '')
    return {'title': d.get('name', ''), 'subtitle': 'Condition', 'facts': [], 'body': body}


def _plain(value):
    """Rules text with the API's ** bold marks dropped (the page shows plain text)."""
    return re.sub(r'^#{1,6}\s*', '', _text(value).replace('**', ''), flags=re.M)      # and markdown heading marks (2014 rules text)


def _signed(n):
    return f'+{n}' if isinstance(n, int) and n >= 0 else str(n)


def _cell(value):
    return str(value).replace('|', '/').replace('\n', ' ').strip()


def _level_table(levels):
    """'| Level | Proficiency bonus | Features |' lines (the page turns consecutive table lines into a real table)."""
    rows = levels.get('results') if isinstance(levels, dict) else levels
    seen, keep = set(), []
    for r in sorted((r for r in rows or [] if isinstance(r, dict) and isinstance(r.get('level'), int)
                     and isinstance(r.get('prof_bonus'), int)), key=lambda r: r['level']):
        if r['level'] not in seen:          # one row per level (subclass level rows carry no proficiency bonus)
            seen.add(r['level'])
            keep.append(r)
    rows = keep
    if not rows:
        return ''
    lines = ['| Level | Proficiency bonus | Features |', '|---|---|---|']
    for r in rows:
        feats = ', '.join(_named(r.get('features'))) or '-'
        lines.append(f'| {r["level"]} | {_signed(r.get("prof_bonus", ""))} | {_cell(feats)} |')
    return '\n'.join(lines)


def _class(d):
    prof = [n for n in _named(d.get('proficiencies')) if not n.startswith('Saving Throw')]
    pre = [f'{(p.get("ability_score") or {}).get("name", "")} {p.get("minimum_score", "")}'.strip()
           for p in (d.get('multi_classing') or {}).get('prerequisites') or [] if isinstance(p, dict)]
    spell = d.get('spellcasting') if isinstance(d.get('spellcasting'), dict) else {}
    facts = [('Hit die', f'd{d["hit_die"]}' if d.get('hit_die') else ''),
             ('Primary ability', (d.get('primary_ability') or {}).get('desc', '')),
             ('Saving throws', ', '.join(_named(d.get('saving_throws')))),
             ('Proficiencies', ', '.join(prof)),
             ('Spellcasting', (spell.get('spellcasting_ability') or {}).get('name', '') and
              f'{spell["spellcasting_ability"]["name"]} (from level {spell.get("level", 1)})'),
             ('Multiclassing', ', '.join(pre)),
             ('Subclasses', ', '.join(_named(d.get('subclasses'))))]
    parts = [str(c.get('desc')) for c in d.get('proficiency_choices') or [] if isinstance(c, dict) and c.get('desc')]
    fixed = [f'{(e.get("equipment") or {}).get("name", "")} x{e.get("quantity", 1)}' for e in d.get('starting_equipment') or []
             if isinstance(e, dict) and (e.get('equipment') or {}).get('name')]
    if fixed:
        parts.append('Starting equipment: ' + ', '.join(fixed))                # 2014; the 2024 API gives only the choices below
    parts += ['Starting equipment: ' + str(o['desc']) for o in d.get('starting_equipment_options') or [] if isinstance(o, dict) and o.get('desc')]
    for info in spell.get('info') or []:
        if isinstance(info, dict) and info.get('name'):
            parts.append(f'{info["name"]}. {_plain(info.get("desc"))}')
    return {'title': d.get('name', ''), 'subtitle': 'Class', 'facts': [(k, v) for k, v in facts if v], 'body': '\n\n'.join(parts)}


def _subclass(d):
    parts = [_plain(d.get('description') or d.get('summary'))]
    for f in d.get('features') or []:
        if isinstance(f, dict) and f.get('name'):
            parts.append(f'Level {f.get("level", "")}: {f["name"]}. {_plain(f.get("description"))}'.replace('Level : ', ''))
    cls = (d.get('class') or {}).get('name', '')
    return {'title': d.get('name', ''), 'subtitle': f'{cls} subclass'.strip(), 'facts': [('Class', cls)] if cls else [],
            'body': '\n\n'.join(p for p in parts if p)}


def _trait_paragraphs(traits, ed):
    parts = []
    for t in (traits or [])[:MAX_TRAITS]:
        idx = t.get('index') if isinstance(t, dict) else None
        if not valid_index(idx):
            continue
        try:
            td = _get_json(f'/traits/{idx}', ed)
        except SrdError:
            continue          # one missing trait costs that paragraph, not the entry
        if isinstance(td, dict) and td.get('name'):
            parts.append(f'{td["name"]}. {_plain(td.get("description") or td.get("desc"))}')
    return parts


def _species(d, ed=DEFAULT_EDITION):
    facts = [('Type', d.get('type')), ('Size', d.get('size')), ('Speed', f'{d["speed"]} ft.' if d.get('speed') else ''),
             ('Lineages', ', '.join(_named(d.get('subspecies'))))]
    parts = _trait_paragraphs(d.get('traits'), ed)
    return {'title': d.get('name', ''), 'subtitle': 'Species', 'facts': [(k, v) for k, v in facts if v], 'body': '\n\n'.join(parts)}


def _bonuses(items):
    return ', '.join(f'{(b.get("ability_score") or {}).get("name", "")} {_signed(b.get("bonus", 0))}'.strip()
                     for b in items or [] if isinstance(b, dict) and (b.get('ability_score') or {}).get('name'))


def _race(d, ed):
    """2014: a race (the 2024 'species')."""
    facts = [('Size', d.get('size')), ('Speed', f'{d["speed"]} ft.' if d.get('speed') else ''),
             ('Ability scores', _bonuses(d.get('ability_bonuses'))), ('Age', d.get('age')), ('Alignment', d.get('alignment')),
             ('Languages', d.get('language_desc')), ('Subraces', ', '.join(_named(d.get('subraces'))))]
    parts = [_plain(d.get('size_description'))] + _trait_paragraphs(d.get('traits'), ed)
    return {'title': d.get('name', ''), 'subtitle': 'Race', 'facts': [(k, str(v)) for k, v in facts if v],
            'body': '\n\n'.join(p for p in parts if p)}


def _subrace(d, ed):
    race = (d.get('race') or {}).get('name', '')
    facts = [('Race', race), ('Ability scores', _bonuses(d.get('ability_bonuses')))]
    parts = [_plain(d.get('desc'))] + _trait_paragraphs(d.get('racial_traits'), ed)
    return {'title': d.get('name', ''), 'subtitle': f'{race} subrace'.strip(), 'facts': [(k, v) for k, v in facts if v],
            'body': '\n\n'.join(p for p in parts if p)}


def _subclass_2014(d, ed):
    """2014: the subclass text, then its features level by level (each feature is its own entry on the API)."""
    cls = (d.get('class') or {}).get('name', '')
    parts = [_plain(d.get('subclass_flavor')), _plain(d.get('desc'))]
    path = str(d.get('subclass_levels') or '')
    path = path[len('/api/' + ed):] if path.startswith('/api/' + ed) else ''
    try:
        levels = _get_json(path, ed) if path else []
    except SrdError:
        levels = []                    # the subclass text stands without its feature list
    seen = set()
    for row in sorted((r for r in levels if isinstance(r, dict) and isinstance(r.get('level'), int)), key=lambda r: r['level']):
        for f in row.get('features') or []:
            idx = f.get('index') if isinstance(f, dict) else None
            if not valid_index(idx) or idx in seen or len(seen) >= MAX_TRAITS:
                continue
            seen.add(idx)
            try:
                fd = _get_json(f'/features/{idx}', ed)
            except SrdError:
                continue
            if isinstance(fd, dict) and fd.get('name'):
                parts.append(f'Level {row["level"]}: {fd["name"]}. {_plain(fd.get("desc"))}')
    return {'title': d.get('name', ''), 'subtitle': f'{cls} subclass'.strip(), 'facts': [('Class', cls)] if cls else [],
            'body': '\n\n'.join(p for p in parts if p)}


def _background(d):
    if d.get('starting_proficiencies') is not None or d.get('feature'):      # 2014
        feature = d.get('feature') if isinstance(d.get('feature'), dict) else {}
        prof = [n.split(': ', 1)[-1] for n in _named(d.get('starting_proficiencies'))]
        gold = d.get('starting_gold') if isinstance(d.get('starting_gold'), dict) else {}
        facts = [('Proficiencies', ', '.join(prof)), ('Feature', feature.get('name', '')),
                 ('Starting gold', f'{gold["quantity"]} {gold.get("unit", "gp")}' if gold.get('quantity') else '')]
        fixed = [f'{(e.get("equipment") or {}).get("name", "")} x{e.get("quantity", 1)}' for e in d.get('starting_equipment') or []
                 if isinstance(e, dict) and (e.get('equipment') or {}).get('name')]
        parts = [f'{feature["name"]}. {_plain(feature.get("desc"))}' if feature.get('name') else '']
        parts.append('Equipment: ' + ', '.join(fixed) if fixed else '')
        parts += ['Equipment choice: ' + str(o['desc']) for o in d.get('starting_equipment_options') or [] if isinstance(o, dict) and o.get('desc')]
        return {'title': d.get('name', ''), 'subtitle': 'Background', 'facts': [(k, v) for k, v in facts if v],
                'body': '\n\n'.join(p for p in parts if p)}
    feat = d.get('feat') if isinstance(d.get('feat'), dict) else {}
    feat_text = feat.get('name', '') + (f' ({feat["note"]})' if feat.get('note') else '') if feat.get('name') else ''
    prof = [n.split(': ', 1)[-1] for n in _named(d.get('proficiencies'))]
    facts = [('Ability scores', ', '.join(_named(d.get('ability_scores')))), ('Feat', feat_text), ('Proficiencies', ', '.join(prof))]
    body = '\n\n'.join('Equipment: ' + str(o['desc']) for o in d.get('equipment_options') or [] if isinstance(o, dict) and o.get('desc'))
    return {'title': d.get('name', ''), 'subtitle': 'Background', 'facts': [(k, v) for k, v in facts if v], 'body': body}


def _feat(d):
    kind = str(d.get('type') or '').replace('-', ' ')
    facts = [('Category', kind.capitalize())] if kind else []
    pre = [f'{(p.get("ability_score") or {}).get("name", "")} {p.get("minimum_score", "")}'.strip()      # 2014
           for p in d.get('prerequisites') or [] if isinstance(p, dict) and (p.get('ability_score') or {}).get('name')]
    if pre:
        facts.append(('Prerequisite', ', '.join(pre)))
    return {'title': d.get('name', ''), 'subtitle': f'{kind.capitalize()} feat' if kind else 'Feat',
            'facts': facts, 'body': _plain(d.get('description') or d.get('desc'))}


def _rule(coll, d):
    label = COLL_LABEL.get(coll, 'Rule')
    title, facts, body = d.get('name', ''), [], _plain(d.get('description') or d.get('desc'))
    if coll == 'skills' and d.get('ability_score'):
        facts.append(('Ability', (d['ability_score'] or {}).get('name', '')))
    elif coll == 'ability-scores':
        title = d.get('full_name') or title
        facts += [('Abbreviation', d.get('name', '')), ('Skills', ', '.join(_named(d.get('skills'))))]
    elif coll == 'alignments' and d.get('abbreviation'):
        facts.append(('Abbreviation', d['abbreviation']))
    elif coll == 'languages':
        if d.get('type') or d.get('script'):                                    # 2014: type, script and who speaks it
            facts += [('Type', d.get('type')), ('Script', d.get('script')), ('Typical speakers', ', '.join(str(x) for x in d.get('typical_speakers') or []))]
        else:
            facts.append(('Rarity', 'Rare' if d.get('is_rare') else 'Standard'))
        body = body or str(d.get('note') or '')
    elif coll == 'poisons':
        facts += [('Type', str(d.get('type') or '').capitalize()), ('Cost', f'{d["cost"]} gp' if d.get('cost') is not None else '')]
    return {'title': title, 'subtitle': label, 'facts': [(k, v) for k, v in facts if v], 'body': body}


def detail(kind, ref, edition=DEFAULT_EDITION):
    """One entry ('spells/fireball', 'monsters/aboleth', 'equipment/longsword', 'magic-items/bag-of-holding')."""
    ed = check_edition(edition)
    kinds = EDITIONS[ed]['kinds']
    if kind not in kinds or not isinstance(ref, str) or '/' not in ref:
        raise SrdError('bad request')
    coll, index = ref.split('/', 1)
    if coll not in kinds[kind] or not valid_index(index):
        raise SrdError('bad request')
    d = _get_json(f'/{coll}/{index}', ed)
    if not isinstance(d, dict) or not d.get('name'):
        raise SrdError('not found')
    if kind == 'spells':
        return _spell(d)
    if kind == 'items':
        return _item(coll, d)
    if kind == 'conditions':
        return _condition(d)
    if kind == 'classes':
        if coll == 'subclasses':
            return _subclass_2014(d, ed) if ed == '2014' else _subclass(d)
        out = _class(d)
        path = str(d.get('class_levels') or f'/classes/{index}/levels')
        path = path[len('/api/' + ed):] if path.startswith('/api/' + ed) else f'/classes/{index}/levels'
        try:
            table = _level_table(_get_json(path, ed))
        except SrdError:
            table = ''        # the class text stands without its level table
        out['body'] = '\n\n'.join(x for x in (out['body'], table) if x)
        return out
    if kind == 'species':
        if coll == 'races':
            return _race(d, ed)
        if coll == 'subraces':
            return _subrace(d, ed)
        return _species(d, ed)
    if kind == 'backgrounds':
        return _background(d)
    if kind == 'feats':
        return _feat(d)
    if kind == 'rules':
        return _rule(coll, d)
    return {'title': d['name'], 'subtitle': ' '.join(x for x in (d.get('size'), d.get('type')) if x),
            'facts': [('Challenge', _cr(d.get('challenge_rating', 0))), ('Armor Class', str(_monster_prefill(d)['armor_class'])),
                      ('Hit Points', str(d.get('hit_points', ''))), ('Speed', ', '.join(f'{k} {v}' for k, v in (d.get('speed') or {}).items()))],
            'body': '', 'prefill': _monster_prefill(d), 'notes': _monster_notes(d),
            'cr': _cr(d.get('challenge_rating', 0)), 'xp': max(0, int(d.get('xp') or 0))}
