"""DL-69: the privacy page is public, says what the app keeps, and shows a contact only when one is configured."""
import pytest

from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def test_privacy_is_public_and_linked_from_the_sign_in_pages(appmod, monkeypatch):
    c = appmod.app.test_client()
    r = c.get('/privacy')
    text = r.get_data(as_text=True)
    assert r.status_code == 200 and 'What we keep' in text and 'No analytics' in text and 'mailto:' not in text
    assert 'href="/privacy"' in c.get('/login').get_data(as_text=True) and 'href="/privacy"' in c.get('/register').get_data(as_text=True)
    monkeypatch.setenv('PRIVACY_CONTACT', 'privacy@example.com')
    assert 'mailto:privacy@example.com' in c.get('/privacy').get_data(as_text=True)
    monkeypatch.setenv('PRIVACY_CONTACT', 'x"><script>')
    assert '<script>' not in c.get('/privacy').get_data(as_text=True)


def test_privacy_is_linked_outside_campaigns_only(make_user):
    u = make_user()
    assert 'href="/privacy"' in u.get('/campaigns').get_data(as_text=True)
    cid = u.new_campaign()
    assert 'href="/privacy"' not in u.get(f'/campaigns/{cid}/battle').get_data(as_text=True)     # in a campaign the menus are for the game


def test_terms_and_cookies_pages_are_public_and_cross_linked(appmod):
    c = appmod.app.test_client()
    for path, needle in (('/terms', 'Terms of use'), ('/cookies', '__Host-ledger'), ('/privacy', 'What we keep')):
        r = c.get(path); text = r.get_data(as_text=True)
        assert r.status_code == 200 and needle in text
        assert 'href="/terms"' in text and 'href="/cookies"' in text and 'href="/privacy"' in text
    assert 'href="/terms"' in c.get('/register').get_data(as_text=True)


def test_signing_up_records_the_agreement_and_the_forms_show_the_notice(appmod):
    from conftest import q
    c = appmod.app.test_client()
    text = c.get('/register').get_data(as_text=True)
    assert 'By creating an account you agree to' in text
    r = c.post('/register', data={'username': 'agreer_one', 'email': 'agree@example.com', 'password': 'correct horse 1', 'confirm': 'correct horse 1'},
               headers={'X-Test-IP': '10.8.8.8'})
    assert r.status_code == 302
    assert q('SELECT terms_accepted_at FROM users WHERE username = ?', 'agreer_one')[0]['terms_accepted_at'] is not None


def test_about_and_contact_are_public_and_contact_waits_for_an_address(appmod, monkeypatch):
    c = appmod.app.test_client()
    monkeypatch.delenv('PRIVACY_CONTACT', raising=False)
    about = c.get('/about'); contact = c.get('/contact')
    assert about.status_code == 200 and 'About Campaign Ledger' in about.get_data(as_text=True)
    assert contact.status_code == 200 and 'being set up' in contact.get_data(as_text=True) and 'mailto:' not in contact.get_data(as_text=True)
    monkeypatch.setenv('PRIVACY_CONTACT', 'hello@example.com')
    assert 'mailto:hello@example.com' in c.get('/contact').get_data(as_text=True)
    assert 'href="/about"' in c.get('/privacy').get_data(as_text=True)
