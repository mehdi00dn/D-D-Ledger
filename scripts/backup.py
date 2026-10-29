#!/usr/bin/env python3
"""Take a full, verifiable backup of Campaign Ledger: the database AND the uploaded images.

    BACKUP_DATABASE_URL=postgresql://...  SUPABASE_URL=...  SUPABASE_SECRET_KEY=...  python scripts/backup.py
    python scripts/backup.py --out ~/ledger-backups --keep 8

What you get (one folder per backup, never overwritten):
    ledger-20261014-221500Z/
        db.dump         pg_dump custom format of schema `public` (restorable with pg_restore)
        files/<key>     every object of the uploads bucket, under its own key (so the folder doubles as UPLOAD_DIR)
        manifest.json   when, which migration, row count of every table, sha256 + size of every file

A backup is only real once a restore has been rehearsed: run scripts/restore_drill.py on it.

Connection notes
  * pg_dump needs a session-capable connection: use Supabase's *direct* or *session pooler* string in
    BACKUP_DATABASE_URL.  The transaction pooler (port 6543, what Vercel uses) does not work for pg_dump.
    Without BACKUP_DATABASE_URL it falls back to DATABASE_URL.
  * pg_dump must be the same major version as the server, or newer (Supabase runs Postgres 17).
  * Backups hold personal data.  Keep them private, encrypt before copying off this machine, never commit them.
    The folder is created readable by you only.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

import psycopg

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from storage import UPLOADS, StorageError, build_storage, safe_key  # noqa: E402

FORMAT = 1


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def table_counts(dsn):
    """{table: rows} for every ordinary table in `public` (exact counts)."""
    with psycopg.connect(dsn, prepare_threshold=None) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")]
        counts = {n: conn.execute(f'SELECT count(*) FROM public."{n}"').fetchone()[0] for n in names}
        has_mig = 'schema_migrations' in counts
        latest = conn.execute('SELECT max(version) FROM schema_migrations').fetchone()[0] if has_mig else None
    return counts, latest


def run_pg_dump(dsn, target):
    cmd = ['pg_dump', '--format=custom', '--no-owner', '--no-acl', '--schema=public', '--file', target, '--dbname', dsn]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        err = (p.stderr or '').strip()
        hint = ''
        if 'server version mismatch' in err:
            hint = '\nHint: install a pg_dump of the same major version as the server (or newer), e.g. postgresql-client-17.'
        elif 'pooler' in err.lower() or 'prepared statement' in err.lower():
            hint = '\nHint: use the direct or session-pooler connection string for BACKUP_DATABASE_URL, not the transaction pooler.'
        raise SystemExit(f'pg_dump failed:\n{err}{hint}')


def backup(out_dir, dsn, storage, keep=None, log=print):
    started = time.time()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%SZ')
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    final = os.path.join(out_dir, f'ledger-{stamp}')
    work = final + '.partial'
    if os.path.exists(final) or os.path.exists(work):
        raise SystemExit(f'{final} already exists; wait a second and retry')
    os.makedirs(os.path.join(work, 'files'), mode=0o700)
    os.chmod(work, 0o700)                                # makedirs only applies the mode to the last folder
    try:
        try:
            counts, latest = table_counts(dsn)          # counted first; the dump below may include newer rows (see restore_drill)
        except psycopg.Error as exc:
            raise SystemExit(f'Cannot connect to the database: {str(exc).strip()[:300]}')
        dump_path = os.path.join(work, 'db.dump')
        log('dumping database ...')
        run_pg_dump(dsn, dump_path)
        counts_after, latest_after = table_counts(dsn)
        files = {}
        keys = sorted(k for k, _ in storage.list_objects(UPLOADS))
        log(f'copying {len(keys)} stored file(s) ...')
        skipped = []
        for key in keys:
            try:
                safe_key(key)
            except StorageError:                           # not a name the app ever creates (e.g. a stray .healthcheck file)
                skipped.append(key)
                continue
            data = storage.get(UPLOADS, key)
            if data is None:                               # deleted between listing and download
                continue
            dest = os.path.join(work, 'files', *key.split('/'))
            os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
            with open(dest, 'wb') as fh:
                fh.write(data)
            files[key] = {'bytes': len(data), 'sha256': sha256_bytes(data)}
        manifest = {
            'format': FORMAT,
            'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'migration': latest_after,
            'tables': counts_after,
            'tables_before_dump': counts,
            'dump': {'file': 'db.dump', 'bytes': os.path.getsize(dump_path), 'sha256': sha256_file(dump_path)},
            'files': files,
            'skipped_keys': skipped,
            'seconds': round(time.time() - started, 1),
        }
        with open(os.path.join(work, 'manifest.json'), 'w') as fh:
            json.dump(manifest, fh, indent=1, sort_keys=True)
        os.rename(work, final)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    if keep:
        old = sorted(d for d in os.listdir(out_dir) if d.startswith('ledger-') and not d.endswith('.partial'))
        for d in old[:-keep]:
            shutil.rmtree(os.path.join(out_dir, d), ignore_errors=True)
            log(f'pruned old backup {d}')
    if skipped:
        log(f'note: skipped {len(skipped)} object(s) with names the app never creates: {skipped[:3]}')
    total = sum(f['bytes'] for f in files.values())
    log(f'backup written: {final}  ({sum(counts_after.values())} rows in {len(counts_after)} tables, '
        f'{len(files)} files / {total / 1048576:.1f} MB, {manifest["seconds"]} s)')
    return final


def main(argv=None, storage=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=os.environ.get('BACKUP_DIR', 'backups'), help='parent folder (default ./backups)')
    ap.add_argument('--keep', type=int, default=None, help='keep only the newest N backups in --out')
    args = ap.parse_args(argv)
    dsn = os.environ.get('BACKUP_DATABASE_URL') or os.environ.get('DATABASE_URL')
    if not dsn:
        raise SystemExit('Set BACKUP_DATABASE_URL (direct / session-pooler string) or DATABASE_URL.')
    return backup(args.out, dsn, storage or build_storage(), keep=args.keep)


if __name__ == '__main__':
    main()
