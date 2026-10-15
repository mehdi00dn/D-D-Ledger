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
