import json, uuid
import pytest
from conftest import q


def _add_player(dm, player, cid):
    r = dm.post(f'/campaigns/{cid}/members/add', data={'username': player.name, 'status': 'player'})
    assert r.status_code == 302


def test_only_dm_manages_groups(make_user):
    dm, player = make_user(), make_user()
    cid = dm.new_campaign()
    _add_player(dm, player, cid)
    gid = dm.new_group(cid, name='Guild')

    assert player.post(f'/campaigns/{cid}/factions/new', data={'name': 'Hax Guild'},
                        content_type='multipart/form-data').status_code == 403
    assert player.post(f'/campaigns/{cid}/factions/{gid}/edit', data={'name': 'Hax'},
                        content_type='multipart/form-data').status_code == 403
    assert player.post(f'/campaigns/{cid}/factions/{gid}/delete').status_code == 403
    assert q('SELECT name FROM groups WHERE id = ?', gid)[0]['name'] == 'Guild'   # unchanged

    # A Player cannot even reach the New/Edit forms via GET.
    assert player.get(f'/campaigns/{cid}/factions/new').status_code == 403
    assert player.get(f'/campaigns/{cid}/factions/{gid}/edit').status_code == 403
    # Neither control renders on the list page for a Player.
    listing = player.get(f'/campaigns/{cid}/factions').data
    assert b'New Faction' not in listing and b'>Edit<' not in listing
    # ...and the same page does show them to the DM, so the check above can actually fail.
    dm_listing = dm.get(f'/campaigns/{cid}/factions').data
    assert b'New Faction' in dm_listing and b'Edit' in dm_listing

    # The DM can still do all of it.
    assert dm.post(f'/campaigns/{cid}/factions/{gid}/edit', data={'name': 'Renamed'},
                    content_type='multipart/form-data').status_code == 302
    assert q('SELECT name FROM groups WHERE id = ?', gid)[0]['name'] == 'Renamed'


def test_npc_stats_hidden_from_players(make_user):
    dm, player = make_user(), make_user()
    cid = dm.new_campaign()
    _add_player(dm, player, cid)
    npc = dm.new_character(cid, name='Goblin Scout', is_npc='on', str_score=17)
    pc = dm.new_character(cid, name='Hero', str_score=12)

    # Server-rendered dossier page: a Player may open the NPC (notes only), the DM sees everything.
    assert player.get(f'/campaigns/{cid}/characters/{npc}').status_code == 200
    assert dm.get(f'/campaigns/{cid}/characters/{npc}').status_code == 200
    assert player.get(f'/campaigns/{cid}/characters/{pc}').status_code == 200   # own party's PC still visible

    # Roster page: the Player sees a hidden-stats badge instead of real numbers for the NPC.
    player_list = player.get(f'/campaigns/{cid}/characters').data.decode()
    assert 'Goblin Scout' in player_list                # the NPC still shows up in the roster
    assert 'stats-fogged' in player_list                # blurred placeholders replace the old text badge
    assert 'Stats Hidden' not in player_list
    assert '<span class="val">17</span>' not in player_list   # the redacted STR score never reaches the page
    dm_list = dm.get(f'/campaigns/{cid}/characters').data.decode()
    assert '<span class="val">17</span>' in dm_list and 'stats-fogged' not in dm_list


def test_player_npc_detail_is_notes_only(make_user):
    """DL-61: a Player can open an NPC's dossier / detail API, but only notes + identity come back."""
    dm, player = make_user(), make_user()
    cid = dm.new_campaign()
    _add_player(dm, player, cid)
    npc = dm.new_character(cid, name='Goblin Scout', is_npc='on', level=3, max_hp=77, armor_class=19,
                           str_score=17, dex_score=18, notes=json.dumps(['Owes the guild a favour']))
    pc = dm.new_character(cid, name='Hero', max_hp=31, str_score=12)

    # API: 200, allow-listed fields only, no numbers anywhere in the payload.
    r = player.get(f'/campaigns/{cid}/api/characters/{npc}/detail')
    assert r.status_code == 200
    body = r.get_json()
    assert body['hidden_stats'] is True and body['sheets'] == []
    assert 'Owes the guild a favour' in body['notes_html']
    for leaked in ('str_score', 'dex_score', 'con_score', 'int_score', 'wis_score', 'cha_score',
                   'max_hp', 'armor_class', 'notes', 'created_by'):
        assert leaked not in body
    assert '77' not in r.data.decode() and '19' not in [str(v) for v in body.values()]

    # Dossier page: notes visible, real numbers absent, fogged placeholders present.
    page = player.get(f'/campaigns/{cid}/characters/{npc}').data.decode()
    assert 'Owes the guild a favour' in page and 'stats-fogged' in page
    assert '<span class="val">17</span>' not in page and '>77<' not in page and '77 HP' not in page
    assert '>Sheets<' not in page

    # The DM still gets the full record, and a Player's own-party PC is unchanged.
    dm_body = dm.get(f'/campaigns/{cid}/api/characters/{npc}/detail').get_json()
    assert dm_body['str_score'] == 17 and dm_body['max_hp'] == 77 and 'hidden_stats' not in dm_body
    dm_page = dm.get(f'/campaigns/{cid}/characters/{npc}').data.decode()
    assert '<span class="val">17</span>' in dm_page and 'stats-fogged' not in dm_page
    pc_body = player.get(f'/campaigns/{cid}/api/characters/{pc}/detail').get_json()
    assert pc_body['max_hp'] == 31 and 'hidden_stats' not in pc_body

PERSIAN = 'کاراکتر آزمایشی'
PERSIAN_NOTES = json.dumps(['یادداشت‌های مهم: <b>مرد</b> در تاریکی', 'خط دوم'])


def test_character_crud_and_delete_redirect(camp):
    u, cid = camp
    ch = u.new_character(cid, name='Thorin', level=5, max_hp=44, str_score=16)
    row = q('SELECT * FROM characters WHERE id = ?', ch)[0]
    assert (row['name'], row['level'], row['max_hp'], row['str_score']) == ('Thorin', 5, 44, 16)
    assert b'Thorin' in u.get(f'/campaigns/{cid}/characters').data
    assert u.get(f'/campaigns/{cid}/characters/{ch}').status_code == 200
    r = u.post(f'/campaigns/{cid}/characters/{ch}/edit', data={'name': 'Thorin II', 'level': '6', 'max_hp': '50'}, content_type='multipart/form-data')
    assert r.status_code == 302
    assert q('SELECT name, level FROM characters WHERE id = ?', ch)[0] == {'name': 'Thorin II', 'level': 6}
    r = u.post(f'/campaigns/{cid}/characters/{ch}/delete')
    assert r.status_code == 302
    follow = u.get(r.headers['Location'])
    assert follow.status_code == 200 and b'Thorin II' not in follow.data
    assert q('SELECT COUNT(*) AS n FROM characters WHERE id = ?', ch)[0]['n'] == 0


def test_persian_text_roundtrip(camp):
    u, cid = camp
    ch = u.new_character(cid, name=PERSIAN, notes=PERSIAN_NOTES)
    assert q('SELECT name FROM characters WHERE id = ?', ch)[0]['name'] == PERSIAN
    page = u.get(f'/campaigns/{cid}/characters/{ch}').data.decode()
    assert PERSIAN in page and 'یادداشت' in page
    gid = u.new_group(cid, name='گروه شمشیرزنان')
    assert q('SELECT name FROM groups WHERE id = ?', gid)[0]['name'] == 'گروه شمشیرزنان'
    api = u.get(f'/campaigns/{cid}/api/characters').get_json()
    assert any(c['name'] == PERSIAN for c in api)


def test_group_lifecycle_and_membership(camp):
    u, cid = camp
    gid = u.new_group(cid, name='Guild')
    a = u.new_character(cid, group_id=gid); b = u.new_character(cid)
    assert q('SELECT group_id FROM characters WHERE id = ?', a)[0]['group_id'] == gid
    r = u.post(f'/campaigns/{cid}/factions/{gid}/edit', data={'name': 'Guild 2', 'color': '#111111'}, content_type='multipart/form-data')
    assert r.status_code == 302 and q('SELECT name FROM groups WHERE id = ?', gid)[0]['name'] == 'Guild 2'
    r = u.post(f'/campaigns/{cid}/factions/{gid}/delete')
    assert r.status_code == 302 and u.get(r.headers['Location']).status_code == 200
    assert q('SELECT COUNT(*) AS n FROM groups WHERE id = ?', gid)[0]['n'] == 0
    # characters survive; their group link is cleared
    assert q('SELECT group_id FROM characters WHERE id = ?', a)[0]['group_id'] is None


def test_case_insensitive_name_ordering(camp):
    u, cid = camp
    for n in ('bravo', 'Alpha', 'charlie', 'Bravo2'):
        u.new_character(cid, name=n)
    names = [c['name'] for c in u.get(f'/campaigns/{cid}/api/characters').get_json()]
    assert names == sorted(names, key=str.lower)


def test_character_detail_api(camp):
    u, cid = camp
    ch = u.new_character(cid, name='Api Guy', max_hp=21)
    d = u.get(f'/campaigns/{cid}/api/characters/{ch}/detail')
    assert d.status_code == 200 and d.get_json()['name'] == 'Api Guy'
