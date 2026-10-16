import re
from unittest import mock
import mailer
import security
from conftest import q



def test_change_username_keeps_you_signed_in_and_login_uses_the_new_one(appmod, make_user):
    u = make_user(); old = u.name; new = old + 'x'
    r = u.post('/account/username', data={'username': new, 'current_password': u.password})
    assert r.status_code == 302 and 'un=1' in r.headers['Location']
    assert u.get('/campaigns').status_code == 200
    assert q('SELECT COUNT(*) AS n FROM users WHERE username = ?', new)[0]['n'] == 1
    assert 'Signed in as' in u.get('/account').get_data(as_text=True) and new in u.get('/account').get_data(as_text=True)
    fresh = appmod.app.test_client()
    assert fresh.post('/login', data={'username': old, 'password': u.password}).status_code == 200      # old name no longer works
    assert fresh.post('/login', data={'username': new, 'password': u.password}).status_code == 302


def test_wrong_password_and_bad_names_are_refused(appmod, make_user):
    u, other = make_user(), make_user()
    base = '/account/username'
    assert u.post(base, data={'username': 'okname', 'current_password': 'wrong-pass'}).status_code == 400
    assert u.post(base, data={'username': 'ab', 'current_password': u.password}).status_code == 400
    assert u.post(base, data={'username': '<b>x</b>', 'current_password': u.password}).status_code == 400
    assert u.post(base, data={'username': u.name, 'current_password': u.password}).status_code == 400
    r = u.post(base, data={'username': other.name.upper(), 'current_password': u.password})
    assert r.status_code == 400 and 'already taken' in r.get_data(as_text=True)
    assert q('SELECT COUNT(*) AS n FROM users WHERE username = ?', u.name)[0]['n'] == 1


def test_wrong_passwords_count_towards_the_login_throttle(appmod, make_user):
    u = make_user()
    with mock.patch.dict(security.WINDOWS, {'login-pair': (2, 3600)}):
        codes = [u.post('/account/username', data={'username': 'freshname', 'current_password': 'nope-nope'}).status_code for _ in range(4)]
    assert 429 in codes


def test_confirmed_email_gets_a_notice(appmod, make_user, monkeypatch):
    monkeypatch.setenv('MAIL_BACKEND', 'memory')
    u = make_user(); u.post('/account/email', data={'email': f'{u.name}@example.com'})
    u.get(re.search(r'/verify-email/\S+', mailer.outbox[-1]['text']).group(0)); mailer.outbox.clear()
    new = u.name + 'q'; old = u.name
    u.post('/account/username', data={'username': new, 'current_password': u.password})
    assert len(mailer.outbox) == 1 and old in mailer.outbox[0]['text'] and new in mailer.outbox[0]['text']
