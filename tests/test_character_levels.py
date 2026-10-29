"""DL-105: stats saved level by level.  The character's own columns are the stats of its current level (battle, sheet and
exports keep reading them); character_levels remembers what each level looked like, and the edit form brings a level's
stats back when the Level field changes."""
import re
import pytest
from conftest import q, BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only (ON CONFLICT upsert)')


def _edit(user, cid, char_id, **fields):
    data = {'name': 'Mira', 'level': '1', 'max_hp': '30', 'armor_class': '12'}
    data.update({k: str(v) for k, v in fields.items()})
    r = user.post(f'/campaigns/{cid}/characters/{char_id}/edit', data=data, content_type='multipart/form-data')
    assert r.status_code == 302, r.status_code


def _levels(char_id):
    return {r['level']: r for r in q('SELECT * FROM character_levels WHERE character_id = ? ORDER BY level', char_id)}


def test_creating_a_character_saves_its_level(make_user):
    dm = make_user(); cid = dm.new_campaign()
    cid_char = dm.new_character(cid, name='Mira', level=3, max_hp=24, armor_class=14, str_score=16)
    rows = _levels(cid_char)
    assert list(rows) == [3] and rows[3]['max_hp'] == 24 and rows[3]['armor_class'] == 14 and rows[3]['str_score'] == 16


def test_each_level_keeps_its_own_stats(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira', level=1, max_hp=10, armor_class=12, str_score=10)
    _edit(dm, cid, ch, level=2, max_hp=18, armor_class=13, str_score=12)
    _edit(dm, cid, ch, level=3, max_hp=27, armor_class=15, str_score=14)
    rows = _levels(ch)
    assert {l: (r['max_hp'], r['armor_class'], r['str_score']) for l, r in rows.items()} == {1: (10, 12, 10), 2: (18, 13, 12), 3: (27, 15, 14)}
    now = q('SELECT level, max_hp, armor_class, str_score FROM characters WHERE id = ?', ch)[0]       # the active level drives everything else
    assert (now['level'], now['max_hp'], now['armor_class'], now['str_score']) == (3, 27, 15, 14)
    _edit(dm, cid, ch, level=2, max_hp=20, armor_class=13, str_score=12)                               # saving a level again replaces it
    assert _levels(ch)[2]['max_hp'] == 20 and len(_levels(ch)) == 3


def test_battle_uses_the_active_levels_stats(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira', level=1, max_hp=10)
    _edit(dm, cid, ch, level=5, max_hp=44)
    pid = dm.add_to_battle(cid, ch)
    row = [r for r in dm.battle(cid) if r['id'] == pid][0]
    assert row['char_max_hp'] == 44


def test_speed_and_proficiencies_are_saved_per_level(make_user):
    import json
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira', level=1, speed=30, skill_stealth=1, save_dex='on')
    _edit(dm, cid, ch, level=5, speed=40, skill_stealth=2, skill_arcana=1, save_dex='on', save_int='on')
    rows = _levels(ch)
    assert rows[1]['speed'] == 30 and json.loads(rows[1]['skill_prof']) == {'stealth': 1} and json.loads(rows[1]['save_prof']) == ['dex']
    assert rows[5]['speed'] == 40 and json.loads(rows[5]['skill_prof']) == {'arcana': 1, 'stealth': 2} and json.loads(rows[5]['save_prof']) == ['dex', 'int']


def test_level_is_clamped_to_1_30(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira', level=99)
    assert q('SELECT level FROM characters WHERE id = ?', ch)[0]['level'] == 30 and list(_levels(ch)) == [30]
    _edit(dm, cid, ch, level=-4)
    assert q('SELECT level FROM characters WHERE id = ?', ch)[0]['level'] == 1


def test_deleting_the_character_removes_its_levels(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira')
    assert _levels(ch)
    assert dm.post(f'/campaigns/{cid}/characters/{ch}/delete').status_code == 302
    assert not _levels(ch)


def test_edit_form_carries_the_saved_levels_and_details_lists_them(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira', level=1, max_hp=10)
    _edit(dm, cid, ch, level=2, max_hp=18)
    form = dm.get(f'/campaigns/{cid}/characters/{ch}/edit').get_data(as_text=True)
    m = re.search(r"data-level-stats='([^']*)'", form)
    import json
    saved = json.loads(m.group(1)) if m else {}
    assert sorted(saved) == ['1', '2'] and saved['1']['max_hp'] == 10 and saved['2']['max_hp'] == 18
    assert saved['1']['speed'] == 30 and saved['1']['skill_prof'] == {} and saved['1']['save_prof'] == []
    page = dm.get(f'/campaigns/{cid}/characters/{ch}').get_data(as_text=True)
    assert 'Stats by level' in page and 'aria-current="true"' in page
    solo = dm.new_character(cid, name='Solo')
    assert 'Stats by level' not in dm.get(f'/campaigns/{cid}/characters/{solo}').get_data(as_text=True)     # one level is no history


def test_players_never_see_an_npcs_level_history(make_user):
    dm, pl = make_user(), make_user(); cid = dm.new_campaign(); dm.add_member(cid, pl)
    ch = dm.new_character(cid, name='Ogre', level=1, max_hp=40, is_npc='on')
    _edit(dm, cid, ch, level=2, max_hp=59, is_npc='on')
    assert 'Stats by level' in dm.get(f'/campaigns/{cid}/characters/{ch}').get_data(as_text=True)
    seen = pl.get(f'/campaigns/{cid}/characters/{ch}').get_data(as_text=True)
    assert 'Stats by level' not in seen
