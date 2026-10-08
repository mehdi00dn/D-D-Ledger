"""Characters live on the map without a battle; a linked map and the battle share one roster (DL map-characters)."""
import pytest
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _setup(dm):
    cid = dm.new_campaign()
    return cid, dm.new_map(cid)


def _place(dm, cid, mid, character_id, x=60, y=60):
    r = dm.json(f'/campaigns/{cid}/api/maps/{mid}/pins', {'pin_type': 'character', 'character_id': character_id, 'x': x, 'y': y})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _pins(dm, cid, mid):
    return dm.get(f'/campaigns/{cid}/api/maps/{mid}/pins').get_json()


def _roster(dm, cid):
    return dm.battle(cid)


def _link(dm, cid, mid, on=1):
    assert dm.json(f'/campaigns/{cid}/api/maps/{mid}/settings', {'linked_to_battle': on}).status_code == 200


def test_the_dm_can_place_characters_on_an_unlinked_map_without_touching_the_battle(make_user):
    dm = make_user(); cid, mid = _setup(dm)
    gob = dm.new_character(cid, name='Goblin', max_hp=7, is_npc='on')
    for _ in range(2):
        _place(dm, cid, mid, gob)
    pins = _pins(dm, cid, mid)
    assert [p['char_name'] for p in pins] == ['Goblin #1', 'Goblin #2']          # duplicates number themselves
    assert all(p['pin_type'] == 'character' and p['participant_id'] is None and p['current_hp'] is None for p in pins)
    assert _roster(dm, cid) == []                                                # the battle is untouched
    d = dm.get(f'/campaigns/{cid}/api/maps/{mid}/sync').get_json()
    assert d['pins'] == pins                                                     # and the live refresh carries them


def test_only_the_dm_places_or_removes_characters_and_ids_are_checked(make_user):
    dm, pl, other = make_user(), make_user(), make_user()
    cid, mid = _setup(dm); dm.add_member(cid, pl)
    hero = dm.new_character(cid, name='Aria')
    ocid = other.new_campaign(); stranger = other.new_character(ocid, name='Stranger')
    url = f'/campaigns/{cid}/api/maps/{mid}/pins'
    assert pl.json(url, {'pin_type': 'character', 'character_id': hero, 'x': 1, 'y': 1}).status_code == 403
    assert dm.json(url, {'pin_type': 'character', 'character_id': stranger, 'x': 1, 'y': 1}).status_code == 404   # another campaign's character
    assert dm.json(url, {'pin_type': 'character', 'character_id': 'x', 'x': 1, 'y': 1}).status_code == 400
    pin = _place(dm, cid, mid, hero)['id']
    assert pl.json(f'{url}/{pin}/delete', {}).status_code == 403                  # removing a character is the DM's call
    assert pl.json(f'{url}/{pin}/update', {'x': 99}).status_code == 403           # an unassigned token is DM-only
    uid = q('SELECT id FROM users WHERE username = ?', pl.name)[0]['id']
    assert dm.json(f'{url}/{pin}/update', {'owner_user_id': uid}).status_code == 200
    assert pl.json(f'{url}/{pin}/update', {'x': 99}).status_code == 200           # once the DM assigns it, that Player may move it
    assert dm.json(f'{url}/{pin}/delete', {}).status_code == 200
    assert _pins(dm, cid, mid) == []


def test_players_see_placed_characters_with_monster_numbers_hidden(make_user):
    dm, pl = make_user(), make_user(); cid, mid = _setup(dm); dm.add_member(cid, pl)
    ogre = dm.new_character(cid, name='Ogre', max_hp=59, is_npc='on'); hero = dm.new_character(cid, name='Aria')
    _place(dm, cid, mid, ogre); _place(dm, cid, mid, hero)
    seen = {p['char_name']: p for p in _pins(pl, cid, mid)}
    assert set(seen) == {'Ogre', 'Aria'} and seen['Ogre']['hidden_stats'] is True and '59' not in str(seen['Ogre'])


def test_a_linked_map_and_the_battle_share_one_roster(make_user):
    dm = make_user(); cid, mid = _setup(dm)
    aria = dm.new_character(cid, name='Aria'); bram = dm.new_character(cid, name='Bram'); gob = dm.new_character(cid, name='Goblin')
    _link(dm, cid, mid)
    # map -> battle
    placed = _place(dm, cid, mid, aria)
    assert placed['participant_id'] and [r['character_id'] for r in _roster(dm, cid)] == [aria]
    # battle -> map (shows up on the next map refresh)
    dm.add_to_battle(cid, bram)
    assert {p['character_id'] for p in _pins(dm, cid, mid)} == {aria, bram}
    # removed from the battle -> removed from the map
    bram_row = [r for r in _roster(dm, cid) if r['character_id'] == bram][0]['id']
    assert dm.json(f'/campaigns/{cid}/api/battle/{bram_row}/remove', {}).status_code == 200
    assert {p['character_id'] for p in _pins(dm, cid, mid)} == {aria}
    # removed from the map -> removed from the battle
    gob_pin = _place(dm, cid, mid, gob)['id']
    assert {r['character_id'] for r in _roster(dm, cid)} == {aria, gob}
    assert dm.json(f'/campaigns/{cid}/api/maps/{mid}/pins/{gob_pin}/delete', {}).status_code == 200
    assert {r['character_id'] for r in _roster(dm, cid)} == {aria}
    assert q('SELECT COUNT(*) AS n FROM battle_participants')[0]['n'] >= 1


def test_linking_merges_without_duplicates_and_unlinking_keeps_the_tokens(make_user):
    dm = make_user(); cid, mid = _setup(dm)
    aria = dm.new_character(cid, name='Aria'); bram = dm.new_character(cid, name='Bram'); gob = dm.new_character(cid, name='Goblin')
    _place(dm, cid, mid, aria); _place(dm, cid, mid, gob)                         # on the map, not in the battle
    dm.add_to_battle(cid, aria); dm.add_to_battle(cid, bram)                      # in the battle, Aria not yet pinned
    _link(dm, cid, mid)
    roster = _roster(dm, cid)
    assert sorted(r['character_id'] for r in roster) == sorted([aria, bram, gob])  # Aria adopted her existing row, Goblin joined
    pins = _pins(dm, cid, mid)
    assert sorted(p['character_id'] for p in pins) == sorted([aria, bram, gob]) and all(p['participant_id'] for p in pins)
    assert len({p['participant_id'] for p in pins}) == 3
    _link(dm, cid, mid, 0)                                                         # unlink: tokens stay, the battle is unchanged
    pins = _pins(dm, cid, mid)
    assert len(pins) == 3 and all(p['participant_id'] is None for p in pins)
    assert len(_roster(dm, cid)) == 3
    dm.add_to_battle(cid, bram)                                                    # battle changes no longer touch the map
    assert len(_pins(dm, cid, mid)) == 3
    _link(dm, cid, mid)                                                            # relink: everything lines up again
    assert len(_roster(dm, cid)) == 4 and len(_pins(dm, cid, mid)) == 4


def test_only_one_map_shares_the_battle_and_the_other_keeps_its_tokens(make_user):
    dm = make_user(); cid, m1 = _setup(dm); m2 = dm.new_map(cid, name='Second')
    aria = dm.new_character(cid, name='Aria')
    _link(dm, cid, m1); _place(dm, cid, m1, aria)
    _link(dm, cid, m2)
    first = _pins(dm, cid, m1)
    assert len(first) == 1 and first[0]['participant_id'] is None                   # m1 kept its token, now plain
    assert len(_pins(dm, cid, m2)) == 1 and len(_roster(dm, cid)) == 1               # m2 got the shared roster, no duplicate


def test_removing_the_acting_character_from_the_map_passes_the_turn(make_user):
    dm = make_user(); cid, mid = _setup(dm)
    a = dm.new_character(cid, name='A'); b = dm.new_character(cid, name='B')
    _link(dm, cid, mid)
    pa = _place(dm, cid, mid, a)['id']; _place(dm, cid, mid, b)
    dm.json(f'/campaigns/{cid}/api/battle/next-turn', {})
    acting = [r for r in _roster(dm, cid) if r['is_active']][0]['character_id']
    pin = [p for p in _pins(dm, cid, mid) if p['character_id'] == acting][0]['id']
    assert dm.json(f'/campaigns/{cid}/api/maps/{mid}/pins/{pin}/delete', {}).status_code == 200
    rows = _roster(dm, cid)
    assert len(rows) == 1 and rows[0]['is_active']                                  # the turn moved on to the one left


def test_deleting_a_character_removes_their_tokens(make_user):
    dm = make_user(); cid, mid = _setup(dm)
    aria = dm.new_character(cid, name='Aria')
    _place(dm, cid, mid, aria)
    assert dm.post(f'/campaigns/{cid}/characters/{aria}/delete').status_code in (200, 302)
    assert _pins(dm, cid, mid) == [] and q('SELECT COUNT(*) AS n FROM map_pins WHERE character_id = ?', aria)[0]['n'] == 0
