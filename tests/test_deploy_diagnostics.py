"""Deployment diagnostics: /healthz and the one-paste Supabase setup script."""
import os, uuid
import pytest, requests
from conftest import BACKEND, PG_ADMIN, Server, CsrfSession

pytestmark = pytest.mark.skipif(BACKEND != 'postgres', reason='postgres backend only')


def _fresh_db():
    import psycopg
    name = f'ledger_hc_{uuid.uuid4().hex[:8]}'
    with psycopg.connect(PG_ADMIN, autocommit=True) as c:
        c.execute(f'CREATE DATABASE {name}')
    return name, PG_ADMIN.replace('dbname=postgres', f'dbname={name}')


def _drop(name):
    import psycopg
    with psycopg.connect(PG_ADMIN, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)')


def test_healthz_ready(appmod):
    r = appmod.app.test_client().get('/healthz')
    assert r.status_code == 200 and r.get_json()['ok'] is True
    assert r.get_json()['migrations'] == ['0001_initial.sql', '0002_lock_down_data_api.sql', '0003_auth_throttle.sql',
                                           '0004_public_ids.sql']
    assert r.headers['Cache-Control'] == 'no-store'


def test_healthz_reports_missing_tables():
    name, dsn = _fresh_db()
    srv = Server({'DATABASE_URL': dsn, 'LEDGER_SKIP_MIGRATE': '1'})
    try:
        r = requests.get(srv.url + '/healthz')
        assert r.status_code == 503 and r.json()['stage'] == 'schema' and 'migrate' in r.json()['hint']
        # ...which is exactly why every action 500s while the login page still renders
        assert requests.get(srv.url + '/login').status_code == 200
        s = CsrfSession(srv.url); s.get(srv.url + '/login')
        assert s.post(srv.url + '/register', data={'username': 'validname', 'password': 'secret12', 'confirm': 'secret12'}).status_code == 500
    finally:
        srv.stop(); _drop(name)


@pytest.mark.parametrize('dsn_tail, expect_stage', [
    ('host=/tmp port=5433 user=postgres password=wrong-pw-SECRET123 dbname=no_such_db', 'connect'),
    ('host=127.0.0.1 port=1 user=postgres password=wrong-pw-SECRET123 dbname=postgres', 'connect'),
])
def test_healthz_reports_connection_failure_without_leaking_secrets(dsn_tail, expect_stage):
    old = os.environ['DATABASE_URL']
    os.environ['DATABASE_URL'] = dsn_tail
    try:
        import app as appmod
        r = appmod.app.test_client().get('/healthz')
    finally:
        os.environ['DATABASE_URL'] = old
    assert r.status_code == 503
    body = r.get_data(as_text=True)
    assert r.get_json()['stage'] == expect_stage and r.get_json()['hint']
    assert 'SECRET123' not in body and 'password=' not in body and '5433' not in body     # nothing sensitive echoed


def test_supabase_setup_script_equals_migrate_py():
    import psycopg, migrate
    name_a, dsn_a = _fresh_db(); name_b, dsn_b = _fresh_db()
    try:
        with psycopg.connect(dsn_a, prepare_threshold=None) as c:           # what the SQL editor does
            c.execute(migrate.bootstrap_sql())
        migrate.run(dsn_b)                                                   # what migrate.py does
        q = ("SELECT table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns "
             "WHERE table_schema='public' ORDER BY table_name, ordinal_position")
        with psycopg.connect(dsn_a) as a, psycopg.connect(dsn_b) as b:
            assert a.execute(q).fetchall() == b.execute(q).fetchall()
            idx = "SELECT indexname FROM pg_indexes WHERE schemaname='public' ORDER BY 1"
            assert a.execute(idx).fetchall() == b.execute(idx).fetchall()
        assert migrate.run(dsn_a) == []                                      # tracked: migrate.py sees it as applied
        assert all(ok for _, ok in migrate.status(dsn_a))
    finally:
        _drop(name_a); _drop(name_b)


def test_setup_script_is_all_or_nothing():
    import psycopg, migrate
    name, dsn = _fresh_db()
    try:
        broken = migrate.bootstrap_sql().replace('COMMIT;', 'SELECT 1/0; COMMIT;')
        with psycopg.connect(dsn, prepare_threshold=None) as c:
            with pytest.raises(psycopg.errors.DivisionByZero):
                c.execute(broken)
        with psycopg.connect(dsn) as c:
            assert c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0] == 0
    finally:
        _drop(name)


def test_healthz_reports_storage_problems_without_leaking_the_key(appmod):
    from conftest import FAKE
    from storage import SupabaseStorage
    import storage as storage_mod
    if FAKE is None:
        pytest.skip('needs the fake supabase server')
    real = storage_mod._instance
    try:
        for label, st, needle in [
            ('wrong key', SupabaseStorage(FAKE.url, 'sb_secret_LEAKME123', 'ledger-uploads', 'ledger-temp'), 'rejected'),
            ('missing bucket', SupabaseStorage(FAKE.url, FAKE.key, 'no-such-bucket', 'ledger-temp'), 'does not exist'),
            ('unreachable', SupabaseStorage('http://127.0.0.1:1', FAKE.key, 'a', 'b', timeout=(0.3, 0.3)), 'unreachable'),
        ]:
            storage_mod._instance = st
            r = appmod.app.test_client().get('/healthz')
            body = r.get_data(as_text=True)
            assert r.status_code == 503 and r.get_json()['stage'] == 'storage' and needle in r.get_json()['hint'], label
            assert 'LEAKME123' not in body and FAKE.key not in body, label
    finally:
        storage_mod._instance = real
