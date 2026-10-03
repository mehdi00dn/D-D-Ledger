"""DL-40: active turn, DM-only Next turn, round counter, dead skipped, persists, scoped per campaign."""
import concurrent.futures as cf
import pytest
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _fight(make_user):
    dm, player = make_user(), make_user()
    cid = dm.new_campaign()
    dm.post(f'/campaigns/{cid}/members/add', data={'username': player.name, 'status': 'player'})
    ids = {}
    for name, init in (('Low', 5), ('High', 20), ('Mid', 12)):
        ids[name] = dm.add_to_battle(cid, dm.new_character(cid, name=name, max_hp=10))
        dm.json(f'/campaigns/{cid}/api/battle/{ids[name]}/initiative', {'value': init})
    return dm, player, cid, ids


def _active(user, cid):
    rows = user.battle(cid)
    act = [r for r in rows if r['is_active']]
    assert len(act) <= 1
    return (act[0]['char_name'] if act else None), (rows[0]['battle_round'] if rows else None)


def _next(dm, cid):
    r = dm.json(f'/campaigns/{cid}/api/battle/next-turn')
    assert r.status_code == 200
    return r


def test_turns_follow_initiative_and_the_round_counter_goes_up(make_user):
    dm, player, cid, ids = _fight(make_user)
    assert _active(dm, cid) == (None, 1)                         # not started yet
    order = []
    for _ in range(7):
        _next(dm, cid)
        order.append(_active(dm, cid))
    assert order == [('High', 1), ('Mid', 1), ('Low', 1), ('High', 2), ('Mid', 2), ('Low', 2), ('High', 3)]


def test_everyone_sees_the_active_turn_but_only_the_dm_can_advance(make_user):
    dm, player, cid, ids = _fight(make_user)
    assert player.json(f'/campaigns/{cid}/api/battle/next-turn').status_code == 403
    _next(dm, cid)
    assert _active(player, cid) == ('High', 1)


def test_dead_participants_are_skipped_and_downed_ones_are_not(make_user):
    dm, player, cid, ids = _fight(make_user)
    dm.json(f'/campaigns/{cid}/api/battle/{ids["Mid"]}/damage', {'amount': 100})
    dm.json(f'/campaigns/{cid}/api/battle/{ids["Mid"]}/die')
    dm.json(f'/campaigns/{cid}/api/battle/{ids["Low"]}/damage', {'amount': 100})      # at 0 HP, not confirmed dead
    seen = []
    for _ in range(3):
        _next(dm, cid); seen.append(_active(dm, cid))
    assert seen == [('High', 1), ('Low', 1), ('High', 2)]        # Mid skipped, Low (downed) still acts


def test_state_persists_and_removing_the_acting_participant_passes_the_turn(make_user):
    dm, player, cid, ids = _fight(make_user)
    _next(dm, cid); _next(dm, cid)                                # Mid's turn
    assert _active(dm, cid) == ('Mid', 1)
    assert _active(dm, cid) == ('Mid', 1)                         # a fresh request sees the same state
    dm.json(f'/campaigns/{cid}/api/battle/{ids["Mid"]}/remove')
    assert _active(dm, cid) == ('Low', 1)
    dm.json(f'/campaigns/{cid}/api/battle/{ids["Low"]}/remove')   # last in order: wraps to a new round
    assert _active(dm, cid) == ('High', 2)


def test_clear_resets_and_a_new_fight_starts_at_round_one(make_user):
    dm, player, cid, ids = _fight(make_user)
    for _ in range(4):
        _next(dm, cid)
    assert _active(dm, cid)[1] == 2
    dm.json(f'/campaigns/{cid}/api/battle/clear')
    ch = dm.new_character(cid, name='Fresh', max_hp=5)
    dm.add_to_battle(cid, ch)
    assert _active(dm, cid) == (None, 1)
    _next(dm, cid)
    assert _active(dm, cid) == ('Fresh', 1)


def test_a_lone_fighter_starts_a_new_round_each_turn(make_user):
    dm = make_user(); cid = dm.new_campaign()
    dm.add_to_battle(cid, dm.new_character(cid, name='Solo', max_hp=5))
    _next(dm, cid); assert _active(dm, cid) == ('Solo', 1)
    _next(dm, cid); assert _active(dm, cid) == ('Solo', 2)


def test_turn_state_is_scoped_to_the_campaign(make_user):
    a, b = make_user(), make_user()
    ca, cb = a.new_campaign(), b.new_campaign()
    a.add_to_battle(ca, a.new_character(ca, name='A1', max_hp=5))
    b.add_to_battle(cb, b.new_character(cb, name='B1', max_hp=5))
    _next(a, ca)
    assert _active(a, ca) == ('A1', 1) and _active(b, cb) == (None, 1)
    assert b.json(f'/campaigns/{ca}/api/battle/next-turn').status_code in (403, 404)


def test_two_quick_next_turn_presses_never_skip_anyone(make_user):
    dm, player, cid, ids = _fight(make_user)
    _next(dm, cid)                                                # High
    with cf.ThreadPoolExecutor(2) as ex:
        list(ex.map(lambda _: dm.json(f'/campaigns/{cid}/api/battle/next-turn').status_code, range(2)))
    name, rnd = _active(dm, cid)
    assert (name, rnd) in (('Mid', 1), ('Low', 1))                # advanced once or twice, never lost or doubled oddly
