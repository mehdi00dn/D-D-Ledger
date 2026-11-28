"""DL-11: the map and the battle share one roster, and every way of syncing them is idempotent.

Rule (one authoritative link): a character token on a linked map points at exactly one battle row through
map_pins.participant_id, and a unique index (map_id, participant_id) enforces it. Reconciling is a no-op when
everything already lines up, so any number of refreshes, links or relinks, even at the same moment, never doubles anyone."""
import concurrent.futures as cf
import pytest
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _settings(c, cid, mid, on):
    return c.post(f'/campaigns/{cid}/api/maps/{mid}/settings', json={'linked_to_battle': on}).status_code


def _world(dm, extra_in_battle=True):
    cid = dm.new_campaign(); mid = dm.new_map(cid)
    chars = [dm.new_character(cid, name=n, max_hp=10) for n in ('Aria', 'Bram', 'Cora')]
    for ch in chars[:2]:                                   # two tokens placed on the unlinked map (no battle row yet)
        assert dm.json(f'/campaigns/{cid}/api/maps/{mid}/pins', {'pin_type': 'character', 'character_id': ch, 'x': 50, 'y': 50}).status_code == 200
    if extra_in_battle:
        dm.add_to_battle(cid, chars[2])                    # one battle row with no token
    return cid, mid, chars


def _counts(cid, mid):
    rows = q('SELECT bp.character_id AS c, COUNT(*) AS n FROM battle_participants bp JOIN characters ch ON ch.id = bp.character_id '
             'WHERE ch.campaign_id = ? AND ch.is_temp_familiar = 0 GROUP BY bp.character_id', cid)
    pins = q("SELECT participant_id AS p, COUNT(*) AS n FROM map_pins WHERE map_id = ? AND pin_type = 'character' GROUP BY participant_id", mid)
    return rows, pins


def test_many_refreshes_of_a_linked_map_change_nothing(user):
    cid, mid, chars = _world(user)
    assert _settings(user, cid, mid, 1) == 200
    before = _counts(cid, mid)
    for _ in range(5):
        assert user.get(f'/campaigns/{cid}/api/maps/{mid}/pins').status_code == 200
        assert user.get(f'/campaigns/{cid}/api/maps/{mid}/sync').status_code == 200
    assert _counts(cid, mid) == before
    rows, pins = before
    assert sorted(r['c'] for r in rows) == sorted(chars) and all(r['n'] == 1 for r in rows)
    assert len(pins) == 3 and all(p['n'] == 1 and p['p'] is not None for p in pins)


def test_parallel_refreshes_and_links_never_double_anyone(user):
    cid, mid, chars = _world(user)
    clients = [user.fresh_client() for _ in range(6)]

    def work(i):
        c = clients[i]
        codes = [_settings(c, cid, mid, 1)]
        for _ in range(3):
            codes.append(c.get(f'/campaigns/{cid}/api/maps/{mid}/pins').status_code)
        return codes
    with cf.ThreadPoolExecutor(6) as ex:
        results = list(ex.map(work, range(6)))
    assert all(code == 200 for codes in results for code in codes)
    rows, pins = _counts(cid, mid)
    assert sorted(r['c'] for r in rows) == sorted(chars) and all(r['n'] == 1 for r in rows)
    assert len(pins) == 3 and all(p['n'] == 1 and p['p'] is not None for p in pins)
    assert q('SELECT COUNT(*) AS n FROM battle_participants bp JOIN characters ch ON ch.id = bp.character_id WHERE ch.campaign_id = ?', cid)[0]['n'] == 3


def test_link_unlink_link_cycles_settle_on_the_same_roster(user):
    cid, mid, chars = _world(user)
    _settings(user, cid, mid, 1)
    first = _counts(cid, mid)
    for _ in range(3):
        assert _settings(user, cid, mid, 0) == 200
        assert _settings(user, cid, mid, 1) == 200
    assert _counts(cid, mid) == first
