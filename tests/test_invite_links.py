"""DL-7: real campaign invite links -- opaque hashed token, expiry, max uses, revoke, login required."""
import re
import pytest
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _link_mode(owner, cid, mode='invite_link'):
    r = owner.post(f'/campaigns/{cid}/edit', data={'name': 'Quest', 'description': 'd', 'access_mode': mode}, content_type='multipart/form-data')
    assert r.status_code == 302


def _create(owner, cid, **form):
    r = owner.json(f'/campaigns/{cid}/invite-links', form)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['url']


def _path(url):
    return re.sub(r'^https?://[^/]+', '', url)


def _members(cid):
    return {r['username'] for r in q('SELECT u.username FROM campaign_members cm JOIN users u ON u.id = cm.user_id WHERE cm.campaign_id = ?', cid)}


def test_enabling_link_mode_alone_grants_nothing(make_user):
    owner, other = make_user(), make_user(); cid = owner.new_campaign()
    _link_mode(owner, cid)
    assert q('SELECT COUNT(*) AS n FROM campaign_invite_links WHERE campaign_id = ?', cid)[0]['n'] == 0
    assert other.get(f'/campaigns/{cid}/characters').status_code in (403, 404)
    assert other.name not in _members(cid)


def test_only_the_owner_creates_links_and_only_in_link_mode(make_user):
    owner, dm = make_user(), make_user(); cid = owner.new_campaign()
    owner.add_member(cid, dm, 'dm')
    assert owner.json(f'/campaigns/{cid}/invite-links', {}).status_code == 400   # still private
    _link_mode(owner, cid)
    assert dm.post(f'/campaigns/{cid}/invite-links', data={}).status_code == 403
    assert _create(owner, cid)


def test_token_is_stored_only_as_a_hash(make_user):
    owner = make_user(); cid = owner.new_campaign(); _link_mode(owner, cid)
    url = _create(owner, cid)
    token = url.rsplit('/', 1)[1]
    row = q('SELECT token_hash FROM campaign_invite_links WHERE campaign_id = ?', cid)[0]
    assert token not in row['token_hash'] and len(row['token_hash']) == 64 and len(token) >= 40


def test_join_flow_requires_login_and_confirmation_then_makes_a_player(make_user, appmod):
    owner, guest = make_user(), make_user(); cid = owner.new_campaign('Quest'); _link_mode(owner, cid)
    path = _path(_create(owner, cid))
    anon = appmod.app.test_client()
    r = anon.get(path)
    assert r.status_code == 302 and '/login' in r.headers['Location'] and 'next=' in r.headers['Location']
    page = guest.get(path)
    assert page.status_code == 200 and b'Join campaign' in page.data
    assert guest.name not in _members(cid)                                   # opening the link alone grants nothing
    r = guest.post(path)
    assert r.status_code == 302 and f'/campaigns/{cid}/' in r.headers['Location']
    assert guest.name in _members(cid)
    row = q('SELECT cm.role, cm.status FROM campaign_members cm JOIN users u ON u.id = cm.user_id WHERE u.username = ? AND cm.campaign_id = ?', guest.name, cid)[0]
    assert (row['role'], row['status']) == ('member', 'player')
    assert f'{guest.name} joined Quest' in owner.get('/notifications').data.decode()


def test_register_carries_the_link_through(appmod):
    anon = appmod.app.test_client()
    r = anon.get('/register?next=/join/abc')
    assert b'name="next" value="/join/abc"' in r.data


def test_max_uses_expiry_and_revoke(make_user):
    owner, a, b, c = make_user(), make_user(), make_user(), make_user(); cid = owner.new_campaign(); _link_mode(owner, cid)
    path = _path(_create(owner, cid, max_uses='1'))
    assert a.post(path).status_code == 302
    assert b.get(path).status_code == 404 and b.post(path).status_code == 404            # used up
    assert b.name not in _members(cid)
    path2 = _path(_create(owner, cid))
    import database
    db = database.get_db()
    db.execute("UPDATE campaign_invite_links SET expires_at = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - interval '1 minute' WHERE campaign_id = ? AND max_uses IS NULL", (cid,))
    db.commit(); db.close()
    assert b.post(path2).status_code == 404                                               # expired
    path3 = _path(_create(owner, cid))
    lid = q('SELECT id FROM campaign_invite_links WHERE campaign_id = ? ORDER BY id DESC', cid)[0]['id']
    assert owner.post(f'/campaigns/{cid}/invite-links/revoke', data={'link_id': lid}).status_code == 302
    assert c.post(path3).status_code == 404 and c.name not in _members(cid)


def test_dead_links_all_look_the_same(make_user):
    owner, g = make_user(), make_user(); cid = owner.new_campaign(); _link_mode(owner, cid)
    good = _path(_create(owner, cid))
    lid = q('SELECT id FROM campaign_invite_links WHERE campaign_id = ?', cid)[0]['id']
    owner.post(f'/campaigns/{cid}/invite-links/revoke', data={'link_id': lid})
    revoked, unknown = g.get(good), g.get('/join/' + 'x' * 43)
    assert revoked.status_code == unknown.status_code == 404 and revoked.data == unknown.data


def test_leaving_link_mode_revokes_every_link_for_good(make_user):
    owner, g = make_user(), make_user(); cid = owner.new_campaign(); _link_mode(owner, cid)
    path = _path(_create(owner, cid))
    _link_mode(owner, cid, 'private')
    assert q('SELECT COUNT(*) AS n FROM campaign_invite_links WHERE campaign_id = ? AND revoked_at IS NULL', cid)[0]['n'] == 0
    _link_mode(owner, cid)                                                                 # switching back does not revive it
    assert g.post(path).status_code == 404 and g.name not in _members(cid)


def test_existing_member_uses_nothing_and_revoke_cannot_cross_campaigns(make_user):
    owner, m, other = make_user(), make_user(), make_user(); cid = owner.new_campaign(); _link_mode(owner, cid)
    owner.add_member(cid, m)
    path = _path(_create(owner, cid, max_uses='5'))
    assert m.post(path).status_code == 302
    assert q('SELECT use_count FROM campaign_invite_links WHERE campaign_id = ?', cid)[0]['use_count'] == 0
    cid2 = other.new_campaign(); _link_mode(other, cid2)
    lid = q('SELECT id FROM campaign_invite_links WHERE campaign_id = ?', cid)[0]['id']
    other.post(f'/campaigns/{cid2}/invite-links/revoke', data={'link_id': lid})
    assert q('SELECT revoked_at FROM campaign_invite_links WHERE id = ?', lid)[0]['revoked_at'] is None


def test_edit_page_shows_a_link_once_and_form_validates(make_user):
    owner = make_user(); cid = owner.new_campaign(); _link_mode(owner, cid)
    assert owner.json(f'/campaigns/{cid}/invite-links', {'max_uses': '0'}).status_code == 400
    assert owner.json(f'/campaigns/{cid}/invite-links', {'expires_in': 'forever'}).status_code == 400
    r = owner.post(f'/campaigns/{cid}/invite-links', data={'expires_in': '1d', 'max_uses': '3'})
    assert r.status_code == 302
    first = owner.get(f'/campaigns/{cid}/edit').data.decode()
    assert '/join/' in first and 'shown only once' in first and 'Link #' in first
    assert '/join/' not in owner.get(f'/campaigns/{cid}/edit').data.decode()


def test_links_are_deleted_with_the_campaign(make_user):
    owner = make_user(); cid = owner.new_campaign(); _link_mode(owner, cid); _create(owner, cid)
    owner.post(f'/campaigns/{cid}/delete')
    assert q('SELECT COUNT(*) AS n FROM campaign_invite_links WHERE campaign_id = ?', cid)[0]['n'] == 0
