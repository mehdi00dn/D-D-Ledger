"""DL-45: a backup is only real once a restore has been rehearsed.  These tests take a real backup of the test
database and storage and run the real restore drill against a throw-away database."""
import io, json, os, shutil, sys

import pytest
import psycopg

from conftest import q, png_bytes, PG_ADMIN

sys.path.insert(0, os.path.join(os.getcwd(), 'scripts'))
import backup as backup_mod            # noqa: E402
import restore_drill                   # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which('pg_dump') is None or shutil.which('pg_restore') is None,
                                reason='needs the PostgreSQL client tools')


@pytest.fixture()
def populated(camp):
    u, cid = camp
    u.post(f'/campaigns/{cid}/characters/new',
           data={'name': 'Backup Hero', 'avatar': (io.BytesIO(png_bytes(90, 90)), 'a.png'),
                 'sheets': [(io.BytesIO(png_bytes(300, 400)), 's.png')]}, content_type='multipart/form-data')
    u.new_map(cid, name='Backup Map')
    return u, cid


def _take(tmp_path, appmod, keep=None):
    from storage import get_storage
    with appmod.app.app_context():
        return backup_mod.backup(str(tmp_path), os.environ['DATABASE_URL'], get_storage(), keep=keep, log=lambda *_: None)


def _drill(path):
    return restore_drill.drill(path, PG_ADMIN, log=lambda *_: None)


def _leftover_drill_dbs():
    with psycopg.connect(PG_ADMIN, autocommit=True) as c:
        return [r[0] for r in c.execute("SELECT datname FROM pg_database WHERE datname LIKE 'ledger_drill_%'")]


def test_backup_and_restore_drill_pass_end_to_end(populated, appmod, tmp_path):
    path = _take(tmp_path, appmod)
    man = json.load(open(os.path.join(path, 'manifest.json')))
    assert man['migration'] and man['tables']['characters'] >= 1 and len(man['files']) >= 3
    assert oct(os.stat(path).st_mode & 0o777) == '0o700'
    rep = _drill(path)
    assert rep.passed, [r for r in rep.rows if not r[1]]
    names = [n for n, ok, _ in rep.rows]
    assert any('row-level security' in n for n in names) and any('app starts' in n for n in names) and any('served byte-for-byte' in n for n in names)
    assert _leftover_drill_dbs() == []                              # the throw-away database is dropped again


def test_drill_fails_when_a_stored_file_is_damaged(populated, appmod, tmp_path):
    path = _take(tmp_path, appmod)
    victim = next(iter(json.load(open(os.path.join(path, 'manifest.json')))['files']))
    with open(os.path.join(path, 'files', *victim.split('/')), 'ab') as fh:
        fh.write(b'rot')
    rep = _drill(path)
    assert not rep.passed and any('checksums' in n and not ok for n, ok, _ in rep.rows)
    assert _leftover_drill_dbs() == []


def test_drill_fails_when_the_dump_is_damaged(populated, appmod, tmp_path):
    path = _take(tmp_path, appmod)
    with open(os.path.join(path, 'db.dump'), 'r+b') as fh:
        fh.seek(200); fh.write(b'\0\0\0\0')
    rep = _drill(path)
    assert not rep.passed and any('dump file' in n and not ok for n, ok, _ in rep.rows)


def test_drill_notices_an_image_missing_from_the_backup(populated, appmod, tmp_path):
    path = _take(tmp_path, appmod)
    man_path = os.path.join(path, 'manifest.json')
    man = json.load(open(man_path))
    gone = next(k for k in man['files'] if k.startswith('avatars/'))
    del man['files'][gone]
    json.dump(man, open(man_path, 'w'))
    rep = _drill(path)
    assert not rep.passed and any('referenced by the database' in n and not ok for n, ok, _ in rep.rows)


def test_old_backups_are_pruned_and_partial_ones_never_left(populated, appmod, tmp_path):
    import time
    first = _take(tmp_path, appmod); time.sleep(1.1)
    second = _take(tmp_path, appmod, keep=1)
    left = sorted(os.listdir(tmp_path))
    assert left == [os.path.basename(second)] and not os.path.exists(first)
    assert not [d for d in left if d.endswith('.partial')]


def test_failed_dump_leaves_no_partial_folder(populated, appmod, tmp_path):
    from storage import get_storage
    with pytest.raises(SystemExit):
        with appmod.app.app_context():
            backup_mod.backup(str(tmp_path), 'host=/tmp port=1 dbname=nope', get_storage(), log=lambda *_: None)
    assert os.listdir(tmp_path) == []
