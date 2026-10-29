"""NPC allies: still NPCs, but Players see their stats and the Hide NPCs switch leaves them alone."""
import pytest
from conftest import q, BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only')


def _setup(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, pl)
    ally = dm.new_character(cid, name='Friendly Knight', is_npc='on', is_ally='on', str_score='17', max_hp='40')
    foe = dm.new_character(cid, name='Goblin Boss', is_npc='on', str_score='15', max_hp='30')
    return dm, pl, cid, ally, foe


def test_ally_is_stored_only_for_an_npc(make_user):
    dm, pl, cid, ally, foe = _setup(make_user)
    row = q('SELECT is_npc, is_ally FROM characters WHERE id = ?', ally)[0]
    assert (row['is_npc'], row['is_ally']) == (1, 1)
    assert q('SELECT is_ally FROM characters WHERE id = ?', foe)[0]['is_ally'] == 0
    pc = dm.new_character(cid, name='Hero', is_ally='on')           # a PC cannot be an ally, whatever the form says
    assert q('SELECT is_ally FROM characters WHERE id = ?', pc)[0]['is_ally'] == 0


def test_players_see_an_allys_stats_but_not_a_foes(make_user):
    dm, pl, cid, ally, foe = _setup(make_user)
    a = pl.get(f'/campaigns/{cid}/api/characters/{ally}/detail').get_json()
    assert a['str_score'] == 17 and a['max_hp'] == 40 and not a.get('hidden_stats')
    f = pl.get(f'/campaigns/{cid}/api/characters/{foe}/detail').get_json()
    assert f.get('hidden_stats') and 'str_score' not in f
    rows = {c['id']: c for c in pl.get(f'/campaigns/{cid}/api/characters').get_json()}
    assert rows[ally]['str_score'] == 17 and rows[foe].get('hidden_stats')


def test_ally_stays_visible_when_npcs_are_hidden(make_user):
    dm, pl, cid, ally, foe = _setup(make_user)
    pa, pf = dm.add_to_battle(cid, ally), dm.add_to_battle(cid, foe)
    assert dm.post(f'/campaigns/{cid}/npc-visibility', data={'hide_npcs': 'on'}).status_code == 302
    html = pl.get(f'/campaigns/{cid}/characters').data.decode()
    assert 'Friendly Knight' in html and 'Goblin Boss' not in html
    assert pl.get(f'/campaigns/{cid}/api/characters/{ally}/detail').status_code == 200
    assert pl.get(f'/campaigns/{cid}/api/characters/{foe}/detail').status_code == 404
    rows = pl.battle(cid)
    assert [r['char_name'] for r in rows] == ['Friendly Knight']
    assert rows[0]['current_hp'] == 40 and not rows[0].get('hidden_stats')
    assert rows[0]['is_npc']                                         # technically still an NPC
    log = pl.get(f'/campaigns/{cid}/api/battle/log').get_json()
    assert any('Friendly Knight' in l['text'] for l in log) and not any('Goblin' in l['text'] for l in log)
    assert 'Friendly Knight' in pl.get(f'/campaigns/{cid}/activity').data.decode()


def test_ally_is_still_an_npc_for_dice_and_initiative(make_user):
    dm, pl, cid, ally, foe = _setup(make_user)
    r = pl.json(f'/campaigns/{cid}/api/dice/roll', {'dice': {'20': 1}, 'modifier': 0, 'label': 'x', 'character_id': ally})
    assert r.status_code == 403                                      # only the DM rolls for NPCs
    dm.add_to_battle(cid, ally)
    assert dm.json(f'/campaigns/{cid}/api/battle/roll-initiative', {'scope': 'npc'}).status_code == 200


def test_ally_token_stays_on_a_players_map(make_user):
    dm, pl, cid, ally, foe = _setup(make_user)
    mid = dm.new_map(cid)
    for ch in (ally, foe):
        assert dm.json(f'/campaigns/{cid}/api/maps/{mid}/pins', {'pin_type': 'character', 'character_id': ch, 'x': 50, 'y': 50}).status_code == 200
    names = lambda u: sorted(p['char_name'] for p in u.get(f'/campaigns/{cid}/api/maps/{mid}/pins').get_json())
    assert names(pl) == ['Friendly Knight', 'Goblin Boss']
    dm.post(f'/campaigns/{cid}/npc-visibility', data={'hide_npcs': 'on'})
    assert names(pl) == ['Friendly Knight'] and names(dm) == ['Friendly Knight', 'Goblin Boss']
    pins = {p['char_name']: p for p in pl.get(f'/campaigns/{cid}/api/maps/{mid}/pins').get_json()}
    assert not pins['Friendly Knight'].get('hidden_stats') and pins['Friendly Knight']['is_npc']


def test_ally_survives_export_and_import(make_user):
    from importer import normalize_manifest
    out = normalize_manifest({'characters': [{'name': 'A', 'is_npc': True, 'is_ally': True}, {'name': 'B', 'is_npc': False, 'is_ally': True}]}, lambda x: x)
    chars = {c['name']: c for c in (out['characters'] if isinstance(out, dict) else out)}
    assert chars['A']['is_ally'] == 1 and chars['B']['is_ally'] == 0
