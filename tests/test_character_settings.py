"""Extra character settings: speed, saving-throw and skill proficiencies, and the modifiers derived from them."""
import io
import json
import zipfile

import pytest

import dnd5e
from conftest import BACKEND, q

new_code = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


# ---- the rules themselves (pure) ----

def test_modifier_rounds_down_like_the_srd():
    assert [dnd5e.modifier(s) for s in (1, 3, 8, 9, 10, 11, 12, 15, 18, 20, 30)] == [-5, -4, -1, -1, 0, 0, 1, 2, 4, 5, 10]


def test_proficiency_bonus_steps_every_four_levels_and_stops_at_six():
    assert [dnd5e.proficiency_bonus(l) for l in (0, 1, 4, 5, 8, 9, 12, 13, 16, 17, 20, 30)] == [2, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 6]


def test_there_are_18_skills_each_on_one_ability():
    assert len(dnd5e.SKILLS) == 18 and len(set(dnd5e.SKILL_KEYS)) == 18
    assert {a for _k, _l, a in dnd5e.SKILLS} <= set(dnd5e.ABILITY_KEYS)
    by = {k: a for k, _l, a in dnd5e.SKILLS}
    assert (by['acrobatics'], by['animal-handling'], by['athletics'], by['arcana'], by['persuasion']) == ('dex', 'wis', 'str', 'int', 'cha')


def test_cleaners_drop_anything_that_is_not_a_known_choice():
    assert dnd5e.clean_skill_prof('{"stealth": 2, "arcana": 1, "bogus": 1, "history": 3, "nature": "x"}') == {'arcana': 1, 'stealth': 2}
    assert dnd5e.clean_skill_prof('not json') == {} and dnd5e.clean_skill_prof(None) == {} and dnd5e.clean_skill_prof('[1]') == {}
    assert dnd5e.clean_save_prof('["wis", "bogus", "str", "str"]') == ['str', 'wis']       # standard order, no duplicates
    assert dnd5e.clean_save_prof('{"a": 1}') == [] and dnd5e.clean_save_prof('nope') == []
    assert [dnd5e.clean_speed(x) for x in ('35', '', 'abc', '-5', '99999', None, 40)] == [35, 30, 30, 0, 500, 30, 40]


def _char(**kw):
    base = dict(level=1, str_score=10, dex_score=10, con_score=10, int_score=10, wis_score=10, cha_score=10)
    base.update(kw)
    return base


def test_derived_numbers_match_a_hand_worked_example():
    d = dnd5e.derive(_char(level=5, str_score=8, dex_score=14, wis_score=16, speed=35,
                           skill_prof={'perception': 1, 'stealth': 2}, save_prof=['wis', 'str']))
    assert (d['speed'], d['prof_bonus'], d['initiative']) == (35, 3, 2)
    skills = {s['key']: s for s in d['skills']}
    assert skills['perception']['text'] == '+6' and d['passive_perception'] == 16     # 3 (WIS) + 3 prof; 10 + 6
    assert skills['stealth']['text'] == '+8'                                           # 2 (DEX) + 2 x 3 (expertise)
    assert skills['athletics']['text'] == '-1' and skills['acrobatics']['text'] == '+2'
    saves = {s['key']: s for s in d['saves']}
    assert saves['str']['text'] == '+2' and saves['str']['proficient'] and saves['dex']['text'] == '+2' and not saves['dex']['proficient']


def test_a_character_with_nothing_chosen_gets_sensible_defaults():
    d = dnd5e.derive(_char())
    assert d['speed'] == 30 and d['prof_text'] == '+2' and d['passive_perception'] == 10
    assert all(s['level'] == 0 for s in d['skills']) and not any(s['proficient'] for s in d['saves'])


# ---- through the app ----

def _setup(make_user):
    dm, player = make_user(), make_user()
    cid = dm.new_campaign()
    dm.add_member(cid, player)
    return dm, player, cid


def _row(cid, name):
    return q('SELECT speed, skill_prof, save_prof FROM characters WHERE campaign_id = ? AND name = ?', cid, name)[0]


@new_code
def test_a_new_character_defaults_to_speed_30_and_nothing_chosen(make_user):
    dm, _p, cid = _setup(make_user)
    dm.new_character(cid, name='Plain')
    r = _row(cid, 'Plain')
    assert r['speed'] == 30 and json.loads(r['skill_prof']) == {} and json.loads(r['save_prof']) == []


@new_code
def test_settings_are_saved_and_edited_through_the_form(make_user):
    dm, _p, cid = _setup(make_user)
    ch = dm.new_character(cid, name='Rogue', level=5, dex_score=14, speed=35, skill_stealth=2, skill_perception=1,
                          save_dex='on', save_int='on')
    r = _row(cid, 'Rogue')
    assert r['speed'] == 35 and json.loads(r['skill_prof']) == {'stealth': 2, 'perception': 1}
    assert json.loads(r['save_prof']) == ['dex', 'int']
    form = dm.get(f'/campaigns/{cid}/characters/{ch}/edit').get_data(as_text=True)
    assert 'name="speed"' in form and 'value="35"' in form and form.count('name="skill_') == 18
    assert 'name="save_dex" checked' in form and 'name="save_wis" checked' not in form
    assert '<option value="2" selected>' in form                                     # the stealth expertise comes back selected
    # editing replaces the choices (an unticked box is removed) and bad values never reach the database
    resp = dm.post(f'/campaigns/{cid}/characters/{ch}/edit', data={
        'name': 'Rogue', 'level': '5', 'max_hp': '30', 'speed': 'fast', 'skill_arcana': '1', 'skill_stealth': '9',
        'skill_nonsense': '2', 'save_str': 'on', 'save_bogus': 'on'}, content_type='multipart/form-data')
    assert resp.status_code == 302
    r = _row(cid, 'Rogue')
    assert r['speed'] == 30 and json.loads(r['skill_prof']) == {'arcana': 1} and json.loads(r['save_prof']) == ['str']


@new_code
def test_details_page_and_api_show_the_derived_sheet(make_user):
    dm, _p, cid = _setup(make_user)
    ch = dm.new_character(cid, name='Ranger', level=5, wis_score=16, dex_score=14, speed=35,
                          skill_perception=1, skill_stealth=2, save_wis='on')
    page = dm.get(f'/campaigns/{cid}/characters/{ch}').get_data(as_text=True)
    for needle in ('35 ft', 'Passive Perc.', '>16<', 'Saving throws', 'Stealth', '+8', 'Prof.', '+3'):
        assert needle in page, needle
    api = dm.get(f'/campaigns/{cid}/api/characters/{ch}/detail').get_json()
    assert '35 ft' in api['sheet_html'] and 'Perception' in api['sheet_html'] and api['speed'] == 35


@new_code
def test_players_never_get_an_npcs_new_numbers(make_user):
    dm, player, cid = _setup(make_user)
    npc = dm.new_character(cid, name='Ogre', is_npc='on', speed=45, skill_stealth=2, save_con='on', wis_score=19)
    pc = dm.new_character(cid, name='Hero', speed=35, skill_arcana=1)
    page = player.get(f'/campaigns/{cid}/characters/{npc}').get_data(as_text=True)
    api = player.get(f'/campaigns/{cid}/api/characters/{npc}/detail').get_json()
    for blob in (page, json.dumps(api)):
        for secret in ('45 ft', 'Saving throws', 'Passive Perc', 'skill_prof', 'save_prof', 'sheet_html', '"speed"'):
            assert secret not in blob, secret
    assert 'hidden_stats' in api
    # a Player's own party member is fully visible, and so is the NPC to the DM
    assert '35 ft' in player.get(f'/campaigns/{cid}/characters/{pc}').get_data(as_text=True)
    assert '45 ft' in dm.get(f'/campaigns/{cid}/characters/{npc}').get_data(as_text=True)
    assert '45 ft' in dm.get(f'/campaigns/{cid}/api/characters/{npc}/detail').get_json()['sheet_html']


@new_code
def test_settings_survive_export_and_import_and_bad_manifests_are_cleaned(make_user):
    dm, _p, cid = _setup(make_user)
    ch = dm.new_character(cid, name='Bard', speed=40, skill_persuasion=2, skill_performance=1, save_cha='on', save_dex='on')
    r = dm.get(f'/campaigns/{cid}/characters/{ch}/export')
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(r.data)).read('manifest.json'))
    c = manifest['characters'][0]
    assert (c['speed'], c['skill_prof'], c['save_prof']) == (40, {'persuasion': 2, 'performance': 1}, ['dex', 'cha'])
    cid2 = dm.new_campaign()
    assert dm.post(f'/campaigns/{cid2}/import/data', data={'import_file': (io.BytesIO(r.data), 'x.zip')},
                   content_type='multipart/form-data').status_code in (200, 302)
    got = _row(cid2, 'Bard')
    assert got['speed'] == 40 and json.loads(got['skill_prof']) == {'persuasion': 2, 'performance': 1} and json.loads(got['save_prof']) == ['dex', 'cha']
    # an older export (no new fields) and a hostile one both import safely
    manifest['characters'][0].update(name='Old', speed='lots', skill_prof={'stealth': 7, 'x': 1}, save_prof='nope')
    del manifest['characters'][0]['save_prof']
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest))
    cid3 = dm.new_campaign()
    assert dm.post(f'/campaigns/{cid3}/import/data', data={'import_file': (io.BytesIO(buf.getvalue()), 'old.zip')},
                   content_type='multipart/form-data').status_code in (200, 302)
    old = _row(cid3, 'Old')
    assert old['speed'] == 30 and json.loads(old['skill_prof']) == {} and json.loads(old['save_prof']) == []
