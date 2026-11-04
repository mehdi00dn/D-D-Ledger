"""Contract check: do the real 2024 and 2014 D&D 5e APIs still look the way srd.py expects?

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
    # Gear carries rules prose in "description" ...
    'items:equipment': ('equipment/backpack', {'name': str, 'cost': dict, 'equipment_categories': list}),
    # ... but the 2024 SRD gives weapons (and armor, tools, ammunition) stat blocks only: the API returns "description": [].
    # So this sample must still have the stat fields srd.py turns into facts, but not any prose.  (Third item: text required?)
    'items:weapon': ('equipment/longsword', {'name': str, 'cost': dict, 'equipment_categories': list, 'damage': dict,
                                             'properties': list, 'mastery': dict}, False),
    'items:magic': ('magic-items/bag-of-holding', {'name': str, 'rarity': dict, 'equipment_category': dict}),
    'classes': ('classes/wizard', {'name': str, 'hit_die': int, 'primary_ability': dict, 'saving_throws': list,
                                   'proficiencies': list, 'class_levels': str}),
    'classes:sub': ('subclasses/evoker', {'name': str, 'class': dict, 'features': list}),
    'species': ('species/elf', {'name': str, 'type': str, 'size': str, 'speed': int, 'traits': list}),
    'backgrounds': ('backgrounds/acolyte', {'name': str, 'ability_scores': list, 'feat': dict, 'proficiencies': list,
                                            'equipment_options': list}),
    'feats': ('feats/alert', {'name': str, 'type': str, 'description': str}),
    'rules:skill': ('skills/acrobatics', {'name': str, 'description': str, 'ability_score': dict}),
    'rules:ability': ('ability-scores/str', {'name': str, 'full_name': str, 'description': str, 'skills': list}),
    'rules:weapon': ('weapon-properties/ammunition', {'name': str, 'description': str}),
}
LISTS = ('spells', 'monsters', 'equipment', 'magic-items', 'classes', 'subclasses', 'species', 'backgrounds', 'feats', 'skills',
         'ability-scores', 'damage-types', 'weapon-properties', 'weapon-mastery-properties', 'magic-schools', 'languages',
         'alignments', 'poisons')

# The 2014 edition (https://www.dnd5eapi.co/api/2014): the same checks with its own field names (races, desc lists, no mastery or poisons).
SAMPLES_2014 = {
    'spells': ('spells/fireball', {'name': str, 'level': int, 'school': dict, 'casting_time': str, 'range': str,
                                   'duration': str, 'components': list, 'classes': list, 'desc': list}),
    'monsters': ('monsters/goblin', {'name': str, 'size': str, 'type': str, 'armor_class': list, 'hit_points': int,
                                     'speed': dict, 'strength': int, 'dexterity': int, 'constitution': int,
                                     'intelligence': int, 'wisdom': int, 'charisma': int,
                                     'challenge_rating': (int, float), 'proficiency_bonus': int}),
    'items:weapon': ('equipment/longsword', {'name': str, 'cost': dict, 'equipment_category': dict, 'damage': dict,
                                             'properties': list}, False),
    'items:magic': ('magic-items/bag-of-holding', {'name': str, 'rarity': dict, 'equipment_category': dict, 'desc': list}),
    'classes': ('classes/wizard', {'name': str, 'hit_die': int, 'saving_throws': list, 'proficiencies': list, 'class_levels': str}),
    'classes:sub': ('subclasses/evocation', {'name': str, 'class': dict, 'desc': list, 'subclass_levels': str}),
    'species': ('races/elf', {'name': str, 'speed': int, 'size': str, 'traits': list, 'ability_bonuses': list}),
    'species:sub': ('subraces/high-elf', {'name': str, 'race': dict, 'racial_traits': list}),
    'backgrounds': ('backgrounds/acolyte', {'name': str, 'starting_proficiencies': list, 'feature': dict}),
    'feats': ('feats/grappler', {'name': str, 'prerequisites': list, 'desc': list}),
    'rules:skill': ('skills/acrobatics', {'name': str, 'desc': list, 'ability_score': dict}),
    'rules:rule': ('rules/adventuring', {'name': str, 'desc': str}),
}
LISTS_2014 = ('spells', 'monsters', 'equipment', 'magic-items', 'classes', 'subclasses', 'races', 'subraces', 'backgrounds', 'feats',
              'skills', 'ability-scores', 'damage-types', 'weapon-properties', 'magic-schools', 'languages', 'alignments', 'rules',
              'rule-sections')
EDITIONS = {'2024': (SAMPLES, LISTS), '2014': (SAMPLES_2014, LISTS_2014)}


def check(get=None, edition='2024'):
    """Return a list of human-readable problems (empty = contract holds).  `get` defaults to srd._get_json for this edition."""
    get = get or (lambda path: srd._get_json(path, edition))
    samples, lists = EDITIONS[edition]
    tag = '' if edition == '2024' else f'[{edition}] '
    problems = []

    for coll in lists:
        try:
            rows = get('/' + coll).get('results')
        except srd.SrdError as e:
            problems.append(f'{tag}/{coll}: cannot fetch ({e})')
            continue
        if not rows or not all(isinstance(r, dict) and r.get('index') and r.get('name') for r in rows[:50]):
            problems.append(f'{tag}/{coll}: list is empty or rows lack index/name')

    for label, (ref, fields, *opt) in samples.items():
        kind = label.split(':')[0]
        needs_text = opt[0] if opt else True
        try:
            raw = get('/' + ref)
        except srd.SrdError as e:
            problems.append(f'{tag}/{ref}: cannot fetch ({e})')
            continue
        for field, typ in fields.items():
            if field not in raw or raw[field] is None:
                problems.append(f'{tag}/{ref}: field "{field}" is missing')
            elif not isinstance(raw[field], typ) or isinstance(raw[field], bool):
                problems.append(f'{tag}/{ref}: field "{field}" is now {type(raw[field]).__name__}')
        if needs_text and not (raw.get('description') or raw.get('desc')) and kind not in ('monsters', 'classes', 'species', 'backgrounds'):
            problems.append(f'{tag}/{ref}: neither "description" nor "desc" has text')
        if kind == 'monsters':
            ac = raw.get('armor_class')
            if not (isinstance(ac, list) and ac and isinstance(ac[0], dict) and isinstance(ac[0].get('value'), int)):
                problems.append(f'{tag}/{ref}: armor_class[0].value is not a number')
        try:
            out = srd.detail(kind, ref, edition=edition)
        except srd.SrdError as e:
            problems.append(f'{tag}detail({kind!r}, {ref!r}) failed: {e}')
            continue
        if not out.get('title') or not out.get('subtitle'):
            problems.append(f'{tag}detail({ref!r}): title or subtitle is empty')
        if kind == 'monsters':
            p = out.get('prefill') or {}
            if p.get('armor_class') == 10 and p.get('max_hp') == 1:
                problems.append(f'{tag}detail({ref!r}): prefill fell back to defaults (AC 10 / 1 HP)')
        elif (not out.get('facts') and kind != 'rules') or (needs_text and not out.get('body')):
            problems.append(f'{tag}detail({ref!r}): facts or body came out empty')
    return problems


def main():
    srd.clear_cache()
    problems = []
    for edition in EDITIONS:
        problems += check(edition=edition)
    if problems:
        print('The 5e API no longer matches what srd.py expects:')
        for p in problems:
            print(' -', p)
        return 1
    print('5e API contract OK (%s)' % ', '.join(f'{ed}: {len(l)} lists, {len(sm)} samples' for ed, (sm, l) in EDITIONS.items()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
