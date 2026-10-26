"""DL-159: a shape can be attached to a token; moving the token carries the shape by the same distance."""
import pytest
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')

RECT = {'kind': 'rect', 'cx': 50, 'cy': 60, 'w': 20, 'h': 20, 'color': '#ff0000', 'data': {}}
PIN = {'pin_type': 'prop', 'icon_key': 'skull', 'x': 100, 'y': 100}


def _world(make_user):
    dm, alice = make_user(), make_user()
    cid = dm.new_campaign()
    dm.add_member(cid, alice)
    mid = dm.new_map(cid)
    return dm, alice, cid, mid, f'/campaigns/{cid}/api/maps/{mid}'


def _pos(did):
    r = q('SELECT cx, cy, attached_pin_id FROM map_drawings WHERE id = ?', did)[0]
    return r['cx'], r['cy'], r['attached_pin_id']


def test_moving_a_token_carries_attached_shapes_only(make_user):
    dm, alice, cid, mid, base = _world(make_user)
    pin = dm.json(f'{base}/pins', PIN).get_json()['id']
    other = dm.json(f'{base}/pins', dict(PIN, x=300)).get_json()['id']
    a = dm.json(f'{base}/drawings', dict(RECT, attached_pin_id=pin)).get_json()['id']
    b = dm.json(f'{base}/drawings', dict(RECT)).get_json()['id']
    c = dm.json(f'{base}/drawings', dict(RECT, attached_pin_id=other)).get_json()['id']
    assert dm.json(f'{base}/pins/{pin}/update', {'x': 130, 'y': 90}).status_code == 200
    assert _pos(a) == (80, 50, pin)
    assert _pos(b) == (50, 60, None)
    assert _pos(c) == (50, 60, other)


def test_a_change_without_movement_leaves_shapes_alone(make_user):
    dm, alice, cid, mid, base = _world(make_user)
    pin = dm.json(f'{base}/pins', PIN).get_json()['id']
    a = dm.json(f'{base}/drawings', dict(RECT, attached_pin_id=pin)).get_json()['id']
    dm.json(f'{base}/pins/{pin}/update', {'scale': 2})
    dm.json(f'{base}/pins/{pin}/update', {'x': 100, 'y': 100})
    assert _pos(a) == (50, 60, pin)


def test_attach_and_detach_through_update(make_user):
    dm, alice, cid, mid, base = _world(make_user)
    pin = dm.json(f'{base}/pins', PIN).get_json()['id']
    a = dm.json(f'{base}/drawings', dict(RECT)).get_json()['id']
    assert dm.json(f'{base}/drawings/{a}/update', {'attached_pin_id': pin}).status_code == 200
    assert _pos(a) == (50, 60, pin)          # attaching never moves the shape
    dm.json(f'{base}/pins/{pin}/update', {'x': 110})
    assert _pos(a) == (60, 60, pin)
    assert dm.json(f'{base}/drawings/{a}/update', {'attached_pin_id': None}).status_code == 200
    dm.json(f'{base}/pins/{pin}/update', {'x': 200})
    assert _pos(a) == (60, 60, None)


def test_unknown_or_foreign_tokens_are_refused(make_user):
    dm, alice, cid, mid, base = _world(make_user)
    mid2 = dm.new_map(cid, name='Other')
    foreign = dm.json(f'/campaigns/{cid}/api/maps/{mid2}/pins', PIN).get_json()['id']
    a = dm.json(f'{base}/drawings', dict(RECT)).get_json()['id']
    assert dm.json(f'{base}/drawings/{a}/update', {'attached_pin_id': foreign}).status_code == 400
    assert dm.json(f'{base}/drawings/{a}/update', {'attached_pin_id': 999999}).status_code == 400
    assert _pos(a)[2] is None


def test_players_cannot_see_or_attach_to_hidden_tokens(make_user):
    dm, alice, cid, mid, base = _world(make_user)
    hidden = dm.json(f'{base}/pins', dict(PIN, dm_only=True)).get_json()['id']
    shown = dm.json(f'{base}/pins', dict(PIN, x=5)).get_json()['id']
    a = dm.json(f'{base}/drawings', dict(RECT, attached_pin_id=hidden)).get_json()['id']
    d = dm.json(f'{base}/drawings', dict(RECT, attached_pin_id=shown)).get_json()['id']
    seen = {r['id']: r['attached_pin_id'] for r in alice.get(f'{base}/drawings').get_json()}
    assert seen == {a: None, d: shown}
    assert alice.json(f'{base}/drawings/{a}/update', {'attached_pin_id': hidden}).status_code in (400, 403)
    mine = alice.json(f'{base}/drawings', dict(RECT, attached_pin_id=hidden))
    assert mine.status_code == 400


def test_deleting_the_token_detaches_but_keeps_the_shape(make_user):
    dm, alice, cid, mid, base = _world(make_user)
    pin = dm.json(f'{base}/pins', PIN).get_json()['id']
    a = dm.json(f'{base}/drawings', dict(RECT, attached_pin_id=pin)).get_json()['id']
    assert dm.json(f'{base}/pins/{pin}/delete').status_code == 200
    assert _pos(a) == (50, 60, None)
