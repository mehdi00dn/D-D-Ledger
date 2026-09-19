"""Every delete/remove/leave flow must land on a page that loads (no 404 after a delete)."""
import pytest
from conftest import q


def _lands_ok(u, resp):
    assert resp.status_code == 302, resp.status_code
    follow = u.get(resp.headers['Location'])
    assert follow.status_code == 200, f"{resp.headers['Location']} -> {follow.status_code}"


def test_all_delete_flows_land_on_working_pages(make_user):
    u, p = make_user(), make_user()
    cid = u.new_campaign()
    u.post(f'/campaigns/{cid}/members/add', data={'username': p.name, 'status': 'player'})
    pid_user = q('SELECT id FROM users WHERE username = ?', p.name)[0]['id']

    gid = u.new_group(cid); ch = u.new_character(cid, group_id=gid); mid = u.new_map(cid)
    _lands_ok(u, u.post(f'/campaigns/{cid}/groups/{gid}/delete'))
    _lands_ok(u, u.post(f'/campaigns/{cid}/characters/{ch}/delete'))
    _lands_ok(u, u.post(f'/campaigns/{cid}/maps/{mid}/delete'))
    _lands_ok(u, u.post(f'/campaigns/{cid}/members/{pid_user}/remove'))
    u.post(f'/campaigns/{cid}/members/add', data={'username': p.name, 'status': 'player'})
    _lands_ok(p, p.post(f'/campaigns/{cid}/leave'))
    _lands_ok(u, u.post(f'/campaigns/{cid}/delete'))


def test_root_redirects_are_consistent_after_delete(make_user):
    u = make_user()
    a, b = u.new_campaign(), u.new_campaign()
    u.get(f'/campaigns/{a}/characters')                 # remembers last_campaign_id = a in the session
    u.post(f'/campaigns/{a}/delete')
    r = u.get('/')
    assert r.status_code == 302
    assert u.get(r.headers['Location']).status_code == 200
