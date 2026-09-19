"""Postgres data layer for Campaign Ledger.

A thin compatibility wrapper over psycopg 3 that lets the application keep its
existing call style, so ~160 query call sites did not have to be rewritten:

    db = get_db()
    row  = db.execute('SELECT * FROM users WHERE id = ?', (uid,)).fetchone()
    cur  = db.execute('INSERT INTO groups (name) VALUES (?)', (name,))
    new_id = cur.lastrowid
    db.commit(); db.close()

What the wrapper does for the app:
  * translates `?` placeholders to psycopg's `%s` (ignoring quoted literals)
  * returns rows as plain dicts (row['col'], dict(row) both work)
  * supplies `cursor.lastrowid` by appending `RETURNING id` to INSERTs
  * returns TIMESTAMP columns as 'YYYY-MM-DD HH:MM:SS' strings, like SQLite did
  * inside a request, get_db() hands out ONE connection per request; close() is
    a no-op there and Flask's teardown always releases it (rolling back anything
    uncommitted), so an error path can never leak a connection
  * outside a request (scripts, tests) get_db() opens a private connection and
    close() really closes it

Connection settings are serverless-friendly: point DATABASE_URL at Supabase's
*transaction pooler*, which does not support prepared statements, so they are
disabled (prepare_threshold=None).
"""
import os
import re

import psycopg
from psycopg.rows import dict_row
from psycopg.types.string import TextLoader
from flask import g, has_app_context

IntegrityError = psycopg.errors.IntegrityError
UniqueViolation = psycopg.errors.UniqueViolation

_INSERT_RE = re.compile(r'^\s*INSERT\s+INTO\b', re.IGNORECASE)
_RETURNING_RE = re.compile(r'\bRETURNING\b', re.IGNORECASE)


def _dsn():
    dsn = os.environ.get('DATABASE_URL')
    if not dsn:
        raise RuntimeError(
            'DATABASE_URL is not set. Point it at your Postgres/Supabase database '
            '(use the transaction-pooler connection string on Vercel).')
    if 'PROJECT_REF' in dsn:
        raise RuntimeError(
            'DATABASE_URL still contains the Supabase PROJECT_REF placeholder. '
            'Replace it with your real project reference in the transaction-pooler '
            'URL from Supabase, then redeploy Vercel.')
    return dsn


def _translate(sql):
    """`?` -> `%s` outside single-quoted literals; literal `%` -> `%%`."""
    out, in_str, i = [], False, 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            if in_str and i + 1 < len(sql) and sql[i + 1] == "'":   # escaped '' inside a literal
                out.append("''"); i += 2; continue
            in_str = not in_str
            out.append(ch)
        elif in_str:
            out.append('%%' if ch == '%' else ch)
        elif ch == '?':
            out.append('%s')
        elif ch == '%':
            out.append('%%')
        else:
            out.append(ch)
        i += 1
    return ''.join(out)


def _clean(params):
    """Postgres text cannot hold NUL (0x00), which SQLite silently allowed; a stray
    one in a pasted form field would otherwise turn into a 500.  Drop them."""
    if not params:
        return ()
    return tuple(p.replace('\x00', '') if isinstance(p, str) and '\x00' in p else p for p in params)


class Cursor:
    def __init__(self, raw, lastrowid=None):
        self._raw = raw
        self.lastrowid = lastrowid

    @property
    def rowcount(self):
        return self._raw.rowcount

    def fetchone(self):
        return self._raw.fetchone()

    def fetchall(self):
        return self._raw.fetchall()

    def __iter__(self):
        return iter(self._raw.fetchall())


class Connection:
    def __init__(self, raw, shared=False):
        self._raw = raw
        self._shared = shared

    @property
    def closed(self):
        return self._raw.closed

    def execute(self, sql, params=()):
        is_insert = bool(_INSERT_RE.match(sql)) and not _RETURNING_RE.search(sql)
        q = _translate(sql)
        if is_insert:
            q = q.rstrip().rstrip(';') + ' RETURNING id'
        cur = self._raw.execute(q, _clean(params))
        if is_insert:
            row = cur.fetchone()
            return Cursor(cur, lastrowid=row['id'] if row else None)
        return Cursor(cur)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        """No-op for the request-scoped connection (teardown releases it)."""
        if not self._shared:
            self.release()

    def release(self):
        if not self._raw.closed:
            try:
                self._raw.rollback()        # discard anything left uncommitted
            except Exception:
                pass
            self._raw.close()


def _connect():
    raw = psycopg.connect(_dsn(), row_factory=dict_row, prepare_threshold=None,
                          autocommit=False, connect_timeout=10)
    # keep TIMESTAMP columns as strings ('2026-09-19 07:49:21'), as SQLite returned them
    raw.adapters.register_loader('timestamp', TextLoader)
    return raw


def get_db():
    if has_app_context():
        conn = g.get('_db')
        if conn is None or conn.closed:
            conn = g._db = Connection(_connect(), shared=True)
        return conn
    return Connection(_connect(), shared=False)


def init_app(app):
    @app.teardown_appcontext
    def _release(_exc):
        conn = g.pop('_db', None)
        if conn is not None:
            conn.release()


def init_db():
    """Schema changes are applied explicitly (see migrate.py), never on a cold
    start.  For local development only, AUTO_MIGRATE=1 applies pending ones."""
    if os.environ.get('AUTO_MIGRATE') == '1':
        import migrate
        migrate.run(_dsn())
