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
    assert 'name="accept_terms"' in text and 'name="accept_cookies"' in text           # DL-167: tick boxes, not just a notice
    r = c.post('/register', data={'username': 'agreer_one', 'email': 'agree@example.com', 'password': 'correct horse 1', 'confirm': 'correct horse 1', 'accept_terms': '1', 'accept_cookies': '1'},
               headers={'X-Test-IP': '10.8.8.8'})
    assert r.status_code == 302
    row = q('SELECT terms_accepted_at, cookies_accepted_at FROM users WHERE username = ?', 'agreer_one')[0]
    assert row['terms_accepted_at'] is not None and row['cookies_accepted_at'] is not None


def test_about_and_contact_are_public_and_contact_waits_for_an_address(appmod, monkeypatch):
    c = appmod.app.test_client()
    monkeypatch.delenv('PRIVACY_CONTACT', raising=False)
    about = c.get('/about'); contact = c.get('/contact')
    assert about.status_code == 200 and 'About Campaign Ledger' in about.get_data(as_text=True)
    assert contact.status_code == 200 and 'being set up' in contact.get_data(as_text=True) and 'mailto:' not in contact.get_data(as_text=True)
    monkeypatch.setenv('PRIVACY_CONTACT', 'hello@example.com')
    assert 'mailto:hello@example.com' in c.get('/contact').get_data(as_text=True)
    assert 'href="/about"' in c.get('/privacy').get_data(as_text=True)


def test_public_pages_make_no_backup_promise_until_backups_exist(appmod):
    """DL-86: the Terms said "we keep backups" before any backup or restore drill existed.  Re-add the sentence only with the backup task (DL-52)."""
    c = appmod.app.test_client()
    for path in ('/terms', '/privacy', '/about', '/cookies'):
        assert 'backup' not in c.get(path).get_data(as_text=True).lower(), path



# ---- DL-170: the Cookies page is checked against what the code really stores ----
import os  # noqa: E402
import re  # noqa: E402

_ROOT = os.path.join(os.path.dirname(__file__), '..')
# every key the scripts keep in the browser -> a phrase the Cookies page must use to describe it
STORAGE_KEYS = {
    'ledger-theme': 'Light or dark theme',
    'ledger-sidebar-collapsed': 'sidebar is collapsed',
    'ledger-map-dock-tucked': 'map tool dock',
    'ledger-map-modifier-hint-dismissed': 'map tips',
    'ledger-map-nav-hint-dismissed': 'map tips',
    'ledger-map-space-hint-dismissed': 'map tips',
    'ledger-map-share-measure': 'Show to table',
    'account-section': 'section of Account settings',
    'ledger-fullscreen': 'full screen',
}


def _source_files():
    for sub, exts in (('static/js', ('.js',)), ('templates', ('.html',))):
        for base, _dirs, files in os.walk(os.path.join(_ROOT, sub)):
            for f in files:
                if f.endswith(exts):
                    yield os.path.join(base, f)


def _storage_keys_in_code():
    found = set()
    call = re.compile(r"(?:localStorage|sessionStorage)\.(?:get|set|remove)Item\(\s*([A-Za-z_][A-Za-z0-9_]*|'[^']+'|\"[^\"]+\")")
    for path in _source_files():
        text = open(path, encoding='utf-8').read()
        consts = dict(re.findall(r"(?:const|let|var)\s+([A-Z_][A-Z0-9_]*)\s*=\s*'([^']+)'", text))
        for arg in call.findall(text):
            if arg[0] in ('"', "'"):
                found.add(arg[1:-1])
            else:
                assert arg in consts, f'{os.path.basename(path)}: storage key {arg} is not a plain string constant, so it cannot be checked'
                found.add(consts[arg])
    return found


def test_cookies_page_describes_every_key_the_scripts_store(appmod):
    keys = _storage_keys_in_code()
    unknown = keys - set(STORAGE_KEYS)
    assert not unknown, f'new browser-storage keys {sorted(unknown)}: describe them on templates/cookies.html and add them to STORAGE_KEYS here'
    page = appmod.app.test_client().get('/cookies').get_data(as_text=True)
    for key, phrase in STORAGE_KEYS.items():
        assert phrase in page, f'/cookies does not mention "{phrase}" (key {key})'


def test_no_script_writes_a_cookie():
    for path in _source_files():
        text = open(path, encoding='utf-8').read()
        assert 'document.cookie' not in text, f'{os.path.basename(path)} writes a cookie by script: add it to the Cookies page first'
        # nothing loading from another site is guarded by tests/test_third_party_audit.py


def test_signing_in_and_browsing_sets_exactly_one_cookie(make_user):
    u = make_user()
    names = set()
    for path in ('/campaigns', '/account', '/privacy', '/cookies', '/terms'):
        r = u.c.get(path)
        names |= {h.split('=', 1)[0] for h in r.headers.getlist('Set-Cookie')}
    jar = {c.key for c in u.c._cookies.values()} if hasattr(u.c, '_cookies') else {k for k in u.c.cookie_jar} if hasattr(u.c, 'cookie_jar') else None
    session_name = u.app.config['SESSION_COOKIE_NAME']
    assert names <= {session_name}, f'extra cookies set: {names}'          # the Cookies page says there is one
    if jar is not None:
        assert jar == {session_name}, jar


def test_srd_credit_is_on_the_about_and_reference_pages(make_user):
    """DL-171: the SRD is under CC BY 4.0, which asks for the credit wherever its text is used."""
    about = make_user().c.get('/about').get_data(as_text=True)
    assert 'id="credits"' in about
    assert 'System Reference Document 5.2.1' in about and 'Wizards of the Coast LLC' in about
    assert 'creativecommons.org/licenses/by/4.0/legalcode' in about and 'Ladon Sklyarov' in about
    u = make_user()
    cid = u.new_campaign()
    ref = u.get(f'/campaigns/{cid}/reference').get_data(as_text=True)
    assert 'ref-credit' in ref and 'Creative Commons Attribution 4.0' in ref and '/about#credits' in ref
