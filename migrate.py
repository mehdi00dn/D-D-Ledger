"""Explicit schema migrations for Postgres.

    DATABASE_URL=postgresql://...  python migrate.py            # apply pending
    DATABASE_URL=postgresql://...  python migrate.py --status   # show state

Files in ./migrations are applied in filename order, each inside its own
transaction, and recorded in schema_migrations.  Never run automatically on a
serverless cold start -- run it as a deploy step (or paste the SQL into the
Supabase SQL editor).  A transaction-scoped advisory lock makes concurrent
runs safe, and it works through Supabase's transaction pooler.
"""
import os
import sys
import glob
import psycopg

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'migrations')
_LOCK_KEY = 727_727_001


def _files():
    return sorted(glob.glob(os.path.join(MIGRATIONS_DIR, '*.sql')))


_BOOKKEEPING = ('CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, '
                "applied_at TIMESTAMP(0) NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'))")


def status(dsn):
    """Read-only: which migrations are applied (creates nothing)."""
    with psycopg.connect(dsn, prepare_threshold=None) as conn:
        has_table = conn.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL").fetchone()[0]
        applied = {r[0] for r in conn.execute('SELECT version FROM schema_migrations')} if has_table else set()
    return [(os.path.basename(f), os.path.basename(f) in applied) for f in _files()]


def run(dsn):
    """Apply all pending migrations; returns the list of versions applied now.

    Each file runs in ONE transaction that starts by taking a transaction-scoped
    advisory lock -- before even the bookkeeping table is created -- so
    concurrent runners queue up instead of racing, and a failing file leaves
    nothing half-applied."""
    done = []
    for path in _files():
        version = os.path.basename(path)
        with psycopg.connect(dsn, prepare_threshold=None) as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (_LOCK_KEY,))
            conn.execute(_BOOKKEEPING)
            if conn.execute('SELECT 1 FROM schema_migrations WHERE version = %s', (version,)).fetchone():
                conn.rollback()
                continue
            with open(path, encoding='utf-8') as fh:
                conn.execute(fh.read())                 # whole file, same transaction
            conn.execute('INSERT INTO schema_migrations (version) VALUES (%s)', (version,))
            conn.commit()
            done.append(version)
    return done


if __name__ == '__main__':
    dsn = os.environ.get('DATABASE_URL')
    if not dsn:
        sys.exit('DATABASE_URL is not set')
    if '--status' in sys.argv:
        for name, ok in status(dsn):
            print(('applied  ' if ok else 'PENDING  ') + name)
    else:
        applied = run(dsn)
        print('applied: ' + ', '.join(applied) if applied else 'nothing to do - schema is up to date')
