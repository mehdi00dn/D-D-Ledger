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
    '/equipment/longsword': {'name': 'Longsword', 'cost': {'quantity': 15, 'unit': 'gp'}, 'equipment_categories': [{'name': 'Weapons'}],
                             'desc': ['A blade.']},
    '/magic-items/bag-of-holding': {'name': 'Bag of Holding', 'rarity': {'name': 'Uncommon'}, 'equipment_category': {'name': 'Wondrous Items'},
                                    'desc': ['Wondrous Item', 'Bigger inside.']},
}


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
    del api['/equipment/longsword']['desc']
    assert any('longsword' in p for p in contract.check())


def test_unreachable_endpoint_is_reported(api):
    del api['/magic-items']
    assert any('/magic-items' in p for p in contract.check())
