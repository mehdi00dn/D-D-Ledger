"""DL-43: optional character class, shown as an icon beside names; hidden on NPCs from Players."""
import io
import json
import zipfile
import pytest
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')

KEYS = ['artificer', 'barbarian', 'bard', 'cleric', 'druid', 'fighter', 'monk', 'paladin',
        'ranger', 'rogue', 'sorcerer', 'warlock', 'wizard']


def _setup(make_user):
    dm, player = make_user(), make_user()
    cid = dm.new_campaign()
    dm.post(f'/campaigns/{cid}/members/add', data={'username': player.name, 'status': 'player'})
    return dm, player, cid


def _class_of(cid, name):
    return q('SELECT class_key FROM characters WHERE campaign_id = ? AND name = ?', cid, name)[0]['class_key']


def test_class_is_optional_validated_and_editable(make_user):
    dm, player, cid = _setup(make_user)
    dm.new_character(cid, name='Plain')
    assert _class_of(cid, 'Plain') is None                                    # can be left empty
    wiz = dm.new_character(cid, name='Merlin', class_key='wizard')
    assert _class_of(cid, 'Merlin') == 'wizard'
    dm.new_character(cid, name='Bogus', class_key='jedi')
    assert _class_of(cid, 'Bogus') is None                                    # unknown value is dropped
    dm.post(f'/campaigns/{cid}/characters/{wiz}/edit', data={'name': 'Merlin', 'class_key': 'bard', 'level': '1', 'max_hp': '10', 'armor_class': '10'})
    assert _class_of(cid, 'Merlin') == 'bard'
    dm.post(f'/campaigns/{cid}/characters/{wiz}/edit', data={'name': 'Merlin', 'level': '1', 'max_hp': '10', 'armor_class': '10'})
    assert _class_of(cid, 'Merlin') is None                                   # clearing it works


def test_the_form_offers_all_thirteen_classes_with_their_hover_text(make_user):
    dm, player, cid = _setup(make_user)
    page = dm.get(f'/campaigns/{cid}/characters/new').data.decode()
    for k in KEYS:
        assert f'data-value="{k}"' in page and f'classes.svg#{k}' in page
    assert 'An inventor who infuses magic into items and constructs.' in page
    assert 'A seeker of secrets who makes a pact with an otherworldly being.' in page


def test_every_class_has_an_icon_in_the_sprite():
    from pathlib import Path
    sprite = (Path(__file__).resolve().parent.parent / 'static' / 'icons' / 'classes.svg').read_text()
    for k in KEYS:
        assert f'<symbol id="{k}"' in sprite


def test_icon_appears_in_lists_battle_and_map_data(make_user):
    dm, player, cid = _setup(make_user)
    pc = dm.new_character(cid, name='Hero', class_key='paladin')
    pid = dm.add_to_battle(cid, pc)
    assert 'classes.svg#paladin' in dm.get(f'/campaigns/{cid}/characters').data.decode()
    assert 'classes.svg#paladin' in player.get(f'/campaigns/{cid}/characters/{pc}').data.decode()
    assert next(r for r in player.battle(cid) if r['id'] == pid)['class_key'] == 'paladin'
    assert next(c for c in player.get(f'/campaigns/{cid}/api/characters').get_json() if c['id'] == pc)['class_key'] == 'paladin'
    assert player.get(f'/campaigns/{cid}/api/characters/{pc}/detail').get_json()['class_key'] == 'paladin'


def test_an_npcs_class_is_not_sent_to_players(make_user):
    dm, player, cid = _setup(make_user)
    npc = dm.new_character(cid, name='Hedge Wizard', class_key='wizard', is_npc='on')
    pid = dm.add_to_battle(cid, npc)
    assert next(r for r in dm.battle(cid) if r['id'] == pid)['class_key'] == 'wizard'
    assert next(r for r in player.battle(cid) if r['id'] == pid)['class_key'] is None
    assert 'class_key' not in next(c for c in player.get(f'/campaigns/{cid}/api/characters').get_json() if c['id'] == npc)
    assert 'class_key' not in player.get(f'/campaigns/{cid}/api/characters/{npc}/detail').get_json()
    assert 'classes.svg#wizard' not in player.get(f'/campaigns/{cid}/characters').data.decode()
    assert 'classes.svg#wizard' not in player.get(f'/campaigns/{cid}/characters/{npc}').data.decode()
    assert 'classes.svg#wizard' in dm.get(f'/campaigns/{cid}/characters/{npc}').data.decode()


def test_class_survives_export_and_import(make_user):
    dm, player, cid = _setup(make_user)
    ch = dm.new_character(cid, name='Rogue One', class_key='rogue')
    r = dm.get(f'/campaigns/{cid}/characters/{ch}/export')
    assert r.status_code == 200
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(r.data)).read('manifest.json'))
    assert manifest['characters'][0]['class_key'] == 'rogue'
    cid2 = dm.new_campaign()
    imp = dm.post(f'/campaigns/{cid2}/import/data', data={'import_file': (io.BytesIO(r.data), 'export.zip')}, content_type='multipart/form-data')
    assert imp.status_code in (200, 302)
    assert _class_of(cid2, 'Rogue One') == 'rogue'
