"""Direct browser-to-storage uploads, hostile inputs, cleanup, and error handling at the app level."""
import io, json, os, re, zipfile
from unittest import mock
import pytest, requests
from PIL import Image
from conftest import BACKEND, q, png_bytes
from storage import get_storage, StorageError, UPLOADS, TEMP
import images

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def noise_png(w, h):
    b = io.BytesIO(); Image.frombytes('RGB', (w, h), os.urandom(w * h * 3)).save(b, 'PNG'); return b.getvalue()


def direct_put(user, purpose, data, ctype='image/png'):
    """Do exactly what the browser does: ask for a signed target, then PUT the bytes to it."""
    r = user.json('/uploads/sign', {'purpose': purpose, 'size': len(data), 'content_type': ctype})
    assert r.status_code == 200, r.get_json()
    j = r.get_json(); t = j['upload']
    if t['url'].startswith('/'):                                        # local backend: same-origin route
        rr = user.c.put(t['url'], data=data, content_type='application/octet-stream')
    else:                                                               # supabase: straight to storage
        rr = requests.put(t['url'], files={'': ('blob', data, ctype)})
    assert rr.status_code == 200, (rr.status_code, rr.text[:200])
    return j['key']


def zip_of(manifest, files=None, raw_manifest=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', raw_manifest if raw_manifest is not None else json.dumps(manifest))
        for name, data in (files or {}).items():
            z.writestr(name, data)
    return b.getvalue()


def do_import(user, cid, data):
    return user.post(f'/campaigns/{cid}/import/data', data={'import_file': (io.BytesIO(data), 'x.zip')}, content_type='multipart/form-data')


# ------------------------------------------------------------------ signing
def test_sign_needs_login_and_validates_everything(appmod, user):
    assert appmod.app.test_client().post('/uploads/sign', json={'purpose': 'avatar', 'size': 10}).status_code == 302
    sign = lambda **kw: user.json('/uploads/sign', {'purpose': 'avatar', 'size': 100, 'content_type': 'image/png', **kw})
    assert sign(purpose='nope').status_code == 400
    assert sign(size=0).status_code == 400 and sign(size='abc').status_code == 400 and sign(size=-5).status_code == 400
    assert sign(size=images.KIND_MAX_BYTES['avatars'] + 1).status_code == 413
    assert sign(content_type='text/html').status_code == 415 and sign(content_type='application/x-msdownload').status_code == 415
    ok = sign().get_json()
    assert re.fullmatch(r'tmp/\d+/[0-9a-f]{32}', ok['key']) and ok['upload']['method'] == 'PUT'
    assert user.json('/uploads/sign', {'purpose': 'import', 'size': 100, 'content_type': 'application/zip'}).status_code == 200
    assert user.json('/uploads/sign', {'purpose': 'import', 'size': 60 * 1024 * 1024, 'content_type': 'application/zip'}).status_code == 413


# ------------------------------------------------------------------ large + direct
def test_large_map_uploaded_directly_is_validated_shrunk_and_servable(camp):
    u, cid = camp
    blob = noise_png(3000, 2000)
    assert len(blob) > 15_000_000                                        # far beyond a serverless body limit
    key = direct_put(u, 'map', blob)
    r = u.post(f'/campaigns/{cid}/maps/new', data={'name': 'Huge', 'image__key': key}, content_type='multipart/form-data')
    assert r.status_code == 302
    row = q('SELECT image_path, image_width, image_height FROM maps WHERE campaign_id = ?', cid)[0]
    assert max(row['image_width'], row['image_height']) <= 2000
    served = u.get('/uploads/' + row['image_path'])
    assert served.status_code == 200 and len(served.data) <= images.SERVE_LIMIT
    assert not get_storage().exists(TEMP, key)                           # staging object cleaned up


def test_direct_avatar_and_multiple_sheets(camp):
    u, cid = camp
    a = direct_put(u, 'avatar', png_bytes(200, 200)); s1 = direct_put(u, 'sheet', png_bytes(300, 400)); s2 = direct_put(u, 'sheet', png_bytes(310, 410))
    r = u.post(f'/campaigns/{cid}/characters/new', data={'name': 'Direct', 'max_hp': '5', 'avatar__key': a, 'sheets__key': [s1, s2]},
               content_type='multipart/form-data')
    assert r.status_code == 302
    ch = q('SELECT id, avatar_path FROM characters WHERE campaign_id = ? AND name = ?', cid, 'Direct')[0]
    assert ch['avatar_path'] and u.get('/uploads/' + ch['avatar_path']).status_code == 200
    assert len(q('SELECT id FROM character_sheets WHERE character_id = ?', ch['id'])) == 2


def test_direct_and_normal_uploads_can_mix(camp):
    u, cid = camp
    a = direct_put(u, 'avatar', png_bytes(64, 64))
    r = u.post(f'/campaigns/{cid}/characters/new', data={'name': 'Mixed', 'avatar__key': a, 'sheets': (io.BytesIO(png_bytes(50, 50)), 's.png')},
               content_type='multipart/form-data')
    ch = q('SELECT id, avatar_path FROM characters WHERE campaign_id = ? AND name = ?', cid, 'Mixed')[0]
    assert ch['avatar_path'] and len(q('SELECT id FROM character_sheets WHERE character_id = ?', ch['id'])) == 1


def test_someone_elses_staged_key_is_ignored_and_left_alone(make_user):
    alice, bob = make_user(), make_user()
    key = direct_put(alice, 'avatar', png_bytes())
    cid = bob.new_campaign()
    r = bob.post(f'/campaigns/{cid}/characters/new', data={'name': 'Thief', 'avatar__key': key}, content_type='multipart/form-data')
    assert r.status_code == 302
    assert q('SELECT avatar_path FROM characters WHERE campaign_id = ? AND name = ?', cid, 'Thief')[0]['avatar_path'] is None
    assert get_storage().exists(TEMP, key)                                # Alice's staged file was not consumed


@pytest.mark.parametrize('forged', ['tmp/../../etc/passwd', 'avatars/existing.png', 'tmp/1/../2/abc', 'tmp/999999/' + 'a' * 32,
                                    'tmp/1/nothex', '', '/tmp/1/' + 'a' * 32, 'tmp/1/' + 'a' * 32 + '/x'])
def test_forged_direct_keys_never_crash_or_attach(camp, forged):
    u, cid = camp
    r = u.post(f'/campaigns/{cid}/characters/new', data={'name': 'Forge', 'avatar__key': forged}, content_type='multipart/form-data')
    assert r.status_code == 302
    assert q('SELECT avatar_path FROM characters WHERE campaign_id = ? AND name = ?', cid, 'Forge')[0]['avatar_path'] is None


def test_non_image_uploaded_directly_is_rejected_and_staging_cleaned(camp):
    u, cid = camp
    key = direct_put(u, 'avatar', b'<html><script>alert(1)</script></html>' * 3, 'image/png')     # lies about its type
    u.post(f'/campaigns/{cid}/characters/new', data={'name': 'Fake', 'avatar__key': key}, content_type='multipart/form-data')
    assert q('SELECT avatar_path FROM characters WHERE campaign_id = ? AND name = ?', cid, 'Fake')[0]['avatar_path'] is None
    assert not get_storage().exists(TEMP, key)


def test_non_image_normal_upload_is_rejected_too(camp):
    u, cid = camp
    u.post(f'/campaigns/{cid}/characters/new', data={'name': 'Fake2', 'avatar': (io.BytesIO(b'MZ\x90\x00' + b'0' * 300), 'evil.png')},
           content_type='multipart/form-data')
    assert q('SELECT avatar_path FROM characters WHERE campaign_id = ? AND name = ?', cid, 'Fake2')[0]['avatar_path'] is None


# ------------------------------------------------------------------ serving
def test_uploads_require_login_and_are_cacheable(appmod, camp):
    u, cid = camp
    u.new_character(cid, name='Pic');
    u.post(f'/campaigns/{cid}/characters/new', data={'name': 'Pic2', 'avatar': (io.BytesIO(png_bytes()), 'a.png')}, content_type='multipart/form-data')
    path = q('SELECT avatar_path FROM characters WHERE name = ?', 'Pic2')[0]['avatar_path']
    anon = appmod.app.test_client().get('/uploads/' + path)
    assert anon.status_code == 302 and '/login' in anon.headers['Location']
    r = u.get('/uploads/' + path)
    assert r.status_code == 200 and r.mimetype == 'image/png'
    assert 'immutable' in r.headers['Cache-Control'] and r.headers['X-Content-Type-Options'] == 'nosniff'
    for bad in ('../app.py', 'avatars/../../x.png', 'avatars/nope.png', 'avatars/a.html', 'other/' + path.split('/')[1], 'avatars/' + 'a' * 100 + '.png'):
        assert u.get('/uploads/' + bad).status_code == 404, bad


def test_storage_outage_is_a_503_not_a_500(camp):
    u, cid = camp
    with mock.patch.object(get_storage(), 'put', side_effect=StorageError('down')):
        r = u.post(f'/campaigns/{cid}/characters/new', data={'name': 'Down', 'avatar': (io.BytesIO(png_bytes()), 'a.png')}, content_type='multipart/form-data')
    assert r.status_code == 503
    with mock.patch.object(get_storage(), 'create_upload_target', side_effect=StorageError('down')):
        r = u.json('/uploads/sign', {'purpose': 'avatar', 'size': 10, 'content_type': 'image/png'})
    assert r.status_code == 503 and 'error' in r.get_json()


# ------------------------------------------------------------------ cleanup
def test_deleting_things_removes_their_files(make_user):
    u = make_user(); cid = u.new_campaign()
    def new_char(name):
        u.post(f'/campaigns/{cid}/characters/new', data={'name': name, 'avatar': (io.BytesIO(png_bytes()), 'a.png'),
               'sheets': (io.BytesIO(png_bytes(30, 30)), 's.png')}, content_type='multipart/form-data')
        ch = q('SELECT id, avatar_path FROM characters WHERE name = ? AND campaign_id = ?', name, cid)[0]
        return ch['id'], ch['avatar_path'], q('SELECT image_path FROM character_sheets WHERE character_id = ?', ch['id'])[0]['image_path']
    st = get_storage()
    ch1, av1, sh1 = new_char('One'); ch2, av2, sh2 = new_char('Two')
    u.post(f'/campaigns/{cid}/characters/{ch1}/delete')
    assert not st.exists(UPLOADS, av1) and not st.exists(UPLOADS, sh1) and st.exists(UPLOADS, av2)
    mid = u.new_map(cid); mp = q('SELECT image_path FROM maps WHERE id = ?', mid)[0]['image_path']
    assert st.exists(UPLOADS, mp)
    u.post(f'/campaigns/{cid}/maps/{mid}/delete')
    assert not st.exists(UPLOADS, mp)
    u.post(f'/campaigns/{cid}/delete')
    assert not st.exists(UPLOADS, av2) and not st.exists(UPLOADS, sh2)


def test_replacing_an_avatar_deletes_the_old_file(camp):
    u, cid = camp
    u.post(f'/campaigns/{cid}/characters/new', data={'name': 'Swap', 'avatar': (io.BytesIO(png_bytes()), 'a.png')}, content_type='multipart/form-data')
    ch = q('SELECT id, avatar_path FROM characters WHERE name = ?', 'Swap')[0]
    u.post(f'/campaigns/{cid}/characters/{ch["id"]}/edit', data={'name': 'Swap', 'avatar': (io.BytesIO(png_bytes(20, 20)), 'b.png')}, content_type='multipart/form-data')
    new = q('SELECT avatar_path FROM characters WHERE id = ?', ch['id'])[0]['avatar_path']
    assert new != ch['avatar_path'] and get_storage().exists(UPLOADS, new) and not get_storage().exists(UPLOADS, ch['avatar_path'])


# ------------------------------------------------------------------ import: happy path + hostile archives
def test_zip_import_can_arrive_by_direct_upload(make_user):
    u = make_user(); src, dst = u.new_campaign(), u.new_campaign()
    u.post(f'/campaigns/{src}/characters/new', data={'name': 'Travel', 'avatar': (io.BytesIO(png_bytes()), 'a.png')}, content_type='multipart/form-data')
    z = u.get(f'/campaigns/{src}/export/data').data
    key = direct_put(u, 'import', z, 'application/zip')
    r = u.post(f'/campaigns/{dst}/import/data', data={'import_file__key': key}, content_type='multipart/form-data')
    assert r.status_code == 302 and 'import_error' not in r.headers['Location']
    ch = q('SELECT avatar_path FROM characters WHERE campaign_id = ? AND name = ?', dst, 'Travel')[0]
    assert ch['avatar_path'] and u.get('/uploads/' + ch['avatar_path']).status_code == 200
    assert not get_storage().exists(TEMP, key)


def test_large_export_goes_through_a_signed_download(camp):
    import app as appmod
    u, cid = camp
    u.post(f'/campaigns/{cid}/characters/new', data={'name': 'Big', 'avatar': (io.BytesIO(png_bytes(300, 300)), 'a.png')}, content_type='multipart/form-data')
    with mock.patch.object(appmod, 'DIRECT_INLINE_LIMIT', 500):
        r = u.get(f'/campaigns/{cid}/export/data')
    assert r.status_code == 302
    loc = r.headers['Location']
    body = requests.get(loc).content if loc.startswith('http') else u.get(loc).data
    z = zipfile.ZipFile(io.BytesIO(body))
    assert json.loads(z.read('manifest.json'))['characters'][0]['name'] == 'Big' and any(n.startswith('images/') for n in z.namelist())


@pytest.mark.parametrize('name, blob', [
    ('not a zip', b'PK\x03\x04garbage'),
    ('zip without manifest', zip_of({}, raw_manifest=None).replace(b'manifest.json', b'manifesx.json')),
    ('manifest not json', zip_of(None, raw_manifest='{{{ nope')),
    ('manifest is a list', zip_of(None, raw_manifest='[1,2,3]')),
])
def test_broken_archives_are_rejected_cleanly(camp, name, blob):
    u, cid = camp
    r = do_import(u, cid, blob)
    assert r.status_code == 302 and 'import_error=1' in r.headers['Location'], name
    assert q('SELECT COUNT(*) AS n FROM characters WHERE campaign_id = ?', cid)[0]['n'] == 0


def test_zip_bomb_is_rejected_before_it_is_read(camp):
    u, cid = camp
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', '{}'); z.writestr('images/avatars/big.png', b'\x00' * (120 * 1024 * 1024))
    assert len(b.getvalue()) < 1_000_000                                 # ~100 KB of "data" that expands to 120 MB
    r = do_import(u, cid, b.getvalue())
    assert 'import_error=1' in r.headers['Location']


def test_read_member_is_bounded_even_if_the_header_lies():
    import importer
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('images/a.png', b'\x00' * (5 * 1024 * 1024))
    z = zipfile.ZipFile(io.BytesIO(b.getvalue()))
    z.getinfo('images/a.png').file_size = 10                              # forged header
    with pytest.raises(importer.ImportRejected):                          # oversized OR detected as corrupt -- never a crash
        importer.read_member(z, 'images/a.png', 1024 * 1024)


def test_too_many_entries_rejected(camp):
    u, cid = camp
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('manifest.json', '{}')
        for i in range(3100):
            z.writestr(f'images/avatars/{i}.png', b'x')
    assert 'import_error=1' in do_import(u, cid, b.getvalue()).headers['Location']


def test_hostile_manifest_values_are_coerced_not_trusted(camp):
    u, cid = camp
    evil = {'groups': [{'name': 'G' * 5000, 'color': 'red;background:url(//evil)', 'bio': 'x\x00y', 'avatar_file': '../../etc/passwd'}, 'not-a-dict', None],
            'characters': [
                {'name': 'A\x00B' + 'z' * 500, 'level': 'abc', 'max_hp': 10 ** 30, 'is_npc': 'yes', 'str_score': -5, 'armor_class': 3.9,
                 'notes': json.dumps(['<img src=x onerror=alert(1)//', '<b>ok</b><script>alert(2)</script>']),
                 'group_name': 'G' * 5000, 'avatar_file': '/etc/passwd', 'sheet_files': ['../x.png', 5, None, 'sheets/ok.png', 'sheets/../../y.png']},
                'junk', 42, {'name': None, 'level': None, 'sheet_files': 'not-a-list'}]}
    r = do_import(u, cid, zip_of(evil, {'images/sheets/ok.png': b'<html>not an image</html>'}))
    assert r.status_code == 302 and 'import_error' not in r.headers['Location']
    rows = q('SELECT * FROM characters WHERE campaign_id = ? ORDER BY id', cid)
    assert len(rows) == 2
    a = rows[0]
    assert '\x00' not in a['name'] and len(a['name']) <= 120
    assert (a['level'], a['max_hp'], a['is_npc'], a['str_score'], a['armor_class']) == (1, 100000, 1, 0, 3)
    assert 'onerror' not in a['notes'] and '<script' not in a['notes'] and '<b>ok</b>' in a['notes']
    assert a['avatar_path'] is None
    assert q('SELECT COUNT(*) AS n FROM character_sheets WHERE character_id = ?', a['id'])[0]['n'] == 0     # 'sheets/ok.png' was HTML, not an image
    g = q('SELECT name, color FROM groups WHERE campaign_id = ?', cid)
    assert len(g) == 1 and g[0]['color'] == '#c9a24b' and len(g[0]['name']) <= 120
    assert rows[1]['name'] == 'Unnamed'


def test_bad_image_in_archive_does_not_block_the_rest(camp):
    u, cid = camp
    m = {'groups': [], 'characters': [{'name': 'Good', 'avatar_file': 'avatars/ok.png'}, {'name': 'Bad', 'avatar_file': 'avatars/bad.png'}]}
    r = do_import(u, cid, zip_of(m, {'images/avatars/ok.png': png_bytes(), 'images/avatars/bad.png': b'garbage'}))
    assert 'import_error' not in r.headers['Location']
    got = {c['name']: c['avatar_path'] for c in q('SELECT name, avatar_path FROM characters WHERE campaign_id = ?', cid)}
    assert got['Good'] and got['Bad'] is None


def test_import_is_atomic_when_storage_fails_midway(camp):
    u, cid = camp
    m = {'groups': [], 'characters': [{'name': 'Atomic', 'avatar_file': 'avatars/ok.png'}]}
    with mock.patch.object(get_storage(), 'put', side_effect=StorageError('down')):
        r = do_import(u, cid, zip_of(m, {'images/avatars/ok.png': png_bytes()}))
    assert r.status_code == 503
    assert q('SELECT COUNT(*) AS n FROM characters WHERE campaign_id = ?', cid)[0]['n'] == 0      # rolled back, no dangling rows
