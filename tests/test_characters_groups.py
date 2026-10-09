import json, uuid
import pytest
from conftest import q


def _add_player(dm, player, cid):
    r = dm.add_member(cid, player)
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
    # Neither control renders for a Player: no New button on the list, no Edit link on the list or Details page.
    listing = player.get(f'/campaigns/{cid}/factions').data
    assert b'New Faction' not in listing and b'>Edit<' not in listing
    assert f'/factions/{gid}/edit'.encode() not in player.get(f'/campaigns/{cid}/factions/{gid}').data
    # ...and the DM does get them (Edit now lives inside the Details view), so the checks above can actually fail.
    dm_listing = dm.get(f'/campaigns/{cid}/factions').data
    assert b'New Faction' in dm_listing and b'Details' in dm_listing
    assert f'/factions/{gid}/edit'.encode() in dm.get(f'/campaigns/{cid}/factions/{gid}').data

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


# ---------------- DL-63: faction Details view + add characters from the edit form ----------------

def _members_of(char_id):
    return {r['group_id'] for r in q('SELECT group_id FROM character_groups WHERE character_id = ?', char_id)}


def _group_of(char_id):
    return q('SELECT group_id FROM characters WHERE id = ?', char_id)[0]['group_id']


def test_faction_details_lists_members_and_hides_edit_from_players(make_user):
    dm, player = make_user(), make_user()
    cid = dm.new_campaign()
    _add_player(dm, player, cid)
    gid = dm.new_group(cid, name='Ashfall Company')
    other = dm.new_group(cid, name='Rival Guild')
    a = dm.new_character(cid, name='Aldric', group_id=gid, str_score=17)
    b = dm.new_character(cid, name='Brute Orc', is_npc='on', group_id=gid, max_hp=8731, armor_class=29)
    c = dm.new_character(cid, name='Cyrene', group_id=other)

    # The list card says Details (for everyone) instead of Edit.
    for who in (dm, player):
        listing = who.get(f'/campaigns/{cid}/factions').data.decode()
        assert f'/factions/{gid}"' in listing and 'Details' in listing
        assert f'/factions/{gid}/edit' not in listing

    # Details lists exactly this faction's members, with a link to each dossier.
    for who in (dm, player):
        page = who.get(f'/campaigns/{cid}/factions/{gid}').data.decode()
        assert 'Aldric' in page and 'Brute Orc' in page and 'Cyrene' not in page
        assert f'/characters/{a}"' in page and '2 members' in page
        # No stat block anywhere on the faction page (the NPC's numbers stay private).
        assert '8731' not in page and 'AC 29' not in page and '>29<' not in page
    # Edit is reachable from inside the details view for the DM only.
    assert f'/factions/{gid}/edit' in dm.get(f'/campaigns/{cid}/factions/{gid}').data.decode()
    assert f'/factions/{gid}/edit' not in player.get(f'/campaigns/{cid}/factions/{gid}').data.decode()
    assert player.get(f'/campaigns/{cid}/factions/{gid}/edit').status_code == 403

    # Empty faction and a missing id are handled; a faction of another campaign is not reachable.
    empty = dm.new_group(cid, name='Nobody')
    assert 'No characters in this faction yet' in dm.get(f'/campaigns/{cid}/factions/{empty}').data.decode()
    cid2 = dm.new_campaign(); gid2 = dm.new_group(cid2, name='Elsewhere')
    assert dm.get(f'/campaigns/{cid}/factions/{gid2}').status_code == 404      # URL-scope layer: another campaign's faction
    assert dm.get(f'/campaigns/{cid}/factions/999999').status_code in (302, 404)   # no such faction: never a 500


def test_faction_edit_adds_characters_scoped_to_the_campaign(make_user):
    dm, player = make_user(), make_user()
    cid = dm.new_campaign(); _add_player(dm, player, cid)
    gid = dm.new_group(cid, name='Guild'); other = dm.new_group(cid, name='Rivals')
    free = dm.new_character(cid, name='Free Agent')
    moving = dm.new_character(cid, name='Defector', group_id=other)
    cid2 = dm.new_campaign()
    foreign = dm.new_character(cid2, name='Elsewhere Hero')

    # The edit page offers this campaign's characters (and not another campaign's) to search.
    form = dm.get(f'/campaigns/{cid}/factions/{gid}/edit').data.decode()
    assert 'member-search' in form and 'Free Agent' in form and 'Defector' in form and 'Elsewhere Hero' not in form
    new_form = dm.get(f'/campaigns/{cid}/factions/new').data.decode()                      # DL-88: also when creating
    assert 'member-search' in new_form and 'Free Agent' in new_form and 'Elsewhere Hero' not in new_form

    r = dm.post(f'/campaigns/{cid}/factions/{gid}/edit', content_type='multipart/form-data',
                data={'name': 'Guild', 'color': '#336699', 'bio': '',
                      'add_character_ids': [str(free), str(moving), str(free), 'oops', str(foreign), '999999']})
    assert r.status_code == 302 and r.headers['Location'].endswith(f'/factions/{gid}')
    assert _group_of(free) == gid and _group_of(moving) == other      # added; Defector keeps Rivals as his main faction
    assert _members_of(moving) == {gid, other}                       # ...and is now also in Guild
    assert _group_of(foreign) is None                                # another campaign's character untouched
    assert 'Defector' in dm.get(f'/campaigns/{cid}/factions/{gid}').data.decode()

    # Saving the form with nothing picked changes no memberships.
    dm.post(f'/campaigns/{cid}/factions/{gid}/edit', data={'name': 'Guild2', 'color': '#336699'}, content_type='multipart/form-data')
    assert _group_of(free) == gid and q('SELECT name FROM groups WHERE id = ?', gid)[0]['name'] == 'Guild2'

    # A Player cannot add anyone.
    stranger = dm.new_character(cid, name='Stranger')
    assert player.post(f'/campaigns/{cid}/factions/{gid}/edit', content_type='multipart/form-data',
                       data={'name': 'Hax', 'add_character_ids': [str(stranger)]}).status_code == 403
    assert _group_of(stranger) is None


def test_faction_edit_cannot_touch_another_campaigns_faction(make_user):
    """A DM of campaign A must not be able to edit campaign B's faction via /campaigns/A/factions/<B's id>/edit."""
    a, b = make_user(), make_user()
    cid_a, cid_b = a.new_campaign(), b.new_campaign()
    victim_group = b.new_group(cid_b, name='B-Faction')
    victim_char = b.new_character(cid_b, name='B-Hero', group_id=victim_group)
    mine = a.new_character(cid_a, name='A-Hero')
    r = a.post(f'/campaigns/{cid_a}/factions/{victim_group}/edit', content_type='multipart/form-data',
               data={'name': 'Hijacked', 'bio': 'owned', 'add_character_ids': [str(mine)]})
    assert r.status_code == 404     # the URL-scope layer refuses another campaign's faction outright
    assert q('SELECT name FROM groups WHERE id = ?', victim_group)[0]['name'] == 'B-Faction'
    assert _group_of(mine) is None and _group_of(victim_char) == victim_group


# ---------------- DL-63 follow-ups: remove from faction, label cleanup, empty notes ----------------

def test_faction_member_remove_button(make_user):
    dm, player = make_user(), make_user()
    cid = dm.new_campaign(); _add_player(dm, player, cid)
    gid = dm.new_group(cid, name='Guild'); other = dm.new_group(cid, name='Rivals')
    a = dm.new_character(cid, name='Aldric', group_id=gid)
    b = dm.new_character(cid, name='Brute', group_id=gid)
    c = dm.new_character(cid, name='Cyrene', group_id=other)

    # The DM gets an X per member; a Player gets none.
    page = dm.get(f'/campaigns/{cid}/factions/{gid}').data.decode()
    assert page.count('faction-member-remove') == 2 and f'/factions/{gid}/members/{a}/remove' in page
    assert 'faction-member-remove' not in player.get(f'/campaigns/{cid}/factions/{gid}').data.decode()

    # Removing one member ungroups only them, and lands back on the Details page.
    r = dm.post(f'/campaigns/{cid}/factions/{gid}/members/{a}/remove')
    assert r.status_code == 302 and r.headers['Location'].endswith(f'/factions/{gid}')
    assert _group_of(a) is None and _group_of(b) == gid and _group_of(c) == other
    assert 'Aldric' not in dm.get(f'/campaigns/{cid}/factions/{gid}').data.decode()

    # Wrong faction for that character: nothing happens. A Player cannot remove anyone.
    dm.post(f'/campaigns/{cid}/factions/{gid}/members/{c}/remove')
    assert _group_of(c) == other
    assert player.post(f'/campaigns/{cid}/factions/{gid}/members/{b}/remove').status_code == 403
    assert _group_of(b) == gid
    # Another campaign's faction/character ids are refused outright.
    cid2 = dm.new_campaign(); g2 = dm.new_group(cid2, name='Else'); x2 = dm.new_character(cid2, name='Else Hero', group_id=g2)
    assert dm.post(f'/campaigns/{cid}/factions/{g2}/members/{x2}/remove').status_code == 404
    assert _group_of(x2) == g2


def test_page_eyebrows_removed_and_empty_notes_render_as_html(make_user):
    dm = make_user(); cid = dm.new_campaign()
    gid = dm.new_group(cid, name='Guild')
    ch = dm.new_character(cid, name='Blank')                      # no notes at all
    detail = dm.get(f'/campaigns/{cid}/characters/{ch}').data.decode()
    assert '<em>No notes recorded.</em>' in detail and '&lt;em&gt;' not in detail   # was shown as literal tags
    # A faction with no bio says so too (same wording as a character with no notes).
    assert '<em>No notes recorded.</em>' in dm.get(f'/campaigns/{cid}/factions/{gid}').data.decode()
    for url in (f'/campaigns/{cid}/characters/{ch}', f'/campaigns/{cid}/factions/{gid}', '/campaigns',
                '/campaigns/new', f'/campaigns/{cid}/edit'):
        assert 'page-eyebrow' not in dm.get(url).data.decode(), url


def test_members_can_be_picked_while_creating_a_faction(make_user):
    """DL-88: the same Add-characters box works on the New Faction form."""
    dm, player = make_user(), make_user()
    cid = dm.new_campaign(); _add_player(dm, player, cid)
    old = dm.new_group(cid, name='Old Guard')
    free = dm.new_character(cid, name='Free Agent')
    moving = dm.new_character(cid, name='Defector', group_id=old)
    cid2 = dm.new_campaign()
    foreign = dm.new_character(cid2, name='Elsewhere Hero')
    r = dm.post(f'/campaigns/{cid}/factions/new', content_type='multipart/form-data',
                data={'name': 'New Order', 'color': '#336699', 'bio': '',
                      'add_character_ids': [str(free), str(moving), str(free), 'oops', str(foreign), '999999']})
    assert r.status_code == 302
    gid = q('SELECT id FROM groups WHERE campaign_id = ? AND name = ?', cid, 'New Order')[0]['id']
    assert q('SELECT group_id FROM characters WHERE id = ?', free)[0]['group_id'] == gid
    assert q('SELECT group_id FROM characters WHERE id = ?', moving)[0]['group_id'] != gid      # kept Old Guard as his main faction ...
    assert _members_of(moving) >= {gid}                                                          # ... and is now also in the new one
    assert q('SELECT group_id FROM characters WHERE id = ?', foreign)[0]['group_id'] is None    # other campaign: untouched
    # a faction made with no members still works
    assert dm.post(f'/campaigns/{cid}/factions/new', content_type='multipart/form-data', data={'name': 'Empty'}).status_code == 302


def _char_in(user, cid, name, group_ids):
    """A character ticked into several factions at once (the shared helper only sends one value per field)."""
    r = user.post(f'/campaigns/{cid}/characters/new', content_type='multipart/form-data',
                  data={'name': name, 'level': '1', 'max_hp': '30', 'group_ids': [str(i) for i in group_ids]})
    assert r.status_code == 302
    return q('SELECT id FROM characters WHERE campaign_id = ? AND name = ? ORDER BY id DESC', cid, name)[0]['id']


def test_a_character_can_be_in_several_factions(make_user):
    dm = make_user(); cid = dm.new_campaign()
    guild = dm.new_group(cid, name='Guild'); rivals = dm.new_group(cid, name='Rivals'); cult = dm.new_group(cid, name='Cult')
    c = _char_in(dm, cid, 'Double Agent', [guild])
    assert _members_of(c) == {guild} and _group_of(c) == guild
    # the form takes any number of ticks; the main faction stays while it is still ticked
    r = dm.post(f'/campaigns/{cid}/characters/{c}/edit', content_type='multipart/form-data',
                data={'name': 'Double Agent', 'level': '1', 'max_hp': '30', 'group_ids': [str(rivals), str(guild), str(rivals), 'junk', '999999']})
    assert r.status_code == 302
    assert _members_of(c) == {guild, rivals} and _group_of(c) == guild
    page = dm.get(f'/campaigns/{cid}/characters/{c}').data.decode()
    assert 'Guild, Rivals' in page
    lst = dm.get(f'/campaigns/{cid}/characters').data.decode()
    assert f'data-group="{guild} {rivals}"' in lst
    detail = dm.get(f'/campaigns/{cid}/api/characters').json
    assert [f['name'] for f in next(x for x in detail if x['id'] == c)['factions']] == ['Guild', 'Rivals']
    # both faction pages list him, and the member counts agree
    for gid in (guild, rivals):
        assert 'Double Agent' in dm.get(f'/campaigns/{cid}/factions/{gid}').data.decode()
    # removing him from the main faction promotes the next one; the other faction page is unaffected
    assert dm.post(f'/campaigns/{cid}/factions/{guild}/members/{c}/remove').status_code == 302
    assert _members_of(c) == {rivals} and _group_of(c) == rivals
    # unticking everything clears the lot
    dm.post(f'/campaigns/{cid}/characters/{c}/edit', content_type='multipart/form-data', data={'name': 'Double Agent', 'level': '1', 'max_hp': '30'})
    assert _members_of(c) == set() and _group_of(c) is None


def test_deleting_a_faction_keeps_the_other_memberships(make_user):
    dm = make_user(); cid = dm.new_campaign()
    a = dm.new_group(cid, name='A'); b = dm.new_group(cid, name='B')
    c = _char_in(dm, cid, 'Two Hats', [a, b])
    assert _group_of(c) == a
    dm.post(f'/campaigns/{cid}/factions/{a}/delete')
    assert _members_of(c) == {b} and _group_of(c) == b


def test_battle_add_faction_uses_every_member_and_other_campaigns_stay_out(make_user):
    dm = make_user(); cid = dm.new_campaign()
    a = dm.new_group(cid, name='A'); b = dm.new_group(cid, name='B')
    only_a = _char_in(dm, cid, 'OnlyA', [a])
    both = _char_in(dm, cid, 'Both', [a, b])
    r = dm.post(f'/campaigns/{cid}/api/battle/add-group', json={'group_id': b})
    assert [p['character_id'] for p in r.json] == [both]
    r = dm.post(f'/campaigns/{cid}/api/battle/add-group', json={'group_id': a})
    assert sorted(p['character_id'] for p in r.json) == sorted([only_a, both, both])


def test_export_and_import_carry_every_faction(make_user):
    import io, json as _json, zipfile
    dm = make_user(); cid = dm.new_campaign()
    a = dm.new_group(cid, name='Alpha'); b = dm.new_group(cid, name='Beta')
    _char_in(dm, cid, 'Multi', [a, b])
    z = zipfile.ZipFile(io.BytesIO(dm.get(f'/campaigns/{cid}/export/data').data))
    manifest = _json.loads(z.read([n for n in z.namelist() if n.endswith('.json')][0]))
    assert manifest['characters'][0]['group_names'] == ['Alpha', 'Beta'] and manifest['characters'][0]['group_name'] == 'Alpha'
    cid2 = dm.new_campaign()
    r = dm.post(f'/campaigns/{cid2}/import/data', data={'import_file': (io.BytesIO(dm.get(f'/campaigns/{cid}/export/data').data), 'x.zip')}, content_type='multipart/form-data')
    assert r.status_code == 302
    row = q('SELECT id FROM characters WHERE campaign_id = ? AND name = ?', cid2, 'Multi')[0]
    assert len(_members_of(row['id'])) == 2
