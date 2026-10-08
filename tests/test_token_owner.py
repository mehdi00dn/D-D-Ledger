"""Token ownership: the DM attaches a map token to a campaign member; a Player moves only their own tokens."""
import pytest
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _world(make_user):
    dm, alice, bob = make_user(), make_user(), make_user()
    cid = dm.new_campaign()
    dm.add_member(cid, alice); dm.add_member(cid, bob)
    mid = dm.new_map(cid)
    base = f'/campaigns/{cid}/api/maps/{mid}'
    return dm, alice, bob, cid, mid, base


def _prop(dm, base, x=10, y=10):
    r = dm.json(f'{base}/pins', {'pin_type': 'prop', 'icon_key': 'skull', 'x': x, 'y': y})
    assert r.status_code == 200
    return r.get_json()['id']


def _user_id(u):
    return q('SELECT id FROM users WHERE username = ?', u.name)[0]['id']


def test_unassigned_tokens_are_dm_only(make_user):
    dm, alice, bob, cid, mid, base = _world(make_user)
    pin = _prop(dm, base)
    assert alice.json(f'{base}/pins/{pin}/update', {'x': 99}).status_code == 403
    assert alice.json(f'{base}/pins/{pin}/delete').status_code == 403
    assert q('SELECT x FROM map_pins WHERE id = ?', pin)[0]['x'] == 10
    assert dm.json(f'{base}/pins/{pin}/update', {'x': 50}).status_code == 200


def test_player_moves_only_the_token_assigned_to_them(make_user):
    dm, alice, bob, cid, mid, base = _world(make_user)
    mine, theirs = _prop(dm, base), _prop(dm, base, 30, 30)
    assert dm.json(f'{base}/pins/{mine}/update', {'owner_user_id': _user_id(alice)}).status_code == 200
    assert dm.json(f'{base}/pins/{theirs}/update', {'owner_user_id': _user_id(bob)}).status_code == 200
    assert alice.json(f'{base}/pins/{mine}/update', {'x': 77, 'y': 78}).status_code == 200
    assert alice.json(f'{base}/pins/{theirs}/update', {'x': 1}).status_code == 403
    assert bob.json(f'{base}/pins/{mine}/update', {'x': 2}).status_code == 403
    row = q('SELECT x, y FROM map_pins WHERE id = ?', mine)[0]
    assert (row['x'], row['y']) == (77, 78)
    assert q('SELECT x FROM map_pins WHERE id = ?', theirs)[0]['x'] == 30


def test_players_cannot_assign_or_reassign_tokens(make_user):
    dm, alice, bob, cid, mid, base = _world(make_user)
    pin = _prop(dm, base)
    dm.json(f'{base}/pins/{pin}/update', {'owner_user_id': _user_id(alice)})
    assert alice.json(f'{base}/pins/{pin}/update', {'owner_user_id': _user_id(bob)}).status_code == 403   # not even the holder
    assert bob.json(f'{base}/pins/{pin}/update', {'owner_user_id': _user_id(bob)}).status_code == 403
    assert q('SELECT owner_user_id FROM map_pins WHERE id = ?', pin)[0]['owner_user_id'] == _user_id(alice)


def test_only_campaign_members_can_hold_a_token_and_it_can_be_cleared(make_user):
    dm, alice, bob, cid, mid, base = _world(make_user)
    outsider = make_user()
    pin = _prop(dm, base)
    assert dm.json(f'{base}/pins/{pin}/update', {'owner_user_id': _user_id(outsider)}).status_code == 400
    assert dm.json(f'{base}/pins/{pin}/update', {'owner_user_id': 'abc'}).status_code == 400
    assert dm.json(f'{base}/pins/{pin}/update', {'owner_user_id': _user_id(alice)}).status_code == 200
    assert dm.json(f'{base}/pins/{pin}/update', {'owner_user_id': None}).status_code == 200
    assert q('SELECT owner_user_id FROM map_pins WHERE id = ?', pin)[0]['owner_user_id'] is None
    assert alice.json(f'{base}/pins/{pin}/update', {'x': 5}).status_code == 403


def test_a_character_token_can_be_assigned_and_moved_by_its_player(make_user):
    dm, alice, bob, cid, mid, base = _world(make_user)
    hero = dm.new_character(cid, name='Hero')
    pin = dm.json(f'{base}/pins', {'pin_type': 'character', 'character_id': hero, 'x': 20, 'y': 20}).get_json()['id']
    assert alice.json(f'{base}/pins/{pin}/update', {'x': 3}).status_code == 403
    dm.json(f'{base}/pins/{pin}/update', {'owner_user_id': _user_id(alice)})
    assert alice.json(f'{base}/pins/{pin}/update', {'x': 33, 'y': 34}).status_code == 200
    assert alice.json(f'{base}/pins/{pin}/delete').status_code == 403            # taking a character off the map stays the DM's call
    assert q('SELECT x FROM map_pins WHERE id = ?', pin)[0]['x'] == 33


def test_payload_tells_each_viewer_what_they_can_move(make_user):
    dm, alice, bob, cid, mid, base = _world(make_user)
    mine, other = _prop(dm, base), _prop(dm, base, 40, 40)
    dm.json(f'{base}/pins/{mine}/update', {'owner_user_id': _user_id(alice)})
    by_id = lambda u: {p['id']: p for p in u.get(f'{base}/pins').get_json()}
    a, b, d = by_id(alice), by_id(bob), by_id(dm)
    assert a[mine]['can_move'] is True and a[other]['can_move'] is False
    assert b[mine]['can_move'] is False
    assert d[mine]['can_move'] is True and d[other]['can_move'] is True
    assert a[mine]['owner_name'] == alice.name and a[other]['owner_name'] is None


def test_a_marker_a_player_places_is_theirs(make_user):
    dm, alice, bob, cid, mid, base = _world(make_user)
    pin = alice.json(f'{base}/pins', {'pin_type': 'prop', 'icon_key': 'paw', 'x': 5, 'y': 5}).get_json()['id']
    assert q('SELECT owner_user_id FROM map_pins WHERE id = ?', pin)[0]['owner_user_id'] == _user_id(alice)
    assert alice.json(f'{base}/pins/{pin}/update', {'x': 6}).status_code == 200
    assert bob.json(f'{base}/pins/{pin}/update', {'x': 7}).status_code == 403
    assert bob.json(f'{base}/pins/{pin}/delete').status_code == 403
    assert alice.json(f'{base}/pins/{pin}/delete').status_code == 200


def test_the_map_lock_still_beats_ownership(make_user):
    dm, alice, bob, cid, mid, base = _world(make_user)
    pin = _prop(dm, base)
    dm.json(f'{base}/pins/{pin}/update', {'owner_user_id': _user_id(alice)})
    assert dm.json(f'{base}/settings', {'locked_for_players': 1}).status_code == 200
    assert alice.json(f'{base}/pins/{pin}/update', {'x': 8}).status_code == 403
    assert dm.json(f'{base}/pins/{pin}/update', {'x': 8}).status_code == 200
    assert dm.get(f'{base}/pins').get_json()[0]['can_move'] is True
    assert alice.get(f'{base}/pins').get_json()[0]['can_move'] is False        # the client greys it out too


def test_a_dm_previewing_as_player_gets_player_rules(make_user):
    dm, alice, bob, cid, mid, base = _world(make_user)
    pin = _prop(dm, base)
    assert dm.post(f'/campaigns/{cid}/preview-as-player', data={'on': '1'}).status_code == 302
    assert dm.json(f'{base}/pins/{pin}/update', {'x': 9}).status_code == 403


def test_the_editor_hands_the_dm_the_member_list(make_user):
    dm, alice, bob, cid, mid, base = _world(make_user)
    html = dm.get(f'/campaigns/{cid}/maps/{mid}').get_data(as_text=True)
    assert 'id="pin-owner-btn"' in html and alice.name in html
    assert 'pin-owner-btn' not in alice.get(f'/campaigns/{cid}/maps/{mid}').get_data(as_text=True)
