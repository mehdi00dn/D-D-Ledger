"""Campaign bio and notes, and attaching characters to players ("played by")."""
import json
import pytest
from conftest import q, BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only')


def _setup(make_user):
    dm, pl, other = make_user(), make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, pl); dm.add_member(cid, other)
    hero = dm.new_character(cid, name='Hero')
    uid = q('SELECT id FROM users WHERE username = ?', pl.name)[0]['id']
    return dm, pl, other, cid, hero, uid


def _attach(dm, cid, uid, *ids):
    r = dm.post(f'/campaigns/{cid}/members/{uid}/characters', data={'character_ids': [str(i) for i in ids]})
    assert r.status_code == 302


def test_a_player_edits_only_the_characters_attached_to_them(make_user):
    dm, pl, other, cid, hero, uid = _setup(make_user)
    assert pl.get(f'/campaigns/{cid}/characters/{hero}/edit').status_code == 403
    assert other.get(f'/campaigns/{cid}/characters/{hero}/edit').status_code == 403
    _attach(dm, cid, uid, hero)
    assert q('SELECT player_id FROM characters WHERE id = ?', hero)[0]['player_id'] == uid
    assert pl.get(f'/campaigns/{cid}/characters/{hero}/edit').status_code == 200
    assert other.get(f'/campaigns/{cid}/characters/{hero}/edit').status_code == 403
    assert 'Edit' in pl.get(f'/campaigns/{cid}/characters/{hero}').data.decode()
    r = pl.post(f'/campaigns/{cid}/characters/{hero}/edit', data={'name': 'Hero Renamed', 'level': '2', 'max_hp': '40', 'armor_class': '15'})
    assert r.status_code == 302
    assert q('SELECT name FROM characters WHERE id = ?', hero)[0]['name'] == 'Hero Renamed'
    # attached is not the same as made: they still cannot delete it
    assert pl.post(f'/campaigns/{cid}/characters/{hero}/delete').status_code == 403
    _attach(dm, cid, uid)                                         # un-attach
    assert pl.get(f'/campaigns/{cid}/characters/{hero}/edit').status_code == 403


def test_only_the_dm_or_owner_attaches_and_only_members_and_real_characters(make_user):
    dm, pl, other, cid, hero, uid = _setup(make_user)
    assert pl.post(f'/campaigns/{cid}/members/{uid}/characters', data={'character_ids': [str(hero)]}).status_code == 403
    stranger = make_user()
    sid = q('SELECT id FROM users WHERE username = ?', stranger.name)[0]['id']
    assert dm.post(f'/campaigns/{cid}/members/{sid}/characters', data={'character_ids': [str(hero)]}).status_code == 404
    cid2 = dm.new_campaign(); foreign = dm.new_character(cid2, name='Elsewhere')
    _attach(dm, cid, uid, foreign, 'junk')
    assert q('SELECT player_id FROM characters WHERE id = ?', foreign)[0]['player_id'] is None
    other_uid = q('SELECT id FROM users WHERE username = ?', other.name)[0]['id']
    _attach(dm, cid, uid, hero); _attach(dm, cid, other_uid, hero)  # one player per character: the second takes it
    assert q('SELECT player_id FROM characters WHERE id = ?', hero)[0]['player_id'] == other_uid


def test_attached_player_moves_the_token_but_not_someone_elses(make_user):
    dm, pl, other, cid, hero, uid = _setup(make_user)
    mid = dm.new_map(cid)
    npc_free = dm.new_character(cid, name='Rogue')
    pins = {}
    for cname, chid in (('Hero', hero), ('Rogue', npc_free)):
        r = dm.json(f'/campaigns/{cid}/api/maps/{mid}/pins', {'pin_type': 'character', 'character_id': chid, 'x': 10, 'y': 10})
        pins[chid] = r.get_json()['id']
    def move(who, pin):
        return who.json(f'/campaigns/{cid}/api/maps/{mid}/pins/{pin}/update', {'x': 99, 'y': 99}).status_code
    assert move(pl, pins[hero]) == 403
    _attach(dm, cid, uid, hero)
    seen = {p['character_id']: p for p in pl.get(f'/campaigns/{cid}/api/maps/{mid}/pins').get_json()}
    assert seen[hero]['can_move'] is True and seen[npc_free]['can_move'] is False
    assert move(pl, pins[hero]) == 200
    assert move(pl, pins[npc_free]) == 403
    assert move(other, pins[hero]) == 403
    assert q('SELECT x FROM map_pins WHERE id = ?', pins[hero])[0]['x'] == 99


def test_bio_is_formatted_text_and_notes_follow_the_hidden_rule(make_user):
    dm, pl, other, cid, hero, uid = _setup(make_user)
    notes = json.dumps(['Open <b>clue</b>', {'t': 'The duke is the traitor', 'h': 1}])
    bio = json.dumps(['A <b>bold</b> and <i>italic</i> tale<script>x()</script>'])
    r = dm.post(f'/campaigns/{cid}/edit', data={'name': 'Camp', 'description': bio, 'notes': notes})
    assert r.status_code == 302
    stored = q('SELECT description, notes FROM campaigns WHERE id = ?', cid)[0]
    assert '<script' not in stored['description'] and '<b>bold</b>' in stored['description']
    home_dm = dm.get(f'/campaigns/{cid}/home').data.decode()
    home_pl = pl.get(f'/campaigns/{cid}/home').data.decode()
    for html in (home_dm, home_pl):
        assert '<b>bold</b>' in html and '<i>italic</i>' in html and 'Open <b>clue</b>' in html
    assert 'The duke is the traitor' in home_dm and 'The duke is the traitor' not in home_pl
    assert 'data-clamp-toggle' in home_pl
    # a Player cannot change either
    assert pl.post(f'/campaigns/{cid}/edit', data={'name': 'X', 'description': 'x', 'notes': '[]'}).status_code == 403
    # plain text, even text that is valid JSON, stays as typed
    dm.post(f'/campaigns/{cid}/edit', data={'name': 'Camp', 'description': '123'})
    assert '123' in dm.get(f'/campaigns/{cid}/home').data.decode()
    # an edit that does not send notes keeps them
    assert 'The duke is the traitor' in dm.get(f'/campaigns/{cid}/home').data.decode()


def test_home_lists_who_plays_whom_and_hides_hidden_npcs(make_user):
    dm, pl, other, cid, hero, uid = _setup(make_user)
    npc = dm.new_character(cid, name='Secret Duke', is_npc='on')
    _attach(dm, cid, uid, hero, npc)
    assert 'Hero' in pl.get(f'/campaigns/{cid}/home').data.decode().split('aria-label="Players"')[1]
    dm.post(f'/campaigns/{cid}/npc-visibility', data={'hide_npcs': 'on'})
    assert 'Secret Duke' not in pl.get(f'/campaigns/{cid}/home').data.decode()
    assert 'Attach characters to players' in dm.get(f'/campaigns/{cid}/home').data.decode()
    assert 'Attach characters to players' not in pl.get(f'/campaigns/{cid}/home').data.decode()
    form = dm.get(f'/campaigns/{cid}/edit').data.decode()
    assert 'Attach characters' in form or 'Plays Hero' in form
    assert 'Attach characters' not in pl.get(f'/campaigns/{cid}/edit').data.decode()
