"""DL-165: a new password account must confirm its email before it can create or join a campaign."""
import re
import pytest

import mailer
from conftest import BACKEND, q, User

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


@pytest.fixture(autouse=True)
def memory_mail(monkeypatch):
    monkeypatch.setenv('MAIL_BACKEND', 'memory')
    mailer.outbox.clear()
    yield


def new_account(appmod):
    import database
    db = database.get_db()                                              # an earlier test may have confirmed this address
    try:
        db.execute("UPDATE users SET email = NULL, email_verified_at = NULL WHERE lower(email) = 'player@example.com'")
        db.commit()
    finally:
        db.close()
    mailer.outbox.clear()
    return User(appmod, keep_email=True)


def link():
    m = re.search(r'/verify-email/\S+', mailer.outbox[-1]['text'])
    return m.group(0)


def test_signup_lands_on_the_confirm_page_and_mails_a_link(appmod):
    u = new_account(appmod)
    assert q('SELECT email_confirm_required FROM users WHERE username = ?', u.name)[0]['email_confirm_required'] is True
    assert len(mailer.outbox) == 1 and mailer.outbox[0]['to'] == 'player@example.com'
    page = u.get('/confirm-email').get_data(as_text=True)
    assert 'Check your inbox' in page and 'player@example.com' in page


def test_campaigns_are_locked_until_confirmed_then_open(appmod):
    u = new_account(appmod)
    r = u.post('/campaigns/new', data={'name': 'Nope'})
    assert r.status_code == 302 and r.headers['Location'].startswith('/confirm-email')
    assert not q('SELECT 1 FROM campaigns WHERE name = ?', 'Nope')
    assert u.get('/campaigns/new').headers['Location'].startswith('/confirm-email')
    assert 'Confirm your email to create or join' in u.get('/campaigns').get_data(as_text=True)      # banner everywhere else
    r = u.get(link())
    assert r.status_code == 302 and 'confirm-email' in r.headers['Location'] and 'done=1' in r.headers['Location']
    assert 'Email confirmed' in u.get(r.headers['Location']).get_data(as_text=True)
    assert u.new_campaign()                                                                           # unlocked


def test_invite_links_wait_for_confirmation_too(appmod):
    u = new_account(appmod)
    r = u.post('/join/anything')
    assert r.status_code == 302 and r.headers['Location'].startswith('/confirm-email')


def test_resend_is_available_and_the_link_works_on_another_device(appmod):
    u = new_account(appmod)
    r = u.post('/confirm-email/resend', data={})
    assert r.status_code == 302 and 'sent=1' in r.headers['Location'] and len(mailer.outbox) == 2
    other = appmod.app.test_client()                                    # signed out: the link itself proves the mailbox
    r = other.get(link())
    assert r.status_code == 302 and r.headers['Location'].startswith('/login') and 'confirmed=1' in r.headers['Location']
    assert q('SELECT email_verified_at FROM users WHERE username = ?', u.name)[0]['email_verified_at'] is not None
    assert u.new_campaign()


def test_no_mail_server_means_no_lock(appmod, monkeypatch):
    monkeypatch.setenv('MAIL_BACKEND', 'off')
    u = new_account(appmod)
    assert q('SELECT email_confirm_required FROM users WHERE username = ?', u.name)[0]['email_confirm_required'] is False
    assert u.new_campaign()


def test_accounts_that_existed_before_are_never_locked(user):
    assert q('SELECT email_confirm_required FROM users WHERE username = ?', user.name)[0]['email_confirm_required'] is False
    assert user.new_campaign()
