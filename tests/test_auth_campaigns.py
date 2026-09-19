import re, uuid
import pytest
from conftest import q, User


def test_register_login_logout_and_gate(appmod, make_user):
    anon = appmod.app.test_client()
    r = anon.get('/campaigns')
    assert r.status_code == 302 and '/login' in r.headers['Location']
    u = make_user()
    assert u.get('/campaigns').status_code == 200
    assert u.post('/logout').status_code == 302
    assert u.get('/campaigns').status_code == 302
    r = u.post('/login', data={'username': u.name, 'password': 'wrong-pass'})
    assert r.status_code == 200                                   # stays on login
    r = u.post('/login', data={'username': u.name, 'password': u.password})
    assert r.status_code == 302
    assert u.get('/campaigns').status_code == 200


def test_duplicate_username_rejected(appmod, make_user):
    u = make_user()
    c = appmod.app.test_client()
    r = c.post('/register', data={'username': u.name, 'password': 'secret12', 'confirm': 'secret12'})
    assert r.status_code == 200          # form re-rendered with an error, no crash


def test_campaign_crud_and_redirects(camp):
    u, cid = camp
    assert q('SELECT COUNT(*) AS n FROM campaigns WHERE id = ?', cid)[0]['n'] == 1
    assert f'/campaigns/{cid}/'.encode() in u.get('/campaigns').data or b'Camp' in u.get('/campaigns').data
    r = u.post(f'/campaigns/{cid}/edit', data={'name': 'Renamed', 'description': 'x'}, content_type='multipart/form-data')
    assert r.status_code == 302
    assert q('SELECT name FROM campaigns WHERE id = ?', cid)[0]['name'] == 'Renamed'
    # delete -> back to the campaigns list, which loads; the campaign is gone
    r = u.post(f'/campaigns/{cid}/delete')
    assert r.status_code == 302 and r.headers['Location'].endswith('/campaigns')
    assert u.get(r.headers['Location']).status_code == 200
    assert u.get(f'/campaigns/{cid}/characters').status_code == 404
    assert q('SELECT COUNT(*) AS n FROM campaigns WHERE id = ?', cid)[0]['n'] == 0


def test_campaign_delete_cascades_everything(camp):
    u, cid = camp
    gid = u.new_group(cid); ch = u.new_character(cid, group_id=gid)
    mid = u.new_map(cid); u.add_to_battle(cid, ch)
    u.json(f'/campaigns/{cid}/api/maps/{mid}/drawings', {'kind': 'rect', 'data': {}, 'cx': 1, 'cy': 1, 'w': 5, 'h': 5})
    assert u.post(f'/campaigns/{cid}/delete').status_code == 302
    for table in ('groups', 'characters', 'maps', 'campaign_members'):
        assert q(f'SELECT COUNT(*) AS n FROM {table} WHERE campaign_id = ?', cid)[0]['n'] == 0, table
    assert q('SELECT COUNT(*) AS n FROM map_drawings WHERE map_id = ?', mid)[0]['n'] == 0
    assert q('SELECT COUNT(*) AS n FROM battle_participants WHERE character_id = ?', ch)[0]['n'] == 0


def test_membership_roles(make_user):
    dm, player = make_user(), make_user()
    cid = dm.new_campaign()
    r = dm.post(f'/campaigns/{cid}/members/add', data={'username': player.name, 'status': 'player'})
    assert r.status_code == 302
    assert player.get(f'/campaigns/{cid}/characters').status_code == 200
    assert player.post(f'/campaigns/{cid}/delete').status_code == 403       # only owners delete
    pc = dm.new_character(cid, name='DM guy')
    assert player.post(f'/campaigns/{cid}/characters/{pc}/edit', data={'name': 'hax'}, content_type='multipart/form-data').status_code == 403
    assert player.json(f'/campaigns/{cid}/api/battle/add', {'character_id': pc}).status_code == 403   # dm_required
    # promote / demote / remove
    assert dm.post(f'/campaigns/{cid}/members/{q("SELECT id FROM users WHERE username = ?", player.name)[0]["id"]}/status', data={'status': 'dm'}).status_code == 302
    uid = q('SELECT id FROM users WHERE username = ?', player.name)[0]['id']
    assert q('SELECT status FROM campaign_members WHERE campaign_id = ? AND user_id = ?', cid, uid)[0]['status'] == 'dm'
    assert dm.post(f'/campaigns/{cid}/members/{uid}/remove').status_code == 302
    assert player.get(f'/campaigns/{cid}/characters').status_code == 404


def test_sole_owner_cannot_leave(camp):
    u, cid = camp
    r = u.post(f'/campaigns/{cid}/leave')
    assert r.status_code == 302
    assert u.get(f'/campaigns/{cid}/characters').status_code == 200      # still a member


def test_non_member_is_locked_out(make_user):
    a, b = make_user(), make_user()
    cid = a.new_campaign(); ch = a.new_character(cid)
    for url in (f'/campaigns/{cid}/characters', f'/campaigns/{cid}/characters/{ch}', f'/campaigns/{cid}/groups',
                f'/campaigns/{cid}/maps', f'/campaigns/{cid}/battle', f'/campaigns/{cid}/api/battle',
                f'/campaigns/{cid}/api/characters', f'/campaigns/{cid}/export/data', f'/campaigns/{cid}/edit'):
        assert b.get(url).status_code == 404, url
    assert b.post(f'/campaigns/{cid}/delete').status_code == 404
    assert b.post(f'/campaigns/{cid}/characters/{ch}/delete').status_code == 404
