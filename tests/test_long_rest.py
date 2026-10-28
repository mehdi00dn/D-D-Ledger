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


def test_short_rest_heals_only_what_the_dm_types(make_user):
    dm, ann = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, ann)
    hero = dm.add_to_battle(cid, ann.new_character(cid, name='Hero', max_hp=20))
    other = dm.add_to_battle(cid, ann.new_character(cid, name='Other', max_hp=20))
    orc = dm.add_to_battle(cid, dm.new_character(cid, name='Orc', max_hp=30, is_npc='on'))
    for pid in (hero, other, orc):
        dm.json(f'/campaigns/{cid}/api/battle/{pid}/damage', {'amount': 10})
    url = f'/campaigns/{cid}/api/battle/short-rest'
    assert ann.json(url, {'heals': {str(hero): 5}}).status_code == 403                       # Players cannot
    rows = {r['id']: r for r in dm.json(url, {'heals': {str(hero): 4, str(orc): 9, str(other): 'x'}}).get_json()}
    assert rows[hero]['current_hp'] == 14 and rows[other]['current_hp'] == 10 and rows[orc]['current_hp'] == 20   # NPC and blank untouched
    rows = {r['id']: r for r in dm.json(url, {'heals': {str(hero): 500}}).get_json()}
    assert rows[hero]['current_hp'] == 20                                                     # never past the maximum
    log = [e['text'] for e in dm.get(f'/campaigns/{cid}/api/battle/log').get_json()]
    assert any(t.startswith('The party took a short rest: Hero +4') for t in log)
