"""DL-146: action economy (action, bonus action, reaction, movement) and concentration on a battle row.

Spent ticks clear when that participant's turn starts.  The DM edits anyone's; a Player only their own player character;
nobody but the DM sees an NPC's.  Concentration is logged, a hit on a concentrating creature logs the Constitution save
DC (10 or half the damage, higher wins) and dropping to 0 HP or dying ends it."""
import pytest
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _setup(make_user):
    dm, ann, bob = make_user(), make_user(), make_user()
    cid = dm.new_campaign()
    dm.add_member(cid, ann)
    dm.add_member(cid, bob)
    hero = dm.add_to_battle(cid, ann.new_character(cid, name='Hero', max_hp=40))
    other = dm.add_to_battle(cid, bob.new_character(cid, name='Other', max_hp=40))
    ogre = dm.add_to_battle(cid, dm.new_character(cid, name='Ogre', max_hp=59, is_npc='on'))
    return dm, ann, bob, cid, hero, other, ogre


def _eco(user, cid, pid, key, on):
    return user.json(f'/campaigns/{cid}/api/battle/{pid}/economy', {'key': key, 'on': on})


def _row(user, cid, pid):
    return [r for r in user.battle(cid) if r['id'] == pid][0]


def _log(user, cid):
    return [e['text'] for e in user.get(f'/campaigns/{cid}/api/battle/log').get_json()]


def _hit(dm, cid, pid, amount):
    assert dm.json(f'/campaigns/{cid}/api/battle/{pid}/damage', {'amount': amount}).status_code == 200


def test_ticks_are_kept_in_a_fixed_order_and_can_be_undone(make_user):
    dm, ann, bob, cid, hero, other, ogre = _setup(make_user)
    assert _row(dm, cid, hero)['spent'] == []
    for key in ('reaction', 'action'):
        assert _eco(dm, cid, hero, key, True).status_code == 200
    assert _row(dm, cid, hero)['spent'] == ['action', 'reaction']
    assert _eco(dm, cid, hero, 'action', False).status_code == 200
    assert _row(dm, cid, hero)['spent'] == ['reaction']


def test_bad_requests_are_refused(make_user):
    dm, ann, bob, cid, hero, other, ogre = _setup(make_user)
    assert _eco(dm, cid, hero, 'dance', True).status_code == 400
    assert dm.json(f'/campaigns/{cid}/api/battle/{hero}/economy', {'key': 'action', 'on': 'yes'}).status_code == 400
    assert _eco(dm, cid, 999999, 'action', True).status_code == 404


def test_the_turn_start_clears_that_participants_ticks_only(make_user):
    dm, ann, bob, cid, hero, other, ogre = _setup(make_user)
    for pid in (hero, other):
        _eco(dm, cid, pid, 'action', True)
        _eco(dm, cid, pid, 'reaction', True)
    _eco(dm, cid, hero, 'concentration', True)
    dm.json(f'/campaigns/{cid}/api/battle/next-turn')                       # combat starts: the first in order acts
    first = [r for r in dm.battle(cid) if r['is_active']][0]['id']
    assert _row(dm, cid, first)['spent'] == []
    waiting = other if first == hero else hero
    assert _row(dm, cid, waiting)['spent'] == ['action', 'reaction']       # not their turn yet: untouched
    assert _row(dm, cid, hero)['concentrating'] is True                     # concentration survives a turn change


def test_restart_turns_clears_everyone(make_user):
    dm, ann, bob, cid, hero, other, ogre = _setup(make_user)
    _eco(dm, cid, hero, 'move', True)
    _eco(dm, cid, other, 'bonus', True)
    dm.json(f'/campaigns/{cid}/api/battle/restart-turns')
    assert all(r['spent'] == [] for r in dm.battle(cid))


def test_who_may_tick(make_user):
    dm, ann, bob, cid, hero, other, ogre = _setup(make_user)
    assert _eco(ann, cid, hero, 'action', True).status_code == 200          # a Player's own character
    assert _eco(ann, cid, other, 'action', True).status_code == 403         # someone else's
    assert _eco(ann, cid, ogre, 'action', True).status_code == 403          # a monster
    assert _eco(dm, cid, ogre, 'action', True).status_code == 200
    assert _row(ann, cid, hero)['can_economy'] is True and _row(ann, cid, other)['can_economy'] is False


def test_players_never_see_an_npcs_economy_or_concentration(make_user):
    dm, ann, bob, cid, hero, other, ogre = _setup(make_user)
    _eco(dm, cid, ogre, 'reaction', True)
    _eco(dm, cid, ogre, 'concentration', True)
    seen = _row(ann, cid, ogre)
    assert seen['spent'] is None and seen['concentrating'] is None and seen['can_economy'] is False
    mine = _row(dm, cid, ogre)
    assert mine['spent'] == ['reaction'] and mine['concentrating'] is True
    # a player character's state is the table's business
    _eco(ann, cid, hero, 'action', True)
    assert _row(bob, cid, hero)['spent'] == ['action']


def test_concentration_is_logged_and_enemy_lines_are_hidden_from_players(make_user):
    dm, ann, bob, cid, hero, other, ogre = _setup(make_user)
    _eco(ann, cid, hero, 'concentration', True)
    _eco(dm, cid, ogre, 'concentration', True)
    assert 'Hero is concentrating' in _log(dm, cid) and 'Ogre is concentrating' in _log(dm, cid)
    assert 'Hero is concentrating' in _log(bob, cid) and 'Ogre is concentrating' not in _log(bob, cid)
    _eco(ann, cid, hero, 'concentration', False)
    assert 'Hero dropped concentration' in _log(bob, cid)
    assert _eco(ann, cid, hero, 'concentration', False).status_code == 200  # already off: nothing more is logged
    assert _log(dm, cid).count('Hero dropped concentration') == 1


def test_a_hit_calls_for_a_concentration_save_with_the_right_dc(make_user):
    dm, ann, bob, cid, hero, other, ogre = _setup(make_user)
    _eco(dm, cid, hero, 'concentration', True)
    _hit(dm, cid, hero, 4)
    assert 'Hero must make a Concentration save (DC 10)' in _log(dm, cid)   # half of 4 is below the floor of 10
    _hit(dm, cid, hero, 30 - 0)                                             # 40 - 4 - 30 = 6 HP left
    assert 'Hero must make a Concentration save (DC 15)' in _log(dm, cid)
    assert _row(dm, cid, hero)['concentrating'] is True


def test_going_down_or_dying_ends_concentration(make_user):
    dm, ann, bob, cid, hero, other, ogre = _setup(make_user)
    _eco(dm, cid, hero, 'concentration', True)
    _hit(dm, cid, hero, 99)
    assert _row(dm, cid, hero)['concentrating'] is False
    assert 'Hero lost their concentration' in _log(dm, cid)
    _eco(dm, cid, other, 'concentration', True)
    assert dm.json(f'/campaigns/{cid}/api/battle/{other}/die').status_code == 200
    assert _row(dm, cid, other)['concentrating'] is False
    assert _eco(dm, cid, other, 'action', True).status_code == 409          # the dead have nothing to spend


def test_setting_hp_lower_also_prompts_the_save(make_user):
    dm, ann, bob, cid, hero, other, ogre = _setup(make_user)
    _eco(dm, cid, hero, 'concentration', True)
    assert dm.json(f'/campaigns/{cid}/api/battle/{hero}/set_hp', {'value': 10}).status_code == 200
    assert 'Hero must make a Concentration save (DC 15)' in _log(dm, cid)   # 30 points lost
