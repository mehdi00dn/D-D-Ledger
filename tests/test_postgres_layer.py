"""Postgres-only checks: schema integrity, adapter behaviour, migrations, connection hygiene."""
import re, os, threading, uuid
import pytest
from conftest import BACKEND, PG_ADMIN, q

pytestmark = pytest.mark.skipif(BACKEND != 'postgres', reason='postgres backend only')


def _db():
    import database
    return database.get_db()


def test_translator_edge_cases():
    from database import _translate
    assert _translate("SELECT ? , 'why?' , '50%'") == "SELECT %s , 'why?' , '50%%'"
    assert _translate("SELECT 'it''s ?' , ?") == "SELECT 'it''s ?' , %s"
    assert _translate('SELECT 7 % 3') == 'SELECT 7 %% 3'


def test_lastrowid_rowcount_and_dict_rows(appmod):
    db = _db()
    try:
        cur = db.execute('INSERT INTO campaigns (name) VALUES (?)', ('LR',))
        assert isinstance(cur.lastrowid, int)
        assert db.execute('UPDATE campaigns SET name = ? WHERE id = ?', ('LR2', cur.lastrowid)).rowcount == 1
        row = db.execute('SELECT id, name FROM campaigns WHERE id = ?', (cur.lastrowid,)).fetchone()
        assert row['name'] == 'LR2' and dict(row) == {'id': cur.lastrowid, 'name': 'LR2'}
        assert [r['name'] for r in db.execute('SELECT name FROM campaigns WHERE id = ?', (cur.lastrowid,))] == ['LR2']  # iterable
        db.rollback()
    finally:
        db.close()


def test_timestamps_come_back_as_strings_like_sqlite(user):
    user.new_campaign()
    ts = q('SELECT created_at FROM campaigns ORDER BY id DESC LIMIT 1')[0]['created_at']
    assert isinstance(ts, str) and re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d', ts), ts


def test_foreign_keys_are_enforced(appmod):
    import database
    db = _db()
    try:
        with pytest.raises(database.IntegrityError):
            db.execute('INSERT INTO characters (name, campaign_id) VALUES (?, ?)', ('orphan', 987654321))
        db.rollback()
        with pytest.raises(database.IntegrityError):
            db.execute('INSERT INTO map_drawings (map_id, kind, data) VALUES (?, ?, ?)', (987654321, 'rect', '{}'))
        db.rollback()
    finally:
        db.close()


def test_deleting_user_keeps_characters_and_clears_created_by(make_user):
    u = make_user(); cid = u.new_campaign(); ch = u.new_character(cid)
    uid = q('SELECT id FROM users WHERE username = ?', u.name)[0]['id']
    db = _db()
    try:
        db.execute('DELETE FROM campaign_members WHERE user_id = ?', (uid,)); db.execute('DELETE FROM users WHERE id = ?', (uid,)); db.commit()
    finally:
        db.close()
    assert q('SELECT created_by FROM characters WHERE id = ?', ch)[0]['created_by'] is None


def test_ids_are_never_reused_like_autoincrement(user):
    a = user.new_campaign(); user.post(f'/campaigns/{a}/delete'); b = user.new_campaign()
    assert b > a


def test_nul_bytes_in_text_do_not_crash(camp):
    u, cid = camp
    r = u.post(f'/campaigns/{cid}/characters/new', data={'name': 'bad\x00name', 'max_hp': '5'}, content_type='multipart/form-data')
    assert r.status_code == 302


@pytest.mark.skipif(bool(os.environ.get('TEST_PG_APP')), reason='a pooler keeps idle server connections, so pg_stat_activity is not a leak signal there')
def test_no_connection_leak_across_error_paths(appmod, make_user):
    import psycopg
    u = make_user(); cid = u.new_campaign()
    for _ in range(25):
        u.get(f'/campaigns/{cid}/characters/999999')            # redirect path
        u.get('/campaigns/999999/characters')                   # 404 via abort
        u.json(f'/campaigns/{cid}/api/battle/999999/damage', {'amount': 1})   # explicit 404 + rollback
        u.json(f'/campaigns/{cid}/api/battle/add', {'character_id': 'not-a-number'})   # crashes -> 500
    dbname = os.environ['DATABASE_URL'].split('dbname=')[1].split()[0]
    with psycopg.connect(PG_ADMIN, autocommit=True) as c:
        n = c.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,)).fetchone()[0]
    assert n == 0, f'{n} connections left open'


def test_simultaneous_registration_of_one_username_never_500s(appmod):
    name = f'race{uuid.uuid4().hex[:8]}'; barrier = threading.Barrier(6); codes = []
    def go():
        c = appmod.app.test_client(); barrier.wait()
        codes.append(c.post('/register', data={'username': name, 'password': 'secret12', 'confirm': 'secret12'}).status_code)
    ts = [threading.Thread(target=go) for _ in range(6)]; [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(codes) == [200] * 5 + [302], codes
    assert len(q('SELECT id FROM users WHERE username = ?', name)) == 1


def test_migrations_are_idempotent_and_race_safe():
    import psycopg, migrate
    name = f'ledger_mig_{uuid.uuid4().hex[:8]}'
    with psycopg.connect(PG_ADMIN, autocommit=True) as c:
        c.execute(f'CREATE DATABASE {name}')
    dsn = PG_ADMIN.replace('dbname=postgres', f'dbname={name}')
    try:
        results = []
        ts = [threading.Thread(target=lambda: results.append(migrate.run(dsn))) for _ in range(4)]
        [t.start() for t in ts]; [t.join() for t in ts]
        applied_now = sorted(v for r in results for v in r)
        assert applied_now == sorted(os.path.basename(f) for f in migrate._files()), results   # each migration applied exactly once, no matter who won the race
        assert migrate.run(dsn) == []
        assert all(ok for _, ok in migrate.status(dsn))
        with psycopg.connect(dsn) as c:
            tables = {r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
        assert {'users', 'campaigns', 'campaign_members', 'groups', 'characters', 'character_sheets', 'battle_participants',
                'maps', 'map_drawings', 'map_pins', 'schema_migrations'} <= tables
    finally:
        with psycopg.connect(PG_ADMIN, autocommit=True) as c:
            c.execute(f'DROP DATABASE {name} WITH (FORCE)')


def test_every_table_has_row_level_security_enabled(appmod):
    """Supabase exposes public tables to its REST API unless RLS is on.  Every future
    migration that adds a table must enable RLS too -- this test enforces it."""
    rows = q("SELECT c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
             "WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity")
    assert rows == [], f'tables without RLS: {[r["t"] for r in rows]}'
