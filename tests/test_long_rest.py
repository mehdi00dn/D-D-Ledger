"""DL-149: the DM's Long Rest button - player characters back to full HP, no temp HP, death saves cleared; NPCs untouched."""
import pytest
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def test_long_rest_heals_the_party_and_leaves_npcs_alone(make_user):
    dm, ann = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, ann)
    hero = dm.add_to_battle(cid, ann.new_character(cid, name='Hero', max_hp=20))
    down = dm.add_to_battle(cid, ann.new_character(cid, name='Downed', max_hp=12))
    orc = dm.add_to_battle(cid, dm.new_character(cid, name='Orc', max_hp=30, is_npc='on'))
    dm.json(f'/campaigns/{cid}/api/battle/{hero}/damage', {'amount': 8})
    dm.json(f'/campaigns/{cid}/api/battle/{hero}/temphp', {'value': 5})
    dm.json(f'/campaigns/{cid}/api/battle/{down}/damage', {'amount': 99})
    dm.json(f'/campaigns/{cid}/api/battle/{orc}/damage', {'amount': 10})
    assert ann.json(f'/campaigns/{cid}/api/battle/long-rest').status_code == 403            # Players cannot
    rows = {r['id']: r for r in dm.json(f'/campaigns/{cid}/api/battle/long-rest').get_json()}
    assert rows[hero]['current_hp'] == 20 and not rows[hero].get('temp_hp')
    assert rows[down]['current_hp'] == 12 and not rows[down]['is_dead']
    assert rows[orc]['current_hp'] == 20                                                      # untouched
    assert 'The party took a long rest' in [e['text'] for e in dm.get(f'/campaigns/{cid}/api/battle/log').get_json()]
