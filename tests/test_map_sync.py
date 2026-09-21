"""/sync must return exactly what the three separate endpoints return -- in one request."""
import pytest
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _setup(dm):
    cid = dm.new_campaign()
    mid = dm.new_map(cid)
    dm.json(f'/campaigns/{cid}/api/maps/{mid}/drawings', {'kind': 'rect', 'data': {'a': 1}, 'cx': 5, 'cy': 6, 'w': 7, 'h': 8})
    return cid, mid


def test_sync_equals_the_three_separate_endpoints_when_unlinked(user):
    cid, mid = _setup(user)
    base = f'/campaigns/{cid}/api/maps/{mid}'
    d = user.get(f'{base}/sync')
    assert d.status_code == 200
    d = d.get_json()
    assert d['state'] == user.get(f'{base}/state').get_json()
    assert d['drawings'] == user.get(f'{base}/drawings').get_json() and len(d['drawings']) == 1
    assert d['pins'] is None                                     # pins are only polled while linked to the battle


def test_sync_includes_pins_when_linked_and_matches_the_pins_endpoint(user):
    cid, mid = _setup(user)
    ch = user.new_character(cid, name='Rogue'); user.add_to_battle(cid, ch)
    base = f'/campaigns/{cid}/api/maps/{mid}'
    user.json(f'{base}/settings', {'linked_to_battle': 1})
    d = user.get(f'{base}/sync').get_json()
    assert d['state']['linked_to_battle'] == 1
    assert d['pins'] and d['pins'] == user.get(f'{base}/pins').get_json()
    assert any(p['pin_type'] == 'character' for p in d['pins'])


def test_players_get_the_same_redacted_pin_data_from_sync(make_user):
    dm, pl = make_user(), make_user()
    cid, mid = _setup(dm)
    dm.post(f'/campaigns/{cid}/members/add', data={'username': pl.name, 'status': 'player'})
    npc = dm.new_character(cid, name='Ogre', max_hp=59, is_npc='on'); dm.add_to_battle(cid, npc)
    base = f'/campaigns/{cid}/api/maps/{mid}'
    dm.json(f'{base}/settings', {'linked_to_battle': 1})
    via_sync = pl.get(f'{base}/sync').get_json()['pins']
    assert via_sync == pl.get(f'{base}/pins').get_json()
    assert '59' not in str(via_sync)                              # NPC hit points never reach the player


def test_sync_is_campaign_scoped(make_user):
    alice, bob = make_user(), make_user()
    cid, mid = _setup(alice)
    cb = bob.new_campaign()
    assert bob.get(f'/campaigns/{cb}/api/maps/{mid}/sync').status_code == 404      # someone else's map id
    assert bob.get(f'/campaigns/{cid}/api/maps/{mid}/sync').status_code == 404      # someone else's campaign
    assert alice.get(f'/campaigns/{cid}/api/maps/999999/sync').status_code == 404


def test_sync_uses_a_single_database_connection(user, appmod):
    """The point of consolidating: one request costs one connection, not three."""
    import database
    cid, mid = _setup(user)
    opened = []
    real = database._connect
    def counting():
        opened.append(1); return real()
    database._connect = counting
    try:
        assert user.get(f'/campaigns/{cid}/api/maps/{mid}/sync').status_code == 200
    finally:
        database._connect = real
    assert len(opened) == 1
