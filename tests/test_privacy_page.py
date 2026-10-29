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


def test_signed_in_pages_link_to_privacy_from_the_sidebar(make_user):
    u = make_user()
    assert 'href="/privacy"' in u.get('/campaigns').get_data(as_text=True)
