"""DL-132: provider-neutral mailer, optional email on accounts, verification links and their rate limits."""
import re
from unittest import mock
import pytest
import mailer
import security
from conftest import q


@pytest.fixture(autouse=True)
def memory_mail(monkeypatch):
    monkeypatch.setenv('MAIL_BACKEND', 'memory')
    mailer.outbox.clear()
    yield


def row(u):
    return q('SELECT * FROM users WHERE username = ?', u.name)[0]


def set_email(u, address):
    return u.post('/account/email', data={'email': address})


def link_from_outbox():
    m = re.search(r'https?://\S+/verify-email/\S+', mailer.outbox[-1]['text'])
    assert m, mailer.outbox[-1]['text']
    return m.group(0).replace('http://localhost', '').replace('http://127.0.0.1', '')


def path(link):
    return '/verify-email/' + link.split('/verify-email/')[1]


# ---- mailer ----
def test_backend_choice(monkeypatch):
    for k in ('MAIL_BACKEND', 'MAIL_HOST', 'VERCEL'):
        monkeypatch.delenv(k, raising=False)
    assert mailer.backend() == 'console'
    monkeypatch.setenv('VERCEL', '1'); assert mailer.backend() == 'off' and not mailer.enabled()
    monkeypatch.setenv('MAIL_HOST', 'smtp.example.com'); assert mailer.backend() == 'smtp'
    monkeypatch.setenv('MAIL_BACKEND', 'off'); assert mailer.backend() == 'off'


def test_build_rejects_header_injection_and_bad_addresses(monkeypatch):
    monkeypatch.setenv('MAIL_FROM', 'Ledger <no-reply@example.com>')
    msg = mailer.build('a@example.com', 'Hi\r\nBcc: evil@example.com', 'body', '<p>body</p>')
    assert 'Bcc' not in msg and '\n' not in msg['Subject']
    for bad in ('nobody', 'a@b.c\r\nBcc: x@y.z', ''):
        with pytest.raises(mailer.MailError):
            mailer.build(bad, 's', 'b')


def test_smtp_sends_with_starttls_and_login(monkeypatch):
    for k, v in dict(MAIL_BACKEND='smtp', MAIL_HOST='smtp.example.com', MAIL_USER='u', MAIL_PASSWORD='p', MAIL_FROM='x@example.com').items():
        monkeypatch.setenv(k, v)
    with mock.patch('mailer.smtplib.SMTP') as smtp:
        mailer.send('to@example.com', 'Subject', 'text')
    conn = smtp.return_value
    conn.starttls.assert_called_once(); conn.login.assert_called_once_with('u', 'p'); conn.send_message.assert_called_once()


def test_smtp_failure_becomes_a_safe_message(monkeypatch):
    for k, v in dict(MAIL_BACKEND='smtp', MAIL_HOST='smtp.example.com', MAIL_FROM='x@example.com').items():
        monkeypatch.setenv(k, v)
    with mock.patch('mailer.smtplib.SMTP', side_effect=OSError('connection refused: secret-host:25')):
        with pytest.raises(mailer.MailError) as e:
            mailer.send('to@example.com', 'Subject', 'text')
    assert 'secret-host' not in str(e.value)


# ---- account flow ----
def test_set_email_sends_a_link_and_verifies(user):
    assert set_email(user, 'Pip@Example.com').status_code == 302
    assert row(user)['email'] == 'Pip@Example.com' and row(user)['email_verified_at'] is None
    assert len(mailer.outbox) == 1 and mailer.outbox[0]['to'] == 'Pip@Example.com'
    assert 'Not confirmed' in user.get('/account?mail=sent').get_data(as_text=True)
    r = user.get(path(link_from_outbox()))
    assert r.status_code == 302 and 'mail=verified' in r.headers['Location']
    assert row(user)['email_verified_at'] is not None
    assert 'Confirmed' in user.get('/account').get_data(as_text=True)
    assert user.get(path(link_from_outbox())).status_code == 302          # opening it again is harmless


def test_invalid_addresses_are_refused(user):
    for bad in ('', 'plain', 'a@b', 'a@@b.com', 'a b@c.com', '<x>@y.com', 'a@b..com', 'x' * 250 + '@y.com'):
        r = set_email(user, bad)
        assert r.status_code == 400, bad
    assert not mailer.outbox and row(user)['email'] is None


def test_links_are_bound_to_the_account_and_the_address(make_user):
    a, b = make_user(), make_user()
    set_email(a, 'a@example.com')
    link = path(link_from_outbox())
    assert 'mail=wrong_account' in b.get(link).headers['Location']             # someone else opening it does nothing
    assert row(a)['email_verified_at'] is None
    set_email(a, 'new@example.com')                                           # address changed after the link was sent
    assert 'mail=invalid' in a.get(link).headers['Location']
    assert row(a)['email_verified_at'] is None
    assert 'mail=invalid' in a.get('/verify-email/not-a-token').headers['Location']


def test_expired_link(user):
    set_email(user, 'old@example.com')
    link = path(link_from_outbox())
    with mock.patch('app.EMAIL_TOKEN_MAX_AGE', -1):
        assert 'mail=expired' in user.get(link).headers['Location']
    assert row(user)['email_verified_at'] is None


def test_changing_email_clears_the_verified_mark(user):
    set_email(user, 'one@example.com'); user.get(path(link_from_outbox()))
    assert row(user)['email_verified_at'] is not None
    assert set_email(user, 'one@example.com').headers['Location'].endswith('mail=already')    # same address: nothing sent
    assert len(mailer.outbox) == 1
    set_email(user, 'two@example.com')
    assert row(user)['email'] == 'two@example.com' and row(user)['email_verified_at'] is None


def test_a_verified_address_cannot_be_claimed_twice(make_user):
    a, b = make_user(), make_user()
    set_email(a, 'shared@example.com'); a.get(path(link_from_outbox()))
    assert set_email(b, 'SHARED@example.com').status_code == 302            # typing it in is allowed...
    assert 'mail=taken' in b.get(path(link_from_outbox())).headers['Location']      # ...confirming it is not
    assert row(b)['email_verified_at'] is None and row(a)['email_verified_at'] is not None


def test_resend_and_remove(user):
    set_email(user, 'x@example.com')
    assert user.post('/account/email/resend').status_code == 302 and len(mailer.outbox) == 2
    user.get(path(link_from_outbox()))
    user.post('/account/email/resend'); assert len(mailer.outbox) == 2         # nothing to resend once confirmed
    assert user.post('/account/email/remove').status_code == 302
    assert row(user)['email'] is None and row(user)['email_verified_at'] is None


def test_mail_is_rate_limited(user, appmod):
    with mock.patch.dict(security.WINDOWS, {'mail-address': (2, 3600)}):
        assert set_email(user, 'limit@example.com').status_code == 302
        assert user.post('/account/email/resend').status_code == 302
        r = user.post('/account/email/resend')
        assert r.status_code == 429 and 'Too many' in r.get_data(as_text=True)
    assert len(mailer.outbox) == 2


def test_no_mail_server_is_explained_not_crashed(user, monkeypatch):
    monkeypatch.setenv('MAIL_BACKEND', 'off')
    r = set_email(user, 'a@example.com')
    assert r.status_code == 503 and 'not set up' in r.get_data(as_text=True)
    assert row(user)['email'] is None
    assert 'not set up' in user.get('/account').get_data(as_text=True)


def test_send_failure_is_reported(user):
    with mock.patch('mailer.send', side_effect=mailer.MailError('The email could not be sent right now.')):
        r = set_email(user, 'a@example.com')
    assert r.status_code == 502 and 'could not be sent' in r.get_data(as_text=True)


def test_csrf_and_login_required(user, appmod):
    assert appmod.app.test_client().post('/account/email', data={'email': 'a@example.com'}).status_code in (302, 401, 403)
    user.c.csrf_enabled = False
    assert user.c.post('/account/email', data={'email': 'a@example.com'}).status_code == 403
    assert user.c.post('/account/email/remove').status_code == 403


def test_account_survives_migration_0024_not_applied(user):
    import database
    def rename(a, b):
        db = database.get_db(); db.execute(f'ALTER TABLE users RENAME COLUMN {a} TO {b}'); db.commit(); db.close()
    rename('email', 'email_off')
    try:
        assert user.get('/account').status_code == 200
        assert user.get('/campaigns').status_code == 200
        assert set_email(user, 'a@example.com').status_code == 503
    finally:
        rename('email_off', 'email')
