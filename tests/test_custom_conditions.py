"""Custom battle conditions: free text the DM types, max 3 per participant, plain text, hidden on NPCs from Players."""
import pytest
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _setup(make_user):
    dm, player = make_user(), make_user(); cid = dm.new_campaign(); dm.add_member(cid, player)
    hero = dm.add_to_battle(cid, dm.new_character(cid, name='Hero', max_hp=30))
    ogre = dm.add_to_battle(cid, dm.new_character(cid, name='Ogre', max_hp=59, is_npc='on'))
    return dm, player, cid, hero, ogre


def _set(user, cid, pid, custom=None, standard=()):
    body = {'conditions': list(standard)}
    if custom is not None:
        body['custom_conditions'] = custom
    return user.json(f'/campaigns/{cid}/api/battle/{pid}/conditions', body)


def _row(user, cid, pid):
    return next(r for r in user.battle(cid) if r['id'] == pid)


def test_dm_adds_up_to_three_custom_conditions_and_they_are_tidied(make_user):
    dm, player, cid, hero, ogre = _setup(make_user)
    r = _set(dm, cid, hero, ['  Hexed  by   Bob ', 'On fire', 'hexed by bob'], ['prone'])
    assert r.status_code == 200
    row = next(x for x in r.get_json() if x['id'] == hero)
    assert row['custom_conditions'] == ['Hexed by Bob', 'On fire'] and row['conditions'] == ['prone']   # tidied, case-insensitive dupes dropped
    assert _row(dm, cid, hero)['custom_conditions'] == ['Hexed by Bob', 'On fire']
    assert _set(dm, cid, hero, ['a', 'b', 'c']).status_code == 200
    assert _set(dm, cid, hero, ['a', 'b', 'c', 'd']).status_code == 400                         # the limit is 3
    assert _row(dm, cid, hero)['custom_conditions'] == ['a', 'b', 'c']                           # a refused request changes nothing


@pytest.mark.parametrize('bad', ['text', 5, {'a': 1}, [''], ['   '], [5], [['x']], ['x' * 25], ['ok', None]])
def test_bad_custom_conditions_are_refused(make_user, bad):
    dm, player, cid, hero, ogre = _setup(make_user)
    assert _set(dm, cid, hero, bad).status_code == 400
    assert _row(dm, cid, hero)['custom_conditions'] == []


def test_leaving_custom_conditions_out_keeps_them(make_user):
    dm, player, cid, hero, ogre = _setup(make_user)
    _set(dm, cid, hero, ['Cursed'])
    assert _set(dm, cid, hero, None, ['blinded']).status_code == 200                              # old-style call: only the standard list
    row = _row(dm, cid, hero)
    assert row['custom_conditions'] == ['Cursed'] and row['conditions'] == ['blinded']
    assert _set(dm, cid, hero, []).status_code == 200                                             # an explicit empty list clears them
    assert _row(dm, cid, hero)['custom_conditions'] == []


def test_only_the_dm_sets_them_and_players_see_them_on_pcs_and_npcs(make_user):
    dm, player, cid, hero, ogre = _setup(make_user)
    assert _set(player, cid, hero, ['Sneaky']).status_code == 403
    _set(dm, cid, hero, ['Inspired']); _set(dm, cid, ogre, ['Secretly a dragon'])
    assert _row(player, cid, hero)['custom_conditions'] == ['Inspired']
    assert _row(player, cid, ogre)['custom_conditions'] == ['Secretly a dragon']                  # NPCs too: the whole table sees conditions
    assert _row(player, cid, ogre)['hidden_stats'] is True                                        # ...while an NPC's HP/AC stay hidden
    assert _row(dm, cid, ogre)['custom_conditions'] == ['Secretly a dragon']


def test_markup_in_a_name_is_kept_as_plain_text(make_user):
    dm, player, cid, hero, ogre = _setup(make_user)
    assert _set(dm, cid, hero, ['<b onclick=x>y']).status_code == 200
    assert _row(player, cid, hero)['custom_conditions'] == ['<b onclick=x>y']                     # stored verbatim; the battle screen escapes it


def test_custom_conditions_disappear_with_the_participant(make_user):
    dm, player, cid, hero, ogre = _setup(make_user)
    _set(dm, cid, hero, ['Temp'])
    dm.json(f'/campaigns/{cid}/api/battle/{hero}/remove', {})
    assert q('SELECT COUNT(*) AS n FROM battle_participants WHERE custom_conditions LIKE ?', '%Temp%')[0]['n'] == 0
