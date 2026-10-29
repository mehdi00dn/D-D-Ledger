"""DL-38: password reset by confirmed email: unknown addresses are told so, one-hour single-use links, ends all sessions."""
import re
from unittest import mock
import pytest
import mailer
import security
from conftest import q, User


@pytest.fixture(autouse=True)
def memory_mail(monkeypatch):
    monkeypatch.setenv('MAIL_BACKEND', 'memory')
    mailer.outbox.clear()
    yield


def row(u):
    return q('SELECT * FROM users WHERE username = ?', u.name)[0]


def confirmed_user(make_user, address=None):
    u = make_user()
    address = address or f'{u.name}@example.com'
    u.post('/account/email', data={'email': address})
    link = re.search(r'/verify-email/\S+', mailer.outbox[-1]['text']).group(0)
    assert u.get(link).status_code == 302 and row(u)['email_verified_at']
    mailer.outbox.clear()
    return u, address


def anon(appmod):
    c = appmod.app.test_client(); return c


def forgot(c, address, ip='10.1.1.1'):
    return c.post('/forgot-password', data={'email': address}, headers={'X-Test-IP': ip})


def reset_path():
    return re.search(r'/reset-password/\S+', mailer.outbox[-1]['text']).group(0)


def test_full_reset_flow(appmod, make_user):
    u, address = confirmed_user(make_user)
    other_device = u.fresh_client()
    c = anon(appmod)
    r = forgot(c, address)
    assert r.status_code == 200 and 'on its way' in r.get_data(as_text=True)
    assert len(mailer.outbox) == 1 and mailer.outbox[0]['to'] == address
    path = reset_path()
    assert c.get(path).status_code == 200
    r = c.post(path, data={'new_password': 'fresh-pass-1', 'confirm_password': 'fresh-pass-1'})
    assert r.status_code == 302 and 'reset=1' in r.headers['Location']
    assert 'Your password was changed' in c.get('/login?reset=1').get_data(as_text=True)
    assert anon(appmod).post('/login', data={'username': u.name, 'password': 'fresh-pass-1'}).status_code == 302
    assert 'Incorrect' in anon(appmod).post('/login', data={'username': u.name, 'password': u.password}).get_data(as_text=True)
    assert other_device.get('/campaigns').status_code == 302                       # old sessions are gone
    assert 'was just changed' in mailer.outbox[-1]['text']                         # heads-up mail to the owner


def test_link_works_once_only(appmod, make_user):
    u, address = confirmed_user(make_user)
    c = anon(appmod); forgot(c, address); path = reset_path()
    assert c.post(path, data={'new_password': 'fresh-pass-1', 'confirm_password': 'fresh-pass-1'}).status_code == 302
    assert c.get(path).status_code == 400
    assert c.post(path, data={'new_password': 'another-pass-2', 'confirm_password': 'another-pass-2'}).status_code == 400
    assert anon(appmod).post('/login', data={'username': u.name, 'password': 'fresh-pass-1'}).status_code == 302


def test_link_dies_when_the_password_changes_another_way(appmod, make_user):
    u, address = confirmed_user(make_user)
    c = anon(appmod); forgot(c, address); path = reset_path()
    assert u.post('/account/password', data={'current_password': u.password, 'new_password': 'changed-pass-9', 'confirm_password': 'changed-pass-9'}).status_code == 302
    assert c.get(path).status_code == 400


def test_expired_and_forged_links(appmod, make_user):
    u, address = confirmed_user(make_user)
    c = anon(appmod); forgot(c, address); path = reset_path()
    with mock.patch('app.RESET_TOKEN_MAX_AGE', -1):
        assert c.get(path).status_code == 400
    assert c.get('/reset-password/not-a-token').status_code == 400
    assert c.get(path[:-3] + 'abc').status_code == 400


def test_only_confirmed_addresses_are_accepted_and_others_are_told_so(appmod, make_user):
    u, address = confirmed_user(make_user)
    unconfirmed = make_user(); unconfirmed.post('/account/email', data={'email': 'unconfirmed@example.com'}); mailer.outbox.clear()
    c = anon(appmod)
    known = forgot(c, address, '10.2.2.1'); n_known = len(mailer.outbox)
    unknown = forgot(c, 'nobody-here@example.com', '10.2.2.2')
    unconf = forgot(c, 'unconfirmed@example.com', '10.2.2.3')
    assert known.status_code == 200 and 'on its way' in known.get_data(as_text=True)
    for r_ in (unknown, unconf):
        assert r_.status_code == 404 and 'No account has this email address confirmed' in r_.get_data(as_text=True)
    assert n_known == 1 and len(mailer.outbox) == 1                                  # only the confirmed address got mail


def test_a_failed_send_is_reported_not_hidden(appmod, make_user):
    u, address = confirmed_user(make_user)
    with mock.patch('app.mailer.send', side_effect=mailer.MailError('smtp down')):
        r = forgot(anon(appmod), address, '10.2.2.9')
    assert r.status_code == 502 and 'could not be sent' in r.get_data(as_text=True)


def test_address_match_is_case_insensitive(appmod, make_user):
    u, address = confirmed_user(make_user, 'Mixed.Case@Example.com')
    forgot(anon(appmod), 'mixed.case@example.COM')
    assert len(mailer.outbox) == 1


def test_forgot_is_rate_limited(appmod):
    c = anon(appmod)
    with mock.patch.dict(security.WINDOWS, {'mail-address': (2, 3600)}):
        codes = [forgot(c, 'spam-target@example.com', '10.3.3.3').status_code for _ in range(4)]
    assert codes[:2] == [404, 404] and codes[2] == 429


def test_validation_and_csrf(appmod, make_user):
    u, address = confirmed_user(make_user)
    c = anon(appmod); forgot(c, address); path = reset_path()
    for pw, confirm, text in [('short', 'short', 'at least 8'), ('long-enough-1', 'different-one', 'do not match')]:
        r = c.post(path, data={'new_password': pw, 'confirm_password': confirm})
        assert r.status_code == 400 and text in r.get_data(as_text=True)
    c2 = appmod.app.test_client(); c2.csrf_enabled = False
    assert c2.post(path, data={'new_password': 'fresh-pass-1', 'confirm_password': 'fresh-pass-1'}).status_code == 403
    assert c2.post('/forgot-password', data={'email': address}).status_code == 403


def test_unavailable_without_mail_server(appmod, monkeypatch):
    monkeypatch.setenv('MAIL_BACKEND', 'off')
    c = anon(appmod)
    assert c.get('/forgot-password').status_code == 503
    assert 'Forgot your password' not in c.get('/login').get_data(as_text=True)
    monkeypatch.setenv('MAIL_BACKEND', 'memory')
    assert 'Forgot your password' in c.get('/login').get_data(as_text=True)


def test_logged_in_users_are_sent_to_their_account(make_user):
    u = make_user()
    r = u.get('/forgot-password')
    assert r.status_code == 302 and '/account' in r.headers['Location']
