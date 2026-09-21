"""Stale temp objects are removed; fresh ones and the real uploads bucket are never touched."""
import os, subprocess, sys, time
import pytest
from storage import LocalStorage, SupabaseStorage, UPLOADS, TEMP
from conftest import BACKEND, FAKE, APP_DIR, _TMP

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


@pytest.fixture(params=['local', 'supabase'])
def store(request):
    if request.param == 'local':
        d = os.path.join(_TMP, 'cleanup-' + os.urandom(3).hex())
        s = LocalStorage(d, 'secret')
        s.age = lambda bucket, key, secs: os.utime(s._path(bucket, key), (time.time() - secs,) * 2)
        s.env = {'STORAGE_BACKEND': 'local', 'UPLOAD_DIR': d, 'SECRET_KEY': 'secret'}
        return s
    if FAKE is None:
        pytest.skip('fake supabase not running')
    s = SupabaseStorage(FAKE.url, FAKE.key, 'ledger-uploads', 'ledger-temp')
    s.age = lambda bucket, key, secs: FAKE.created.__setitem__((s.buckets[bucket], key), time.time() - secs)
    s.env = {'STORAGE_BACKEND': 'supabase', 'SUPABASE_URL': FAKE.url, 'SUPABASE_SECRET_KEY': FAKE.key}
    return s


def run(store, *args):
    return subprocess.run([sys.executable, os.path.join(APP_DIR, 'scripts', 'cleanup_temp.py'), *args], cwd=APP_DIR,
                          env=dict(os.environ, **store.env), capture_output=True, text=True, timeout=60)


def test_list_objects_walks_nested_folders(store):
    store.put(TEMP, 'tmp/7/aaa', b'1'); store.put(TEMP, 'exports/7/bbb.zip', b'2'); store.put(TEMP, 'top.bin', b'3')
    keys = {k for k, _ in store.list_objects(TEMP)}
    assert {'tmp/7/aaa', 'exports/7/bbb.zip', 'top.bin'} <= keys
    assert all(created is not None for _, created in store.list_objects(TEMP))
    store.delete_many(TEMP, ['tmp/7/aaa', 'exports/7/bbb.zip', 'top.bin'])


def test_cleanup_deletes_only_old_temp_objects(store):
    store.put(TEMP, 'tmp/1/old', b'x'); store.put(TEMP, 'exports/1/old.zip', b'x'); store.put(TEMP, 'tmp/1/fresh', b'x')
    store.put(UPLOADS, 'avatars/keep.png', b'x', 'image/png')
    store.age(TEMP, 'tmp/1/old', 3 * 86400); store.age(TEMP, 'exports/1/old.zip', 2 * 86400)
    dry = run(store, '--older-than-hours', '24', '--dry-run')
    assert dry.returncode == 0 and 'would delete' in dry.stdout and store.get(TEMP, 'tmp/1/old') == b'x'      # dry run changes nothing
    real = run(store, '--older-than-hours', '24')
    assert real.returncode == 0, real.stdout + real.stderr
    assert store.get(TEMP, 'tmp/1/old') is None and store.get(TEMP, 'exports/1/old.zip') is None
    assert store.get(TEMP, 'tmp/1/fresh') == b'x'                                                             # young file kept
    assert store.get(UPLOADS, 'avatars/keep.png') == b'x'                                                     # real uploads untouched
    store.delete_many(TEMP, ['tmp/1/fresh']); store.delete_many(UPLOADS, ['avatars/keep.png'])
