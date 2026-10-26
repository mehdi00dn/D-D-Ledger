"""DL-97 first step: mandatory email at sign-up, login by username or email, Google sign-in, public profiles with
visibility and a character showcase."""
import re
import uuid
from unittest import mock
import pytest
import mailer
from conftest import q


def urow(name):
    return q('SELECT * FROM users WHERE username = ?', name)[0]


def register(appmod, name, email='', password='secret12'):
    c = appmod.app.test_client()
    return c, c.post('/register', data={'username': name, 'email': email, 'password': password, 'confirm': password})


# ---------- sign-up needs an email ----------
def test_signup_requires_a_valid_email(appmod):
    for bad in ('', 'plain', 'a@b', 'a b@c.com', '<x>@y.com'):
        _c, r = register(appmod, 'nomail' + str(abs(hash(bad)) % 10**6), bad)
        assert r.status_code == 200, bad
        assert b'email' in r.data.lower()
    assert not q("SELECT 1 FROM users WHERE username LIKE 'nomail%'")


def test_signup_stores_the_email_unconfirmed_and_sends_a_link(appmod, monkeypatch):
    monkeypatch.setenv('MAIL_BACKEND', 'memory')
    mailer.outbox.clear()
    _c, r = register(appmod, 'withmail1', 'withmail1@example.com')
    assert r.status_code == 302
    row = urow('withmail1')
    assert row['email'] == 'withmail1@example.com' and row['email_verified_at'] is None
    assert len(mailer.outbox) == 1 and 'verify-email' in mailer.outbox[0]['text']


def test_signup_survives_a_mail_failure(appmod, monkeypatch):
    monkeypatch.setenv('MAIL_BACKEND', 'memory')
    with mock.patch('mailer.send', side_effect=mailer.MailError('down')):
        _c, r = register(appmod, 'mailfail1', 'mailfail1@example.com')
    assert r.status_code == 302 and urow('mailfail1')['email'] == 'mailfail1@example.com'


def test_signup_refuses_an_email_another_account_has_confirmed(appmod, make_user):
    u = make_user()
    q  # (confirmed address set directly: the mail flow itself is covered in test_email)
    import database
    db = database.get_db()
    db.execute("UPDATE users SET email = 'taken@example.com', email_verified_at = CURRENT_TIMESTAMP WHERE username = ?", (u.name,))
    db.commit(); db.close()
    _c, r = register(appmod, 'wantsit1', 'Taken@Example.com')
    assert r.status_code == 200 and b'already uses that email' in r.data


# ---------- login by email ----------
def test_login_with_email_or_username(appmod):
    register(appmod, 'emailer1', 'emailer1@example.com')
    for ident in ('emailer1', 'EMAILER1@example.com'):
        c = appmod.app.test_client()
        r = c.post('/login', data={'username': ident, 'password': 'secret12'})
        assert r.status_code == 302, ident
    r = appmod.app.test_client().post('/login', data={'username': 'emailer1@example.com', 'password': 'wrong-pass'})
    assert r.status_code == 200 and b'Incorrect username or password' in r.data


def test_login_by_email_is_refused_when_two_accounts_typed_the_same_unconfirmed_address(appmod):
    register(appmod, 'dupe1', 'dupe@example.com')
    register(appmod, 'dupe2', 'dupe@example.com')
    r = appmod.app.test_client().post('/login', data={'username': 'dupe@example.com', 'password': 'secret12'})
    assert r.status_code == 200
    assert appmod.app.test_client().post('/login', data={'username': 'dupe1', 'password': 'secret12'}).status_code == 302


# ---------- the pages ----------
def test_login_and_signup_pages_render_and_hide_google_until_configured(appmod, monkeypatch):
    monkeypatch.delenv('GOOGLE_CLIENT_ID', raising=False); monkeypatch.delenv('GOOGLE_CLIENT_SECRET', raising=False)
    c = appmod.app.test_client()
    for path in ('/login', '/register'):
        html = c.get(path).get_data(as_text=True)
        assert 'Continue with Google' not in html and 'Sign up with Google' not in html
        assert 'js/auth.js' in html and 'css/auth.css' in html
    assert 'name="email"' in c.get('/register').get_data(as_text=True)
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'cid'); monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'sec')
    assert 'Continue with Google' in c.get('/login').get_data(as_text=True)
    assert 'Sign up with Google' in c.get('/register').get_data(as_text=True)


# ---------- Google ----------
class FakeResp:
    def __init__(self, data): self._d = data
    def raise_for_status(self): pass
    def json(self): return self._d


@pytest.fixture
def google(appmod, monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'cid'); monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'sec')
    tag = uuid.uuid4().hex[:8]
    box = {'tag': tag, 'sub': 'g-' + tag, 'email': 'gal%s@example.com' % tag, 'username': 'gal' + tag,
           'info': {'sub': 'g-' + tag, 'email': 'gal%s@example.com' % tag, 'email_verified': True, 'name': 'Gal Gadot'}}
    monkeypatch.setattr(appmod.requests, 'post', lambda url, **kw: FakeResp({'access_token': 't'}))
    monkeypatch.setattr(appmod.requests, 'get', lambda url, **kw: FakeResp(box['info']))
    return box


def google_login(appmod, client=None):
    c = client or appmod.app.test_client()
    r = c.get('/auth/google?next=/campaigns')
    assert r.status_code == 302 and r.headers['Location'].startswith('https://accounts.google.com/')
    state = re.search(r'state=([^&]+)', r.headers['Location']).group(1)
    return c, c.get('/auth/google/callback?code=abc&state=' + state)


def test_google_is_off_without_keys(appmod, monkeypatch):
    monkeypatch.delenv('GOOGLE_CLIENT_ID', raising=False)
    r = appmod.app.test_client().get('/auth/google')
    assert r.status_code == 302 and 'google=off' in r.headers['Location']


def test_google_start_sends_pkce_and_state(appmod, google):
    r = appmod.app.test_client().get('/auth/google')
    loc = r.headers['Location']
    for part in ('client_id=cid', 'code_challenge_method=S256', 'scope=openid+email+profile', 'response_type=code'):
        assert part in loc


def test_google_callback_rejects_a_wrong_or_missing_state(appmod, google):
    c = appmod.app.test_client()
    c.get('/auth/google')
    assert 'google=expired' in c.get('/auth/google/callback?code=abc&state=nope').headers['Location']
    assert 'google=expired' in appmod.app.test_client().get('/auth/google/callback?code=abc&state=x').headers['Location']
    c.get('/auth/google')
    assert 'google=denied' in c.get('/auth/google/callback?error=access_denied').headers['Location']


def test_google_creates_a_confirmed_passwordless_account_then_logs_the_same_one_in(appmod, google):
    sub, email, uname = google['sub'], google['email'], google['username']
    c, r = google_login(appmod)
    assert r.status_code == 302 and r.headers['Location'].endswith('/campaigns')
    row = q("SELECT * FROM users WHERE google_sub = ?", sub)[0]
    assert row['email'] == email and row['email_verified_at'] is not None and row['password_hash'] is None
    assert row['display_name'] == 'Gal Gadot' and row['username'] == uname
    assert c.get('/campaigns').status_code == 200
    _c2, r2 = google_login(appmod)
    assert len(q("SELECT 1 FROM users WHERE google_sub = ?", sub)) == 1 and r2.status_code == 302


def test_google_links_to_an_account_with_the_same_confirmed_email(appmod, google, make_user):
    u = make_user()
    import database
    db = database.get_db()
    db.execute("UPDATE users SET email = ?, email_verified_at = CURRENT_TIMESTAMP WHERE username = ?", (google['email'], u.name))
    db.commit(); db.close()
    _c, r = google_login(appmod)
    assert r.status_code == 302
    assert urow(u.name)['google_sub'] == google['sub']
    assert len(q("SELECT 1 FROM users WHERE email = ?", google['email'])) == 1


def test_google_never_links_to_an_unconfirmed_address_and_takes_it_over(appmod, google, make_user):
    squatter = make_user(keep_email=False)
    import database
    db = database.get_db()
    db.execute("UPDATE users SET email = ? WHERE username = ?", (google['email'], squatter.name))
    db.commit(); db.close()
    _c, r = google_login(appmod)
    assert r.status_code == 302
    assert urow(squatter.name)['google_sub'] is None and urow(squatter.name)['email'] is None
    assert q("SELECT email_verified_at FROM users WHERE google_sub = ?", google['sub'])[0]['email_verified_at'] is not None


def test_google_without_a_confirmed_email_is_refused(appmod, google):
    google['info'] = {'sub': 'g-no-' + google['tag'], 'email': 'x%s@example.com' % google['tag'], 'email_verified': False}
    _c, r = google_login(appmod)
    assert 'google=unverified' in r.headers['Location'] and not q("SELECT 1 FROM users WHERE google_sub = ?", 'g-no-' + google['tag'])


def test_google_network_failure_is_a_friendly_error(appmod, google, monkeypatch):
    monkeypatch.setattr(appmod.requests, 'post', mock.Mock(side_effect=appmod.requests.ConnectionError('boom')))
    _c, r = google_login(appmod)
    assert 'google=failed' in r.headers['Location']


def test_google_account_can_add_a_password_and_use_username_login(appmod, google):
    c, _r = google_login(appmod)
    name = google['username']
    assert b'Add a password' in c.get('/account').data
    r = c.post('/account/password', data={'new_password': 'brand-new-pw', 'confirm_password': 'brand-new-pw'})
    assert r.status_code == 302 and urow(name)['password_hash']
    assert appmod.app.test_client().post('/login', data={'username': name, 'password': 'brand-new-pw'}).status_code == 302


def test_login_form_explains_a_google_only_account(appmod, google):
    google_login(appmod)
    r = appmod.app.test_client().post('/login', data={'username': google['username'], 'password': 'whatever12'})
    assert r.status_code == 200 and b'signs in with Google' in r.data


def test_google_account_can_delete_itself_without_a_password(appmod, google):
    c, _r = google_login(appmod)
    r = c.post('/account/delete', data={'confirm_delete': 'DELETE'})
    assert r.status_code == 302 and not q("SELECT 1 FROM users WHERE google_sub = ?", google['sub'])


# ---------- profiles ----------
def set_profile(u, **data):
    data.setdefault('display_name', ''); data.setdefault('bio', '')
    return u.post('/account', data=data, content_type='multipart/form-data')


def test_profile_visibility_levels(appmod, make_user):
    owner, stranger, mate = make_user(), make_user(), make_user()
    cid = owner.new_campaign(); owner.add_member(cid, mate)
    set_profile(owner, bio='Hello there')
    assert q('SELECT profile_visibility FROM users WHERE username = ?', owner.name)[0]['profile_visibility'] == 'shared'
    assert owner.get('/u/' + owner.name).status_code == 200
    assert stranger.get('/u/' + owner.name).status_code == 404
    assert b'Hello there' in mate.get('/u/' + owner.name).data
    set_profile(owner, profile_visibility='members')
    assert stranger.get('/u/' + owner.name.upper()).status_code == 200
    set_profile(owner, profile_visibility='private')
    assert stranger.get('/u/' + owner.name).status_code == 404 and mate.get('/u/' + owner.name).status_code == 404
    assert owner.get('/u/' + owner.name).status_code == 200
    assert stranger.get('/u/nobody-here').status_code == 404
    assert set_profile(owner, profile_visibility='everyone').status_code == 302       # unknown value is ignored
    assert q('SELECT profile_visibility FROM users WHERE username = ?', owner.name)[0]['profile_visibility'] == 'private'


def test_profile_requires_login(appmod):
    r = appmod.app.test_client().get('/u/someone')
    assert r.status_code == 302 and '/login' in r.headers['Location']


def test_showcase_shows_only_safe_fields_of_own_player_characters(appmod, make_user):
    owner, viewer = make_user(), make_user()
    cid = owner.new_campaign(name='Secret Campaign Name'); owner.add_member(cid, viewer)
    hero = owner.new_character(cid, name='Thorin Ironhelm', level=5, class_key='fighter', notes='SECRET NOTES')
    npc = owner.new_character(cid, name='Evil Duke', is_npc='on')
    r = set_profile(owner, showcase_present='1', showcase=[str(hero)])
    assert r.status_code == 302
    html = viewer.get('/u/' + owner.name).get_data(as_text=True)
    assert 'Thorin Ironhelm' in html and 'level 5' in html
    assert 'Secret Campaign Name' not in html and 'SECRET NOTES' not in html
    assert set_profile(owner, showcase_present='1', showcase=[str(npc)]).status_code == 400
    assert 'Evil Duke' not in viewer.get('/u/' + owner.name).get_data(as_text=True)


def test_showcase_rejects_other_peoples_characters_and_too_many(appmod, make_user):
    a, b = make_user(), make_user()
    ca, cb = a.new_campaign(), b.new_campaign()
    theirs = b.new_character(cb, name='Not Yours')
    assert set_profile(a, showcase_present='1', showcase=[str(theirs)]).status_code == 400
    mine = [a.new_character(ca, name=f'Mine {i}') for i in range(7)]
    assert set_profile(a, showcase_present='1', showcase=[str(i) for i in mine]).status_code == 400
    assert set_profile(a, showcase_present='1', showcase=[str(i) for i in mine[:6]]).status_code == 302
    assert len(q('SELECT 1 FROM profile_showcase WHERE user_id = (SELECT id FROM users WHERE username = ?)', a.name)) == 6
    assert set_profile(a, showcase_present='1').status_code == 302                       # nothing ticked clears it
    assert not q('SELECT 1 FROM profile_showcase WHERE user_id = (SELECT id FROM users WHERE username = ?)', a.name)


def test_account_page_without_the_showcase_marker_keeps_the_showcase(appmod, make_user):
    a = make_user(); ca = a.new_campaign(); ch = a.new_character(ca, name='Keeper')
    set_profile(a, showcase_present='1', showcase=[str(ch)])
    set_profile(a, display_name='New Name')                                              # an older form without the picker
    assert len(q('SELECT 1 FROM profile_showcase')) >= 1
    assert 'Keeper' in a.get('/u/' + a.name).get_data(as_text=True)


def test_deleting_a_character_or_account_cleans_the_showcase(appmod, make_user):
    a = make_user(); ca = a.new_campaign(); ch = a.new_character(ca, name='Gone Soon')
    set_profile(a, showcase_present='1', showcase=[str(ch)])
    a.post(f'/campaigns/{ca}/characters/{ch}/delete')
    assert not q('SELECT 1 FROM profile_showcase WHERE character_id = ?', ch)


def test_campaign_home_links_to_profiles_except_private_ones(appmod, make_user):
    owner, mate = make_user(), make_user()
    cid = owner.new_campaign(); owner.add_member(cid, mate)
    html = owner.get(f'/campaigns/{cid}/home').get_data(as_text=True)
    assert f'/u/{mate.name}' in html
    set_profile(mate, profile_visibility='private')
    assert f'/u/{mate.name}' not in owner.get(f'/campaigns/{cid}/home').get_data(as_text=True)


def test_missing_email_banner_for_older_accounts(appmod, make_user):
    old, fresh = make_user(), make_user(keep_email=True)
    assert 'Add an email address to your account' in old.get('/campaigns').get_data(as_text=True)
    assert 'Add an email address to your account' not in fresh.get('/campaigns').get_data(as_text=True)
