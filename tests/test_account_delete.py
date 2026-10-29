"""DL-38: deleting an account. Sole-owned campaigns are handed to a member or deleted for good; everything else is
cleaned up; the password and a typed confirmation are required."""
import io
from conftest import q, png_bytes


def uid(u):
    return q('SELECT id FROM users WHERE username = ?', u.name)[0]['id']


def delete(u, password=None, confirm='DELETE', **transfers):
    data = {'current_password': u.password if password is None else password, 'confirm_delete': confirm}
    data.update({f'transfer_{k}': str(v) for k, v in transfers.items()})
    return u.post('/account/delete', data=data)


def exists(u):
    return bool(q('SELECT 1 FROM users WHERE username = ?', u.name))


def test_panel_warns_and_lists_owned_campaigns(make_user):
    owner, other = make_user(), make_user()
    cid = owner.new_campaign('Ashfall'); owner.add_member(cid, other, 'player')
    html = owner.get('/account').get_data(as_text=True)
    assert 'Ashfall' in html and 'cannot be recovered' in html and f'name="transfer_{cid}"' in html
    assert other.name in html                                              # offered as the new owner


def test_wrong_password_or_missing_confirmation_deletes_nothing(user):
    cid = user.new_campaign('Keep me')
    assert delete(user, password='nope-nope').status_code == 400
    assert delete(user, confirm='delete').status_code == 400
    assert delete(user, confirm='').status_code == 400
    assert exists(user) and q('SELECT 1 FROM campaigns WHERE id = ?', cid)


def test_solo_campaign_is_deleted_with_the_account_and_its_files(user):
    cid = user.new_campaign('Gone')
    user.post(f'/campaigns/{cid}/characters/new', data={'name': 'Hero', 'level': '1', 'max_hp': '5', 'armor_class': '10',
                                                        'avatar': (io.BytesIO(png_bytes()), 'a.png')}, content_type='multipart/form-data')
    avatar = q('SELECT avatar_path FROM characters WHERE campaign_id = ?', cid)[0]['avatar_path']
    assert avatar and user.get('/uploads/' + avatar).status_code == 200
    me = user.fresh_client()
    r = delete(user)
    assert r.status_code == 302 and 'deleted=1' in r.headers['Location']
    assert not exists(user) and not q('SELECT 1 FROM campaigns WHERE id = ?', cid)
    assert not q('SELECT 1 FROM characters WHERE campaign_id = ?', cid) and not q('SELECT 1 FROM stored_files WHERE key = ?', avatar)
    assert me.get('/campaigns').status_code == 302                          # every session is gone
    assert 'Your account was deleted' in user.get('/login?deleted=1').get_data(as_text=True)


def test_handing_a_campaign_to_a_member_keeps_it(make_user):
    owner, dm, player = make_user(), make_user(), make_user()
    cid = owner.new_campaign('Handover'); owner.add_member(cid, dm, 'dm'); owner.add_member(cid, player, 'player')
    pc = player.new_character(cid, 'Pip')
    assert delete(owner, **{str(cid): uid(player)}).status_code == 302
    assert not exists(owner)
    assert q('SELECT 1 FROM campaigns WHERE id = ?', cid)
    m = q('SELECT role, status FROM campaign_members WHERE campaign_id = ? AND user_id = ?', cid, uid(player))[0]
    assert m['role'] == 'owner' and m['status'] == 'dm'
    assert player.get(f'/campaigns/{cid}/characters/{pc}').status_code == 200
    assert not q('SELECT 1 FROM campaign_members WHERE campaign_id = ? AND user_id NOT IN (?, ?)', cid, uid(player), uid(dm))
    assert 'handed you the campaign' in player.get('/notifications').get_data(as_text=True)


def test_cannot_hand_a_campaign_to_a_stranger(make_user):
    owner, member, stranger = make_user(), make_user(), make_user()
    cid = owner.new_campaign('Mine'); owner.add_member(cid, member, 'player')
    r = delete(owner, **{str(cid): uid(stranger)})
    assert r.status_code == 400 and exists(owner) and q('SELECT 1 FROM campaigns WHERE id = ?', cid)
    r = delete(owner, **{str(cid): 'abc'})
    assert r.status_code == 400 and exists(owner)


def test_campaign_with_another_owner_just_loses_a_member(make_user):
    owner, co = make_user(), make_user()
    cid = owner.new_campaign('Shared'); owner.add_member(cid, co, 'dm')
    owner.post(f'/campaigns/{cid}/members/{uid(co)}/make-owner')
    assert delete(owner).status_code == 302
    assert q('SELECT 1 FROM campaigns WHERE id = ?', cid)
    assert [r['user_id'] for r in q('SELECT user_id FROM campaign_members WHERE campaign_id = ?', cid)] == [uid(co)]


def test_player_account_deletion_leaves_other_campaigns_alone(make_user):
    owner, player = make_user(), make_user()
    cid = owner.new_campaign('Table'); owner.add_member(cid, player, 'dm')
    pc = player.new_character(cid, 'Pip'); gid = player.new_group(cid, 'Pip Guild')
    assert delete(player).status_code == 302 and not exists(player)
    assert q('SELECT created_by FROM characters WHERE id = ?', pc)[0]['created_by'] is None      # stays, anonymised
    assert q('SELECT 1 FROM groups WHERE id = ?', gid)
    assert owner.get(f'/campaigns/{cid}/characters/{pc}').status_code == 200


def test_own_avatar_and_profile_are_removed(user):
    user.post('/account', data={'display_name': 'Pip', 'bio': 'x', 'avatar': (io.BytesIO(png_bytes()), 'a.png')}, content_type='multipart/form-data')
    avatar = q('SELECT avatar_path FROM users WHERE username = ?', user.name)[0]['avatar_path']
    assert avatar
    delete(user)
    assert not q('SELECT 1 FROM stored_files WHERE key = ?', avatar)


def test_password_guesses_are_throttled_and_csrf_enforced(user, appmod):
    from unittest import mock
    import security
    with mock.patch.dict(security.WINDOWS, {'login-pair': (2, 900)}), mock.patch.dict(appmod.app.config, {'CLIENT_IP_HEADER': 'X-Test-IP'}):
        user.c.environ_base['HTTP_X_TEST_IP'] = '10.7.7.7'
        codes = [delete(user, password='wrong-%d' % i).status_code for i in range(4)]
        assert codes[:2] == [400, 400] and codes[2] == 429
        assert delete(user).status_code == 429 and exists(user)
    user.c.csrf_enabled = False
    assert user.c.post('/account/delete', data={'current_password': user.password, 'confirm_delete': 'DELETE'}).status_code == 403
    assert exists(user)


def test_anonymous_cannot_delete(appmod):
    r = appmod.app.test_client().post('/account/delete', data={'current_password': 'x', 'confirm_delete': 'DELETE'})
    assert r.status_code in (302, 401, 403)
