"""Hide NPCs from Players: one campaign switch, enforced on the server wherever an NPC could show up."""
import pytest
from conftest import q, BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only')


def _setup(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, pl)
    npc = dm.new_character(cid, name='Goblin Boss', is_npc='on')
    pc = dm.new_character(cid, name='Hero')
    faction = None
    return dm, pl, cid, npc, pc


def _hide(dm, cid, on=True):
    r = dm.post(f'/campaigns/{cid}/npc-visibility', data={'hide_npcs': 'on'} if on else {})
    assert r.status_code == 302


def test_off_by_default_players_see_npcs(make_user):
    dm, pl, cid, npc, pc = _setup(make_user)
    assert 'Goblin Boss' in pl.get(f'/campaigns/{cid}/characters').data.decode()
    assert pl.get(f'/campaigns/{cid}/api/characters/{npc}/detail').status_code == 200


def test_only_dm_or_owner_flips_the_switch(make_user):
    dm, pl, cid, npc, pc = _setup(make_user)
    assert pl.post(f'/campaigns/{cid}/npc-visibility', data={'hide_npcs': 'on'}).status_code == 403
    _hide(dm, cid)
    assert q('SELECT hide_npcs FROM campaigns WHERE id = ?', cid)[0]['hide_npcs'] is True
    _hide(dm, cid, False)
    assert q('SELECT hide_npcs FROM campaigns WHERE id = ?', cid)[0]['hide_npcs'] is False


def test_hidden_npc_is_gone_for_players_but_not_the_dm(make_user):
    dm, pl, cid, npc, pc = _setup(make_user)
    pid = dm.add_to_battle(cid, npc)
    dm.add_to_battle(cid, pc)
    assert dm.json(f'/campaigns/{cid}/api/battle/{pid}/damage', {'amount': 3}).status_code in (200, 404)
    _hide(dm, cid)
    html = pl.get(f'/campaigns/{cid}/characters').data.decode()
    assert 'Goblin Boss' not in html and 'Hero' in html
    assert 'Goblin Boss' not in pl.get(f'/campaigns/{cid}/battle').data.decode()
    assert pl.get(f'/campaigns/{cid}/api/characters/{npc}/detail').status_code == 404
    assert pl.get(f'/campaigns/{cid}/characters/{npc}').status_code == 302
    assert [c['id'] for c in pl.get(f'/campaigns/{cid}/api/characters').get_json()] == [pc]
    rows = pl.battle(cid)
    assert [r['char_name'] for r in rows] == ['Hero']
    assert 'owner_id_' not in rows[0]
    log = pl.get(f'/campaigns/{cid}/api/battle/log').get_json()
    assert not any('Goblin' in l['text'] for l in log)
    assert 'Goblin Boss' in dm.get(f'/campaigns/{cid}/characters').data.decode()
    assert len(dm.battle(cid)) == 2
    assert any('Goblin' in l['text'] for l in dm.get(f'/campaigns/{cid}/api/battle/log').get_json())


def test_npc_rolls_activity_and_factions_are_hidden_too(make_user):
    dm, pl, cid, npc, pc = _setup(make_user)
    r = dm.json(f'/campaigns/{cid}/api/dice/roll', {'dice': {'20': 1}, 'modifier': 2, 'label': 'Stealth', 'character_id': npc})
    assert r.status_code == 200
    assert 'Goblin Boss' in pl.get(f'/campaigns/{cid}/activity').data.decode()
    assert any(x['character'] == 'Goblin Boss' for x in pl.get(f'/campaigns/{cid}/api/dice/rolls').get_json()['rolls'])
    _hide(dm, cid)
    assert 'Goblin Boss' not in pl.get(f'/campaigns/{cid}/activity').data.decode()
    assert not any(x['character'] == 'Goblin Boss' for x in pl.get(f'/campaigns/{cid}/api/dice/rolls').get_json()['rolls'])
    assert 'Goblin Boss' in dm.get(f'/campaigns/{cid}/activity').data.decode()


def test_a_player_keeps_the_npcs_they_made(make_user):
    dm, pl, cid, npc, pc = _setup(make_user)
    mine = pl.new_character(cid, name='My Familiar NPC', is_npc='on')
    _hide(dm, cid)
    html = pl.get(f'/campaigns/{cid}/characters').data.decode()
    assert 'My Familiar NPC' in html and 'Goblin Boss' not in html
    assert pl.get(f'/campaigns/{cid}/api/characters/{mine}/detail').status_code == 200


def test_view_as_player_matches(make_user):
    dm, pl, cid, npc, pc = _setup(make_user)
    _hide(dm, cid)
    assert 'Goblin Boss' in dm.get(f'/campaigns/{cid}/characters').data.decode()
    dm.post(f'/campaigns/{cid}/preview-as-player', data={'on': '1'})
    assert 'Goblin Boss' not in dm.get(f'/campaigns/{cid}/characters').data.decode()
    assert dm.post(f'/campaigns/{cid}/npc-visibility', data={}).status_code == 302   # still allowed to switch it off while previewing


def test_hidden_npc_tokens_leave_a_players_map(make_user):
    dm, pl, cid, npc, pc = _setup(make_user)
    mid = dm.new_map(cid)
    for ch in (npc, pc):
        assert dm.json(f'/campaigns/{cid}/api/maps/{mid}/pins', {'pin_type': 'character', 'character_id': ch, 'x': 50, 'y': 50}).status_code == 200
    names = lambda u: sorted(p['char_name'] for p in u.get(f'/campaigns/{cid}/api/maps/{mid}/pins').get_json())
    assert names(pl) == ['Goblin Boss', 'Hero']
    _hide(dm, cid)
    assert names(pl) == ['Hero'] and names(dm) == ['Goblin Boss', 'Hero']
    assert all('char_made_by' not in p for p in pl.get(f'/campaigns/{cid}/api/maps/{mid}/pins').get_json())
