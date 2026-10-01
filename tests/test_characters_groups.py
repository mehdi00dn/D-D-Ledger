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


# ---------------- DL-62: per-note "hidden from players" ----------------

def _notes(*entries):
    return json.dumps(list(entries))


def test_hidden_notes_never_reach_other_members(make_user):
    """A note flagged hidden is visible to the DM and the character's creator only -- on every
    surface that sends notes (dossier page, detail API, roster preview, list API, exports)."""
    import io, zipfile
    dm, p1, p2 = make_user(), make_user(), make_user()
    cid = dm.new_campaign()
    _add_player(dm, p1, cid); _add_player(dm, p2, cid)
    SECRET, SHARED, LEGACY = 'dm-only-secret-xyz', 'shared-lore-abc', 'legacy-plain-note'

    # p1 creates a PC with one shared and one hidden note (checkbox -> {"t","h":1}).
    pc = p1.new_character(cid, name='Hero', notes=_notes(SHARED, {'t': SECRET, 'h': 1}))
    # The DM makes an NPC with a hidden note, plus a legacy plain-string note.
    npc = dm.new_character(cid, name='Ogre', is_npc='on', max_hp=99, str_score=21,
                           notes=_notes(SHARED, {'t': SECRET, 'h': 1}))
    legacy = dm.new_character(cid, name='Old', notes=LEGACY)             # pre-flag plain-text notes

    def surfaces(user, char):
        page = user.get(f'/campaigns/{cid}/characters/{char}').data.decode()
        api = user.get(f'/campaigns/{cid}/api/characters/{char}/detail')
        listing = user.get(f'/campaigns/{cid}/api/characters').data.decode()
        roster = user.get(f'/campaigns/{cid}/characters').data.decode()
        return page, api.data.decode(), listing, roster

    # DM and the PC's creator see the hidden note, clearly marked.
    for viewer in (dm, p1):
        page, api, _, roster = surfaces(viewer, pc)
        assert SECRET in page and 'Hidden from players' in page and SHARED in page
        assert SECRET in api and SECRET in roster
    # Another Player (p2) never receives it anywhere, but still sees the shared note.
    page, api, listing, roster = surfaces(p2, pc)
    assert SHARED in page and SHARED in api
    for blob in (page, api, listing, roster):
        assert SECRET not in blob, 'hidden note leaked'
    assert 'Hidden from players' not in page
    # ... including the NPC, whose notes are the only thing a Player may read.
    page, api, listing, roster = surfaces(p2, npc)
    assert SHARED in page and SHARED in api
    for blob in (page, api, listing, roster):
        assert SECRET not in blob and '"max_hp": 99' not in blob.replace('"max_hp":99', '"max_hp": 99')
    assert '99' not in listing.split('Ogre')[1].split('}')[0]            # list API row carries no numbers
    # Legacy plain-text notes stay visible to everyone.
    assert LEGACY in surfaces(p2, legacy)[0]

    # Exports (campaign-wide and per-character) for a Player: no hidden note, no foreign NPC.
    def manifest(resp):
        assert resp.status_code == 200
        return json.loads(zipfile.ZipFile(io.BytesIO(resp.data)).read('manifest.json'))
    m = manifest(p2.get(f'/campaigns/{cid}/export/data'))
    names = {c['name'] for c in m['characters']}
    assert 'Ogre' not in names and 'Hero' in names
    assert SECRET not in json.dumps(m) and SHARED in json.dumps(m)
    assert p2.get(f'/campaigns/{cid}/characters/{npc}/export').status_code == 403
    assert SECRET not in json.dumps(manifest(p2.get(f'/campaigns/{cid}/characters/{pc}/export')))
    # The DM's and the creator's exports keep everything.
    assert SECRET in json.dumps(manifest(dm.get(f'/campaigns/{cid}/export/data')))
    assert SECRET in json.dumps(manifest(p1.get(f'/campaigns/{cid}/characters/{pc}/export')))
    assert dm.get(f'/campaigns/{cid}/characters/{npc}/export').status_code == 200


def test_hidden_flag_round_trips_through_the_editor(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Rogue', notes=_notes('open', {'t': 'closed', 'h': 1}, {'t': '<script>x</script>kept', 'h': 'no'},
                                                         {'t': '', 'h': 1}, 42, {'h': 1}))
    stored = json.loads(q('SELECT notes FROM characters WHERE id = ?', ch)[0]['notes'])
    # visible -> plain string; hidden -> {"t","h":1}; junk/empty entries dropped; 'no' is not a truthy flag
    assert stored[0] == 'open' and stored[1] == {'t': 'closed', 'h': 1} and stored[2] == 'kept' and len(stored) == 3
    form = dm.get(f'/campaigns/{cid}/characters/{ch}/edit').data.decode()
    # 3 stored notes + the empty <template> used by "Add Note"; only the hidden one is pre-checked
    assert form.count('data-note-hidden') == 4 and form.count('data-note-hidden checked') == 1
    # Saving the form back unchanged keeps the flag.
    dm.post(f'/campaigns/{cid}/characters/{ch}/edit', data={'name': 'Rogue', 'notes': json.dumps(stored)},
            content_type='multipart/form-data')
    assert json.loads(q('SELECT notes FROM characters WHERE id = ?', ch)[0]['notes']) == stored
