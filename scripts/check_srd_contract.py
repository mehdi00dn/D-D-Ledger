"""Contract check: does the real 2024 D&D 5e API still look the way srd.py expects?

The normal tests fake the API, so they cannot notice when dnd5eapi.co renames or drops a field -- and srd.py
quietly falls back to defaults (AC 10, 1 HP, empty text), which would ship wrong rules text.  This script fetches
one spell, monster, equipment and magic item for real, checks the raw fields srd.py reads, then runs them through
srd.detail().  Run by the nightly GitHub Actions job (.github/workflows/srd-contract.yml), or by hand:

    python scripts/check_srd_contract.py            # exit 0 = fine, 1 = the API changed (problems listed)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import srd  # noqa: E402

# kind -> (reference, raw fields that must exist with these types)
SAMPLES = {
    'spells': ('spells/fireball', {'name': str, 'level': int, 'school': dict, 'casting_time': str, 'range': str,
                                   'duration': str, 'components': list, 'classes': list}),
    'monsters': ('monsters/goblin-warrior', {'name': str, 'size': str, 'type': str, 'armor_class': list, 'hit_points': int,
                                             'speed': dict, 'strength': int, 'dexterity': int, 'constitution': int,
                                             'intelligence': int, 'wisdom': int, 'charisma': int,
                                             'challenge_rating': (int, float), 'proficiency_bonus': int}),
    'items:equipment': ('equipment/longsword', {'name': str, 'cost': dict, 'equipment_categories': list}),
    'items:magic': ('magic-items/bag-of-holding', {'name': str, 'rarity': dict, 'equipment_category': dict}),
}
LISTS = ('spells', 'monsters', 'equipment', 'magic-items')


def check(get=None):
    """Return a list of human-readable problems (empty = contract holds).  `get` defaults to srd._get_json."""
    get = get or srd._get_json
    problems = []

    for coll in LISTS:
        try:
            rows = get('/' + coll).get('results')
        except srd.SrdError as e:
            problems.append(f'/{coll}: cannot fetch ({e})')
            continue
        if not rows or not all(isinstance(r, dict) and r.get('index') and r.get('name') for r in rows[:50]):
            problems.append(f'/{coll}: list is empty or rows lack index/name')

    for label, (ref, fields) in SAMPLES.items():
        kind = label.split(':')[0]
        try:
            raw = get('/' + ref)
        except srd.SrdError as e:
            problems.append(f'/{ref}: cannot fetch ({e})')
            continue
        for field, typ in fields.items():
            if field not in raw or raw[field] is None:
                problems.append(f'/{ref}: field "{field}" is missing')
            elif not isinstance(raw[field], typ) or isinstance(raw[field], bool):
                problems.append(f'/{ref}: field "{field}" is now {type(raw[field]).__name__}')
        if not (raw.get('description') or raw.get('desc')) and kind != 'monsters':
            problems.append(f'/{ref}: neither "description" nor "desc" has text')
        if kind == 'monsters':
            ac = raw.get('armor_class')
            if not (isinstance(ac, list) and ac and isinstance(ac[0], dict) and isinstance(ac[0].get('value'), int)):
                problems.append(f'/{ref}: armor_class[0].value is not a number')
        try:
            out = srd.detail(kind, ref)
        except srd.SrdError as e:
            problems.append(f'detail({kind!r}, {ref!r}) failed: {e}')
            continue
        if not out.get('title') or not out.get('subtitle'):
            problems.append(f'detail({ref!r}): title or subtitle is empty')
        if kind == 'monsters':
            p = out.get('prefill') or {}
            if p.get('armor_class') == 10 and p.get('max_hp') == 1:
                problems.append(f'detail({ref!r}): prefill fell back to defaults (AC 10 / 1 HP)')
        elif not out.get('facts') or not out.get('body'):
            problems.append(f'detail({ref!r}): facts or body came out empty')
    return problems


def main():
    srd.clear_cache()
    problems = check()
    if problems:
        print('The 5e API no longer matches what srd.py expects:')
        for p in problems:
            print(' -', p)
        return 1
    print('5e API contract OK (%d lists, %d samples)' % (len(LISTS), len(SAMPLES)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
