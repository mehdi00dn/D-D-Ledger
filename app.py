import os
import re
import uuid
import io
import json
import zipfile
import hmac
import secrets
from urllib.parse import urlparse
from datetime import datetime, timedelta
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, flash, send_file, g, abort, session
)
from werkzeug.security import generate_password_hash, check_password_hash
import nh3
import psycopg.errors as pgerr
from markupsafe import Markup
import security
from concurrent.futures import ThreadPoolExecutor
from flask import Response
import images
import importer
from storage import get_storage, StorageError, UPLOADS, TEMP
from database import get_db, init_db, init_app as init_database_app, UniqueViolation


app = Flask(__name__)


# ---------------- SECURITY CONFIGURATION ----------------
def _is_production():
    return bool(os.environ.get('VERCEL') or os.environ.get('FLASK_ENV') == 'production'
                or os.environ.get('REQUIRE_SECRET_KEY') == '1')


def _load_secret_key():
    """The key that signs login cookies.  In production it MUST be configured: a missing,
    short or well-known key would let anyone forge a login cookie, so we refuse to start."""
    key = os.environ.get('SECRET_KEY', '')
    if _is_production():
        if len(key) < 32 or key in ('dnd-campaign-manager-dev-key', 'change-me', 'secret', 'dev'):
            raise RuntimeError('SECRET_KEY is missing or too weak. Set it to a random string of at least 32 '
                               "characters: python -c \"import secrets; print(secrets.token_hex(32))\"")
        return key
    if not key:
        key = secrets.token_hex(32)          # local development: random per run (logins reset on restart)
        print('WARNING: SECRET_KEY is not set - using a random one for this run.')
    return key


app.secret_key = _load_secret_key()
_SECURE_COOKIES = _is_production() or os.environ.get('SECURE_COOKIES') == '1'
app.config.update(
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,                    # 16MB per form post
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=_SECURE_COOKIES,
    SESSION_COOKIE_NAME='__Host-ledger' if _SECURE_COOKIES else 'ledger_session',
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    # Header holding the real client IP when behind a trusted proxy (Vercel sets x-vercel-forwarded-for).
    CLIENT_IP_HEADER=os.environ.get('CLIENT_IP_HEADER') or ('x-vercel-forwarded-for' if os.environ.get('VERCEL') else None),
)
init_database_app(app)   # one DB connection per request, always released in teardown

CAMPAIGN_SETTING_PRESETS = ('Forgotten Realms', 'Eberron', 'Homebrew')
CAMPAIGN_STATUS_CHOICES = ('active', 'on_hold', 'completed', 'archived')
CAMPAIGN_ACCESS_CHOICES = ('private', 'invite_link')


def _resolve_campaign_setting(form):
    """The Setting/World dropdown collapses to a single TEXT column: one of
    the presets verbatim, or whatever the user typed once they picked
    Custom. Nothing to resolve if they left it on 'Not set'."""
    preset = form.get('setting_preset', '')
    if preset == '__custom__':
        return form.get('setting_custom', '').strip() or None
    return preset or None


def _split_campaign_setting(value):
    """Inverse of _resolve_campaign_setting, for re-populating the form:
    is the stored value one of the presets, or a custom one the DM typed?"""
    if not value:
        return '', ''
    if value in CAMPAIGN_SETTING_PRESETS:
        return value, ''
    return '__custom__', value


def _campaign_status_field(form):
    val = form.get('status', 'active')
    return val if val in CAMPAIGN_STATUS_CHOICES else 'active'


def _campaign_access_field(form):
    val = form.get('access_mode', 'private')
    return val if val in CAMPAIGN_ACCESS_CHOICES else 'private'


# ---------------- AUTH (Phase F) ----------------
# Full accounts: anyone can self-register, and every route requires a
# logged-in session except the handful that must stay reachable to get one.

PUBLIC_ENDPOINTS = {'login', 'register', 'static', 'healthz'}


# Which classification of connection failure gets which fixed, secret-free hint.
_CONNECT_HINTS = (
    ('tenant or user not found', 'The pooler does not recognise the username/host. Copy the Transaction-pooler '
                                 'string from the Supabase dashboard exactly (user is postgres.<project-ref>).'),
    ('password authentication failed', 'Wrong database password - or it contains characters such as @ / # % : '
                                       'that must be URL-encoded inside DATABASE_URL.'),
    ('could not translate host name', 'The host name in DATABASE_URL is wrong or malformed.'),
    ('invalid', 'DATABASE_URL is malformed (special characters in the password need URL-encoding).'),
    ('timeout', 'The database did not answer in time (wrong host/port, or the Supabase project is paused).'),
    ('connection refused', 'Nothing is listening at that host/port (use the pooler on port 6543).'),
)


@app.route('/healthz')
def healthz():
    """Deployment diagnostic: is the database reachable, and is the schema applied?
    Public, but returns only fixed hints - never connection strings or raw errors
    (those go to the server log)."""
    def reply(payload, code):
        resp = jsonify(payload)
        resp.status_code = code
        resp.headers['Cache-Control'] = 'no-store'
        return resp

    try:
        db = get_db()
        db.execute('SELECT 1').fetchone()
    except Exception as exc:                                  # noqa: BLE001 - diagnostic
        app.logger.error('healthz: database connection failed: %s: %s', type(exc).__name__, exc)
        text = str(exc).lower()
        hint = next((h for needle, h in _CONNECT_HINTS if needle in text),
                    'Could not connect. Check DATABASE_URL (Supabase Transaction-pooler string, port 6543) '
                    'and the Vercel logs.')
        return reply({'ok': False, 'stage': 'connect', 'error': type(exc).__name__, 'hint': hint}, 503)
    try:
        has_users = db.execute("SELECT to_regclass('public.users') IS NOT NULL AS ok").fetchone()['ok']
        if not has_users:
            return reply({'ok': False, 'stage': 'schema',
                          'hint': 'Connected, but the tables do not exist yet. Run "python migrate.py" '
                                  '(or paste supabase_setup.sql into the Supabase SQL editor).'}, 503)
        applied = [r['version'] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')] \
            if db.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL AS ok").fetchone()['ok'] else []
        storage_ok, storage_hint = get_storage().check()
        if not storage_ok:
            return reply({'ok': False, 'stage': 'storage', 'migrations': applied, 'hint': storage_hint}, 503)
        return reply({'ok': True, 'stage': 'ready', 'migrations': applied, 'storage': get_storage().name}, 200)
    except Exception as exc:                                  # noqa: BLE001 - diagnostic
        app.logger.error('healthz: schema check failed: %s: %s', type(exc).__name__, exc)
        return reply({'ok': False, 'stage': 'schema', 'error': type(exc).__name__,
                      'hint': 'Connected, but the schema check failed - see the Vercel logs.'}, 503)


def _csrf_token():
    token = session.get('csrf_token')
    if not token:
        token = session['csrf_token'] = secrets.token_urlsafe(32)
    return token


app.jinja_env.globals['csrf_token'] = _csrf_token
app.jinja_env.globals['csrf_field'] = lambda: Markup('<input type="hidden" name="csrf_token" value="%s">' % _csrf_token())


@app.before_request
def csrf_protect():
    """Every state-changing request must prove it came from one of our own pages:
    a per-session token (form field or X-CSRF-Token header), plus a same-origin check."""
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return
    origin = request.headers.get('Origin')
    if (origin and urlparse(origin).netloc != request.host) or request.headers.get('Sec-Fetch-Site') == 'cross-site':
        return _reject_request('Cross-site requests are not allowed.', 403)
    sent = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
    expected = session.get('csrf_token', '')
    if not expected or not sent or not hmac.compare_digest(str(sent), expected):
        return _reject_request('Your session expired or the form was not valid. Reload the page and try again.', 403)


def _wants_json():
    return request.is_json or '/api/' in request.path or request.path.startswith('/uploads/sign')


def _reject_request(message, status):
    if _wants_json():
        return jsonify({'error': message}), status
    return Response(message, status=status, mimetype='text/plain')


@app.before_request
def require_login():
    if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS:
        return
    if not session.get('user_id'):
        return redirect(url_for('login', next=request.path))


# Every ID in a URL must belong to the campaign in that URL (and children to their parent).
# Enforced here, once, for ALL routes -- a route cannot forget it.  A resource that does not
# exist at all is left to the route's own handling (stale links keep redirecting gracefully);
# one that exists in ANOTHER campaign is a hard 404.
_SCOPE_OWNER_SQL = {
    'map_id': 'SELECT campaign_id AS owner FROM maps WHERE id = ?',
    'char_id': 'SELECT campaign_id AS owner FROM characters WHERE id = ?',
    'group_id': 'SELECT campaign_id AS owner FROM groups WHERE id = ?',
    'pid': 'SELECT c.campaign_id AS owner FROM battle_participants bp JOIN characters c ON c.id = bp.character_id WHERE bp.id = ?',
}
_SCOPE_PARENT_SQL = {            # arg -> (SQL returning the parent id, name of the parent arg)
    'drawing_id': ('SELECT map_id AS parent FROM map_drawings WHERE id = ?', 'map_id'),
    'pin_id': ('SELECT map_id AS parent FROM map_pins WHERE id = ?', 'map_id'),
    'sheet_id': ('SELECT character_id AS parent FROM character_sheets WHERE id = ?', 'char_id'),
}
# URL arguments the policy knows about.  tests/test_authorization_matrix.py fails if a route
# introduces an argument that is not listed here (so a new kind of ID cannot slip through).
SCOPED_URL_ARGS = {'campaign_id', 'user_id', 'filename', 'token'} | set(_SCOPE_OWNER_SQL) | set(_SCOPE_PARENT_SQL)


@app.before_request
def enforce_resource_scope():
    args = request.view_args or {}
    campaign_id = getattr(g, 'campaign_id', None)     # set (and popped from view_args) by the URL preprocessor
    if campaign_id is None:
        return
    db = get_db()
    for name, sql in _SCOPE_OWNER_SQL.items():
        if name in args:
            row = db.execute(sql, (args[name],)).fetchone()
            if row is not None and row['owner'] != campaign_id:
                abort(404)
    for name, (sql, parent_name) in _SCOPE_PARENT_SQL.items():
        if name in args:
            row = db.execute(sql, (args[name],)).fetchone()
            if row is not None and row['parent'] != args.get(parent_name):
                abort(404)


def _claim_orphaned_campaigns(db, user_id):
    """When auth is introduced onto a database that already has campaigns
    from before accounts existed, those campaigns have zero rows in
    campaign_members. Without this, they'd be permanently unreachable once
    campaign_access_required starts requiring real membership. The first
    person to ever register becomes owner of every such campaign."""
    orphaned = db.execute('''
        SELECT c.id FROM campaigns c
        LEFT JOIN campaign_members cm ON cm.campaign_id = c.id
        WHERE cm.id IS NULL
    ''').fetchall()
    for row in orphaned:
        db.execute('INSERT INTO campaign_members (campaign_id, user_id, role, status) VALUES (?, ?, ?, ?)',
                   (row['id'], user_id, 'owner', 'dm'))


DUMMY_PASSWORD_HASH = generate_password_hash('not-a-real-password')   # equalises login timing for unknown users


def client_ip():
    header = app.config.get('CLIENT_IP_HEADER')
    raw = request.headers.get(header, '') if header else ''
    ip = raw.split(',')[0].strip()[:45]
    return ip or request.remote_addr or 'unknown'


def _wait_message(seconds):
    minutes = max(1, (seconds + 59) // 60)
    return 'Too many attempts. Please try again in %d minute%s.' % (minutes, '' if minutes == 1 else 's')


def _safe_next(path):
    """Only ever redirect to a path on this site (blocks //host, /\\host, scheme URLs, control chars)."""
    if not path or not isinstance(path, str) or len(path) > 2000 or '\\' in path or any(ord(c) < 32 for c in path):
        return None
    parts = urlparse(path)
    if parts.scheme or parts.netloc or not path.startswith('/') or path.startswith('//'):
        return None
    return path


def _start_session(user_id, username, remember=True):
    """Fresh session on login: drops anything an earlier visitor of this browser left behind.
    remember=True issues a persistent cookie (PERMANENT_SESSION_LIFETIME); False makes it a
    plain session cookie that the browser drops when it's closed."""
    session.clear()
    session['user_id'] = user_id
    session['username'] = username
    session['csrf_token'] = secrets.token_urlsafe(32)
    session.permanent = remember


def _username_error(username):
    if not username:
        return 'Username and password are required.'
    if not (3 <= len(username) <= 32) or username != username.strip() or not username.isprintable() or any(c in username for c in '<>'):
        return 'Username must be 3-32 characters (letters, numbers, spaces and symbols; no < or >).'
    return None


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        ip = client_ip()

        db = get_db()
        wait = security.register_blocked_for(db, app.secret_key, ip)
        if wait:
            db.close()
            return render_template('register.html', error=_wait_message(wait), username=username), 429
        security.record_registration(db, app.secret_key, ip)
        db.commit()

        error = None
        if not username or not password:
            error = 'Username and password are required.'
        elif _username_error(username):
            error = _username_error(username)
        elif password != confirm:
            error = 'Passwords do not match.'
        elif len(password) < 8:
            error = 'Password must be at least 8 characters.'
        elif len(password) > 128:
            error = 'Password must be at most 128 characters.'

        if error is None and db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone():
            error = 'That username is already taken.'
        if error:
            db.close()
            return render_template('register.html', error=error, username=username)

        is_first_user = db.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n'] == 0
        try:
            cur = db.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)',
                              (username, generate_password_hash(password)))
        except UniqueViolation:      # two people registering the same name at once
            db.rollback()
            db.close()
            return render_template('register.html', error='That username is already taken.', username=username)
        user_id = cur.lastrowid
        if is_first_user:
            _claim_orphaned_campaigns(db, user_id)
        db.commit()
        db.close()

        _start_session(user_id, username)
        return redirect(url_for('campaigns_list'))
    return render_template('register.html', error=None, username='')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()[:200]
        password = request.form.get('password', '')[:1024]
        next_value = request.form.get('next', '')
        ip = client_ip()
        db = get_db()

        wait = security.login_blocked_for(db, app.secret_key, username, ip)
        if wait:
            db.close()
            return render_template('login.html', error=_wait_message(wait), username=username, next=next_value), 429

        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        stored_hash = (user['password_hash'] if user else None) or DUMMY_PASSWORD_HASH     # always do one hash check
        password_ok = check_password_hash(stored_hash, password)
        if user is None or not user['password_hash'] or not password_ok:
            security.record_login_failure(db, app.secret_key, username, ip)
            db.commit()
            db.close()
            return render_template('login.html', error='Incorrect username or password.', username=username, next=next_value)

        security.clear_login_failures(db, app.secret_key, username, ip)
        db.commit()
        db.close()
        _start_session(user['id'], user['username'], remember=bool(request.form.get('remember')))
        return redirect(_safe_next(next_value or request.args.get('next')) or url_for('campaigns_list'))
    return render_template('login.html', error=None, username='', next=request.args.get('next', ''))


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))


# ---------------- CAMPAIGN SCOPING (Phase C) ----------------
# Every data-bearing route lives under /campaigns/<campaign_id>/... . These
# three pieces work together so that (a) view functions don't each need a
# campaign_id parameter, and (b) none of the existing url_for() calls in this
# file or in the templates needed to change to pass campaign_id explicitly.

@app.url_value_preprocessor
def _pull_campaign_id(endpoint, values):
    """Pull campaign_id out of the URL and into g before the view function
    is called, so view function signatures stay exactly as they were."""
    if values is not None and 'campaign_id' in values:
        g.campaign_id = values.pop('campaign_id')


@app.url_defaults
def _inject_campaign_id(endpoint, values):
    """Mirror of the preprocessor above: when url_for() targets an endpoint
    that needs campaign_id and none was passed explicitly, fill in the
    current request's campaign_id automatically."""
    if 'campaign_id' in values:
        return
    if app.url_map.is_endpoint_expecting(endpoint, 'campaign_id'):
        campaign_id = getattr(g, 'campaign_id', None)
        if campaign_id is not None:
            values['campaign_id'] = campaign_id


def campaign_access_required(view_func):
    """Confirms the campaign in the URL exists AND the logged-in user is
    one of its members, exposing the campaign as g.campaign, their
    ownership role as g.campaign_role ('owner'/'member'), and their in-game
    status as g.campaign_status ('dm'/'player') plus the g.is_dm shortcut.
    This replaces Phase C/D's existence-only stub now that real accounts
    exist -- a valid campaign_id alone is no longer enough."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        campaign_id = getattr(g, 'campaign_id', None)
        if campaign_id is None:
            abort(404)
        db = get_db()
        campaign = db.execute('SELECT * FROM campaigns WHERE id = ?', (campaign_id,)).fetchone()
        if campaign is None:
            db.close()
            abort(404)
        member = db.execute(
            'SELECT role, status FROM campaign_members WHERE campaign_id = ? AND user_id = ?',
            (campaign_id, session['user_id'])
        ).fetchone()
        db.close()
        if member is None:
            abort(404)  # don't reveal a campaign's existence to non-members
        g.campaign = campaign
        g.campaign_role = member['role']
        g.campaign_status = member['status'] or 'player'
        g.is_dm = g.campaign_status == 'dm'
        session['last_campaign_id'] = campaign_id
        return view_func(*args, **kwargs)
    return wrapped


def dm_required(view_func):
    """Stacks on top of campaign_access_required. Rejects the request with
    403 unless the logged-in member's in-game status is Dungeon Master --
    used on every battle- and map-mutating route so a Player's access is
    enforced server-side, not just hidden in the UI."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not getattr(g, 'is_dm', False):
            abort(403)
        return view_func(*args, **kwargs)
    return wrapped


# ---------------- CAMPAIGNS (Phase D) ----------------

@app.route('/campaigns')
def campaigns_list():
    db = get_db()
    campaigns = db.execute('''
        SELECT c.*, cm.role AS my_role,
               (SELECT COUNT(*) FROM characters WHERE campaign_id = c.id AND is_temp_familiar = 0) AS character_count,
               (SELECT COUNT(*) FROM groups WHERE campaign_id = c.id) AS group_count,
               (SELECT COUNT(*) FROM maps WHERE campaign_id = c.id) AS map_count
        FROM campaigns c
        JOIN campaign_members cm ON cm.campaign_id = c.id AND cm.user_id = ?
        ORDER BY c.created_at DESC
    ''', (session['user_id'],)).fetchall()
    db.close()
    return render_template('campaigns_list.html', campaigns=campaigns)


@app.route('/campaigns/new', methods=['GET', 'POST'])
def campaign_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip() or 'Untitled Campaign'
        description = request.form.get('description', '').strip()
        avatar_path = save_upload_field('avatar', 'avatars')
        setting = _resolve_campaign_setting(request.form)
        status = _campaign_status_field(request.form)
        access_mode = _campaign_access_field(request.form)
        db = get_db()
        cur = db.execute('''
            INSERT INTO campaigns (name, description, avatar_path, setting, status, access_mode)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (name, description, avatar_path, setting, status, access_mode))
        campaign_id = cur.lastrowid
        db.execute('INSERT INTO campaign_members (campaign_id, user_id, role, status) VALUES (?, ?, ?, ?)',
                   (campaign_id, session['user_id'], 'owner', 'dm'))
        db.commit()
        db.close()
        session['last_campaign_id'] = campaign_id
        return redirect(url_for('characters_list', campaign_id=campaign_id))
    setting_preset, setting_custom = _split_campaign_setting(None)
    return render_template('campaign_form.html', campaign=None, members=None,
                            setting_preset=setting_preset, setting_custom=setting_custom)


@app.route('/campaigns/<int:campaign_id>/edit', methods=['GET', 'POST'])
@campaign_access_required
def campaign_edit():
    db = get_db()
    if request.method == 'POST':
        if g.campaign_role != 'owner':
            abort(403)
        name = request.form.get('name', '').strip() or 'Untitled Campaign'
        description = request.form.get('description', '').strip()
        avatar_path = save_upload_field('avatar', 'avatars')
        remove_avatar = request.form.get('remove_avatar') == '1'
        if avatar_path:
            old = db.execute('SELECT avatar_path FROM campaigns WHERE id = ?', (g.campaign_id,)).fetchone()
            if old and old['avatar_path']:
                delete_upload(old['avatar_path'])
            db.execute('UPDATE campaigns SET avatar_path = ? WHERE id = ?', (avatar_path, g.campaign_id))
        elif remove_avatar:
            old = db.execute('SELECT avatar_path FROM campaigns WHERE id = ?', (g.campaign_id,)).fetchone()
            if old and old['avatar_path']:
                delete_upload(old['avatar_path'])
            db.execute('UPDATE campaigns SET avatar_path = NULL WHERE id = ?', (g.campaign_id,))
        setting = _resolve_campaign_setting(request.form)
        status = _campaign_status_field(request.form)
        access_mode = _campaign_access_field(request.form)
        db.execute('''
            UPDATE campaigns SET name = ?, description = ?, setting = ?, status = ?, access_mode = ?
            WHERE id = ?
        ''', (name, description, setting, status, access_mode, g.campaign_id))
        db.commit()
        db.close()
        return redirect(url_for('campaigns_list'))
    campaign = g.campaign
    members = db.execute('''
        SELECT u.id, u.username, cm.role, cm.status
        FROM campaign_members cm JOIN users u ON cm.user_id = u.id
        WHERE cm.campaign_id = ?
        ORDER BY cm.role DESC, lower(u.username) ASC
    ''', (g.campaign_id,)).fetchall()
    db.close()
    setting_preset, setting_custom = _split_campaign_setting(campaign['setting'])
    return render_template('campaign_form.html', campaign=campaign, members=members,
                            setting_preset=setting_preset, setting_custom=setting_custom)


@app.route('/campaigns/<int:campaign_id>/members/add', methods=['POST'])
@campaign_access_required
def campaign_member_add():
    if g.campaign_role != 'owner':
        abort(403)
    username = request.form.get('username', '').strip()
    status = request.form.get('status') if request.form.get('status') in ('dm', 'player') else 'player'
    db = get_db()
    user = db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
    if user:
        existing = db.execute('SELECT id FROM campaign_members WHERE campaign_id = ? AND user_id = ?',
                               (g.campaign_id, user['id'])).fetchone()
        if not existing:
            # New invites always join as a plain member -- ownership is granted
            # separately (see campaign_member_make_owner), never at invite time.
            db.execute('INSERT INTO campaign_members (campaign_id, user_id, role, status) VALUES (?, ?, ?, ?)',
                       (g.campaign_id, user['id'], 'member', status))
            db.commit()
    db.close()
    return redirect(url_for('campaign_edit', campaign_id=g.campaign_id))


@app.route('/campaigns/<int:campaign_id>/members/<int:user_id>/status', methods=['POST'])
@campaign_access_required
def campaign_member_status(user_id):
    """Owner-only: set a member's DM/Player status. The owner can change
    anyone's status at any time, including their own."""
    if g.campaign_role != 'owner':
        abort(403)
    status = request.form.get('status')
    if status not in ('dm', 'player'):
        abort(400)
    db = get_db()
    db.execute('UPDATE campaign_members SET status = ? WHERE campaign_id = ? AND user_id = ?',
               (status, g.campaign_id, user_id))
    db.commit()
    db.close()
    return redirect(url_for('campaign_edit', campaign_id=g.campaign_id))


@app.route('/campaigns/<int:campaign_id>/members/<int:user_id>/make-owner', methods=['POST'])
@campaign_access_required
def campaign_member_make_owner(user_id):
    """Owner-only: grant another member co-ownership. Owners default to
    Dungeon Master status, matching how the very first owner is set up."""
    if g.campaign_role != 'owner':
        abort(403)
    db = get_db()
    db.execute("UPDATE campaign_members SET role = 'owner', status = 'dm' WHERE campaign_id = ? AND user_id = ?",
               (g.campaign_id, user_id))
    db.commit()
    db.close()
    return redirect(url_for('campaign_edit', campaign_id=g.campaign_id))


@app.route('/campaigns/<int:campaign_id>/leave', methods=['POST'])
@campaign_access_required
def campaign_leave():
    """Any member can leave a campaign on their own. The sole owner can't
    leave until they've promoted someone else to Owner first -- the edit
    page only shows this button when that's actually possible, but the
    check is repeated here since it's the only thing that matters."""
    db = get_db()
    if g.campaign_role == 'owner':
        owner_count = db.execute(
            "SELECT COUNT(*) AS n FROM campaign_members WHERE campaign_id = ? AND role = 'owner'", (g.campaign_id,)
        ).fetchone()['n']
        if owner_count <= 1:
            db.close()
            return redirect(url_for('campaign_edit', campaign_id=g.campaign_id))
    db.execute('DELETE FROM campaign_members WHERE campaign_id = ? AND user_id = ?', (g.campaign_id, session['user_id']))
    db.commit()
    db.close()
    if session.get('last_campaign_id') == g.campaign_id:
        session.pop('last_campaign_id', None)
    return redirect(url_for('campaigns_list'))


@app.route('/campaigns/<int:campaign_id>/members/<int:user_id>/remove', methods=['POST'])
@campaign_access_required
def campaign_member_remove(user_id):
    if g.campaign_role != 'owner':
        abort(403)
    db = get_db()
    owner_count = db.execute(
        "SELECT COUNT(*) AS n FROM campaign_members WHERE campaign_id = ? AND role = 'owner'", (g.campaign_id,)
    ).fetchone()['n']
    target = db.execute('SELECT role FROM campaign_members WHERE campaign_id = ? AND user_id = ?',
                         (g.campaign_id, user_id)).fetchone()
    # Never remove the last owner -- that would leave the campaign with no
    # one able to manage it.
    if target and not (target['role'] == 'owner' and owner_count <= 1):
        db.execute('DELETE FROM campaign_members WHERE campaign_id = ? AND user_id = ?', (g.campaign_id, user_id))
        db.commit()
    db.close()
    return redirect(url_for('campaign_edit', campaign_id=g.campaign_id))


@app.route('/campaigns/<int:campaign_id>/delete', methods=['POST'])
@campaign_access_required
def campaign_delete():
    if g.campaign_role != 'owner':
        abort(403)
    db = get_db()

    # Collect every uploaded file this campaign owns before touching any
    # rows (once the rows are gone we could no longer find the files). The
    # explicit deletes below are kept even though Postgres enforces the same
    # ON DELETE CASCADE constraints -- belt and braces.
    avatar_paths = [r['avatar_path'] for r in db.execute(
        'SELECT avatar_path FROM characters WHERE campaign_id = ? AND avatar_path IS NOT NULL', (g.campaign_id,)
    )]
    avatar_paths += [r['avatar_path'] for r in db.execute(
        'SELECT avatar_path FROM groups WHERE campaign_id = ? AND avatar_path IS NOT NULL', (g.campaign_id,)
    )]
    if g.campaign['avatar_path']:
        avatar_paths.append(g.campaign['avatar_path'])
    sheet_paths = [r['image_path'] for r in db.execute('''
        SELECT cs.image_path FROM character_sheets cs
        JOIN characters c ON cs.character_id = c.id
        WHERE c.campaign_id = ?
    ''', (g.campaign_id,))]
    map_paths = [r['image_path'] for r in db.execute(
        'SELECT image_path FROM maps WHERE campaign_id = ? AND image_path IS NOT NULL', (g.campaign_id,)
    )]

    db.execute('DELETE FROM battle_participants WHERE character_id IN (SELECT id FROM characters WHERE campaign_id = ?)', (g.campaign_id,))
    db.execute('DELETE FROM character_sheets WHERE character_id IN (SELECT id FROM characters WHERE campaign_id = ?)', (g.campaign_id,))
    db.execute('DELETE FROM map_drawings WHERE map_id IN (SELECT id FROM maps WHERE campaign_id = ?)', (g.campaign_id,))
    db.execute('DELETE FROM map_pins WHERE map_id IN (SELECT id FROM maps WHERE campaign_id = ?)', (g.campaign_id,))
    db.execute('DELETE FROM characters WHERE campaign_id = ?', (g.campaign_id,))
    db.execute('DELETE FROM groups WHERE campaign_id = ?', (g.campaign_id,))
    db.execute('DELETE FROM maps WHERE campaign_id = ?', (g.campaign_id,))
    db.execute('DELETE FROM campaign_members WHERE campaign_id = ?', (g.campaign_id,))
    db.execute('DELETE FROM campaigns WHERE id = ?', (g.campaign_id,))
    db.commit()
    db.close()

    delete_uploads(avatar_paths + sheet_paths + map_paths)

    if session.get('last_campaign_id') == g.campaign_id:
        session.pop('last_campaign_id', None)

    return redirect(url_for('campaigns_list'))


# ---------------- CHARACTER NOTES (multi-box rich text) ----------------
# Notes are stored in characters.notes as a JSON array of small HTML fragments,
# one per note box, produced by the note editor in character_form.html.
# Characters saved before this feature existed have notes as a plain string,
# which is treated as a single legacy block wherever notes are read back.

_NOTE_ALLOWED_TAGS = {'b', 'strong', 'i', 'em', 'ul', 'li', 'br', 'div'}
_NOTE_STRIP_TAGS_RE = re.compile(r'<[^>]+>')


def _sanitize_note_fragment(fragment):
    """Reduce one note box to a tiny whitelist of formatting tags with NO attributes.
    Uses a real HTML parser (nh3/ammonia) - the old regex let an unterminated tag such
    as '<img src=x onerror=...' through."""
    return nh3.clean(fragment or '', tags=_NOTE_ALLOWED_TAGS, attributes={}, strip_comments=True, link_rel=None)


def _escape_text(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def sanitize_notes_payload(raw):
    """Sanitize the notes field (from the editor OR an imported file) before storing it."""
    if not raw:
        return ''
    raw = raw.replace('\x00', '') if isinstance(raw, str) else str(raw)
    try:
        blocks = json.loads(raw)
    except (ValueError, TypeError):
        # Plain text (legacy / hand-made): store it as one escaped block, never raw.
        return json.dumps([_escape_text(raw).replace('\n', '<br>')])
    if not isinstance(blocks, list):
        return ''
    cleaned = [_sanitize_note_fragment(b) for b in blocks if isinstance(b, str)]
    cleaned = [b for b in cleaned if b and b not in ('<br>', '<div><br></div>')]
    return json.dumps(cleaned)


def notes_parse_blocks(raw):
    """Parse characters.notes into a list of trusted HTML fragments, one per
    note box (used to prefill the note editor when editing a character)."""
    if not raw:
        return []
    try:
        blocks = json.loads(raw)
        if isinstance(blocks, list):
            return [c for c in (_sanitize_note_fragment(b) for b in blocks if isinstance(b, str)) if c]
    except (ValueError, TypeError):
        pass
    # Legacy plain-text notes: escape and turn line breaks into <br> so each
    # line the user typed still shows up as its own line.
    escaped = raw.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return [escaped.replace('\n', '<br>')]


def notes_to_html(raw):
    """Render stored notes as HTML for detail views (battle/map popups)."""
    blocks = notes_parse_blocks(raw)
    if not blocks:
        return ''
    return ''.join(f'<div class="note-block-text">{b}</div>' for b in blocks)


def notes_plain_preview(raw):
    """Flatten stored notes to plain text for compact card previews."""
    blocks = notes_parse_blocks(raw)
    if not blocks:
        return ''
    joined = re.sub(r'<(br|/div|/li|/p)\s*/?>', ' ', ' '.join(blocks), flags=re.I)
    joined = _NOTE_STRIP_TAGS_RE.sub('', joined)
    joined = joined.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
    return re.sub(r'\s+', ' ', joined).strip()


app.jinja_env.filters['notes_preview'] = notes_plain_preview


# ---------------- UPLOADS ----------------
# Every image goes through images.process_image (validate + re-encode) and is stored
# through the storage layer (local disk in development, Supabase Storage in the cloud).
# The database keeps the same relative keys as always (e.g. 'avatars/<uuid>.png').

_DIRECT_KEY_RE = re.compile(r'^tmp/(\d+)/([0-9a-f]{32})$')
_UPLOAD_KEY_RE = re.compile(r'^(avatars|sheets|maps)/[0-9A-Za-z_\-]{1,80}\.(png|jpe?g|gif|webp)$')
DIRECT_INLINE_LIMIT = 3_400_000      # bytes of files a form may carry itself (Vercel body cap is 4.5 MB)
UPLOAD_PURPOSES = {                  # purpose -> (max bytes, allowed content types)
    'avatar': (images.KIND_MAX_BYTES['avatars'], images.ALLOWED_CONTENT_TYPES),
    'sheet': (images.KIND_MAX_BYTES['sheets'], images.ALLOWED_CONTENT_TYPES),
    'map': (images.KIND_MAX_BYTES['maps'], images.ALLOWED_CONTENT_TYPES),
    'import': (importer.MAX_ZIP_BYTES, {'application/zip', 'application/x-zip-compressed', 'application/octet-stream'}),
}


class Stored:
    def __init__(self, path, width, height):
        self.path, self.width, self.height = path, width, height


def _direct_key_ok(key):
    m = _DIRECT_KEY_RE.match(key or '')
    return bool(m) and int(m.group(1)) == session.get('user_id')


def _read_upload_source(file_storage=None, direct_key=None):
    """Bytes of an upload that either came with the form or was staged by the browser
    straight into storage.  Returns (data, temp_key_to_delete)."""
    if direct_key:
        if not _direct_key_ok(direct_key):
            return None, None
        return get_storage().get(TEMP, direct_key), direct_key
    if not file_storage or not file_storage.filename:
        return None, None
    return file_storage.read(), None


def _store_image(data, subfolder, cap_dimension=None):
    """Validate + store; returns Stored or None when the file is not an acceptable image."""
    try:
        p = images.process_image(data, subfolder, cap_dimension)
    except images.ImageError:
        return None
    key = f"{subfolder}/{uuid.uuid4().hex}.{p.ext}"
    get_storage().put(UPLOADS, key, p.data, p.content_type)
    return Stored(key, p.width, p.height)


def store_upload(field, subfolder, cap_dimension=None):
    """One image from form field `field` (a normal file part, or `<field>__key` when the
    browser uploaded it directly to storage)."""
    data, temp = _read_upload_source(request.files.get(field), request.form.get(field + '__key'))
    try:
        return _store_image(data, subfolder, cap_dimension) if data else None
    finally:
        if temp:
            get_storage().delete(TEMP, temp)


def store_uploads(field, subfolder):
    """Every image submitted under `field` (multi-file input), in order."""
    sources = [(f, None) for f in request.files.getlist(field) if f and f.filename]
    sources += [(None, k) for k in request.form.getlist(field + '__key')]
    out = []
    for f, key in sources:
        data, temp = _read_upload_source(f, key)
        try:
            if data:
                stored = _store_image(data, subfolder)
                if stored:
                    out.append(stored)
        finally:
            if temp:
                get_storage().delete(TEMP, temp)
    return out


def save_upload_field(field, subfolder):
    stored = store_upload(field, subfolder)
    return stored.path if stored else None


def delete_upload(rel_path):
    if rel_path:
        try:
            get_storage().delete(UPLOADS, rel_path)
        except StorageError:
            pass                       # an orphaned file is better than a failed delete


def delete_uploads(paths):
    paths = [p for p in paths if p]
    if paths:
        try:
            get_storage().delete_many(UPLOADS, paths)
        except StorageError:
            pass


def _generate_blank_canvas(width, height):
    """Create a plain white PNG for maps without a background image; returns its key."""
    p = images.blank_canvas(width, height)
    key = f"maps/{uuid.uuid4().hex}.png"
    get_storage().put(UPLOADS, key, p.data, p.content_type)
    return key


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """Serve a stored image.  Login required (images are no longer world-readable);
    keys are random, immutable, so browsers may cache them forever."""
    m = _UPLOAD_KEY_RE.match(filename)
    if not m:
        abort(404)
    data = get_storage().get(UPLOADS, filename)
    if data is None:
        abort(404)
    resp = Response(data, mimetype=images.EXT_TO_CONTENT_TYPE[m.group(2).lower()])
    resp.headers['Cache-Control'] = 'private, max-age=31536000, immutable'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp


@app.route('/uploads/sign', methods=['POST'])
def upload_sign():
    """Hand the browser a one-off URL to upload a large file straight to storage
    (bypassing the app server's request-size limit).  The server chooses the object
    key, so the browser can only ever write to its own private staging area."""
    data = request.get_json(silent=True) or {}
    purpose = data.get('purpose')
    if purpose not in UPLOAD_PURPOSES:
        return jsonify({'error': 'unknown upload type'}), 400
    max_bytes, allowed_types = UPLOAD_PURPOSES[purpose]
    try:
        size = int(data.get('size', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid size'}), 400
    if size <= 0:
        return jsonify({'error': 'empty file'}), 400
    if size > max_bytes:
        return jsonify({'error': 'file too large (limit %d MB)' % (max_bytes // (1024 * 1024))}), 413
    if (data.get('content_type') or 'application/octet-stream') not in allowed_types:
        return jsonify({'error': 'unsupported file type'}), 415
    key = f"tmp/{session['user_id']}/{uuid.uuid4().hex}"
    target = get_storage().create_upload_target(TEMP, key, data.get('content_type'), max_bytes)
    return jsonify({'key': key, 'upload': target})


@app.route('/_direct-upload/<token>', methods=['PUT'])
def local_direct_upload(token):
    """Same-origin stand-in for a storage signed-upload URL (local backend only)."""
    st = get_storage()
    info = st.read_upload_token(token) if hasattr(st, 'read_upload_token') else None
    if not info:
        abort(404)
    request.max_content_length = int(info['max']) + 1        # this route's own cap, not the app-wide form cap
    body = request.get_data(cache=False)
    if len(body) > int(info['max']):
        abort(413)
    st.put(info['b'], info['k'], body)
    return jsonify({'ok': True})


@app.route('/_download/<token>')
def local_download(token):
    st = get_storage()
    info = st.read_download_token(token) if hasattr(st, 'read_download_token') else None
    data = st.get(info['b'], info['k']) if info else None
    if data is None:
        abort(404)
    resp = Response(data, mimetype='application/zip')
    resp.headers['Content-Disposition'] = 'attachment; filename="%s"' % (info.get('n') or 'download.zip')
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _csp():
    connect = ["'self'"]
    supa = os.environ.get('SUPABASE_URL', '')
    if supa and os.environ.get('STORAGE_BACKEND', 'supabase') == 'supabase':
        p = urlparse(supa)
        if p.scheme and p.netloc:
            connect.append('%s://%s' % (p.scheme, p.netloc))      # browsers upload big files straight to storage
    return '; '.join([
        "default-src 'self'",
        "img-src 'self' data: blob:",
        "media-src 'self' data: blob:",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' data: https://fonts.gstatic.com",
        "script-src 'self' 'unsafe-inline'",
        "connect-src " + ' '.join(connect),
        "worker-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


@app.after_request
def security_headers(resp):
    h = resp.headers
    h.setdefault('X-Content-Type-Options', 'nosniff')
    h.setdefault('X-Frame-Options', 'DENY')
    h.setdefault('Referrer-Policy', 'same-origin')
    h.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=(), usb=()')
    h.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
    if resp.mimetype in ('text/html', 'application/json'):
        h.setdefault('Content-Security-Policy', _csp())
        h.setdefault('Cache-Control', 'no-store')                  # logged-in pages never linger in caches / back button
    if _SECURE_COOKIES:
        h.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    return resp


# Malformed input (text where a number belongs, absurd sizes, wrong JSON shape) is the
# CLIENT's mistake: answer 400, never a 500.  Details go to the log, not to the user.
@app.errorhandler(ValueError)
@app.errorhandler(TypeError)
@app.errorhandler(AttributeError)
@app.errorhandler(KeyError)
@app.errorhandler(pgerr.DataError)
def bad_input(exc):
    app.logger.warning('rejected malformed input on %s %s: %s: %s', request.method, request.path, type(exc).__name__, exc)
    return _reject_request('The submitted data was not valid.', 400)


@app.errorhandler(StorageError)
def storage_unavailable(exc):
    app.logger.error('storage error: %s', exc)
    if request.is_json or '/api/' in request.path or request.path.startswith('/uploads/sign'):
        return jsonify({'error': 'file storage is temporarily unavailable'}), 503
    return Response('File storage is temporarily unavailable. Please try again in a moment.', status=503,
                    mimetype='text/plain')


@app.route('/')
def index():
    db = get_db()
    last_id = session.get('last_campaign_id')
    if last_id and db.execute(
        'SELECT 1 FROM campaign_members WHERE campaign_id = ? AND user_id = ?', (last_id, session['user_id'])
    ).fetchone():
        db.close()
        return redirect(url_for('characters_list', campaign_id=last_id))
    campaigns = db.execute(
        'SELECT c.id FROM campaigns c JOIN campaign_members cm ON cm.campaign_id = c.id WHERE cm.user_id = ?',
        (session['user_id'],)
    ).fetchall()
    db.close()
    if len(campaigns) == 1:
        return redirect(url_for('characters_list', campaign_id=campaigns[0]['id']))
    # Zero campaigns, or more than one with no remembered choice: let the
    # person pick (or create their first one) on the campaigns list.
    return redirect(url_for('campaigns_list'))


# ---------------- CHARACTERS ----------------

@app.route('/campaigns/<int:campaign_id>/characters')
@campaign_access_required
def characters_list():
    db = get_db()
    characters = db.execute('''
        SELECT c.*, g.name AS group_name, g.color AS group_color
        FROM characters c
        LEFT JOIN groups g ON c.group_id = g.id
        WHERE c.is_temp_familiar = 0 AND c.campaign_id = ?
        ORDER BY lower(c.name) ASC
    ''', (g.campaign_id,)).fetchall()
    groups = db.execute('SELECT * FROM groups WHERE campaign_id = ? ORDER BY lower(name) ASC', (g.campaign_id,)).fetchall()
    db.close()
    return render_template('characters_list.html', characters=characters, groups=groups)


@app.route('/campaigns/<int:campaign_id>/characters/new', methods=['GET', 'POST'])
@campaign_access_required
def character_new():
    db = get_db()
    if request.method == 'POST':
        new_id = _save_character(db, None)
        db.close()
        if request.form.get('from_battle') == '1':
            return redirect(url_for('battle_view', added=new_id))
        return redirect(url_for('characters_list'))
    groups = db.execute('SELECT * FROM groups WHERE campaign_id = ? ORDER BY lower(name) ASC', (g.campaign_id,)).fetchall()
    db.close()
    from_battle = request.args.get('from') == 'battle'
    return render_template('character_form.html', character=None, sheets=[], groups=groups, from_battle=from_battle, notes_blocks=[''])


@app.route('/campaigns/<int:campaign_id>/characters/<int:char_id>')
@campaign_access_required
def character_detail(char_id):
    db = get_db()
    character = db.execute('''
        SELECT c.*, g.name AS group_name, g.color AS group_color
        FROM characters c LEFT JOIN groups g ON c.group_id = g.id
        WHERE c.id = ? AND c.campaign_id = ?
    ''', (char_id, g.campaign_id)).fetchone()
    if character is None:
        db.close()
        return redirect(url_for('characters_list'))
    sheets = db.execute('SELECT * FROM character_sheets WHERE character_id = ? ORDER BY sort_order', (char_id,)).fetchall()
    db.close()
    can_edit = g.is_dm or character['created_by'] == session['user_id']
    return render_template('character_detail.html', character=character, sheets=sheets,
                            notes_html=notes_to_html(character['notes']), can_edit=can_edit)


@app.route('/campaigns/<int:campaign_id>/characters/<int:char_id>/edit', methods=['GET', 'POST'])
@campaign_access_required
def character_edit(char_id):
    db = get_db()
    owner_row = db.execute('SELECT created_by FROM characters WHERE id = ? AND campaign_id = ?',
                            (char_id, g.campaign_id)).fetchone()
    if owner_row is None:
        db.close()
        return redirect(url_for('characters_list'))
    # A Player can only edit a character they personally added; the DM can
    # edit anyone's.
    if not g.is_dm and owner_row['created_by'] != session['user_id']:
        db.close()
        abort(403)
    if request.method == 'POST':
        _save_character(db, char_id)
        db.close()
        return redirect(url_for('characters_list'))
    character = db.execute('SELECT * FROM characters WHERE id = ? AND campaign_id = ?', (char_id, g.campaign_id)).fetchone()
    sheets = db.execute('SELECT * FROM character_sheets WHERE character_id = ? ORDER BY sort_order', (char_id,)).fetchall()
    groups = db.execute('SELECT * FROM groups WHERE campaign_id = ? ORDER BY lower(name) ASC', (g.campaign_id,)).fetchall()
    db.close()
    if character is None:
        return redirect(url_for('characters_list'))
    notes_blocks = notes_parse_blocks(character['notes']) or ['']
    return render_template('character_form.html', character=character, sheets=sheets, groups=groups, from_battle=False, notes_blocks=notes_blocks)


def _save_character(db, char_id):
    form = request.form
    name = form.get('name', '').strip() or 'Unnamed'
    is_npc = 1 if form.get('is_npc') == 'on' else 0
    level = int(form.get('level') or 1)
    max_hp = int(form.get('max_hp') or 10)
    str_score = int(form.get('str_score') or 10)
    dex_score = int(form.get('dex_score') or 10)
    con_score = int(form.get('con_score') or 10)
    int_score = int(form.get('int_score') or 10)
    wis_score = int(form.get('wis_score') or 10)
    cha_score = int(form.get('cha_score') or 10)
    armor_class = int(form.get('armor_class') or 10)
    notes = sanitize_notes_payload(form.get('notes', ''))
    group_id = _campaign_group_id(db, form.get('group_id'))

    avatar_path = save_upload_field('avatar', 'avatars')
    remove_avatar = form.get('remove_avatar') == '1'

    if char_id is None:
        cur = db.execute('''
            INSERT INTO characters
            (name, is_npc, level, max_hp, str_score, dex_score, con_score, int_score,
             wis_score, cha_score, armor_class, avatar_path, notes, group_id, campaign_id, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, is_npc, level, max_hp, str_score, dex_score, con_score, int_score,
              wis_score, cha_score, armor_class, avatar_path, notes, group_id, g.campaign_id, session['user_id']))
        char_id = cur.lastrowid
    else:
        if avatar_path:
            old = db.execute('SELECT avatar_path FROM characters WHERE id = ?', (char_id,)).fetchone()
            if old and old['avatar_path']:
                delete_upload(old['avatar_path'])
            db.execute('UPDATE characters SET avatar_path = ? WHERE id = ?', (avatar_path, char_id))
        elif remove_avatar:
            old = db.execute('SELECT avatar_path FROM characters WHERE id = ?', (char_id,)).fetchone()
            if old and old['avatar_path']:
                delete_upload(old['avatar_path'])
            db.execute('UPDATE characters SET avatar_path = NULL WHERE id = ?', (char_id,))
        db.execute('''
            UPDATE characters SET name=?, is_npc=?, level=?, max_hp=?, str_score=?, dex_score=?,
                con_score=?, int_score=?, wis_score=?, cha_score=?, armor_class=?, notes=?, group_id=?
            WHERE id=?
        ''', (name, is_npc, level, max_hp, str_score, dex_score, con_score, int_score,
              wis_score, cha_score, armor_class, notes, group_id, char_id))

    # multiple sheet images
    max_order = db.execute('SELECT COALESCE(MAX(sort_order), -1) AS m FROM character_sheets WHERE character_id = ?', (char_id,)).fetchone()['m']
    for stored in store_uploads('sheets', 'sheets'):
        max_order += 1
        db.execute('INSERT INTO character_sheets (character_id, image_path, sort_order) VALUES (?, ?, ?)',
                   (char_id, stored.path, max_order))

    db.commit()
    return char_id


@app.route('/campaigns/<int:campaign_id>/characters/<int:char_id>/delete', methods=['POST'])
@campaign_access_required
def character_delete(char_id):
    db = get_db()
    char = db.execute('SELECT avatar_path, created_by FROM characters WHERE id = ? AND campaign_id = ?',
                       (char_id, g.campaign_id)).fetchone()
    if char and not g.is_dm and char['created_by'] != session['user_id']:
        db.close()
        abort(403)
    sheets = db.execute('SELECT image_path FROM character_sheets WHERE character_id = ?', (char_id,)).fetchall()
    if char:
        delete_upload(char['avatar_path'])
        for s in sheets:
            delete_upload(s['image_path'])
        db.execute('DELETE FROM characters WHERE id = ? AND campaign_id = ?', (char_id, g.campaign_id))
        db.commit()
    db.close()
    return redirect(url_for('characters_list'))


@app.route('/campaigns/<int:campaign_id>/characters/<int:char_id>/sheets/<int:sheet_id>/delete', methods=['POST'])
@campaign_access_required
def sheet_delete(char_id, sheet_id):
    db = get_db()
    owner_row = db.execute('SELECT created_by FROM characters WHERE id = ? AND campaign_id = ?',
                            (char_id, g.campaign_id)).fetchone()
    if owner_row and not g.is_dm and owner_row['created_by'] != session['user_id']:
        db.close()
        abort(403)
    sheet = db.execute('SELECT image_path FROM character_sheets WHERE id = ?', (sheet_id,)).fetchone()
    if sheet:
        delete_upload(sheet['image_path'])
    db.execute('DELETE FROM character_sheets WHERE id = ?', (sheet_id,))
    db.commit()
    db.close()
    return redirect(url_for('character_edit', char_id=char_id))


# ---------------- GROUPS ----------------

@app.route('/campaigns/<int:campaign_id>/groups')
@campaign_access_required
def groups_list():
    db = get_db()
    groups = db.execute('''
        SELECT g.*, (SELECT COUNT(*) FROM characters c WHERE c.group_id = g.id) AS member_count
        FROM groups g WHERE g.campaign_id = ? ORDER BY lower(g.name) ASC
    ''', (g.campaign_id,)).fetchall()
    db.close()
    return render_template('groups_list.html', groups=groups)


@app.route('/campaigns/<int:campaign_id>/groups/new', methods=['GET', 'POST'])
@campaign_access_required
def group_new():
    db = get_db()
    if request.method == 'POST':
        _save_group(db, None)
        db.close()
        return redirect(url_for('groups_list'))
    db.close()
    return render_template('group_form.html', group=None)


@app.route('/campaigns/<int:campaign_id>/groups/<int:group_id>/edit', methods=['GET', 'POST'])
@campaign_access_required
def group_edit(group_id):
    db = get_db()
    if request.method == 'POST':
        _save_group(db, group_id)
        db.close()
        return redirect(url_for('groups_list'))
    group = db.execute('SELECT * FROM groups WHERE id = ? AND campaign_id = ?', (group_id, g.campaign_id)).fetchone()
    db.close()
    if group is None:
        return redirect(url_for('groups_list'))
    return render_template('group_form.html', group=group)


_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')


def _campaign_group_id(db, raw):
    """A character may only join a group of ITS OWN campaign; anything else becomes 'no group'."""
    try:
        gid = int(raw)
    except (TypeError, ValueError):
        return None
    ok = db.execute('SELECT 1 FROM groups WHERE id = ? AND campaign_id = ?', (gid, g.campaign_id)).fetchone()
    return gid if ok else None


def _save_group(db, group_id):
    form = request.form
    name = form.get('name', '').strip() or 'Unnamed Group'
    bio = form.get('bio', '')
    color = form.get('color') or '#c9a24b'
    if not _COLOR_RE.match(color):
        color = '#c9a24b'
    avatar_path = save_upload_field('avatar', 'avatars')
    remove_avatar = form.get('remove_avatar') == '1'

    if group_id is None:
        db.execute('INSERT INTO groups (name, avatar_path, bio, color, campaign_id) VALUES (?, ?, ?, ?, ?)',
                   (name, avatar_path, bio, color, g.campaign_id))
    else:
        if avatar_path:
            old = db.execute('SELECT avatar_path FROM groups WHERE id = ?', (group_id,)).fetchone()
            if old and old['avatar_path']:
                delete_upload(old['avatar_path'])
            db.execute('UPDATE groups SET avatar_path = ? WHERE id = ?', (avatar_path, group_id))
        elif remove_avatar:
            old = db.execute('SELECT avatar_path FROM groups WHERE id = ?', (group_id,)).fetchone()
            if old and old['avatar_path']:
                delete_upload(old['avatar_path'])
            db.execute('UPDATE groups SET avatar_path = NULL WHERE id = ?', (group_id,))
        db.execute('UPDATE groups SET name=?, bio=?, color=? WHERE id=?', (name, bio, color, group_id))
    db.commit()


@app.route('/campaigns/<int:campaign_id>/groups/<int:group_id>/delete', methods=['POST'])
@campaign_access_required
def group_delete(group_id):
    db = get_db()
    group = db.execute('SELECT avatar_path FROM groups WHERE id = ? AND campaign_id = ?', (group_id, g.campaign_id)).fetchone()
    if group:
        delete_upload(group['avatar_path'])
        db.execute('DELETE FROM groups WHERE id = ? AND campaign_id = ?', (group_id, g.campaign_id))
        db.commit()
    db.close()
    return redirect(url_for('groups_list'))


# ---------------- API (for future battle screen use) ----------------

@app.route('/campaigns/<int:campaign_id>/api/characters')
@campaign_access_required
def api_characters():
    db = get_db()
    rows = db.execute('''
        SELECT c.*, g.name AS group_name, g.color AS group_color
        FROM characters c LEFT JOIN groups g ON c.group_id = g.id
        WHERE c.is_temp_familiar = 0 AND c.campaign_id = ?
        ORDER BY lower(c.name) ASC
    ''', (g.campaign_id,)).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route('/campaigns/<int:campaign_id>/api/characters/<int:char_id>/detail')
@campaign_access_required
def api_character_detail(char_id):
    db = get_db()
    character = db.execute('''
        SELECT c.*, g.name AS group_name, g.color AS group_color
        FROM characters c LEFT JOIN groups g ON c.group_id = g.id
        WHERE c.id = ? AND c.campaign_id = ?
    ''', (char_id, g.campaign_id)).fetchone()
    if character is None:
        db.close()
        return jsonify({'error': 'not found'}), 404
    if not g.is_dm and character['is_npc']:
        # 5e convention: a Player can look up their own party's dossiers but
        # not a monster/NPC's -- this is the real enforcement point, the
        # "View details" button being hidden client-side is just UI polish.
        db.close()
        return jsonify({'error': 'hidden'}), 403
    sheets = db.execute('SELECT * FROM character_sheets WHERE character_id = ? ORDER BY sort_order', (char_id,)).fetchall()
    db.close()
    data = dict(character)
    data['sheets'] = [dict(s) for s in sheets]
    data['notes_html'] = notes_to_html(character['notes'])
    return jsonify(data)


# ---------------- BATTLE ----------------

@app.route('/campaigns/<int:campaign_id>/battle')
@campaign_access_required
def battle_view():
    db = get_db()
    characters = db.execute('''
        SELECT c.*, g.name AS group_name, g.color AS group_color
        FROM characters c LEFT JOIN groups g ON c.group_id = g.id
        WHERE c.is_temp_familiar = 0 AND c.campaign_id = ?
        ORDER BY lower(c.name) ASC
    ''', (g.campaign_id,)).fetchall()
    db.close()
    characters = [dict(c) for c in characters]
    if not g.is_dm:
        # A Player can't add anyone to the battle anyway (the "Add Character"
        # modal is DM-only), but don't hand the roster's stat block to the
        # page source either -- keep only what the (hidden) picker UI needs.
        characters = [{
            'id': c['id'], 'name': c['name'], 'avatar_path': c['avatar_path'],
            'group_id': c['group_id'], 'group_name': c['group_name'], 'group_color': c['group_color'],
            'is_npc': c['is_npc'],
        } for c in characters]
    return render_template('battle.html', characters=characters,
                            added_id=request.args.get('added'))


def _battle_rows(db, redact_enemies=False):
    """redact_enemies=True (a Player viewing the battle) strips HP, AC, and
    initiative off every NPC/monster row before it ever reaches the
    response -- this is the actual enforcement point (5e conventions: a
    player shouldn't know a monster's exact numbers), the client-side hiding
    is just presentation on top of it. PCs (is_npc=0) and familiars are
    never redacted -- a party can see its own stats."""
    rows = db.execute('''
        SELECT bp.*, c.name AS char_name, c.avatar_path, c.is_npc, c.max_hp AS char_max_hp,
               c.armor_class AS base_ac, c.is_temp_familiar, c.familiar_icon_key,
               g.id AS gid, g.name AS group_name, g.color AS group_color
        FROM battle_participants bp
        JOIN characters c ON bp.character_id = c.id
        LEFT JOIN groups g ON c.group_id = g.id
        WHERE c.campaign_id = ?
        ORDER BY bp.sort_order ASC, bp.id ASC
    ''', (g.campaign_id,)).fetchall()
    rows = [dict(r) for r in rows]

    for r in rows:
        r['effective_ac'] = r['ac_override'] if r['ac_override'] is not None else r['base_ac']
        # A binary "at 0 HP but not yet confirmed dead" flag is safe to send
        # even when the exact HP is redacted -- it's what lets a Player see
        # a hidden enemy go down without ever learning its actual numbers.
        r['is_downed'] = bool(r['current_hp'] is not None and r['current_hp'] <= 0 and not r['is_dead'])

    # compute duplicate-name suffixes, in the order participants were added
    name_counts = {}
    name_seen = {}
    for r in rows:
        name_counts[r['char_name']] = name_counts.get(r['char_name'], 0) + 1
    for r in rows:
        n = r['char_name']
        if name_counts[n] > 1:
            name_seen[n] = name_seen.get(n, 0) + 1
            r['display_name'] = f"{n} #{name_seen[n]}"
        else:
            r['display_name'] = n

    if redact_enemies:
        for r in rows:
            # A familiar the DM has marked as an NPC gets the same treatment
            # as any other monster -- only a familiar left as a PC (or any
            # real PC) stays fully visible to its party.
            if r['is_npc']:
                r['hidden_stats'] = True
                r['current_hp'] = None
                r['char_max_hp'] = None
                r['temp_hp'] = None
                r['initiative'] = None
                r['ac_override'] = None
                r['effective_ac'] = None
                r['base_ac'] = None
    return rows


def _create_familiar_participant(db, icon_key, custom_name):
    """A familiar/prop pin gets a lightweight, temporary battle presence: a
    throwaway character (flagged is_temp_familiar so it never shows in the
    real character roster) plus a normal battle_participants row. Deleting
    the character (see _cleanup_familiar_participants) cascades to remove
    the participant row too."""
    display_name = custom_name or (icon_key or 'familiar').capitalize()
    cur = db.execute(
        '''INSERT INTO characters (name, level, max_hp, armor_class, is_npc, is_temp_familiar, familiar_icon_key, campaign_id)
           VALUES (?, 1, 4, 10, 0, 1, ?, ?)''',
        (display_name, icon_key, g.campaign_id)
    )
    char_id = cur.lastrowid
    cur2 = db.execute('INSERT INTO battle_participants (character_id, current_hp) VALUES (?, 4)', (char_id,))
    return cur2.lastrowid


def _cleanup_familiar_participants(db, map_id):
    """Remove every temporary familiar this map ever added to the battle —
    called when the map is unlinked from battle, another map takes over as
    the linked map, or this map is deleted."""
    pins = db.execute(
        "SELECT id, participant_id FROM map_pins WHERE map_id = ? AND pin_type = 'prop' AND participant_id IS NOT NULL",
        (map_id,)
    ).fetchall()
    for p in pins:
        row = db.execute('SELECT character_id FROM battle_participants WHERE id = ?', (p['participant_id'],)).fetchone()
        if row:
            db.execute('DELETE FROM characters WHERE id = ? AND is_temp_familiar = 1', (row['character_id'],))
        db.execute('UPDATE map_pins SET participant_id = NULL WHERE id = ?', (p['id'],))
    db.commit()


@app.route('/campaigns/<int:campaign_id>/api/battle')
@campaign_access_required
def api_battle_list():
    db = get_db()
    rows = _battle_rows(db, redact_enemies=not g.is_dm)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/add', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_add():
    data = request.get_json(force=True)
    character_id = data.get('character_id')
    db = get_db()
    char = db.execute('SELECT max_hp FROM characters WHERE id = ? AND campaign_id = ?', (character_id, g.campaign_id)).fetchone()
    if char is None:
        db.close()
        return jsonify({'error': 'character not found'}), 404
    max_order = db.execute('SELECT COALESCE(MAX(sort_order), -1) AS m FROM battle_participants').fetchone()['m']
    db.execute('''
        INSERT INTO battle_participants (character_id, current_hp, initiative, is_dead, sort_order)
        VALUES (?, ?, 0, 0, ?)
    ''', (character_id, char['max_hp'], max_order + 1))
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/add-group', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_add_group():
    data = request.get_json(force=True)
    group_id = data.get('group_id')
    db = get_db()
    members = db.execute('SELECT id, max_hp FROM characters WHERE group_id = ? AND campaign_id = ?', (group_id, g.campaign_id)).fetchall()
    max_order = db.execute('SELECT COALESCE(MAX(sort_order), -1) AS m FROM battle_participants').fetchone()['m']
    for i, m in enumerate(members):
        db.execute('''
            INSERT INTO battle_participants (character_id, current_hp, initiative, is_dead, sort_order)
            VALUES (?, ?, 0, 0, ?)
        ''', (m['id'], m['max_hp'], max_order + 1 + i))
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/<int:pid>/heal', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_heal(pid):
    data = request.get_json(force=True)
    amount = max(0, int(data.get('amount', 0)))
    db = get_db()
    # One atomic statement (no read-then-write), so two people healing/damaging
    # the same creature at the same moment can never overwrite each other.
    cur = db.execute('''
        UPDATE battle_participants SET
            current_hp = LEAST(c.max_hp, battle_participants.current_hp + ?),
            is_dead = CASE WHEN LEAST(c.max_hp, battle_participants.current_hp + ?) > 0
                           THEN 0 ELSE battle_participants.is_dead END
        FROM characters c
        WHERE battle_participants.id = ? AND battle_participants.character_id = c.id
    ''', (amount, amount, pid))
    if cur.rowcount == 0:
        db.rollback()
        db.close()
        return jsonify({'error': 'not found'}), 404
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/<int:pid>/damage', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_damage(pid):
    data = request.get_json(force=True)
    amount = max(0, int(data.get('amount', 0)))
    db = get_db()
    # Temporary HP absorbs damage first (standard D&D rule); any leftover spills
    # onto real HP.  Done as ONE atomic statement (all right-hand sides see the
    # row's pre-update values) so concurrent hits are never lost.
    cur = db.execute('''
        UPDATE battle_participants SET
            temp_hp    = COALESCE(temp_hp, 0) - LEAST(COALESCE(temp_hp, 0), ?),
            current_hp = GREATEST(0, current_hp - (? - LEAST(COALESCE(temp_hp, 0), ?)))
        WHERE id = ?
    ''', (amount, amount, amount, pid))
    if cur.rowcount == 0:
        db.rollback()
        db.close()
        return jsonify({'error': 'not found'}), 404
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/<int:pid>/temphp', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_temphp(pid):
    data = request.get_json(force=True)
    value = max(0, int(data.get('value', 0)))
    db = get_db()
    db.execute('UPDATE battle_participants SET temp_hp = ? WHERE id = ?', (value, pid))
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/<int:pid>/set_hp', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_set_hp(pid):
    data = request.get_json(force=True)
    db = get_db()
    wanted = int(data.get('value', 0))
    cur = db.execute('''
        UPDATE battle_participants SET
            current_hp = GREATEST(0, LEAST(c.max_hp, ?)),
            is_dead = CASE WHEN GREATEST(0, LEAST(c.max_hp, ?)) > 0
                           THEN 0 ELSE battle_participants.is_dead END
        FROM characters c
        WHERE battle_participants.id = ? AND battle_participants.character_id = c.id
    ''', (wanted, wanted, pid))
    if cur.rowcount == 0:
        db.rollback()
        db.close()
        return jsonify({'error': 'not found'}), 404
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/<int:pid>/initiative', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_initiative(pid):
    data = request.get_json(force=True)
    value = int(data.get('value', 0))
    db = get_db()
    db.execute('UPDATE battle_participants SET initiative = ? WHERE id = ?', (value, pid))
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/<int:pid>/ac', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_ac(pid):
    data = request.get_json(force=True)
    value = int(data.get('value', 0))
    db = get_db()
    db.execute('UPDATE battle_participants SET ac_override = ? WHERE id = ?', (value, pid))
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/<int:pid>/max-hp', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_max_hp(pid):
    """Familiars have no character sheet to edit max HP on, so the battle
    tracker lets you set it directly — real characters keep their sheet as
    the single source of truth and aren't affected here."""
    data = request.get_json(force=True)
    value = max(1, int(data.get('value', 1)))
    db = get_db()
    row = db.execute('''
        SELECT c.id AS character_id FROM battle_participants bp
        JOIN characters c ON bp.character_id = c.id
        WHERE bp.id = ? AND c.is_temp_familiar = 1
    ''', (pid,)).fetchone()
    if row:
        db.execute('UPDATE characters SET max_hp = ? WHERE id = ?', (value, row['character_id']))
        db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/<int:pid>/die', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_die(pid):
    db = get_db()
    db.execute('UPDATE battle_participants SET is_dead = 1 WHERE id = ?', (pid,))
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/<int:pid>/revive', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_revive(pid):
    db = get_db()
    db.execute('UPDATE battle_participants SET is_dead = 0 WHERE id = ?', (pid,))
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/<int:pid>/remove', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_remove(pid):
    db = get_db()
    db.execute('DELETE FROM battle_participants WHERE id = ?', (pid,))
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/clear', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_clear():
    db = get_db()
    db.execute(
        'DELETE FROM battle_participants WHERE character_id IN (SELECT id FROM characters WHERE campaign_id = ?)',
        (g.campaign_id,)
    )
    db.commit()
    db.close()
    return jsonify([])


@app.route('/campaigns/<int:campaign_id>/dice')
@campaign_access_required
def dice_view():
    return render_template('dice.html')


# ---------------- EXPORT / IMPORT ----------------

def _build_export_zip(campaign_id, group_ids=None, character_ids=None, download_name='campaign_export.zip'):
    """Build a zip export scoped to one campaign. group_ids/character_ids of
    None means 'all in this campaign'; an explicit list (including empty)
    restricts to just those rows. Groups referenced by an exported character
    are always pulled in too, so relinking on import still works even for a
    single-character export."""
    db = get_db()

    if group_ids is None:
        groups = db.execute('SELECT * FROM groups WHERE campaign_id = ? ORDER BY id', (campaign_id,)).fetchall()
    elif group_ids:
        placeholders = ','.join('?' * len(group_ids))
        groups = db.execute(f'SELECT * FROM groups WHERE id IN ({placeholders}) ORDER BY id', group_ids).fetchall()
    else:
        groups = []

    if character_ids is None:
        characters = db.execute('SELECT * FROM characters WHERE campaign_id = ? ORDER BY id', (campaign_id,)).fetchall()
    elif character_ids:
        placeholders = ','.join('?' * len(character_ids))
        characters = db.execute(f'SELECT * FROM characters WHERE id IN ({placeholders}) ORDER BY id', character_ids).fetchall()
    else:
        characters = []

    # Pull in any group referenced by an exported character but not already included
    existing_group_ids = {g['id'] for g in groups}
    referenced_group_ids = {c['group_id'] for c in characters if c['group_id']} - existing_group_ids
    if referenced_group_ids:
        placeholders = ','.join('?' * len(referenced_group_ids))
        extra_groups = db.execute(f'SELECT * FROM groups WHERE id IN ({placeholders})', list(referenced_group_ids)).fetchall()
        groups = list(groups) + list(extra_groups)

    char_ids_for_sheets = [c['id'] for c in characters]
    sheets_by_char = {}
    if char_ids_for_sheets:
        placeholders = ','.join('?' * len(char_ids_for_sheets))
        sheets = db.execute(f'SELECT * FROM character_sheets WHERE character_id IN ({placeholders}) ORDER BY id', char_ids_for_sheets).fetchall()
        for s in sheets:
            sheets_by_char.setdefault(s['character_id'], []).append(s['image_path'])
    db.close()

    group_id_to_name = {g['id']: g['name'] for g in groups}

    manifest = {
        'version': 1,
        'exported_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'groups': [
            {
                'name': g['name'],
                'bio': g['bio'],
                'color': g['color'],
                'avatar_file': g['avatar_path'],
            }
            for g in groups
        ],
        'characters': [
            {
                'name': c['name'],
                'is_npc': c['is_npc'],
                'level': c['level'],
                'max_hp': c['max_hp'],
                'str_score': c['str_score'],
                'dex_score': c['dex_score'],
                'con_score': c['con_score'],
                'int_score': c['int_score'],
                'wis_score': c['wis_score'],
                'cha_score': c['cha_score'],
                'armor_class': c['armor_class'],
                'notes': c['notes'],
                'group_name': group_id_to_name.get(c['group_id']),
                'avatar_file': c['avatar_path'],
                'sheet_files': sheets_by_char.get(c['id'], []),
            }
            for c in characters
        ],
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('manifest.json', json.dumps(manifest, indent=2))
        seen = set()
        referenced_paths = set()
        for grp in manifest['groups']:
            if grp['avatar_file']:
                referenced_paths.add(grp['avatar_file'])
        for c in manifest['characters']:
            if c['avatar_file']:
                referenced_paths.add(c['avatar_file'])
            for sp in c['sheet_files']:
                referenced_paths.add(sp)
        st = get_storage()
        wanted = sorted(p for p in referenced_paths if _UPLOAD_KEY_RE.match(p))
        with ThreadPoolExecutor(8) as pool:                    # fetch the images concurrently
            blobs = list(pool.map(lambda rel: (rel, st.get(UPLOADS, rel)), wanted))
        for rel_path, blob in blobs:
            if blob is not None and rel_path not in seen:
                zf.writestr(f'images/{rel_path}', blob)
                seen.add(rel_path)

    filename = _dated_filename(download_name)
    payload = buf.getvalue()
    if len(payload) <= DIRECT_INLINE_LIMIT:
        return send_file(io.BytesIO(payload), mimetype='application/zip', as_attachment=True, download_name=filename)
    # Too big to return through a serverless function (4.5 MB response cap): park it in
    # private temp storage and send the browser to a short-lived signed download link.
    key = f"exports/{session['user_id']}/{uuid.uuid4().hex}.zip"
    st.put(TEMP, key, payload, 'application/zip')
    return redirect(st.signed_download_url(TEMP, key, filename, 120))


def _dated_filename(base_name):
    """Stamp an export filename with today's date, e.g. campaign_export_2026-09-12.zip."""
    stamp = datetime.now().strftime('%Y-%m-%d')
    if base_name.endswith('.zip'):
        return f'{base_name[:-4]}_{stamp}.zip'
    return f'{base_name}_{stamp}'


def _slugify(name):
    slug = ''.join(ch.lower() if ch.isalnum() else '-' for ch in name).strip('-')
    while '--' in slug:
        slug = slug.replace('--', '-')
    return slug or 'export'


@app.route('/campaigns/<int:campaign_id>/export/data')
@campaign_access_required
def export_data():
    return _build_export_zip(g.campaign_id, download_name='campaign_export.zip')


@app.route('/campaigns/<int:campaign_id>/characters/<int:char_id>/export')
@campaign_access_required
def character_export(char_id):
    db = get_db()
    row = db.execute('SELECT name FROM characters WHERE id = ? AND campaign_id = ?', (char_id, g.campaign_id)).fetchone()
    db.close()
    if row is None:
        return redirect(url_for('characters_list'))
    return _build_export_zip(g.campaign_id, character_ids=[char_id], download_name=f'{_slugify(row["name"])}.zip')


@app.route('/campaigns/<int:campaign_id>/groups/<int:group_id>/export')
@campaign_access_required
def group_export(group_id):
    db = get_db()
    row = db.execute('SELECT name FROM groups WHERE id = ? AND campaign_id = ?', (group_id, g.campaign_id)).fetchone()
    db.close()
    if row is None:
        return redirect(url_for('groups_list'))
    return _build_export_zip(g.campaign_id, group_ids=[group_id], character_ids=[], download_name=f'{_slugify(row["name"])}.zip')


@app.route('/campaigns/<int:campaign_id>/import/data', methods=['POST'])
@campaign_access_required
def import_data():
    dest = 'groups_list' if 'groups' in (request.referrer or '') else 'characters_list'
    data, temp_key = _read_upload_source(request.files.get('import_file'), request.form.get('import_file__key'))
    try:
        if not data:
            return redirect(request.referrer or url_for('characters_list'))
        try:
            zf, raw_manifest = importer.open_zip(data)
            manifest = importer.normalize_manifest(raw_manifest, sanitize_notes_payload)
        except importer.ImportRejected:
            return redirect(url_for(dest, import_error=1))
    finally:
        if temp_key:
            get_storage().delete(TEMP, temp_key)

    pending = []                                   # (key, Processed) uploaded just before commit

    def extract_image(rel_path, subfolder):
        """Validate an image from the zip and queue it under a fresh key."""
        if not rel_path:
            return None
        try:
            blob = importer.read_member(zf, f'images/{rel_path}', images.KIND_MAX_BYTES['maps'])
            processed = images.process_image(blob, subfolder) if blob else None
        except (importer.ImportRejected, images.ImageError):
            return None                            # a bad image never blocks the rest of the import
        if processed is None:
            return None
        key = f"{subfolder}/{uuid.uuid4().hex}.{processed.ext}"
        pending.append((key, processed))
        return key

    db = get_db()

    # Groups: dedupe by name (case-insensitive) so re-importing doesn't duplicate factions
    # -- scoped to this campaign only, so importing into campaign B never
    # matches a same-named group that only exists in campaign A.
    existing_groups = {
        row['name'].lower(): row['id']
        for row in db.execute('SELECT id, name FROM groups WHERE campaign_id = ?', (g.campaign_id,))
    }
    group_name_to_id = dict(existing_groups)

    for grp in manifest['groups']:
        key = grp['name'].lower()
        if key in group_name_to_id:
            continue
        avatar_path = extract_image(grp['avatar_file'], 'avatars')
        cur = db.execute('INSERT INTO groups (name, avatar_path, bio, color, campaign_id) VALUES (?, ?, ?, ?, ?)',
                          (grp['name'], avatar_path, grp['bio'], grp['color'], g.campaign_id))
        group_name_to_id[key] = cur.lastrowid

    # Characters: always inserted as new records (never merged/overwritten)
    for c in manifest['characters']:
        group_id = group_name_to_id.get((c['group_name'] or '').lower())
        avatar_path = extract_image(c['avatar_file'], 'avatars')
        cur = db.execute('''
            INSERT INTO characters
            (name, is_npc, level, max_hp, str_score, dex_score, con_score, int_score,
             wis_score, cha_score, armor_class, avatar_path, notes, group_id, campaign_id, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (c['name'], c['is_npc'], c['level'], c['max_hp'],
              c['str_score'], c['dex_score'], c['con_score'], c['int_score'],
              c['wis_score'], c['cha_score'], c['armor_class'],
              avatar_path, c['notes'], group_id, g.campaign_id, session['user_id']))
        new_char_id = cur.lastrowid

        for i, sheet_rel_path in enumerate(c['sheet_files']):
            sheet_path = extract_image(sheet_rel_path, 'sheets')
            if sheet_path:
                db.execute('INSERT INTO character_sheets (character_id, image_path, sort_order) VALUES (?, ?, ?)',
                           (new_char_id, sheet_path, i))

    # Upload every queued image (in parallel) BEFORE committing, so a storage failure
    # rolls the whole import back instead of leaving rows that point at missing files.
    st = get_storage()
    try:
        with ThreadPoolExecutor(6) as pool:
            list(pool.map(lambda item: st.put(UPLOADS, item[0], item[1].data, item[1].content_type), pending))
    except Exception:
        db.rollback()
        db.close()
        try:
            st.delete_many(UPLOADS, [k for k, _ in pending])
        except StorageError:
            pass
        raise
    db.commit()
    db.close()

    return redirect(url_for(dest, imported=1))


# ---------------- MAPS ----------------

@app.route('/campaigns/<int:campaign_id>/maps')
@campaign_access_required
def maps_list():
    # Reopen the last map viewed in this campaign, like the editor was never
    # left — unless we got here via the editor's own "Back to maps" link
    # (?browse=1), which means the user explicitly wants the picker.
    if not request.args.get('browse'):
        last_id = session.get('last_map_by_campaign', {}).get(str(g.campaign_id))
        if last_id:
            db = get_db()
            still_exists = db.execute(
                'SELECT 1 FROM maps WHERE id = ? AND campaign_id = ?', (last_id, g.campaign_id)
            ).fetchone()
            db.close()
            if still_exists:
                return redirect(url_for('map_editor', map_id=last_id))

    db = get_db()
    maps = db.execute('SELECT * FROM maps WHERE campaign_id = ? ORDER BY created_at DESC', (g.campaign_id,)).fetchall()
    db.close()
    return render_template('maps_list.html', maps=[dict(m) for m in maps])


@app.route('/campaigns/<int:campaign_id>/maps/new', methods=['POST'])
@campaign_access_required
def map_new():
    name = request.form.get('name', '').strip() or 'Untitled Map'
    image_file = request.files.get('image')

    if (image_file and image_file.filename) or request.form.get('image__key'):
        stored = store_upload('image', 'maps', cap_dimension=images.OPTIMIZE_MAX_DIM)
        if not stored:
            return redirect(url_for('maps_list'))
        image_path, width, height = stored.path, stored.width, stored.height
    else:
        # No image provided: start with a blank white canvas at the requested size
        width = max(200, min(2000, int(request.form.get('blank_width', 1500) or 1500)))
        height = max(200, min(2000, int(request.form.get('blank_height', 1000) or 1000)))
        image_path = _generate_blank_canvas(width, height)

    db = get_db()
    cur = db.execute(
        'INSERT INTO maps (name, image_path, image_width, image_height, campaign_id) VALUES (?, ?, ?, ?, ?)',
        (name, image_path, width, height, g.campaign_id)
    )
    map_id = cur.lastrowid
    db.commit()
    db.close()
    return redirect(url_for('map_editor', map_id=map_id))


@app.route('/campaigns/<int:campaign_id>/maps/<int:map_id>')
@campaign_access_required
def map_editor(map_id):
    db = get_db()
    m = db.execute('SELECT * FROM maps WHERE id = ? AND campaign_id = ?', (map_id, g.campaign_id)).fetchone()
    db.close()
    if m is None:
        return redirect(url_for('maps_list', browse=1))
    session.setdefault('last_map_by_campaign', {})[str(g.campaign_id)] = map_id
    session.modified = True
    return render_template('map_editor.html', map=dict(m))


@app.route('/campaigns/<int:campaign_id>/maps/<int:map_id>/delete', methods=['POST'])
@campaign_access_required
def map_delete(map_id):
    db = get_db()
    m = db.execute('SELECT image_path FROM maps WHERE id = ? AND campaign_id = ?', (map_id, g.campaign_id)).fetchone()
    if m:
        _cleanup_familiar_participants(db, map_id)
        delete_upload(m['image_path'])
        db.execute('DELETE FROM maps WHERE id = ? AND campaign_id = ?', (map_id, g.campaign_id))
        db.commit()
    db.close()
    last_map_by_campaign = session.get('last_map_by_campaign', {})
    if last_map_by_campaign.get(str(g.campaign_id)) == map_id:
        last_map_by_campaign.pop(str(g.campaign_id), None)
        session.modified = True
    return redirect(url_for('maps_list', browse=1))


def _map_is_locked(db, map_id):
    row = db.execute('SELECT locked_for_players FROM maps WHERE id = ?', (map_id,)).fetchone()
    return bool(row and row['locked_for_players'])


def map_edit_allowed(view_func):
    """Stacks on top of campaign_access_required for every map-mutating
    route. The DM can always edit; a Player can edit only while the DM
    hasn't locked this particular map. Enforced here so the "Lock the map
    for players" toggle actually locks the map, not just the toolbar UI."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not g.is_dm:
            db = get_db()
            locked = _map_is_locked(db, kwargs.get('map_id'))
            db.close()
            if locked:
                abort(403)
        return view_func(*args, **kwargs)
    return wrapped


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/state')
@campaign_access_required
def api_map_state(map_id):
    """Polled by every open map tab so grid settings, the lock, and the
    battle-link toggle all take effect live instead of only on reload --
    only the drawings/pins themselves are fetched separately (they already
    have their own endpoints)."""
    db = get_db()
    state = _map_state_payload(db, map_id)
    db.close()
    if state is None:
        abort(404)
    return jsonify(state)


def _map_state_payload(db, map_id):
    row = db.execute('''
        SELECT grid_size, grid_color, grid_visible, grid_offset_x, grid_offset_y,
               snap_to_grid, grid_setup_done, locked_for_players, linked_to_battle
        FROM maps WHERE id = ? AND campaign_id = ?
    ''', (map_id, g.campaign_id)).fetchone()
    return dict(row) if row is not None else None


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/settings', methods=['POST'])
@campaign_access_required
def api_map_settings(map_id):
    data = request.get_json(force=True)
    db = get_db()
    # Locking/unlocking the map, and linking it to the battle tracker, are
    # DM-only actions, regardless of current lock state. Every other
    # setting follows the normal lock: a Player can't touch grid/canvas
    # settings on a map the DM has locked.
    if ('locked_for_players' in data or 'linked_to_battle' in data) and not g.is_dm:
        db.close()
        abort(403)
    if not g.is_dm and _map_is_locked(db, map_id):
        db.close()
        abort(403)
    fields = []
    values = []
    if 'locked_for_players' in data:
        fields.append('locked_for_players = ?'); values.append(1 if data['locked_for_players'] else 0)
    if 'grid_size' in data:
        fields.append('grid_size = ?'); values.append(max(25, min(100, int(data['grid_size']))))
    if 'grid_offset_x' in data:
        fields.append('grid_offset_x = ?'); values.append(int(data['grid_offset_x']))
    if 'grid_offset_y' in data:
        fields.append('grid_offset_y = ?'); values.append(int(data['grid_offset_y']))
    if 'grid_color' in data and data['grid_color'] in ('gray', 'white', 'black'):
        fields.append('grid_color = ?'); values.append(data['grid_color'])
    if 'grid_visible' in data:
        fields.append('grid_visible = ?'); values.append(1 if data['grid_visible'] else 0)
    if 'grid_setup_done' in data:
        fields.append('grid_setup_done = ?'); values.append(1 if data['grid_setup_done'] else 0)
    if 'snap_to_grid' in data:
        fields.append('snap_to_grid = ?'); values.append(1 if data['snap_to_grid'] else 0)
    if 'linked_to_battle' in data:
        new_val = 1 if data['linked_to_battle'] else 0
        if new_val:
            # Only one map per campaign may be linked at a time — unlink any
            # other map in this campaign first, cleaning up whatever
            # familiars it added to the battle. (Verified per-campaign in
            # Phase E once more than one campaign actually exists.)
            others = db.execute(
                'SELECT id FROM maps WHERE linked_to_battle = 1 AND id != ? AND campaign_id = ?',
                (map_id, g.campaign_id)
            ).fetchall()
            for o in others:
                _cleanup_familiar_participants(db, o['id'])
                db.execute('UPDATE maps SET linked_to_battle = 0 WHERE id = ?', (o['id'],))
        else:
            _cleanup_familiar_participants(db, map_id)
        fields.append('linked_to_battle = ?'); values.append(new_val)
    if fields:
        values.append(map_id)
        db.execute(f'UPDATE maps SET {", ".join(fields)} WHERE id = ?', values)
        db.commit()
    m = db.execute('SELECT * FROM maps WHERE id = ?', (map_id,)).fetchone()
    db.close()
    return jsonify(dict(m) if m else {})


# ---- Drawings ----

@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/drawings')
@campaign_access_required
def api_map_drawings_list(map_id):
    db = get_db()
    result = _map_drawings_payload(db, map_id)
    db.close()
    return jsonify(result)


def _map_drawings_payload(db, map_id):
    rows = db.execute('SELECT * FROM map_drawings WHERE map_id = ? ORDER BY sort_order, id', (map_id,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['data'] = json.loads(d['data'])
        result.append(d)
    return result


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/drawings', methods=['POST'])
@campaign_access_required
@map_edit_allowed
def api_map_drawings_add(map_id):
    data = request.get_json(force=True)
    kind = data.get('kind')
    geometry = data.get('data', {})
    color = data.get('color', '#c9a24b')
    fill = 1 if data.get('fill') else 0
    fill_opacity = float(data.get('fill_opacity', 0.4))
    cx = float(data.get('cx', 0))
    cy = float(data.get('cy', 0))
    w = float(data.get('w', 0))
    h = float(data.get('h', 0))
    rotation = float(data.get('rotation', 0))
    locked = 1 if data.get('locked') else 0
    if kind not in ('line', 'rect', 'oval', 'pen', 'angle'):
        return jsonify({'error': 'invalid kind'}), 400
    db = get_db()
    max_order = db.execute('SELECT COALESCE(MAX(sort_order), -1) AS m FROM map_drawings WHERE map_id = ?', (map_id,)).fetchone()['m']
    cur = db.execute(
        '''INSERT INTO map_drawings (map_id, kind, data, cx, cy, w, h, rotation, color, fill, fill_opacity, sort_order, locked)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (map_id, kind, json.dumps(geometry), cx, cy, w, h, rotation, color, fill, fill_opacity, max_order + 1, locked)
    )
    db.commit()
    db.close()
    return jsonify({'id': cur.lastrowid})


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/drawings/<int:drawing_id>/update', methods=['POST'])
@campaign_access_required
@map_edit_allowed
def api_map_drawing_update(map_id, drawing_id):
    data = request.get_json(force=True)
    db = get_db()
    fields = []
    values = []
    for key in ('cx', 'cy', 'w', 'h', 'rotation', 'fill_opacity'):
        if key in data:
            fields.append(f'{key} = ?'); values.append(float(data[key]))
    if 'color' in data:
        fields.append('color = ?'); values.append(str(data['color']))
    if 'fill' in data:
        fields.append('fill = ?'); values.append(1 if data['fill'] else 0)
    if 'locked' in data:
        fields.append('locked = ?'); values.append(1 if data['locked'] else 0)
    if 'data' in data:
        fields.append('data = ?'); values.append(json.dumps(data['data']))
    if fields:
        values.append(drawing_id)
        values.append(map_id)
        db.execute(f'UPDATE map_drawings SET {", ".join(fields)} WHERE id = ? AND map_id = ?', values)
        db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/drawings/<int:drawing_id>/delete', methods=['POST'])
@campaign_access_required
@map_edit_allowed
def api_map_drawing_delete(map_id, drawing_id):
    db = get_db()
    db.execute('DELETE FROM map_drawings WHERE id = ? AND map_id = ?', (drawing_id, map_id))
    db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/drawings/clear', methods=['POST'])
@campaign_access_required
@map_edit_allowed
def api_map_drawings_clear(map_id):
    db = get_db()
    db.execute('DELETE FROM map_drawings WHERE map_id = ?', (map_id,))
    db.commit()
    db.close()
    return jsonify({'ok': True})


# ---- Pins ----

def _map_pins_payload(db, map_id):
    m = db.execute('SELECT * FROM maps WHERE id = ? AND campaign_id = ?', (map_id, g.campaign_id)).fetchone()
    if m is None:
        return []

    if m['linked_to_battle']:
        # Auto-place a pin for any battle participant that doesn't have one
        # yet — but never for a familiar's own participant, since a familiar
        # already has its own prop pin representing it on the map. Scoped to
        # this campaign so a battle running in another campaign never bleeds
        # pins onto this map.
        participants = db.execute('''
            SELECT bp.id FROM battle_participants bp
            JOIN characters c ON bp.character_id = c.id
            WHERE c.is_temp_familiar = 0 AND c.campaign_id = ?
        ''', (g.campaign_id,)).fetchall()
        participant_ids = [p['id'] for p in participants]
        existing = db.execute(
            "SELECT participant_id FROM map_pins WHERE map_id = ? AND pin_type = 'character'", (map_id,)
        ).fetchall()
        existing_ids = {e['participant_id'] for e in existing}
        new_ids = [pid for pid in participant_ids if pid not in existing_ids]
        for i, participant_id in enumerate(new_ids):
            offset = 40 + (i * 30) % 200
            db.execute(
                "INSERT INTO map_pins (map_id, pin_type, participant_id, x, y, scale) VALUES (?, 'character', ?, ?, ?, 1.0)",
                (map_id, participant_id, offset, offset)
            )
        # Drop pins for participants no longer in the current battle
        if participant_ids:
            placeholders = ','.join('?' * len(participant_ids))
            db.execute(
                f"DELETE FROM map_pins WHERE map_id = ? AND pin_type = 'character' AND participant_id NOT IN ({placeholders})",
                [map_id] + participant_ids
            )
        else:
            db.execute("DELETE FROM map_pins WHERE map_id = ? AND pin_type = 'character'", (map_id,))
        db.commit()

    rows = db.execute('SELECT * FROM map_pins WHERE map_id = ?', (map_id,)).fetchall()
    # Reuse the battle tracker's own duplicate-numbering (e.g. "Goblin #1",
    # "Goblin #2") so character pins on the map match the battle menu exactly.
    battle_display_names = {r['id']: r['display_name'] for r in _battle_rows(db)}
    result = []
    for r in rows:
        p = dict(r)
        # A prop pin with a participant_id is a familiar wired into the
        # battle tracker -- fetch its live is_npc/HP too, not just real
        # "character" pins, so a familiar the DM has marked as an NPC gets
        # the same PC/NPC-aware treatment everywhere else does.
        if p['participant_id']:
            live = db.execute('''
                SELECT bp.current_hp, bp.is_dead, c.id AS character_id, c.name AS char_name, c.avatar_path,
                       c.max_hp AS char_max_hp, c.is_npc, c.is_temp_familiar, g.color AS group_color
                FROM battle_participants bp
                JOIN characters c ON bp.character_id = c.id
                LEFT JOIN groups g ON c.group_id = g.id
                WHERE bp.id = ?
            ''', (p['participant_id'],)).fetchone()
            if live is None:
                continue
            p.update(dict(live))
            p['char_name'] = battle_display_names.get(p['participant_id'], p['char_name'])
            # Same redaction as the battle menu: a Player doesn't get a
            # monster's (or an NPC-marked familiar's) HP just by opening
            # the map instead.
            if not g.is_dm and p['is_npc']:
                p['hidden_stats'] = True
                p['current_hp'] = None
                p['char_max_hp'] = None
        result.append(p)
    return result


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/pins')
@campaign_access_required
def api_map_pins_list(map_id):
    db = get_db()
    result = _map_pins_payload(db, map_id)
    db.close()
    return jsonify(result)


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/sync')
@campaign_access_required
def api_map_sync(map_id):
    """One request instead of three: the map editor's live refresh needs the grid/lock
    state, the drawings and (only while the map is linked to the battle) the pins.
    Serving them together means one serverless invocation and one database connection
    per refresh instead of three."""
    db = get_db()
    state = _map_state_payload(db, map_id)
    if state is None:
        db.close()
        abort(404)
    drawings = _map_drawings_payload(db, map_id)
    pins = _map_pins_payload(db, map_id) if state['linked_to_battle'] else None
    db.close()
    return jsonify({'state': state, 'drawings': drawings, 'pins': pins})


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/pins', methods=['POST'])
@campaign_access_required
@map_edit_allowed
def api_map_pins_add(map_id):
    data = request.get_json(force=True)
    pin_type = data.get('pin_type', 'prop')
    icon_key = data.get('icon_key')
    custom_name = data.get('custom_name')
    x = float(data.get('x', 0))
    y = float(data.get('y', 0))
    scale = float(data.get('scale', 1.0))
    rotation = float(data.get('rotation', 0))
    locked = 1 if data.get('locked') else 0
    db = get_db()
    participant_id = None
    if pin_type == 'prop':
        m = db.execute('SELECT linked_to_battle FROM maps WHERE id = ?', (map_id,)).fetchone()
        if m and m['linked_to_battle']:
            participant_id = _create_familiar_participant(db, icon_key, custom_name)
    cur = db.execute(
        "INSERT INTO map_pins (map_id, pin_type, icon_key, custom_name, x, y, scale, rotation, locked, participant_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (map_id, pin_type, icon_key, custom_name, x, y, scale, rotation, locked, participant_id)
    )
    db.commit()
    new_id = cur.lastrowid
    db.close()
    return jsonify({'id': new_id, 'participant_id': participant_id})


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/pins/<int:pin_id>/update', methods=['POST'])
@campaign_access_required
@map_edit_allowed
def api_map_pin_update(map_id, pin_id):
    data = request.get_json(force=True)
    db = get_db()
    fields = []
    values = []
    if 'x' in data:
        fields.append('x = ?'); values.append(float(data['x']))
    if 'y' in data:
        fields.append('y = ?'); values.append(float(data['y']))
    if 'scale' in data:
        fields.append('scale = ?'); values.append(float(data['scale']))
    if 'rotation' in data:
        fields.append('rotation = ?'); values.append(float(data['rotation']))
    if 'locked' in data:
        fields.append('locked = ?'); values.append(1 if data['locked'] else 0)
    if 'custom_name' in data:
        fields.append('custom_name = ?'); values.append(data['custom_name'])
        # Keep a familiar's battle-tracker name in sync with its pin name.
        pin_row = db.execute('SELECT participant_id, icon_key FROM map_pins WHERE id = ? AND map_id = ?', (pin_id, map_id)).fetchone()
        if pin_row and pin_row['participant_id']:
            new_name = data['custom_name'] or (pin_row['icon_key'] or 'familiar').capitalize()
            char_row = db.execute('SELECT character_id FROM battle_participants WHERE id = ?', (pin_row['participant_id'],)).fetchone()
            if char_row:
                db.execute('UPDATE characters SET name = ? WHERE id = ?', (new_name, char_row['character_id']))
    if 'is_npc' in data:
        # Whether a familiar counts as a PC or an NPC (and therefore whether
        # its stats get hidden from Players) is the DM's call alone.
        if not g.is_dm:
            db.close()
            abort(403)
        pin_row = db.execute('SELECT participant_id FROM map_pins WHERE id = ? AND map_id = ?', (pin_id, map_id)).fetchone()
        if pin_row and pin_row['participant_id']:
            char_row = db.execute('SELECT character_id FROM battle_participants WHERE id = ?', (pin_row['participant_id'],)).fetchone()
            if char_row:
                db.execute('UPDATE characters SET is_npc = ? WHERE id = ? AND is_temp_familiar = 1',
                           (1 if data['is_npc'] else 0, char_row['character_id']))
                db.commit()
    if fields:
        values.append(pin_id)
        values.append(map_id)
        db.execute(f'UPDATE map_pins SET {", ".join(fields)} WHERE id = ? AND map_id = ?', values)
        db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/pins/<int:pin_id>/delete', methods=['POST'])
@campaign_access_required
@map_edit_allowed
def api_map_pin_delete(map_id, pin_id):
    db = get_db()
    pin = db.execute('SELECT participant_id FROM map_pins WHERE id = ? AND map_id = ?', (pin_id, map_id)).fetchone()
    if pin and pin['participant_id']:
        char_row = db.execute('SELECT character_id FROM battle_participants WHERE id = ?', (pin['participant_id'],)).fetchone()
        if char_row:
            db.execute('DELETE FROM characters WHERE id = ? AND is_temp_familiar = 1', (char_row['character_id'],))
    db.execute('DELETE FROM map_pins WHERE id = ? AND map_id = ?', (pin_id, map_id))
    db.commit()
    db.close()
    return jsonify({'ok': True})


if __name__ == '__main__':
    init_db()
    print("=" * 60)
    print("D&D Campaign Manager running at http://127.0.0.1:5000")
    print("=" * 60)
    app.run(debug=True, port=5000)
