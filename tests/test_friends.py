"""DL-98: friends (ask, accept, decline, remove, block) and per-element profile visibility."""
import pytest
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _act(c, who, action, **form):
    return c.post(f'/friends/{who}/{action}', data=form)


def _name(u):
    return u.name


def _id(u):
    return q('SELECT id FROM users WHERE username = ?', u.name)[0]['id']


def _set(u, **cols):
    import database
    db = database.get_db()
    for k, v in cols.items():
        db.execute(f'UPDATE users SET {k} = ? WHERE id = ?', (v, _id(u)))
    db.commit(); db.close()


def _status(a, b):
    lo, hi = sorted((_id(a), _id(b)))
    r = q('SELECT status, requested_by FROM friendships WHERE user_low = ? AND user_high = ?', lo, hi)
    return (r[0]['status'], r[0]['requested_by']) if r else None


def test_ask_accept_remove_and_asking_twice_is_harmless(make_user):
    a, b = make_user(), make_user()
    assert _act(a, _name(b), 'request').status_code == 302
    assert _act(a, _name(b), 'request').status_code == 302                    # asking twice: still one row
    assert _status(a, b) == ('pending', _id(a))
    assert q('SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND kind = ?', _id(b), 'friend_request')[0]['n'] == 1
    assert _act(b, _name(a), 'accept').status_code == 302
    assert _status(a, b) == ('accepted', _id(a))
    assert q('SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND kind = ?', _id(a), 'friend_accepted')[0]['n'] == 1
    assert _act(a, _name(b), 'remove').status_code == 302
    assert _status(a, b) is None


def test_asking_someone_who_already_asked_you_makes_you_friends(make_user):
    a, b = make_user(), make_user()
    _act(a, _name(b), 'request'); _act(b, _name(a), 'request')
    assert _status(a, b)[0] == 'accepted'


def test_decline_and_cancel_remove_the_request(make_user):
    a, b = make_user(), make_user()
    _act(a, _name(b), 'request'); _act(b, _name(a), 'decline')
    assert _status(a, b) is None
    _act(a, _name(b), 'request'); _act(a, _name(b), 'cancel')
    assert _status(a, b) is None


def test_nobody_can_befriend_themselves_or_a_stranger_name_without_a_trace(make_user):
    a = make_user()
    r1 = a.post('/friends/add', data={'username': _name(a)})
    r2 = a.post('/friends/add', data={'username': 'nobody-by-this-name'})
    assert r1.status_code == r2.status_code == 302 and r1.headers['Location'] == r2.headers['Location']       # the same answer either way
    assert q('SELECT COUNT(*) AS n FROM friendships WHERE user_low = ? OR user_high = ?', _id(a), _id(a))[0]['n'] == 0
    assert a.post('/friends/nobody-by-this-name/accept').status_code == 404


def test_a_block_hides_both_profiles_and_stops_requests(make_user):
    a, b = make_user(), make_user()
    _set(a, profile_visibility='members'); _set(b, profile_visibility='members')
    assert b.get(f'/u/{_name(a)}').status_code == 200
    _act(a, _name(b), 'block')
    assert _status(a, b) == ('blocked', _id(a))
    assert b.get(f'/u/{_name(a)}').status_code == 404 and a.get(f'/u/{_name(b)}').status_code == 404
    r = _act(b, _name(a), 'request')                                           # looks like it worked, stores nothing, tells nobody
    assert r.status_code == 302 and _status(a, b) == ('blocked', _id(a))
    assert q('SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND kind = ?', _id(a), 'friend_request')[0]['n'] == 0
    assert 'Blocked' in a.get('/friends').data.decode() and _name(a) not in b.get('/friends').data.decode()
    _act(b, _name(a), 'unblock')                                               # the blocked person cannot lift it
    assert _status(a, b) == ('blocked', _id(a))
    _act(a, _name(b), 'unblock')
    assert _status(a, b) is None and b.get(f'/u/{_name(a)}').status_code == 200


def test_friends_only_profile_and_per_element_visibility(make_user):
    owner, friend, stranger = make_user(), make_user(), make_user()
    _set(owner, profile_visibility='friends', bio='A secret bio line')
    assert friend.get(f'/u/{_name(owner)}').status_code == 404                  # not friends yet
    _act(friend, _name(owner), 'request'); _act(owner, _name(friend), 'accept')
    assert friend.get(f'/u/{_name(owner)}').status_code == 200 and stranger.get(f'/u/{_name(owner)}').status_code == 404
    _set(owner, profile_visibility='members', bio_visibility='friends', showcase_visibility='private')
    page_friend, page_other = friend.get(f'/u/{_name(owner)}').data.decode(), stranger.get(f'/u/{_name(owner)}').data.decode()
    assert 'A secret bio line' in page_friend and 'A secret bio line' not in page_other
    assert 'id="profile-chars"' not in page_friend and 'id="profile-chars"' not in page_other               # characters: only me
    assert 'id="profile-chars"' in owner.get(f'/u/{_name(owner)}').data.decode()


def test_friends_also_pass_the_people_i_play_with_level(make_user):
    owner, friend = make_user(), make_user()
    _set(owner, profile_visibility='shared')
    assert friend.get(f'/u/{_name(owner)}').status_code == 404
    _act(friend, _name(owner), 'request'); _act(owner, _name(friend), 'accept')
    assert friend.get(f'/u/{_name(owner)}').status_code == 200


def test_account_page_saves_the_element_choices_and_pending_requests_show(make_user):
    a, b = make_user(), make_user()
    r = a.post('/account', data={'display_name': '', 'bio': 'hello', 'profile_visibility': 'friends', 'bio_visibility': 'private', 'showcase_visibility': 'friends'})
    assert r.status_code == 302
    row = q('SELECT profile_visibility, bio_visibility, showcase_visibility FROM users WHERE id = ?', _id(a))[0]
    assert (row['profile_visibility'], row['bio_visibility'], row['showcase_visibility']) == ('friends', 'private', 'friends')
    _act(b, _name(a), 'request')
    assert '1 waiting' in a.get('/account').data.decode()
    assert _name(b) in a.get('/friends').data.decode()


def test_friendship_is_never_needed_to_join_a_campaign(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, pl)
    assert pl.get(f'/campaigns/{cid}/home').status_code == 200
