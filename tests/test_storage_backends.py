"""Both storage backends obey one contract; the Supabase one is exercised against the fake server."""
import io, os
import pytest, requests
import storage
from storage import StorageError, LocalStorage, SupabaseStorage, UPLOADS, TEMP
from conftest import BACKEND, FAKE, _TMP

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code unit tests')


@pytest.fixture(params=['local', 'supabase'])
def st(request):
    if request.param == 'local':
        return LocalStorage(os.path.join(_TMP, 'contract-uploads'), 'secret')
    if FAKE is None:
        pytest.skip('fake supabase not running (LEDGER_TEST_STORAGE=local)')
    return SupabaseStorage(FAKE.url, FAKE.key, 'ledger-uploads', 'ledger-temp')


def test_put_get_exists_delete(st):
    st.put(UPLOADS, 'avatars/abc.png', b'\x89PNGdata', 'image/png')
    assert st.get(UPLOADS, 'avatars/abc.png') == b'\x89PNGdata' and st.exists(UPLOADS, 'avatars/abc.png')
    st.put(UPLOADS, 'avatars/abc.png', b'v2', 'image/png')                       # overwrite (upsert)
    assert st.get(UPLOADS, 'avatars/abc.png') == b'v2'
    st.delete(UPLOADS, 'avatars/abc.png')
    assert st.get(UPLOADS, 'avatars/abc.png') is None and not st.exists(UPLOADS, 'avatars/abc.png')
    st.delete(UPLOADS, 'avatars/abc.png')                                        # deleting a missing key is fine


def test_delete_many_and_missing_keys(st):
    for i in range(5):
        st.put(TEMP, f'x/{i}.bin', b'1')
    st.delete_many(TEMP, [f'x/{i}.bin' for i in range(5)] + ['x/never-existed.bin'])
    assert all(st.get(TEMP, f'x/{i}.bin') is None for i in range(5))


def test_buckets_are_separate(st):
    st.put(UPLOADS, 'maps/a.png', b'U', 'image/png'); st.put(TEMP, 'maps/a.png', b'T')
    assert st.get(UPLOADS, 'maps/a.png') == b'U' and st.get(TEMP, 'maps/a.png') == b'T'
    st.delete_many(UPLOADS, ['maps/a.png']); st.delete_many(TEMP, ['maps/a.png'])


@pytest.mark.parametrize('bad', ['../etc/passwd', '/abs', 'a/../../b', 'a//b', '', 'a/', 'sp ace.png', 'x' * 300, 'a\\b', 'a\x00b', None])
def test_unsafe_keys_are_refused(st, bad):
    with pytest.raises(StorageError):
        st.put(UPLOADS, bad, b'x')
    with pytest.raises(StorageError):
        st.get(UPLOADS, bad)


def test_supabase_direct_signed_upload_and_download_roundtrip():
    if FAKE is None:
        pytest.skip('fake supabase not running')
    st = SupabaseStorage(FAKE.url, FAKE.key, 'ledger-uploads', 'ledger-temp')
    t = st.create_upload_target(TEMP, 'tmp/7/deadbeef', 'application/zip', 1000)
    assert t['method'] == 'PUT' and t['body'] == 'formdata' and 'token=' in t['url'] and t['url'].startswith(FAKE.url)
    r = requests.put(t['url'], files={'': ('blob', b'PK-data', 'application/zip')})      # what the browser does
    assert r.status_code == 200 and st.get(TEMP, 'tmp/7/deadbeef') == b'PK-data'
    url = st.signed_download_url(TEMP, 'tmp/7/deadbeef', 'export.zip', 60)
    dl = requests.get(url)
    assert dl.content == b'PK-data' and 'export.zip' in dl.headers.get('Content-Disposition', '')


def test_supabase_signed_upload_token_is_bound_to_its_key():
    if FAKE is None:
        pytest.skip('fake supabase not running')
    st = SupabaseStorage(FAKE.url, FAKE.key, 'ledger-uploads', 'ledger-temp')
    t = st.create_upload_target(TEMP, 'tmp/7/aaaa', 'image/png', 10)
    hijack = t['url'].replace('tmp/7/aaaa', 'tmp/8/bbbb')
    assert requests.put(hijack, files={'': ('b', b'x', 'image/png')}).status_code == 403


def test_bucket_limits_are_enforced_by_the_service():
    if FAKE is None:
        pytest.skip('fake supabase not running')
    st = SupabaseStorage(FAKE.url, FAKE.key, 'ledger-uploads', 'ledger-temp')
    with pytest.raises(StorageError):                                   # wrong MIME for the uploads bucket
        st.put(UPLOADS, 'avatars/x.png', b'<html>', 'text/html')
    FAKE.buckets['ledger-uploads']['limit'] = 10
    try:
        with pytest.raises(StorageError):
            st.put(UPLOADS, 'avatars/y.png', b'0123456789ABC', 'image/png')
    finally:
        FAKE.buckets['ledger-uploads']['limit'] = 30 * 1024 * 1024


def test_wrong_api_key_and_unreachable_service_raise_storage_error():
    if FAKE is None:
        pytest.skip('fake supabase not running')
    with pytest.raises(StorageError):
        SupabaseStorage(FAKE.url, 'sb_secret_WRONG', 'ledger-uploads', 'ledger-temp').put(UPLOADS, 'a/b.png', b'x', 'image/png')
    with pytest.raises(StorageError):
        SupabaseStorage('http://127.0.0.1:1', 'k', 'a', 'b', timeout=(0.3, 0.3)).get(UPLOADS, 'a/b.png')


def test_secret_key_is_sent_in_both_headers_with_identical_value():
    if FAKE is None:
        pytest.skip('fake supabase not running')
    st = SupabaseStorage(FAKE.url, 'sb_secret_abc', 'b', 'b')
    assert st._headers == {'apikey': 'sb_secret_abc', 'Authorization': 'Bearer sb_secret_abc'}


def test_backend_selection():
    s = storage.build_storage({'SUPABASE_URL': 'https://x.supabase.co', 'SUPABASE_SECRET_KEY': 'k'})
    assert s.name == 'supabase' and s.buckets == {'uploads': 'ledger-uploads', 'temp': 'ledger-temp'}
    assert storage.build_storage({'SUPABASE_URL': 'https://x.supabase.co', 'SUPABASE_SERVICE_ROLE_KEY': 'legacy'}).name == 'supabase'   # legacy fallback
    assert storage.build_storage({'UPLOAD_DIR': '/tmp/u', 'SECRET_KEY': 's'}).name == 'local'
    assert storage.build_storage({'STORAGE_BACKEND': 'local', 'SUPABASE_URL': 'https://x', 'SUPABASE_SECRET_KEY': 'k'}).name == 'local'
    with pytest.raises(StorageError):
        storage.build_storage({'STORAGE_BACKEND': 'supabase'})
