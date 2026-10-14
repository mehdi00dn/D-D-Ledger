#!/usr/bin/env python3
"""Rehearse a restore: prove a backup made by scripts/backup.py can bring the app back.

    DRILL_PG_ADMIN="postgresql://postgres:PASSWORD@localhost:5432/postgres"  python scripts/restore_drill.py backups/ledger-20261014-221500Z

It never touches production.  It creates a throw-away database on the Postgres server DRILL_PG_ADMIN points at
(a local Postgres or Docker container -- NOT your Supabase project), restores the dump into it, and checks that

  1. the dump and every stored file still match the checksums written at backup time,
  2. every table has the rows the backup recorded (between the counts taken before and after the dump),
  3. the same schema migration is in place and every table still has row-level security on,
  4. every image the database points at exists in the backup (and its size matches the storage ledger),
  5. the real app starts on the restored data (/healthz is green, a stored image is served from the restored files),

prints how long the restore took (your recovery time) and drops the database again.  Exit status 0 = PASS.
Add --keep-db to keep the restored database for poking around.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

import psycopg
from psycopg.conninfo import make_conninfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backup import sha256_file, table_counts  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every column that holds an image key.  (Same list as scripts/backfill_storage_usage.py.)
IMAGE_KEY_QUERIES = [
    'SELECT avatar_path AS k FROM campaigns WHERE avatar_path IS NOT NULL',
    'SELECT avatar_path AS k FROM characters WHERE avatar_path IS NOT NULL',
    'SELECT avatar_path AS k FROM groups WHERE avatar_path IS NOT NULL',
    'SELECT image_path AS k FROM character_sheets',
    'SELECT image_path AS k FROM maps WHERE image_path IS NOT NULL',
]

APP_CHECK = r'''
import hashlib, json, os, sys
os.environ.setdefault("SECRET_KEY", "restore-drill")
import app as appmod
from storage import get_storage, UPLOADS
c = appmod.app.test_client()
r = c.get("/healthz")
out = {"healthz": r.status_code, "ok": (r.get_json() or {}).get("ok")}
key = sys.argv[1] if len(sys.argv) > 1 else None
if key:
    data = get_storage().get(UPLOADS, key)
    out["image_sha256"] = hashlib.sha256(data).hexdigest() if data else None
print(json.dumps(out))
'''


class Report:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail=''):
        self.rows.append((name, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f' - {detail}' if detail else ''))
        return ok

    @property
    def passed(self):
        return all(ok for _, ok, _ in self.rows)


def _admin(dsn, dbname):
    return make_conninfo(dsn, dbname=dbname)


def drill(backup_dir, admin_dsn, keep_db=False, log=print):
    started = time.time()
    rep = Report()
    with open(os.path.join(backup_dir, 'manifest.json')) as fh:
        man = json.load(fh)
    log(f"Restore drill for {os.path.basename(os.path.normpath(backup_dir))} (taken {man['created_at']})")

    # 1. the backup itself is intact
    dump = os.path.join(backup_dir, man['dump']['file'])
    rep.check('dump file matches its checksum', sha256_file(dump) == man['dump']['sha256'])
    bad = []
    for key, meta in man['files'].items():
        path = os.path.join(backup_dir, 'files', *key.split('/'))
        if not os.path.isfile(path) or sha256_file(path) != meta['sha256']:
            bad.append(key)
    rep.check(f"all {len(man['files'])} stored files match their checksums", not bad, f'{len(bad)} damaged or missing: {bad[:3]}' if bad else '')

    # 2. restore into a throw-away database
    name = 'ledger_drill_' + uuid.uuid4().hex[:8]
    with psycopg.connect(_admin(admin_dsn, 'postgres'), autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    target = _admin(admin_dsn, name)
    restore_started, restore_seconds = time.time(), None
    try:
        p = subprocess.run(['pg_restore', '--clean', '--if-exists', '--no-owner', '--no-acl', '--exit-on-error', '--dbname', target, dump],
                           capture_output=True, text=True)
        rep.check('pg_restore finished without errors', p.returncode == 0, (p.stderr or '').strip()[:300])
        restore_seconds = round(time.time() - restore_started, 1)
        if p.returncode == 0:
            counts, latest = table_counts(target)
            lo, hi = man['tables_before_dump'], man['tables']
            off = {t: (lo.get(t), counts.get(t), hi.get(t)) for t in hi
                   if not (lo.get(t, 0) <= counts.get(t, -1) <= hi[t])}
            missing = sorted(set(hi) - set(counts))
            rep.check(f'all {len(hi)} tables restored with the recorded row counts', not off and not missing,
                      f'tables off (before, restored, after): {off}; missing: {missing}' if (off or missing) else
                      f'{sum(counts.values())} rows')
            rep.check('schema migration level matches', latest == man['migration'], f"{latest} vs {man['migration']}")
            with psycopg.connect(target, prepare_threshold=None) as conn:
                open_tables = [r[0] for r in conn.execute(
                    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity")]
            rep.check('every table has row-level security on after the restore', not open_tables, f'open: {open_tables}' if open_tables else '')

            # 4. every image the database references is in the backup
            with psycopg.connect(target, prepare_threshold=None) as conn:
                keys = sorted({r[0] for q in IMAGE_KEY_QUERIES for r in conn.execute(q)})
                ledger = ({r[0]: r[1] for r in conn.execute('SELECT key, bytes FROM stored_files')}
                          if 'stored_files' in counts else {})
            absent = [k for k in keys if k not in man['files']]
            rep.check(f'all {len(keys)} images referenced by the database are in the backup', not absent,
                      f'{len(absent)} missing: {absent[:3]}' if absent else '')
            wrong = [k for k, b in ledger.items() if k in man['files'] and man['files'][k]['bytes'] != b]
            rep.check('storage ledger sizes match the stored files', not wrong, f'{len(wrong)} differ: {wrong[:3]}' if wrong else '')

            # 5. the real app runs on it
            env = dict(os.environ, DATABASE_URL=target, STORAGE_BACKEND='local',
                       UPLOAD_DIR=os.path.join(backup_dir, 'files'), SECRET_KEY='restore-drill', PYTHONPATH=REPO)
            for var in ('SUPABASE_URL', 'SUPABASE_SECRET_KEY', 'SUPABASE_SERVICE_ROLE_KEY', 'VERCEL'):
                env.pop(var, None)
            with tempfile.TemporaryDirectory() as tmp:
                script = os.path.join(tmp, 'app_check.py')
                with open(script, 'w') as fh:
                    fh.write(APP_CHECK)
                sample = next((k for k in keys if k in man['files']), '')
                q = subprocess.run([sys.executable, script, sample],
                                   capture_output=True, text=True, env=env, cwd=REPO)
            try:
                res = json.loads(q.stdout.strip().splitlines()[-1])
            except Exception:
                res = {}
            rep.check('the app starts on the restored data (/healthz is green)', res.get('healthz') == 200 and res.get('ok') is True,
                      (q.stderr or q.stdout or '').strip()[-300:] if res.get('ok') is not True else '')
            if sample:
                rep.check('a restored image is served byte-for-byte', res.get('image_sha256') == man['files'][sample]['sha256'])
    finally:
        if keep_db:
            log(f'kept database {name} (drop it with: DROP DATABASE "{name}")')
        else:
            with psycopg.connect(_admin(admin_dsn, 'postgres'), autocommit=True) as c:
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    total = round(time.time() - started, 1)
    log(f"{'PASS' if rep.passed else 'FAIL'}: restore took {restore_seconds} s, whole drill {total} s")
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('backup_dir')
    ap.add_argument('--admin-dsn', default=os.environ.get('DRILL_PG_ADMIN'),
                    help='a Postgres server where a throw-away database may be created (env DRILL_PG_ADMIN)')
    ap.add_argument('--keep-db', action='store_true')
    args = ap.parse_args(argv)
    if not args.admin_dsn:
        raise SystemExit('Set DRILL_PG_ADMIN (or --admin-dsn) to a local Postgres server; never point it at production.')
    rep = drill(args.backup_dir, args.admin_dsn, args.keep_db)
    sys.exit(0 if rep.passed else 1)


if __name__ == '__main__':
    main()
