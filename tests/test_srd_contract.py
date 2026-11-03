"""The contract checker itself (scripts/check_srd_contract.py): passes on well-formed data, flags drift.
The real-network run is the nightly workflow; nothing here touches the network."""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import check_srd_contract as contract  # noqa: E402
import srd  # noqa: E402

GOOD = {
    '/spells': {'results': [{'index': 'fireball', 'name': 'Fireball', 'level': 3}]},
    '/monsters': {'results': [{'index': 'goblin-warrior', 'name': 'Goblin Warrior'}]},
    '/equipment': {'results': [{'index': 'longsword', 'name': 'Longsword'}]},
    '/magic-items': {'results': [{'index': 'bag-of-holding', 'name': 'Bag of Holding'}]},
    '/spells/fireball': {'name': 'Fireball', 'level': 3, 'school': {'name': 'Evocation'}, 'casting_time': 'Action', 'range': '150 feet',
                         'components': ['V', 'S'], 'duration': 'Instantaneous', 'classes': [{'name': 'Wizard'}], 'description': 'Boom.'},
    '/monsters/goblin-warrior': {'name': 'Goblin Warrior', 'size': 'Small', 'type': 'humanoid', 'armor_class': [{'value': 15}],
                                 'hit_points': 10, 'speed': {'walk': '30 ft.'}, 'strength': 8, 'dexterity': 15, 'constitution': 10,
                                 'intelligence': 10, 'wisdom': 8, 'charisma': 8, 'challenge_rating': 0.25, 'proficiency_bonus': 2},
    # Real 2024 shape: weapons have stats but no prose ("description" is an empty list).
    '/equipment/longsword': {'name': 'Longsword', 'cost': {'quantity': 15, 'unit': 'gp'}, 'equipment_categories': [{'name': 'Weapons'}],
                             'damage': {'damage_dice': '1d8', 'damage_type': {'name': 'Slashing'}}, 'properties': [{'name': 'Versatile'}],
                             'mastery': {'name': 'Sap'}, 'description': []},
    '/equipment/backpack': {'name': 'Backpack', 'cost': {'quantity': 2, 'unit': 'gp'}, 'weight': 5,
                            'equipment_categories': [{'name': 'Adventuring Gear'}], 'description': ['Holds up to 30 pounds.']},
    '/magic-items/bag-of-holding': {'name': 'Bag of Holding', 'rarity': {'name': 'Uncommon'}, 'equipment_category': {'name': 'Wondrous Items'},
                                    'desc': ['Wondrous Item', 'Bigger inside.']},
    '/classes/wizard': {'name': 'Wizard', 'hit_die': 6, 'primary_ability': {'desc': 'Intelligence'}, 'saving_throws': [{'name': 'INT'}],
                        'proficiencies': [{'name': 'Simple Weapons'}], 'class_levels': '/api/2024/classes/wizard/levels',
                        'proficiency_choices': [{'desc': 'Choose 2'}]},
    '/classes/wizard/levels': [{'level': 1, 'prof_bonus': 2, 'features': [{'name': 'Spellcasting'}]}],
    '/subclasses/evoker': {'name': 'Evoker', 'class': {'name': 'Wizard'}, 'description': 'Force.',
                           'features': [{'name': 'Potent Cantrip', 'level': 3, 'description': 'Hit harder.'}]},
    '/species/elf': {'name': 'Elf', 'type': 'Humanoid', 'size': 'Medium', 'speed': 30, 'traits': [{'index': 'darkvision-60'}]},
    '/traits/darkvision-60': {'name': 'Darkvision', 'description': 'See in the dark.'},
    '/backgrounds/acolyte': {'name': 'Acolyte', 'ability_scores': [{'name': 'INT'}], 'feat': {'name': 'Magic Initiate'},
                             'proficiencies': [{'name': 'Skill: Insight'}], 'equipment_options': [{'desc': 'Choose A or B'}]},
    '/feats/alert': {'name': 'Alert', 'type': 'origin', 'description': 'Initiative.'},
    '/skills/acrobatics': {'name': 'Acrobatics', 'description': 'Stay up.', 'ability_score': {'name': 'DEX'}},
    '/ability-scores/str': {'name': 'STR', 'full_name': 'Strength', 'description': 'Might', 'skills': [{'name': 'Athletics'}]},
    '/weapon-properties/ammunition': {'name': 'Ammunition', 'description': 'Needs ammo.'},
}
for _coll, _idx, _name in (('classes', 'wizard', 'Wizard'), ('subclasses', 'evoker', 'Evoker'), ('species', 'elf', 'Elf'),
                           ('backgrounds', 'acolyte', 'Acolyte'), ('feats', 'alert', 'Alert'), ('skills', 'acrobatics', 'Acrobatics'),
                           ('ability-scores', 'str', 'STR'), ('weapon-properties', 'ammunition', 'Ammunition'),
                           ('damage-types', 'acid', 'Acid'), ('weapon-mastery-properties', 'cleave', 'Cleave'),
                           ('magic-schools', 'abjuration', 'Abjuration'), ('languages', 'common', 'Common'),
                           ('alignments', 'lawful-good', 'Lawful Good'), ('poisons', 'assassins-blood', "Assassin's Blood")):
    GOOD['/' + _coll] = {'results': [{'index': _idx, 'name': _name}]}


@pytest.fixture
def api(monkeypatch):
    data = copy.deepcopy(GOOD)

    def fake(path):
        if path in data:
            return data[path]
        raise srd.SrdError('not found')
    monkeypatch.setattr(srd, '_get_json', fake)
    return data


def test_well_formed_api_passes(api):
    assert contract.check() == []


def test_renamed_field_is_reported(api):
    del api['/spells/fireball']['casting_time']
    assert any('casting_time' in p for p in contract.check())


def test_changed_type_is_reported(api):
    api['/monsters/goblin-warrior']['armor_class'] = 15
    problems = contract.check()
    assert any('armor_class' in p for p in problems)


def test_missing_description_is_reported(api):
    api['/equipment/backpack']['description'] = []
    assert any('backpack' in p for p in contract.check())


def test_weapon_without_prose_is_fine_but_without_stats_is_not(api):
    assert contract.check() == []          # longsword has "description": [] and that is the real 2024 shape
    del api['/equipment/longsword']['mastery']
    assert any('longsword' in p and 'mastery' in p for p in contract.check())
    api['/equipment/longsword']['mastery'] = {'name': 'Sap'}
    api['/equipment/longsword']['damage'] = '1d8'
    assert any('longsword' in p and 'damage' in p for p in contract.check())


def test_unreachable_endpoint_is_reported(api):
    del api['/magic-items']
    assert any('/magic-items' in p for p in contract.check())
