"""DL-145: death saving throws - the 3-and-3 tally on a downed player character.

2024 rules as built: 10+ is a success, 9 or less a failure, natural 1 = two failures, natural 20 = back on 1 HP; three
successes = stable, three failures = dead.  The d20 is rolled on the server (patched here so every case is deterministic).
The DM can roll for any PC, a Player only for a character they made, monsters/NPCs never.  Healing, setting HP above 0 or
reviving clears the tally.  Players see the tally of their own character only, and per-roll log lines are DM-only."""
import pytest
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


@pytest.fixture
def dice(appmod, monkeypatch):
    """dice(14, 3, ...) makes the next d20 rolls come up 14, then 3, ..."""
    queue = []
    monkeypatch.setattr(appmod.secrets, 'randbelow', lambda n: queue.pop(0) - 1)
    def feed(*rolls):
        queue.extend(rolls)
    return feed


def _table(make_user):
    dm, ann, bob = make_user(), make_user(), make_user()
    cid = dm.new_campaign()
    dm.add_member(cid, ann)
    dm.add_member(cid, bob)
    return dm, ann, bob, cid


def _down(dm, cid, user, name='Hero'):
    """A PC made by `user`, in the battle, knocked to 0 HP.  Returns the participant id."""
    pid = dm.add_to_battle(cid, user.new_character(cid, name=name, max_hp=10))
    assert dm.json(f'/campaigns/{cid}/api/battle/{pid}/damage', {'amount': 50}).status_code == 200
    return pid


def _save(user, cid, pid):
    return user.json(f'/campaigns/{cid}/api/battle/{pid}/death-save')


def _row(user, cid, pid):
    return [r for r in user.battle(cid) if r['id'] == pid][0]


def _log(user, cid):
    return [e['text'] for e in user.get(f'/campaigns/{cid}/api/battle/log').get_json()]


def test_success_and_failure_counting(make_user, dice):
    dm, ann, bob, cid = _table(make_user)
    pid = _down(dm, cid, ann)
    dice(10, 9, 15)
    for roll, outcome, tally in ((10, 'success', (1, 0)), (9, 'failure', (1, 1)), (15, 'success', (2, 1))):
        r = _save(dm, cid, pid)
        assert r.status_code == 200
        body = r.get_json()
        assert (body['roll'], body['outcome']) == (roll, outcome)
        row = _row(dm, cid, pid)
        assert (row['death_successes'], row['death_failures']) == tally
        assert row['is_downed'] and not row['is_stable'] and row['current_hp'] == 0


def test_three_successes_make_the_character_stable(make_user, dice):
    dm, ann, bob, cid = _table(make_user)
    pid = _down(dm, cid, ann)
    dice(10, 11, 19)
    outcomes = [_save(dm, cid, pid).get_json()['outcome'] for _ in range(3)]
    assert outcomes == ['success', 'success', 'stable']
    row = _row(dm, cid, pid)
    assert row['is_stable'] and row['is_downed'] and row['current_hp'] == 0 and not row['is_dead']
    assert (row['death_successes'], row['death_failures']) == (0, 0)
    assert row['can_death_save'] is False
    assert _save(dm, cid, pid).status_code == 409                       # stable: no more saves


def test_three_failures_kill_and_a_natural_one_counts_twice(make_user, dice):
    dm, ann, bob, cid = _table(make_user)
    pid = _down(dm, cid, ann)
    dice(5, 1)                                                          # one failure, then a natural 1 = two more
    assert _save(dm, cid, pid).get_json()['outcome'] == 'failure'
    body = _save(dm, cid, pid).get_json()
    assert body['outcome'] == 'dead'
    row = _row(dm, cid, pid)
    assert row['is_dead'] and row['death_failures'] == 3
    assert _save(dm, cid, pid).status_code == 409                       # dead: nothing to roll
    pid2 = _down(dm, cid, ann, name='Second')
    dice(1)
    body = _save(dm, cid, pid2).get_json()                              # a lone natural 1 is two failures, not death
    assert body['outcome'] == 'failure' and _row(dm, cid, pid2)['death_failures'] == 2


def test_a_natural_twenty_puts_them_back_on_one_hp(make_user, dice):
    dm, ann, bob, cid = _table(make_user)
    pid = _down(dm, cid, ann)
    dice(4, 20)
    _save(dm, cid, pid)
    body = _save(dm, cid, pid).get_json()
    assert body['outcome'] == 'revived'
    row = _row(dm, cid, pid)
    assert row['current_hp'] == 1 and not row['is_downed'] and (row['death_successes'], row['death_failures']) == (0, 0)


def test_who_may_roll(make_user, dice):
    dm, ann, bob, cid = _table(make_user)
    pid = _down(dm, cid, ann)
    dice(12, 12, 12)
    assert _save(bob, cid, pid).status_code == 403                      # someone else's character
    assert _save(ann, cid, pid).status_code == 200                      # the player who made it
    assert _save(dm, cid, pid).status_code == 200                       # the DM, for anyone
    monster = dm.add_to_battle(cid, dm.new_character(cid, name='Ogre', max_hp=10, is_npc='on'))
    dm.json(f'/campaigns/{cid}/api/battle/{monster}/damage', {'amount': 50})
    assert _save(dm, cid, monster).status_code == 400                   # monsters just die
    healthy = dm.add_to_battle(cid, ann.new_character(cid, name='Standing', max_hp=10))
    assert _save(dm, cid, healthy).status_code == 409                   # not down
    assert _save(dm, cid, 999999).status_code == 404
    other = make_user()
    assert _save(other, cid, pid).status_code in (403, 404)             # not in the campaign at all


def test_only_the_dm_and_the_owner_see_the_tally(make_user, dice):
    dm, ann, bob, cid = _table(make_user)
    pid = _down(dm, cid, ann)
    dice(10, 4)
    _save(dm, cid, pid); _save(ann, cid, pid)
    mine, seen_by_dm, seen_by_bob = _row(ann, cid, pid), _row(dm, cid, pid), _row(bob, cid, pid)
    assert (mine['death_successes'], mine['death_failures']) == (1, 1) and mine['can_death_save'] is True
    assert (seen_by_dm['death_successes'], seen_by_dm['death_failures']) == (1, 1) and seen_by_dm['can_death_save'] is True
    assert seen_by_bob['death_successes'] is None and seen_by_bob['death_failures'] is None
    assert seen_by_bob['can_death_save'] is False and seen_by_bob['is_downed'] is True
    assert 'owner_id' not in seen_by_bob and 'created_by' not in seen_by_bob


def test_healing_setting_hp_or_reviving_clears_the_tally(make_user, dice):
    dm, ann, bob, cid = _table(make_user)
    pid = _down(dm, cid, ann)
    dice(5, 5)
    _save(dm, cid, pid); _save(dm, cid, pid)
    dm.json(f'/campaigns/{cid}/api/battle/{pid}/heal', {'amount': 0})   # a heal that leaves them down keeps the count
    assert _row(dm, cid, pid)['death_failures'] == 2
    dm.json(f'/campaigns/{cid}/api/battle/{pid}/heal', {'amount': 3})
    row = _row(dm, cid, pid)
    assert row['current_hp'] == 3 and (row['death_successes'], row['death_failures'], row['is_stable']) == (0, 0, False)

    dm.json(f'/campaigns/{cid}/api/battle/{pid}/damage', {'amount': 50})
    dice(5)
    _save(dm, cid, pid)
    dm.json(f'/campaigns/{cid}/api/battle/{pid}/set_hp', {'value': 4})
    assert _row(dm, cid, pid)['death_failures'] == 0

    dm.json(f'/campaigns/{cid}/api/battle/{pid}/damage', {'amount': 50})
    dice(5, 5, 5)
    for _ in range(3):
        _save(dm, cid, pid)
    assert _row(dm, cid, pid)['is_dead']
    dm.json(f'/campaigns/{cid}/api/battle/{pid}/revive')                # revived at 0 HP: a fresh count
    row = _row(dm, cid, pid)
    assert not row['is_dead'] and row['is_downed'] and (row['death_successes'], row['death_failures']) == (0, 0)
    assert row['can_death_save'] is True


def test_log_lines_the_dm_sees_every_roll_and_players_see_only_outcomes(make_user, dice):
    dm, ann, bob, cid = _table(make_user)
    pid = _down(dm, cid, ann, name='Mira')
    dice(7, 12, 14, 17)
    for _ in range(4):
        if _row(dm, cid, pid)['can_death_save']:
            _save(ann, cid, pid)
    dm_log, player_log = _log(dm, cid), _log(bob, cid)
    assert any('Mira rolled 7 on a death save: failure (0 of 3 successes, 1 of 3 failures)' in t for t in dm_log)
    assert any('Mira is stable' in t for t in dm_log) and any('Mira is stable' in t for t in player_log)
    assert not any('death save' in t for t in player_log)               # the individual rolls are the DM's business
    pid2 = _down(dm, cid, bob, name='Tam')
    dice(2, 2, 2)
    for _ in range(3):
        _save(bob, cid, pid2)
    assert any('Tam died' in t for t in _log(ann, cid))
    pid3 = _down(dm, cid, ann, name='Lux')
    dice(20)
    _save(dm, cid, pid3)
    assert any('Lux rolled a natural 20 on a death save and is back on 1 HP' in t for t in _log(bob, cid))


def test_two_stale_rolls_cannot_overwrite_each_other(make_user, dice, appmod):
    """The tally is written with a compare-and-swap: a roll made against a tally that has since changed is refused."""
    import database
    dm, ann, bob, cid = _table(make_user)
    pid = _down(dm, cid, ann)
    dice(10)
    real = appmod._bp_state

    def sneaky(db, p):                                                  # someone else's save lands between our read and write
        state = real(db, p)
        other = database.get_db()
        other.execute('UPDATE battle_participants SET death_failures = 1 WHERE id = ?', (p,))
        other.commit(); other.close()
        return state
    appmod._bp_state = sneaky
    try:
        r = _save(dm, cid, pid)
    finally:
        appmod._bp_state = real
    assert r.status_code == 409 and r.get_json()['code'] == 'conflict'
    row = _row(dm, cid, pid)
    assert (row['death_successes'], row['death_failures']) == (0, 1)    # the other roll stands; ours was not applied
