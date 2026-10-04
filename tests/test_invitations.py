"""DL-93: inviting someone creates a pending invitation + notification; they join only by accepting."""
import pytest
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _invite(dm, cid, who, status='player'):
    r = dm.json(f'/campaigns/{cid}/members/add', {'username': who.name, 'status': status})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['invitation_id']


def _members(cid):
    return {r['username']: r['status'] for r in q('SELECT u.username, cm.status FROM campaign_members cm JOIN users u ON u.id = cm.user_id WHERE cm.campaign_id = ?', cid)}


def test_an_invitation_adds_nobody_and_notifies_the_invitee(make_user):
    dm, pl = make_user(), make_user(); cid = dm.new_campaign('Quest')
    inv = _invite(dm, cid, pl, 'dm')
    assert pl.name not in _members(cid)
    page = pl.get('/notifications').data.decode()
    assert 'invited you to Quest' in page and 'Accept' in page and 'Decline' in page and f'/invitations/{inv}/accept' in page
    assert 'nav-badge' in pl.get('/campaigns').data.decode()                   # unread badge in the sidebar
    assert 'nav-badge' not in dm.get('/campaigns').data.decode()


def test_accepting_joins_with_the_offered_status_and_tells_the_owner(make_user):
    dm, pl = make_user(), make_user(); cid = dm.new_campaign('Quest')
    inv = _invite(dm, cid, pl, 'dm')
    r = pl.post(f'/invitations/{inv}/accept')
    assert r.status_code == 302 and f'/campaigns/{cid}/' in r.headers['Location']
    assert _members(cid)[pl.name] == 'dm'
    assert q('SELECT role FROM campaign_members cm JOIN users u ON u.id = cm.user_id WHERE u.username = ? AND cm.campaign_id = ?', pl.name, cid)[0]['role'] == 'member'
    assert q('SELECT read_at IS NOT NULL AS r FROM notifications n JOIN users u ON u.id = n.user_id WHERE u.username = ? AND n.kind = ?', pl.name, 'campaign_invite')[0]['r']
    assert f'{pl.name} accepted your invitation' in dm.get('/notifications').data.decode()
    assert pl.post(f'/invitations/{inv}/accept').status_code == 302            # a repeated click is harmless...
    assert q('SELECT COUNT(*) AS n FROM campaign_members WHERE campaign_id = ?', cid)[0]['n'] == 2
    assert len(q("SELECT 1 FROM notifications WHERE kind = 'invite_accepted'")) >= 1
    assert len(q("SELECT 1 FROM notifications n JOIN users u ON u.id = n.user_id WHERE u.username = ? AND n.kind = 'invite_accepted'", dm.name)) == 1   # ...and doesn't notify twice


def test_declining_keeps_them_out_and_allows_a_fresh_invitation(make_user):
    dm, pl = make_user(), make_user(); cid = dm.new_campaign()
    inv = _invite(dm, cid, pl)
    assert pl.post(f'/invitations/{inv}/decline').status_code == 302
    assert pl.name not in _members(cid) and pl.get(f'/campaigns/{cid}/characters').status_code == 404
    assert f'{pl.name} declined your invitation' in dm.get('/notifications').data.decode()
    assert pl.post(f'/invitations/{inv}/accept').status_code == 409            # a declined invitation can't be revived
    inv2 = _invite(dm, cid, pl)                                                # but the owner may ask again
    assert inv2 != inv and pl.post(f'/invitations/{inv2}/accept').status_code == 302 and pl.name in _members(cid)


def test_only_the_invited_person_can_answer_and_strangers_cannot_even_tell_it_exists(make_user):
    dm, pl, mallory = make_user(), make_user(), make_user(); cid = dm.new_campaign()
    inv = _invite(dm, cid, pl)
    for who in (mallory, dm):
        assert who.post(f'/invitations/{inv}/accept').status_code == 404
        assert who.post(f'/invitations/{inv}/decline').status_code == 404
    assert mallory.post('/invitations/999999/accept').status_code == 404
    assert _members(cid).get(mallory.name) is None and pl.name not in _members(cid)
    assert q("SELECT state FROM campaign_invitations WHERE id = ?", inv)[0]['state'] == 'pending'
    assert 'invited you' not in mallory.get('/notifications').data.decode()


def test_owner_can_withdraw_and_a_withdrawn_invitation_cannot_be_accepted(make_user):
    dm, pl = make_user(), make_user(); cid = dm.new_campaign()
    inv = _invite(dm, cid, pl)
    uid = q('SELECT id FROM users WHERE username = ?', pl.name)[0]['id']
    assert pl.post(f'/campaigns/{cid}/members/{uid}/uninvite').status_code == 404       # not even a member: invisible
    assert dm.post(f'/campaigns/{cid}/members/{uid}/uninvite').status_code == 302
    assert pl.post(f'/invitations/{inv}/accept').status_code == 409
    assert pl.name not in _members(cid)
    assert 'Withdrawn' in pl.get('/notifications').data.decode()


def test_only_the_owner_invites_and_duplicates_and_limits_hold(make_user, monkeypatch):
    import app as appmod
    dm, pl, other = make_user(), make_user(), make_user(); cid = dm.new_campaign()
    dm.add_member(cid, other)
    assert other.json(f'/campaigns/{cid}/members/add', {'username': pl.name}).status_code == 403
    _invite(dm, cid, pl)
    assert dm.json(f'/campaigns/{cid}/members/add', {'username': pl.name}).status_code == 400          # already pending
    assert pl.name not in dm.get(f'/campaigns/{cid}/api/members/search?q={pl.name[:6]}').get_json()['results']
    monkeypatch.setattr(appmod, 'MAX_PENDING_INVITATIONS', 1)
    third = make_user()
    r = dm.json(f'/campaigns/{cid}/members/add', {'username': third.name})
    assert r.status_code == 400 and 'open invitations' in r.get_json()['error']


def test_notifications_are_private_markable_and_removed_with_the_campaign(make_user):
    dm, pl, other = make_user(), make_user(), make_user(); cid = dm.new_campaign()
    inv = _invite(dm, cid, pl)
    nid = q('SELECT n.id FROM notifications n JOIN users u ON u.id = n.user_id WHERE u.username = ?', pl.name)[0]['id']
    assert other.post(f'/notifications/{nid}/read').status_code == 302
    assert q('SELECT read_at FROM notifications WHERE id = ?', nid)[0]['read_at'] is None           # someone else's: no effect
    assert pl.post('/notifications/read-all').status_code == 302
    assert q('SELECT read_at FROM notifications WHERE id = ?', nid)[0]['read_at'] is not None
    assert dm.post(f'/campaigns/{cid}/delete').status_code == 302
    assert q('SELECT COUNT(*) AS n FROM campaign_invitations WHERE campaign_id = ?', cid)[0]['n'] == 0
    assert q('SELECT COUNT(*) AS n FROM notifications WHERE id = ?', nid)[0]['n'] == 0


def test_login_is_required_for_every_new_route(appmod):
    anon = appmod.app.test_client()
    for path in ('/notifications',):
        assert anon.get(path).status_code == 302
    for path in ('/invitations/1/accept', '/invitations/1/decline', '/notifications/1/read', '/notifications/read-all'):
        assert anon.post(path).status_code in (302, 403)
