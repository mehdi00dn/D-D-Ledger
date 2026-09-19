"""Backend-agnostic regression suite for Campaign Ledger.

Run from the application directory:
    LEDGER_TEST_BACKEND=sqlite   pytest tests     # legacy build (baseline)
    LEDGER_TEST_BACKEND=postgres pytest tests     # migrated build

Everything goes through HTTP (Flask test client / real servers) so the same
assertions hold on both backends.  DB helpers use the app's own get_db().
"""
import io, os, re, sys, uuid, json, shutil, socket, subprocess, tempfile, time, atexit
import pytest

APP_DIR = os.getcwd()
sys.path.insert(0, APP_DIR)
BACKEND = os.environ.get('LEDGER_TEST_BACKEND', 'sqlite')
SECRET = 'test-secret-key-not-for-production'
os.environ['SECRET_KEY'] = SECRET
os.environ.pop('VERCEL', None)

_TMP = tempfile.mkdtemp(prefix='ledger-tests-')
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

PG_ADMIN = os.environ.get('TEST_PG_ADMIN', 'host=/tmp port=5433 user=postgres dbname=postgres')
PG_DBNAME = f'ledger_test_{os.getpid()}'


def _pg_url(dbname):
    base = PG_ADMIN.replace('dbname=postgres', f'dbname={dbname}')
    return base  # libpq key/value DSN


if BACKEND == 'sqlite':
    os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'main.db')
    os.environ['UPLOAD_DIR'] = os.path.join(_TMP, 'uploads')
else:
    import psycopg
    with psycopg.connect(PG_ADMIN, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS {PG_DBNAME}')
        c.execute(f'CREATE DATABASE {PG_DBNAME}')
    os.environ['DATABASE_URL'] = _pg_url(PG_DBNAME)
    os.environ['UPLOAD_DIR'] = os.path.join(_TMP, 'uploads')
    def _drop():
        try:
            with psycopg.connect(PG_ADMIN, autocommit=True) as c:
                c.execute(f'DROP DATABASE IF EXISTS {PG_DBNAME} WITH (FORCE)')
        except Exception:
            pass
    atexit.register(_drop)


@pytest.fixture(scope='session')
def appmod():
    import database
    if BACKEND == 'sqlite':
        database.init_db()
    else:
        import migrate
        migrate.run(os.environ['DATABASE_URL'])
    import app as appmod
    flask_app = appmod.app
    flask_app.config['TESTING'] = False           # real 500s, not re-raised exceptions
    flask_app.config['PROPAGATE_EXCEPTIONS'] = False
    return appmod


def q(sql, *params):
    """Run a read query through the app's own data layer."""
    import database
    db = database.get_db()
    try:
        return [dict(r) for r in db.execute(sql, params).fetchall()]
    finally:
        db.close()


def png_bytes(w=64, h=48, color=(120, 60, 60)):
    from PIL import Image
    buf = io.BytesIO(); Image.new('RGB', (w, h), color).save(buf, 'PNG'); return buf.getvalue()


class User:
    """A logged-in test user with its own cookie jar."""
    def __init__(self, appmod, name=None, password='secret12'):
        self.app = appmod.app
        self.name = name or f'u{uuid.uuid4().hex[:10]}'
        self.password = password
        self.c = self.app.test_client()
        r = self.c.post('/register', data={'username': self.name, 'password': password, 'confirm': password})
        assert r.status_code == 302, f'register failed: {r.status_code}'

    def fresh_client(self):
        c = self.app.test_client()
        r = c.post('/login', data={'username': self.name, 'password': self.password})
        assert r.status_code == 302
        return c

    def get(self, url, **kw): return self.c.get(url, **kw)
    def post(self, url, **kw): return self.c.post(url, **kw)
    def json(self, url, payload=None, method='post'):
        r = getattr(self.c, method)(url, json=payload if payload is not None else {})
        return r

    # --- helpers ---
    def new_campaign(self, name=None):
        name = name or f'Camp {uuid.uuid4().hex[:6]}'
        r = self.post('/campaigns/new', data={'name': name, 'description': 'd'})
        assert r.status_code == 302, r.status_code
        return int(re.search(r'/campaigns/(\d+)/', r.headers['Location']).group(1))

    def new_character(self, cid, name=None, **fields):
        name = name or f'Char {uuid.uuid4().hex[:6]}'
        data = {'name': name, 'level': '1', 'max_hp': '30', 'armor_class': '12'}
        data.update({k: str(v) for k, v in fields.items()})
        files = data.pop('_files', None)
        r = self.post(f'/campaigns/{cid}/characters/new', data=data, content_type='multipart/form-data')
        assert r.status_code == 302, f'character create -> {r.status_code}'
        rows = q('SELECT id FROM characters WHERE campaign_id = ? AND name = ? ORDER BY id DESC', cid, name)
        assert rows, 'character not persisted'
        return rows[0]['id']

    def new_group(self, cid, name=None, color='#336699'):
        name = name or f'Grp {uuid.uuid4().hex[:6]}'
        r = self.post(f'/campaigns/{cid}/groups/new', data={'name': name, 'color': color}, content_type='multipart/form-data')
        assert r.status_code == 302
        return q('SELECT id FROM groups WHERE campaign_id = ? AND name = ?', cid, name)[0]['id']

    def new_map(self, cid, name='Test Map', w=400, h=300):
        r = self.post(f'/campaigns/{cid}/maps/new',
                      data={'name': name, 'blank_width': str(w), 'blank_height': str(h)},
                      content_type='multipart/form-data')
        assert r.status_code == 302, r.status_code
        return int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))

    def add_to_battle(self, cid, char_id):
        r = self.json(f'/campaigns/{cid}/api/battle/add', {'character_id': char_id})
        assert r.status_code == 200
        rows = r.get_json()
        return [x for x in rows if x['character_id'] == char_id][-1]['id']

    def battle(self, cid):
        return self.get(f'/campaigns/{cid}/api/battle').get_json()


@pytest.fixture
def user(appmod): return User(appmod)

@pytest.fixture
def make_user(appmod):
    return lambda name=None: User(appmod, name)

@pytest.fixture
def camp(user):
    return user, user.new_campaign()


# ---- live servers (real processes) for cross-instance / browser tests ----
def _free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p

class Server:
    def __init__(self, env_extra):
        self.port = _free_port()
        env = dict(os.environ, **env_extra, PYTHONPATH=APP_DIR, LEDGER_PORT=str(self.port))
        self.proc = subprocess.Popen([sys.executable, os.path.join(APP_DIR, 'tests', 'serve.py')],
                                     cwd=APP_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.url = f'http://127.0.0.1:{self.port}'
        import requests
        for _ in range(80):
            try:
                if requests.get(self.url + '/login', timeout=1).status_code == 200: return
            except Exception: time.sleep(0.25)
        self.stop(); raise RuntimeError('server did not start')
    def stop(self):
        self.proc.terminate()
        try: self.proc.wait(5)
        except Exception: self.proc.kill()


@pytest.fixture(scope='session')
def shared_server(appmod):
    """One real server on the same database as the in-process app."""
    s = Server({}); yield s; s.stop()

@pytest.fixture
def two_instances(appmod):
    """Two independent app processes = two serverless instances.
    postgres: they share the one database (correct cloud behaviour).
    sqlite:   each gets its own private DB + uploads folder, exactly what a
              Vercel cold start does with /tmp -- reproduces the reported bug."""
    if BACKEND == 'sqlite':
        envs = [{'DATABASE_PATH': os.path.join(_TMP, f'inst{i}.db'), 'UPLOAD_DIR': os.path.join(_TMP, f'up{i}')} for i in (1, 2)]
        for e in envs:
            shutil.copy(os.environ['DATABASE_PATH'], e['DATABASE_PATH']) if os.path.exists(os.environ['DATABASE_PATH']) else None
    else:
        # shared database, but -- like Vercel's /tmp -- a PRIVATE uploads folder per instance,
        # so anything still stored on local disk shows up as a failure until Phase 2.
        envs = [{'UPLOAD_DIR': os.path.join(_TMP, f'up{i}')} for i in (1, 2)]
    a, b = Server(envs[0]), Server(envs[1])
    yield a, b
    a.stop(); b.stop()
