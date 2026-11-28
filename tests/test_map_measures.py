"""DL-153: shared measurements -- the ruler and the cone / sphere / line / cube templates shown to the table."""
import pytest
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _world(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, pl)
    return dm, pl, cid, dm.new_map(cid)


def _send(c, cid, mid, kind='cone', a=(10, 20), b=(110, 20), done=False):
    return c.json(f'/campaigns/{cid}/api/maps/{mid}/measure', {'kind': kind, 'a': {'x': a[0], 'y': a[1]}, 'b': {'x': b[0], 'y': b[1]}, 'done': done})


def _measures(c, cid, mid):
    return c.get(f'/campaigns/{cid}/api/maps/{mid}/sync').get_json()['measures']


def test_what_one_person_shows_the_others_see_but_not_themselves(make_user):
    dm, pl, cid, mid = _world(make_user)
    assert _send(pl, cid, mid, 'sphere', (5, 6), (55, 6)).status_code == 200
    seen = _measures(dm, cid, mid)
    assert len(seen) == 1 and seen[0]['kind'] == 'sphere' and (seen[0]['ax'], seen[0]['bx']) == (5, 55)
    assert 0 < seen[0]['ttl_ms'] <= 5000 and seen[0]['name']
    assert _measures(pl, cid, mid) == []                                  # your own is drawn locally, never echoed back


def test_one_row_per_person_is_replaced_and_clears(make_user):
    dm, pl, cid, mid = _world(make_user)
    _send(pl, cid, mid, 'cone'); _send(pl, cid, mid, 'cube', done=True)
    seen = _measures(dm, cid, mid)
    assert len(seen) == 1 and seen[0]['kind'] == 'cube' and seen[0]['ttl_ms'] > 5000    # let go: held a little longer
    assert pl.json(f'/campaigns/{cid}/api/maps/{mid}/measure/clear', {}).status_code == 200
    assert _measures(dm, cid, mid) == []


def test_expired_measurements_are_not_sent(make_user):
    dm, pl, cid, mid = _world(make_user)
    _send(pl, cid, mid)
    import database
    db = database.get_db()
    db.execute("UPDATE map_measures SET expires_at = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - INTERVAL '2 seconds' WHERE map_id = ?", (mid,))
    db.commit(); db.close()
    assert _measures(dm, cid, mid) == []


def test_bad_input_is_refused_and_other_campaigns_cannot_reach_it(make_user):
    dm, pl, cid, mid = _world(make_user)
    for bad in ({'kind': 'laser', 'a': {'x': 1, 'y': 1}, 'b': {'x': 2, 'y': 2}},
                {'kind': 'ruler', 'a': {'x': 'x', 'y': 1}, 'b': {'x': 2, 'y': 2}},
                {'kind': 'ruler', 'a': {'x': 1e12, 'y': 1}, 'b': {'x': 2, 'y': 2}},
                {'kind': 'ruler', 'a': {'x': 1, 'y': 1}}):
        assert dm.json(f'/campaigns/{cid}/api/maps/{mid}/measure', bad).status_code == 400
    other = make_user(); ocid = other.new_campaign()
    assert other.json(f'/campaigns/{ocid}/api/maps/{mid}/measure', {'kind': 'ruler', 'a': {'x': 1, 'y': 1}, 'b': {'x': 2, 'y': 2}}).status_code == 404
    assert q('SELECT COUNT(*) AS n FROM map_measures WHERE map_id = ?', mid)[0]['n'] == 0


def test_a_locked_map_does_not_stop_players_measuring(make_user):
    dm, pl, cid, mid = _world(make_user)
    assert dm.json(f'/campaigns/{cid}/api/maps/{mid}/settings', {'locked_for_players': 1}).status_code == 200
    assert _send(pl, cid, mid, 'line').status_code == 200
    assert [m['kind'] for m in _measures(dm, cid, mid)] == ['line']
