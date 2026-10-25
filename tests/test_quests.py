"""DL-151: the quest list on the campaign Home page - the DM keeps it, Players read the quests that are not hidden."""
import pytest
from conftest import q, BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only')


def _setup(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, pl)
    return dm, pl, cid


def _home(user, cid):
    return user.get(f'/campaigns/{cid}/home').data.decode()


def test_dm_adds_finishes_hides_and_deletes_and_players_only_read(make_user):
    dm, pl, cid = _setup(make_user)
    assert 'No quests yet' in _home(dm, cid) and 'Quests' not in _home(pl, cid)                 # nothing to show a Player yet
    assert pl.post(f'/campaigns/{cid}/quests/add', data={'title': 'Sneaky'}).status_code == 403
    assert dm.post(f'/campaigns/{cid}/quests/add', data={'title': '   '}).status_code == 400
    dm.post(f'/campaigns/{cid}/quests/add', data={'title': 'Find the  caravan', 'details': 'Last seen near the ford'})
    dm.post(f'/campaigns/{cid}/quests/add', data={'title': 'The vizier is a lich', 'hidden': '1'})
    qid, secret = [r['id'] for r in q('SELECT id FROM quests WHERE campaign_id = ? ORDER BY id', cid)]
    assert 'Find the caravan' in _home(pl, cid) and 'Last seen near the ford' in _home(pl, cid)
    assert 'lich' not in _home(pl, cid) and 'lich' in _home(dm, cid)                            # hidden quests are the DM's alone
    assert pl.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'done'}).status_code == 403
    dm.post(f'/campaigns/{cid}/quests/{secret}', data={'action': 'show'})
    assert 'lich' in _home(pl, cid)
    dm.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'done'})
    assert q('SELECT done FROM quests WHERE id = ?', qid)[0]['done'] is True and 'Done' in _home(pl, cid)
    dm.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'reopen'})
    assert q('SELECT done FROM quests WHERE id = ?', qid)[0]['done'] is False
    assert dm.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'explode'}).status_code == 400
    dm.post(f'/campaigns/{cid}/quests/{secret}', data={'action': 'delete'})
    assert len(q('SELECT id FROM quests WHERE campaign_id = ?', cid)) == 1


def test_a_quest_of_another_campaign_cannot_be_touched(make_user):
    dm, pl, cid = _setup(make_user)
    other = dm.new_campaign()
    dm.post(f'/campaigns/{other}/quests/add', data={'title': 'Elsewhere'})
    qid = q('SELECT id FROM quests WHERE campaign_id = ?', other)[0]['id']
    assert dm.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'delete'}).status_code == 404
    assert len(q('SELECT id FROM quests WHERE id = ?', qid)) == 1
