"""DL-161: a new account (password or Google) lands on the one-time "Set up your profile" page; saving or skipping ends it."""
import pytest

from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def test_new_account_goes_to_the_welcome_page_then_never_again(make_user):
    u = make_user()
    c = u.fresh_client()                                                    # a plain sign-in of a not-yet-set-up account
    r = c.post('/login', data={'username': u.name, 'password': u.password})
    assert r.status_code == 302 and r.headers['Location'].startswith('/welcome')
    page = c.get('/welcome')
    assert page.status_code == 200 and 'Set up your profile' in page.get_data(as_text=True) and f'value="{u.name}"' in page.get_data(as_text=True)
    r = c.post('/welcome', data={'username': 'Mira_the_bold', 'display_name': 'Mira', 'bio': 'DM of many tables', 'profile_visibility': 'private'})
    assert r.status_code == 302 and r.headers['Location'] == '/campaigns'
    assert c.get('/welcome').status_code == 302                             # done: never shown again
    row = q('SELECT username, display_name, bio, profile_visibility FROM users WHERE username = ?', 'Mira_the_bold')[0]
    assert (row['display_name'], row['bio'], row['profile_visibility']) == ('Mira', 'DM of many tables', 'private')
    r = c.post('/login', data={'username': 'Mira_the_bold', 'password': u.password})
    assert r.headers['Location'] == '/campaigns'


def test_skip_keeps_the_username_and_ends_the_page(make_user):
    u = make_user()
    r = u.post('/welcome', data={'action': 'skip'})
    assert r.status_code == 302 and u.get('/welcome').status_code == 302
    assert q('SELECT username FROM users WHERE username = ?', u.name)


def test_a_taken_username_is_refused_and_nothing_is_saved(make_user):
    a, b = make_user(), make_user()
    r = b.post('/welcome', data={'username': a.name.upper(), 'display_name': 'x'})
    assert r.status_code == 400 and 'already taken' in r.get_data(as_text=True)
    assert b.get('/welcome').status_code == 200                             # still not done
    assert q('SELECT display_name FROM users WHERE username = ?', b.name)[0]['display_name'] is None


def test_a_bad_username_is_refused(make_user):
    u = make_user()
    assert u.post('/welcome', data={'username': 'a<b'}).status_code == 400
    assert u.post('/welcome', data={'username': 'ab'}).status_code == 400


def test_register_sends_people_to_welcome_and_keeps_next(appmod):
    c = appmod.app.test_client()
    r = c.post('/register', data={'username': 'goer_one', 'email': 'goer@example.com', 'password': 'correct horse 1', 'confirm': 'correct horse 1', 'accept_terms': '1', 'accept_cookies': '1',
                                  'next': '/campaigns/new'}, headers={'X-Test-IP': '10.9.9.9'})
    assert r.status_code == 302 and r.headers['Location'] == '/confirm-email?next=/campaigns/new'        # DL-165: confirm first, then on
    assert c.get('/confirm-email?next=/campaigns/new').get_data(as_text=True).count('/welcome?next=') == 1      # "Continue without confirming"
    r = c.post('/welcome', data={'action': 'skip', 'next': '/campaigns/new'})
    assert r.headers['Location'] == '/campaigns/new'


# ---- DL-167: Terms / Privacy and cookies tick boxes ----
def _signup(c, **over):
    data = {'username': 'tick_' + __import__('uuid').uuid4().hex[:8], 'email': 'tick@example.com', 'password': 'correct horse 1', 'confirm': 'correct horse 1',
            'accept_terms': '1', 'accept_cookies': '1'}
    data.update(over)
    for k in [k for k, v in data.items() if v is None]:
        del data[k]
    return c.post('/register', data=data, headers={'X-Test-IP': '10.7.7.%d' % (hash(data['username']) % 200)})


def test_signup_needs_both_boxes_and_records_them(appmod):
    c = appmod.app.test_client()
    r = _signup(c, accept_terms=None)
    assert r.status_code == 200 and 'agree to the Terms' in r.get_data(as_text=True)
    r = _signup(c, accept_cookies=None)
    assert r.status_code == 200 and 'accept the cookies' in r.get_data(as_text=True)
    page = r.get_data(as_text=True)
    assert 'target="_blank"' in page and page.count('rel="noopener"') >= 3                 # opening the pages never leaves the form
    r = _signup(c, username='ticked_one')
    assert r.status_code == 302
    row = q('SELECT terms_accepted_at, cookies_accepted_at FROM users WHERE username = ?', 'ticked_one')[0]
    assert row['terms_accepted_at'] is not None and row['cookies_accepted_at'] is not None
    assert not q('SELECT 1 FROM users WHERE username = ?', 'tick_nothing')
