"""DL-42: a DM can see the campaign exactly as a Player does (the server treats them as a Player)."""
import pytest
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _setup(make_user):
    dm, player = make_user(), make_user()
    cid = dm.new_campaign()
    dm.add_member(cid, player)
    ogre = dm.add_to_battle(cid, dm.new_character(cid, name='Ogre', max_hp=59, is_npc='on'))
    hero = dm.add_to_battle(cid, dm.new_character(cid, name='Hero', max_hp=30))
    dm.json(f'/campaigns/{cid}/api/battle/{ogre}/conditions', {'conditions': ['charmed']})
    return dm, player, cid, ogre, hero


def _toggle(user, cid, on):
    return user.post(f'/campaigns/{cid}/preview-as-player', data={'on': '1' if on else '0'})


def _row(user, cid, pid):
    return next(r for r in user.battle(cid) if r['id'] == pid)


def test_preview_sends_the_dm_exactly_what_a_player_gets(make_user):
    dm, player, cid, ogre, hero = _setup(make_user)
    assert _row(dm, cid, ogre)['current_hp'] == 59 and _row(dm, cid, ogre)['conditions'] == ['charmed']
    assert _toggle(dm, cid, True).status_code == 302
    seen = _row(dm, cid, ogre)
    assert seen['hidden_stats'] is True and seen['current_hp'] is None and seen['conditions'] == []
    assert _row(dm, cid, hero)['current_hp'] == 30                         # a party member is still visible
    assert dm.battle(cid) == player.battle(cid)                            # identical to a real Player's response
    assert _toggle(dm, cid, False).status_code == 302
    assert _row(dm, cid, ogre)['current_hp'] == 59                         # back to normal


def test_dm_powers_are_off_while_previewing_and_the_exit_still_works(make_user):
    dm, player, cid, ogre, hero = _setup(make_user)
    _toggle(dm, cid, True)
    assert dm.json(f'/campaigns/{cid}/api/battle/next-turn').status_code == 403
    assert dm.json(f'/campaigns/{cid}/api/battle/{hero}/damage', {'amount': 5}).status_code == 403
    assert _toggle(dm, cid, False).status_code == 302                      # a DM in preview can always leave it
    assert dm.json(f'/campaigns/{cid}/api/battle/next-turn').status_code == 200


def test_only_a_dm_can_use_it_and_it_is_per_campaign(make_user):
    dm, player, cid, ogre, hero = _setup(make_user)
    assert _toggle(player, cid, True).status_code == 403
    assert player.battle(cid) is not None and _row(player, cid, ogre)['hidden_stats'] is True
    other = dm.new_campaign()
    o2 = dm.add_to_battle(other, dm.new_character(other, name='Troll', max_hp=80, is_npc='on'))
    _toggle(dm, cid, True)
    assert _row(dm, other, o2)['current_hp'] == 80                         # the other campaign is unaffected
    assert _row(dm, cid, ogre)['current_hp'] is None
