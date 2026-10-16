"""DL-18: basic profile (display name, avatar, bio) on the account, independent of any campaign."""
from conftest import q, png_bytes
import io


def row(u):
    return q('SELECT * FROM users WHERE username = ?', u.name)[0]


def save(u, **data):
    files = data.pop('avatar_png', None)
    if files:
        data['avatar'] = (io.BytesIO(files), 'a.png')
    return u.post('/account', data=data, content_type='multipart/form-data')


def test_account_requires_login(appmod):
    r = appmod.app.test_client().get('/account')
    assert r.status_code == 302 and '/login' in r.headers['Location']


def test_save_display_name_and_bio_show_in_sidebar(user):
    r = save(user, display_name='  Sir   Pip  ', bio='Rolls a lot of ones.')
    assert r.status_code == 302 and 'saved=1' in r.headers['Location']
    assert row(user)['display_name'] == 'Sir Pip' and row(user)['bio'] == 'Rolls a lot of ones.'
    html = user.get('/campaigns').get_data(as_text=True)
    assert 'Sir Pip' in html and 'Your profile' in html
    assert 'Profile saved.' in user.get('/account?saved=1').get_data(as_text=True)


def test_empty_display_name_falls_back_to_username(user):
    save(user, display_name='Pip', bio='')
    save(user, display_name='', bio='')
    assert row(user)['display_name'] is None and row(user)['bio'] is None
    assert user.name in user.get('/campaigns').get_data(as_text=True)


def test_display_name_is_escaped_and_validated(user):
    for bad in ('<script>x</script>', 'x' * 41):
        r = save(user, display_name=bad, bio='')
        assert r.status_code == 400 and row(user)['display_name'] is None
    assert save(user, display_name='A', bio='b' * 501).status_code == 400
    assert row(user)['display_name'] is None


def test_username_is_unchanged_and_login_still_uses_it(user):
    save(user, display_name='Someone Else', bio='')
    assert row(user)['username'] == user.name
    user.fresh_client()          # logs in with the username + password


def test_avatar_upload_replace_and_remove_tracks_storage(user):
    save(user, display_name='', bio='', avatar_png=png_bytes(300, 200))
    first = row(user)['avatar_path']
    assert first and first.startswith('avatars/')
    assert user.get('/uploads/' + first).status_code == 200
    assert q('SELECT bytes FROM stored_files WHERE key = ?', first)[0]['bytes'] > 0
    assert q('SELECT user_id FROM stored_files WHERE key = ?', first)[0]['user_id'] == row(user)['id']

    save(user, display_name='', bio='', avatar_png=png_bytes(100, 100, (0, 90, 0)))
    second = row(user)['avatar_path']
    assert second and second != first
    assert not q('SELECT 1 FROM stored_files WHERE key = ?', first)       # old file's quota was freed
    assert user.get('/uploads/' + first).status_code == 404

    save(user, display_name='', bio='', remove_avatar='1')
    assert row(user)['avatar_path'] is None
    assert not q('SELECT 1 FROM stored_files WHERE key = ?', second)


def test_bad_image_is_ignored_not_a_crash(user):
    r = user.post('/account', data={'display_name': 'Pip', 'bio': '', 'avatar': (io.BytesIO(b'not an image'), 'a.png')},
                  content_type='multipart/form-data')
    assert r.status_code in (302, 400) and row(user)['avatar_path'] is None


def test_profiles_are_private_to_their_owner(make_user):
    a, b = make_user(), make_user()
    save(a, display_name='Secret Name', bio='private bio')
    assert 'Secret Name' not in b.get('/account').get_data(as_text=True)
    cid = a.new_campaign(); a.add_member(cid, b, 'player')
    assert 'private bio' not in b.get(f'/campaigns/{cid}/characters').get_data(as_text=True)


def test_profile_needs_no_campaign(user):
    assert user.get('/account').status_code == 200          # works with zero campaigns


def test_csrf_is_enforced(user):
    user.c.csrf_enabled = False
    assert user.c.post('/account', data={'display_name': 'x', 'bio': ''}).status_code == 403
    assert row(user)['display_name'] is None


def test_site_survives_migration_0022_not_applied(user):
    """Deploying the code before the migration must not 500 every page (it did): pages fall back to the username."""
    import database
    def rename(a, b):
        db = database.get_db()
        db.execute(f'ALTER TABLE users RENAME COLUMN {a} TO {b}'); db.commit(); db.close()
    rename('display_name', 'display_name_off')
    try:
        r = user.get('/campaigns')
        assert r.status_code == 200 and user.name in r.get_data(as_text=True)
        assert user.get('/account').status_code == 503
    finally:
        rename('display_name_off', 'display_name')


# ---- change password ----
def change(u, current, new, confirm=None):
    return u.post('/account/password', data={'current_password': current, 'new_password': new,
                                             'confirm_password': new if confirm is None else confirm})


def test_change_password_then_login_with_new_one(user, appmod):
    r = change(user, user.password, 'brand-new-pass')
    assert r.status_code == 302 and 'pw=1' in r.headers['Location']
    assert 'Password changed.' in user.get('/account?pw=1').get_data(as_text=True)
    c = appmod.app.test_client()
    assert c.post('/login', data={'username': user.name, 'password': 'brand-new-pass'}).status_code == 302
    assert 'Incorrect' in appmod.app.test_client().post('/login', data={'username': user.name, 'password': user.password}).get_data(as_text=True)
    assert user.get('/campaigns').status_code == 200          # this session keeps working


def test_change_password_checks_current_and_rules(user):
    before = row(user)['password_hash']
    for args, text in [((user.password + 'x', 'brand-new-pass'), 'current password is not correct'),
                       ((user.password, 'short'), 'at least 8'),
                       ((user.password, 'brand-new-pass', 'different-one'), 'do not match'),
                       ((user.password, user.password), 'different from the current')]:
        r = change(user, *args)
        assert r.status_code == 400 and text in r.get_data(as_text=True)
    assert row(user)['password_hash'] == before


def test_change_password_wrong_current_is_throttled(user, appmod):
    from unittest import mock
    import security
    with mock.patch.dict(security.WINDOWS, {'login-pair': (3, 900)}), mock.patch.dict(appmod.app.config, {'CLIENT_IP_HEADER': 'X-Test-IP'}):
        user.c.environ_base['HTTP_X_TEST_IP'] = '10.9.8.7'
        codes = [change(user, 'wrong-guess-%d' % i, 'brand-new-pass').status_code for i in range(5)]
        assert codes[:3] == [400, 400, 400] and codes[3] == 429
        assert change(user, user.password, 'brand-new-pass').status_code == 429      # even the right one waits


def test_change_password_csrf(user):
    user.c.csrf_enabled = False
    assert user.c.post('/account/password', data={'current_password': user.password, 'new_password': 'brand-new-pass',
                                                  'confirm_password': 'brand-new-pass'}).status_code == 403


# ---- sessions: log out everywhere ----
def test_password_change_signs_out_other_devices(user):
    other = user.fresh_client()
    assert other.get('/campaigns').status_code == 200
    assert change(user, user.password, 'brand-new-pass').status_code == 302
    r = other.get('/campaigns')
    assert r.status_code == 302 and '/login' in r.headers['Location']
    assert user.get('/campaigns').status_code == 200                 # the device that changed it stays in


def test_logout_everywhere_keeps_only_this_device(user):
    other = user.fresh_client()
    r = user.post('/account/sessions/logout-all')
    assert r.status_code == 302 and 'out=1' in r.headers['Location']
    assert other.get('/campaigns').status_code == 302
    assert user.get('/campaigns').status_code == 200
    assert user.fresh_client().get('/campaigns').status_code == 200   # logging in again works


def test_signed_out_json_calls_get_a_401(user):
    other = user.fresh_client()
    user.post('/account/sessions/logout-all')
    r = other.post('/uploads/sign', json={'purpose': 'avatar', 'size': 10, 'content_type': 'image/png'})
    assert r.status_code == 401 and r.get_json()['code'] == 'unauthenticated'


def test_logout_everywhere_csrf(user):
    user.c.csrf_enabled = False
    assert user.c.post('/account/sessions/logout-all').status_code == 403


def test_sessions_survive_migration_0023_not_applied(user):
    import database
    def rename(a, b):
        db = database.get_db(); db.execute(f'ALTER TABLE users RENAME COLUMN {a} TO {b}'); db.commit(); db.close()
    rename('session_version', 'session_version_off')
    try:
        assert user.get('/campaigns').status_code == 200
        assert user.post('/account/sessions/logout-all').status_code == 503
    finally:
        rename('session_version_off', 'session_version')


def test_account_page_structure(user):
    """Regression: the password form once ended up inside the <title> block."""
    html = user.get('/account').get_data(as_text=True)
    title = html[html.index('<title>'):html.index('</title>')]
    assert '<' not in title[len('<title>'):] and 'Your profile' in title
    for form_id in ('account-form', 'email-panel', 'password-form', 'sessions-form'):
        assert html.count(f'id="{form_id}"') == 1, form_id
