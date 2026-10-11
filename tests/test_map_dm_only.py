"""DL-43: DM-only (hidden) map objects are filtered server-side -- a Player never receives one, never changes one,
and can never set the flag; the DM sees and manages them."""
import pytest
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')

RECT = {'kind': 'rect', 'cx': 50, 'cy': 50, 'w': 20, 'h': 20, 'color': '#ff0000', 'data': {}}


def _world(make_user):
    dm, alice = make_user(), make_user()
    cid = dm.new_campaign()
    dm.add_member(cid, alice)
    mid = dm.new_map(cid)
    base = f'/campaigns/{cid}/api/maps/{mid}'
    return dm, alice, cid, mid, base


def _ids(rows):
    return {r['id'] for r in rows}


def _shapes(user, base):
    return user.get(f'{base}/drawings').get_json()


def _pins(user, base):
    return user.get(f'{base}/pins').get_json()


def test_hidden_drawing_is_withheld_from_players_on_every_read_path(make_user):
    dm, alice, cid, mid, base = _world(make_user)
    shown = dm.json(f'{base}/drawings', dict(RECT)).get_json()['id']
    hidden = dm.json(f'{base}/drawings', dict(RECT, dm_only=True)).get_json()['id']
    assert _ids(_shapes(dm, base)) == {shown, hidden}
    assert _ids(_shapes(alice, base)) == {shown}
    assert _ids(alice.get(f'{base}/sync').get_json()['drawings']) == {shown}
    assert _ids(dm.get(f'{base}/sync').get_json()['drawings']) == {shown, hidden}
    assert next(r for r in _shapes(dm, base) if r['id'] == hidden)['dm_only'] == 1


def test_hidden_token_is_withheld_from_players(make_user):
    dm, alice, cid, mid, base = _world(make_user)
    shown = dm.json(f'{base}/pins', {'pin_type': 'prop', 'icon_key': 'skull', 'x': 5, 'y': 5}).get_json()['id']
    hidden = dm.json(f'{base}/pins', {'pin_type': 'prop', 'icon_key': 'skull', 'x': 9, 'y': 9, 'dm_only': True}).get_json()['id']
    assert _ids(_pins(dm, base)) == {shown, hidden}
    assert _ids(_pins(alice, base)) == {shown}
    assert _ids(alice.get(f'{base}/sync').get_json()['pins']) == {shown}


def test_dm_can_toggle_the_flag_and_players_see_the_change(make_user):
    dm, alice, cid, mid, base = _world(make_user)
    d = dm.json(f'{base}/drawings', dict(RECT)).get_json()['id']
    p = dm.json(f'{base}/pins', {'pin_type': 'prop', 'icon_key': 'skull', 'x': 5, 'y': 5}).get_json()['id']
    assert _ids(_shapes(alice, base)) == {d} and _ids(_pins(alice, base)) == {p}
    assert dm.json(f'{base}/drawings/{d}/update', {'dm_only': True}).status_code == 200
    assert dm.json(f'{base}/pins/{p}/update', {'dm_only': True}).status_code == 200
    assert _shapes(alice, base) == [] and _pins(alice, base) == []
    assert dm.json(f'{base}/drawings/{d}/update', {'dm_only': False}).status_code == 200
    assert dm.json(f'{base}/pins/{p}/update', {'dm_only': False}).status_code == 200
    assert _ids(_shapes(alice, base)) == {d} and _ids(_pins(alice, base)) == {p}


def test_players_cannot_set_the_flag_or_touch_hidden_objects(make_user):
    dm, alice, cid, mid, base = _world(make_user)
    # A Player's own drawing/marker ignores the flag on creation and cannot be hidden afterwards.
    mine = alice.json(f'{base}/drawings', dict(RECT, dm_only=True)).get_json()['id']
    assert q('SELECT dm_only FROM map_drawings WHERE id = ?', mine)[0]['dm_only'] == 0
    assert alice.json(f'{base}/drawings/{mine}/update', {'dm_only': True}).status_code == 403
    pin_mine = alice.json(f'{base}/pins', {'pin_type': 'prop', 'icon_key': 'skull', 'x': 1, 'y': 1, 'dm_only': True}).get_json()['id']
    assert q('SELECT dm_only FROM map_pins WHERE id = ?', pin_mine)[0]['dm_only'] == 0
    assert alice.json(f'{base}/pins/{pin_mine}/update', {'dm_only': True}).status_code == 403

    # Guessing the id of a hidden object changes nothing.
    hidden_d = dm.json(f'{base}/drawings', dict(RECT, dm_only=True)).get_json()['id']
    hidden_p = dm.json(f'{base}/pins', {'pin_type': 'prop', 'icon_key': 'skull', 'x': 9, 'y': 9, 'dm_only': True}).get_json()['id']
    dm.json(f'{base}/pins/{hidden_p}/update', {'owner_user_id': q('SELECT id FROM users WHERE username = ?', alice.name)[0]['id']})
    alice.json(f'{base}/drawings/{hidden_d}/update', {'cx': 1, 'color': '#000000'})
    alice.json(f'{base}/drawings/{hidden_d}/delete')
    assert alice.json(f'{base}/pins/{hidden_p}/update', {'x': 77}).status_code == 403   # even when assigned to them
    assert alice.json(f'{base}/pins/{hidden_p}/delete').status_code == 403
    row = q('SELECT cx, color FROM map_drawings WHERE id = ?', hidden_d)[0]
    assert (row['cx'], row['color']) == (50, '#ff0000')
    assert q('SELECT x FROM map_pins WHERE id = ?', hidden_p)[0]['x'] == 9


def test_a_player_clearing_the_map_leaves_hidden_drawings(make_user):
    dm, alice, cid, mid, base = _world(make_user)
    dm.json(f'{base}/drawings', dict(RECT))
    hidden = dm.json(f'{base}/drawings', dict(RECT, dm_only=True)).get_json()['id']
    alice.json(f'{base}/drawings/clear')
    assert _ids(_shapes(dm, base)) == {hidden}


def test_previewing_as_a_player_hides_them_from_the_dm_too(make_user):
    dm, alice, cid, mid, base = _world(make_user)
    d = dm.json(f'{base}/drawings', dict(RECT, dm_only=True)).get_json()['id']
    assert _ids(_shapes(dm, base)) == {d}
    dm.post(f'/campaigns/{cid}/preview-as-player', data={'on': '1'})
    assert _shapes(dm, base) == []
    dm.post(f'/campaigns/{cid}/preview-as-player', data={'on': '0'})
    assert _ids(_shapes(dm, base)) == {d}
