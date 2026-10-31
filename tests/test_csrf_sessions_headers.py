"""CSRF, cookies, secrets, headers, redirects, password rules."""
import glob, os, re, subprocess, sys
from unittest import mock
import pytest
from conftest import q, APP_DIR

LOGIN = '/login'


def raw(appmod):
    """A bare client that sends NO csrf token automatically."""
    c = appmod.app.test_client(); c.csrf_enabled = False; return c


def token_of(client, path='/login'):
    m = re.search(r'name="csrf-token" content="([^"]+)"', client.get(path).get_data(as_text=True)); return m.group(1)


# ------------------------------------------------------------------ CSRF
def test_resubmitted_login_form_after_signing_in_is_not_an_error(appmod, user):
    # The first submit signed the person in and rotated the token; a second submit still carries the old one.
    c = user.c; c.csrf_enabled = False
    r = c.post('/login', data={'username': user.name, 'password': user.password, 'csrf_token': 'old-token'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/campaigns')
    anon = appmod.app.test_client(); anon.csrf_enabled = False
    assert anon.post('/login', data={'username': 'x', 'password': 'y', 'csrf_token': 'old-token'}).status_code == 403   # signed-out still refused


def test_logout_always_signs_out_even_with_a_stale_token_or_odd_origin(appmod, user):
    # A failed security check must never leave someone signed in behind an error page (phone "Log out" bug).
    c = user.c; c.csrf_enabled = False
    r = c.post('/logout', data={'csrf_token': 'stale'}, headers={'Origin': 'null'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/login')
    assert c.get('/campaigns').status_code == 302


def test_state_changing_requests_without_a_token_are_rejected(appmod, user):
    cid = user.new_campaign(); ch = user.new_character(cid)
    c = user.c; c.csrf_enabled = False
    assert c.post(f'/campaigns/{cid}/characters/{ch}/delete').status_code == 403
    assert c.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': ch}).status_code == 403
    assert c.post(f'/campaigns/{cid}/delete').status_code == 403
    assert c.post('/uploads/sign', json={'purpose': 'avatar', 'size': 5}).status_code == 403
    assert q('SELECT COUNT(*) AS n FROM characters WHERE id = ?', ch)[0]['n'] == 1 and c.get('/campaigns').status_code == 200   # nothing happened, still logged in


def test_login_and_register_need_a_token_too(appmod):
    c = raw(appmod)
    assert c.post('/login', data={'username': 'x', 'password': 'y'}).status_code == 403
    assert c.post('/register', data={'username': 'newperson', 'password': 'longenough1', 'email': 'player@example.com', 'confirm': 'longenough1', 'accept_terms': '1', 'accept_cookies': '1'}).status_code == 403


def test_wrong_and_foreign_tokens_are_rejected(appmod, make_user):
    a, b = make_user(), make_user()
    cid = a.new_campaign()
    good = token_of(a.c)
    other = token_of(b.c)
    a.c.csrf_enabled = False
    assert a.c.post(f'/campaigns/{cid}/delete', headers={'X-CSRF-Token': 'guess'}).status_code == 403
    assert a.c.post(f'/campaigns/{cid}/delete', headers={'X-CSRF-Token': other}).status_code == 403      # someone else's token
    assert a.c.post(f'/campaigns/{cid}/delete', data={'csrf_token': ''}).status_code == 403
    assert a.c.post(f'/campaigns/{cid}/delete', data={'csrf_token': good}).status_code == 302            # form field works
    assert q('SELECT COUNT(*) AS n FROM campaigns WHERE id = ?', cid)[0]['n'] == 0


def test_cross_origin_posts_are_rejected_even_with_a_valid_token(appmod, user):
    cid = user.new_campaign(); tok = token_of(user.c)
    user.c.csrf_enabled = False
    h = {'X-CSRF-Token': tok}
    assert user.c.post(f'/campaigns/{cid}/delete', headers={**h, 'Origin': 'https://evil.example'}).status_code == 403
    assert user.c.post(f'/campaigns/{cid}/delete', headers={**h, 'Origin': 'null'}).status_code == 403
    assert user.c.post(f'/campaigns/{cid}/delete', headers={**h, 'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert user.c.post(f'/campaigns/{cid}/delete', headers={**h, 'Origin': 'http://localhost', 'Sec-Fetch-Site': 'same-origin'}).status_code == 302


def test_get_requests_do_not_need_a_token(appmod, user):
    c = user.c; c.csrf_enabled = False
    assert c.get('/campaigns').status_code == 200


def test_token_rotates_on_login_and_dies_on_logout(appmod, user):
    before = token_of(user.c, '/campaigns')
    user.post('/logout')
    after_logout = token_of(user.c)                                       # a fresh anonymous session
    assert after_logout != before
    user.post('/login', data={'username': user.name, 'password': user.password})
    assert token_of(user.c, '/campaigns') not in (before, after_logout)
    stale = before; user.c.csrf_enabled = False
    assert user.c.post('/campaigns/new', data={'name': 'x'}, headers={'X-CSRF-Token': stale}).status_code == 403


def test_every_post_form_in_every_template_carries_the_token():
    form_re = re.compile(r'<form\b[^>]*\bmethod\s*=\s*["\']?post["\']?[^>]*>(.*?)</form>', re.I | re.S)
    script_re = re.compile(r'<script\b[^>]*>.*?</script>', re.I | re.S)   # a JS-built form string (e.g. one
    missing, total = [], 0                                                # assembled client-side after a fetch())
    for path in glob.glob(os.path.join(APP_DIR, 'templates', '*.html')):  # isn't a template-rendered form; the
        html = script_re.sub('', open(path, encoding='utf-8').read())     # real security check for it lives in a
        for m in form_re.finditer(html):                                  # JS-level test, not this template scan.
            total += 1
            if 'csrf_field()' not in m.group(1):
                missing.append(os.path.basename(path))
    assert total >= 15 and not missing, missing


def test_pages_expose_the_token_to_scripts(appmod, user):
    for path in ('/campaigns', '/login', '/register'):
        assert re.search(r'<meta name="csrf-token" content="[\w\-]{20,}">', user.get(path).get_data(as_text=True)), path
    assert '/static/js/csrf.js' in user.get('/campaigns').get_data(as_text=True)


# ------------------------------------------------------------------ cookies / secrets
def run_python(code, env_extra, drop=()):
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(env_extra, PYTHONPATH=APP_DIR)
    return subprocess.run([sys.executable, '-c', code], cwd=APP_DIR, env=env, capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize('secret', [None, '', 'short', 'dnd-campaign-manager-dev-key', 'change-me', 'x' * 31])
def test_production_refuses_to_start_without_a_strong_secret_key(secret):
    env = {'VERCEL': '1'}
    if secret is not None:
        env['SECRET_KEY'] = secret
    r = run_python('import app', env, drop=() if secret is not None else ('SECRET_KEY',))
    assert r.returncode != 0 and 'SECRET_KEY' in r.stderr, r.stderr[-300:]


def test_production_starts_with_a_strong_secret_and_secure_cookies():
    r = run_python("import app; c=app.app.config; print(c['SESSION_COOKIE_NAME'], c['SESSION_COOKIE_SECURE'], c['SESSION_COOKIE_HTTPONLY'], c['SESSION_COOKIE_SAMESITE'])",
                   {'VERCEL': '1', 'SECRET_KEY': 'k' * 40, 'DATABASE_URL': os.environ['DATABASE_URL']})
    assert r.returncode == 0, r.stderr[-300:]
    assert r.stdout.split() == ['__Host-ledger', 'True', 'True', 'Lax']


def test_development_without_a_secret_gets_a_random_one_not_a_known_constant():
    outs = set()
    for _ in range(2):
        r = run_python("import app; print(app.app.secret_key)", {}, drop=('SECRET_KEY',))
        assert r.returncode == 0, r.stderr[-300:]
        outs.add(r.stdout.strip().splitlines()[-1])
    assert len(outs) == 2 and all(len(o) >= 32 and 'dnd-campaign' not in o for o in outs)


def test_session_cookie_flags_when_secure(appmod, make_user):
    with mock.patch.dict(appmod.app.config, {'SESSION_COOKIE_SECURE': True}):
        c = raw(appmod); tok = token_of(c)
        name = 'cookieuser' + os.urandom(3).hex()
        c.post('/register', data={'username': name, 'password': 'secret12', 'email': 'player@example.com', 'confirm': 'secret12', 'accept_terms': '1', 'accept_cookies': '1', 'csrf_token': tok})
        r = c.post('/logout', data={'csrf_token': token_of(c, '/campaigns')})
        # 'remember' is checked by default on the real login page -- a real browser sends it unless unchecked.
        r = c.post('/login', data={'username': name, 'password': 'secret12', 'remember': 'on', 'csrf_token': token_of(c)})
        cookie = ' '.join(r.headers.getlist('Set-Cookie')).lower()
    assert 'httponly' in cookie and 'secure' in cookie and 'samesite=lax' in cookie and 'expires=' in cookie      # persistent, not session-only


# ------------------------------------------------------------------ headers
def test_security_headers_on_pages_and_api(appmod, user):
    cid = user.new_campaign()
    for r in (user.get('/campaigns'), user.get(f'/campaigns/{cid}/api/battle'), appmod.app.test_client().get('/login')):
        h = r.headers
        assert h['X-Content-Type-Options'] == 'nosniff' and h['X-Frame-Options'] == 'DENY' and h['Referrer-Policy'] == 'same-origin'
        assert h['Cache-Control'] == 'no-store'
        csp = h['Content-Security-Policy']
        assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "base-uri 'self'" in csp and "form-action 'self'" in csp
        assert 'unsafe-eval' not in csp
        script_src = [d for d in csp.split('; ') if d.startswith('script-src')][0]
        assert "'unsafe-inline'" not in script_src                      # DL-123: injected scripts must not run
        style_src = [d for d in csp.split('; ') if d.startswith('style-src ')][0]
        assert "'unsafe-inline'" not in csp and 'style-src-attr' not in csp      # DL-140 / DL-172: no injected <style> blocks and no style="" attributes
        assert h['Cross-Origin-Resource-Policy'] == 'same-origin' and 'report-uri /csp-report' in csp


def test_csp_report_endpoint_takes_a_browser_report_without_sign_in_or_token(appmod):
    c = appmod.app.test_client()
    body = '{"csp-report": {"violated-directive": "style-src-elem", "blocked-uri": "inline", "document-uri": "https://x/y"}}'
    assert c.post('/csp-report', data=body, content_type='application/csp-report').status_code == 204
    assert c.post('/csp-report', data='not json', content_type='application/csp-report').status_code == 204


def test_csp_allows_only_our_own_storage_host_for_direct_uploads(appmod):
    with mock.patch.dict(os.environ, {'SUPABASE_URL': 'https://abcd.supabase.co', 'STORAGE_BACKEND': 'supabase'}):
        assert "connect-src 'self' https://abcd.supabase.co;" in appmod._csp() + ';'
    with mock.patch.dict(os.environ, {'STORAGE_BACKEND': 'local', 'SUPABASE_URL': 'https://abcd.supabase.co'}):
        assert 'supabase' not in appmod._csp()


def test_uploaded_images_keep_their_own_cache_policy(appmod, user):
    import io
    from conftest import png_bytes
    cid = user.new_campaign()
    user.post(f'/campaigns/{cid}/characters/new', data={'name': 'P', 'avatar': (io.BytesIO(png_bytes()), 'a.png')}, content_type='multipart/form-data')
    path = q('SELECT avatar_path FROM characters WHERE name = ?', 'P')[0]['avatar_path']
    assert 'immutable' in user.get('/uploads/' + path).headers['Cache-Control']


# ------------------------------------------------------------------ open redirect
@pytest.mark.parametrize('nxt', ['//evil.example', '/\\evil.example', '\\\\evil.example', 'https://evil.example', 'http://evil.example/x',
                                 'javascript:alert(1)', '/ok\r\nSet-Cookie: a=b', '/\tevil', 'evil.example', '///evil.example', '/' + 'a' * 2100])
def test_login_never_redirects_off_site(appmod, make_user, nxt):
    u = make_user(); u.post('/logout')
    r = u.post('/login', data={'username': u.name, 'password': u.password, 'next': nxt})
    assert r.status_code == 302
    loc = r.headers['Location']
    assert not re.match(r'^(https?:)?//', loc) and '\\' not in loc and 'evil' not in loc, loc


def test_login_still_honours_safe_next_paths(appmod, make_user):
    u = make_user(); u.post('/welcome', data={'action': 'skip'}); u.post('/logout')
    r = u.post('/login', data={'username': u.name, 'password': u.password, 'next': '/campaigns?x=1'})
    assert r.headers['Location'] == '/campaigns?x=1'


def test_login_of_an_account_without_a_finished_profile_goes_through_welcome_and_keeps_next(appmod, make_user):
    u = make_user(); u.post('/logout')
    r = u.post('/login', data={'username': u.name, 'password': u.password, 'next': '/campaigns?x=1'})
    assert r.headers['Location'] == '/welcome?next=/campaigns?x%3D1'


# ------------------------------------------------------------------ sessions / passwords
def test_session_is_reset_on_login(appmod, make_user):
    u = make_user()
    with u.c.session_transaction() as s:
        s['leftover'] = 'from-a-previous-visitor'
    u.post('/logout')
    # 'remember' is checked by default on the real login page -- match that here.
    u.post('/login', data={'username': u.name, 'password': u.password, 'remember': 'on'})
    with u.c.session_transaction() as s:
        assert 'leftover' not in s and s.get('user_id') and s.permanent


def test_unchecking_remember_makes_a_session_only_cookie(appmod, make_user):
    """The checkbox defaults to checked (covered above); this is the other half --
    explicitly unchecking it (i.e. not sending 'remember' at all) should make the
    login session-only, dropped when the browser closes rather than persisted."""
    u = make_user()
    u.post('/logout')
    u.post('/login', data={'username': u.name, 'password': u.password})   # no 'remember' field at all
    with u.c.session_transaction() as s:
        assert s.get('user_id') and not s.permanent


@pytest.mark.parametrize('username, password, fragment', [
    ('okname', 'short7!', 'at least 8'), ('okname', 'x' * 129, 'at most 128'), ('ab', 'longenough1', 'Username must be'),
    ('a' * 33, 'longenough1', 'Username must be'), ('<b>bold</b>', 'longenough1', 'Username must be'),
    ('bad\x07name', 'longenough1', 'Username must be'), ('', 'longenough1', 'required'), ('okname', '', 'required')])
def test_registration_rules(appmod, username, password, fragment):
    c = appmod.app.test_client()
    r = c.post('/register', data={'username': username, 'password': password, 'email': 'player@example.com', 'confirm': password, 'accept_terms': '1', 'accept_cookies': '1'})
    assert r.status_code == 200 and fragment in r.get_data(as_text=True), fragment


def test_persian_usernames_and_passwords_are_fine(appmod):
    c = appmod.app.test_client(); name = 'مهدی' + os.urandom(2).hex()
    assert c.post('/register', data={'username': name, 'password': 'رمز-عبور-طولانی', 'email': 'player@example.com', 'confirm': 'رمز-عبور-طولانی', 'accept_terms': '1', 'accept_cookies': '1'}).status_code == 302
    c.post('/logout')
    assert c.post('/login', data={'username': name, 'password': 'رمز-عبور-طولانی'}).status_code == 302
    assert c.get('/campaigns').status_code == 200


def test_inline_scripts_carry_this_pages_nonce_and_no_inline_handlers_remain(appmod, user):
    """DL-123: every executable inline <script> has the nonce announced in the header; templates hold no on*= handlers."""
    import glob
    for f in glob.glob(os.path.join(APP_DIR, 'templates', '*.html')):
        src = open(f, encoding='utf-8').read()
        assert not re.search(r'<[a-z][^>]*\son[a-z]+\s*=', src), f'{f} has an inline event handler'
        for tag in re.findall(r'<script(?![^>]*\ssrc=)[^>]*>', src):
            assert 'nonce="{{ csp_nonce() }}"' in tag or 'type="application/json"' in tag, (f, tag)
    cid = user.new_campaign()
    for url in ('/campaigns', f'/campaigns/{cid}/characters', f'/campaigns/{cid}/battle'):
        r = user.get(url)
        nonce = re.search(r"'nonce-([^']+)'", r.headers['Content-Security-Policy']).group(1)
        page = r.get_data(as_text=True)
        tags = [t for t in re.findall(r'<script(?![^>]*\ssrc=)[^>]*>', page) if 'application/json' not in t]
        assert tags and all(f'nonce="{nonce}"' in t for t in tags), (url, tags)
    assert user.get('/campaigns').headers['Content-Security-Policy'] != user.get('/campaigns').headers['Content-Security-Policy']   # fresh each time


def test_fonts_are_served_from_this_site_not_google(appmod, user):
    """DL-36: no runtime dependency on Google Fonts (slow or filtered in some regions)."""
    cid = user.new_campaign()
    for r in (user.get(f'/campaigns/{cid}/characters'), appmod.app.test_client().get('/login'), appmod.app.test_client().get('/register')):
        page = r.get_data(as_text=True)
        assert 'fonts.googleapis' not in page and 'fonts.gstatic' not in page
        csp = r.headers['Content-Security-Policy']
        assert 'google' not in csp and 'gstatic' not in csp
    css = appmod.app.test_client().get('/static/css/style.css').get_data(as_text=True)
    files = re.findall(r"url\('\.\./fonts/([^']+)'\)", css)
    assert len(files) >= 6
    for name in files:
        r = appmod.app.test_client().get('/static/fonts/' + name)
        assert r.status_code == 200 and r.mimetype in ('font/woff2', 'application/font-woff2') and len(r.data) > 10000, name
        assert 'max-age=' in r.headers['Cache-Control'] and 'no-store' not in r.headers['Cache-Control']
