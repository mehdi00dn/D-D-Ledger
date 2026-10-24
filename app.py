import os
import re
import uuid
import io
import json
import zipfile
import hashlib
import hmac
import secrets
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, flash, send_file, g, abort, session
)
from werkzeug.security import generate_password_hash, check_password_hash, safe_join
from werkzeug.exceptions import HTTPException
import nh3
import psycopg.errors as pgerr
from markupsafe import Markup
import security
import mailer
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import realtime
import observability
from concurrent.futures import ThreadPoolExecutor
from flask import Response
import images
import fog
import dnd5e
import srd
import importer
import quotas
from storage import get_storage, StorageError, UPLOADS, TEMP
from database import get_db, init_db, init_app as init_database_app, UniqueViolation


app = Flask(__name__)
observability.init_app(app)


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

PUBLIC_ENDPOINTS = {'login', 'register', 'static', 'healthz', 'forgot_password', 'reset_password', 'robots_txt', 'security_txt'}


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


@app.route('/robots.txt')
def robots_txt():
    """Search engines may read the sign-in pages and nothing else: campaigns, accounts and uploads are private."""
    body = 'User-agent: *\nAllow: /login\nAllow: /register\nDisallow: /campaigns\nDisallow: /account\nDisallow: /notifications\nDisallow: /uploads\n'
    return Response(body, mimetype='text/plain', headers={'Cache-Control': 'public, max-age=86400'})


@app.route('/.well-known/security.txt')
def security_txt():
    """RFC 9116: where to report a vulnerability.  Published only once SECURITY_CONTACT (a mailto: or https: URL) is set."""
    contact = os.environ.get('SECURITY_CONTACT', '').strip()
    if not contact.startswith(('mailto:', 'https://')):
        abort(404)
    expires = (datetime.now(timezone.utc) + timedelta(days=365)).strftime('%Y-%m-%dT%H:%M:%SZ')
    body = 'Contact: %s\nExpires: %s\nPreferred-Languages: en\n' % (contact, expires)
    return Response(body, mimetype='text/plain', headers={'Cache-Control': 'public, max-age=86400'})


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


# Sign-in style forms: a second submit with the old token arrives after the first one already signed the person in
# (double tap, slow first request, autofill).  That is not an error, they are signed in -- send them on.
_AUTH_FORM_ENDPOINTS = {'login', 'register', 'forgot_password', 'reset_password'}


@app.before_request
def csrf_protect():
    """Every state-changing request must prove it came from one of our own pages:
    a per-session token (form field or X-CSRF-Token header), plus a same-origin check."""
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return
    if request.endpoint == 'logout':
        return          # signing out must always work: a stale page or an odd Origin header must never leave someone signed in
    origin = request.headers.get('Origin')
    if (origin and urlparse(origin).netloc != request.host) or request.headers.get('Sec-Fetch-Site') == 'cross-site':
        return _reject_request('Cross-site requests are not allowed.', 403)
    sent = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
    expected = session.get('csrf_token', '')
    if not expected or not sent or not hmac.compare_digest(str(sent), expected):
        if request.endpoint in _AUTH_FORM_ENDPOINTS and session.get('user_id'):
            return redirect(url_for('campaigns_list'))      # the page was submitted again after signing in succeeded: nothing to do
        return _reject_request('Your session expired or the form was not valid. Reload the page and try again.', 403)


def _wants_json():
    return request.is_json or '/api/' in request.path or request.path.startswith('/uploads/sign')


# One error shape for every AJAX/JSON route:  {"error": "<sentence safe to show>", "code": "<stable slug>", "request_id": "..."}
# (plus optional extra keys).  The frontend shows `error`, and can branch on `code` or the HTTP status.
API_ERROR_CODES = {400: 'bad_request', 401: 'unauthenticated', 403: 'forbidden', 404: 'not_found', 405: 'method_not_allowed',
                   409: 'conflict', 413: 'too_large', 415: 'unsupported_type', 422: 'unprocessable', 429: 'rate_limited',
                   500: 'server_error', 503: 'unavailable'}
API_ERROR_MESSAGES = {400: 'That request was not valid.', 401: 'Your session ended. Reload the page to sign in again.',
                      403: 'You do not have permission to do that.', 404: 'That could not be found.',
                      405: 'That action is not allowed here.', 409: 'That conflicts with the current state. Reload and try again.',
                      413: 'That is too large to send.', 415: 'That type of file is not supported.',
                      429: 'Too many requests. Wait a moment and try again.', 500: 'Something went wrong on our side.',
                      503: 'This is temporarily unavailable. Try again in a moment.'}


def api_error(message, status, code=None, **extra):
    """The standard JSON error response: `return api_error('Unknown pin type.', 400)`."""
    body = {'error': message or API_ERROR_MESSAGES.get(status, 'The request failed.'),
            'code': code or API_ERROR_CODES.get(status, 'error')}
    rid = g.get('request_id')
    if rid:
        body['request_id'] = rid
    body.update(extra)
    resp = jsonify(body)
    resp.status_code = status
    return resp


def _reject_request(message, status):
    if _wants_json():
        return api_error(message, status)
    return Response(message, status=status, mimetype='text/plain')


@app.errorhandler(HTTPException)
def http_error(exc):
    """abort(403), a 404 on an unknown id, a wrong method, an oversized body ... on an AJAX route answers in the standard
    JSON shape instead of an HTML page the frontend cannot read.  Normal pages keep Flask's HTML error pages."""
    if not _wants_json() or exc.code is None or exc.code < 400:
        return exc
    resp = api_error(API_ERROR_MESSAGES.get(exc.code), exc.code)
    for name, value in exc.get_headers():                      # keep Allow on a 405, Retry-After on a 429, ...
        if name.lower() not in ('content-type', 'content-length'):
            resp.headers[name] = value
    return resp


@app.before_request
def require_login():
    if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS:
        return
    if session.get('user_id') and not _session_is_current():
        session.clear()
    if not session.get('user_id'):
        if _wants_json():
            return api_error(None, 401, login_url=url_for('login'))
        return redirect(url_for('login', next=request.path))


@app.before_request
def limit_writes():
    """A signed-in account can only change things so fast (security.WINDOWS['write-user']): enough for a busy fight,
    not enough for a script to flood a campaign.  Reads are never limited; signing out always works."""
    if request.method in ('GET', 'HEAD', 'OPTIONS') or request.endpoint in (None, 'logout') or not session.get('user_id'):
        return
    db = get_db()
    try:
        wait = security.hit(db, app.secret_key, 'write-user', session['user_id'])
        db.commit()
    except pgerr.UndefinedTable:
        db.rollback()
        return
    finally:
        db.close()
    if wait:
        resp = api_error('You are doing that too fast. Wait a moment and try again.', 429) if _wants_json() \
            else Response('You are doing that too fast. Wait a moment and try again.', status=429, mimetype='text/plain')
        resp.headers['Retry-After'] = str(wait)
        return resp


def _session_is_current():
    """False when the account is gone or its session_version moved on (password change / log out everywhere)."""
    db = get_db()
    try:
        row = db.execute('SELECT session_version FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    except pgerr.UndefinedColumn:
        db.rollback()                       # code deployed before migration 0023: skip the check rather than fail every page
        app.logger.error('users.session_version is missing: apply migration 0023_session_version.sql')
        return True
    return row is not None and int(row['session_version']) == int(session.get('sv', 0))


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
# invitation_id / notification_id are not campaign-scoped: each is checked against the logged-in user in its route
# (an invitation is only ever visible to the person invited; a notification only to its recipient).
SCOPED_URL_ARGS = {'campaign_id', 'user_id', 'filename', 'token', 'invitation_id', 'notification_id'} | set(_SCOPE_OWNER_SQL) | set(_SCOPE_PARENT_SQL)


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


def _start_session(user_id, username, remember=True, version=0):
    """Fresh session on login: drops anything an earlier visitor of this browser left behind.
    remember=True issues a persistent cookie (PERMANENT_SESSION_LIFETIME); False makes it a
    plain session cookie that the browser drops when it's closed."""
    session.clear()
    session['user_id'] = user_id
    session['username'] = username
    session['sv'] = int(version or 0)
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
            return render_template('register.html', error=_wait_message(wait), username=username, next=request.form.get('next', '')), 429
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

        if error is None and db.execute('SELECT id FROM users WHERE lower(username) = lower(?)', (username,)).fetchone():
            error = 'That username is already taken.'
        if error:
            db.close()
            return render_template('register.html', error=error, username=username, next=request.form.get('next', ''))

        try:
            cur = db.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)',
                              (username, generate_password_hash(password)))
        except UniqueViolation:      # two people registering the same name at once
            db.rollback()
            db.close()
            return render_template('register.html', error='That username is already taken.', username=username, next=request.form.get('next', ''))
        user_id = cur.lastrowid
        db.commit()
        db.close()

        _start_session(user_id, username)
        return redirect(_safe_next(request.form.get('next', '')) or url_for('campaigns_list'))
    return render_template('register.html', error=None, username='', next=request.args.get('next', ''))


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

        user = db.execute('SELECT * FROM users WHERE lower(username) = lower(?)', (username,)).fetchone()
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
        _start_session(user['id'], user['username'], remember=bool(request.form.get('remember')),
                       version=dict(user).get('session_version', 0))
        return redirect(_safe_next(next_value or request.args.get('next')) or url_for('campaigns_list'))
    return render_template('login.html', error=None, username='', next=request.args.get('next', ''))


@app.route('/logout', methods=['POST'])
def logout():
    # Exempt from the CSRF check on purpose (see csrf_protect): the worst a forged logout can do is sign someone out,
    # while a failed check here used to leave a real user signed in with an error page.
    session.clear()
    return redirect(url_for('login'))


# ---------------- ACCOUNT / PROFILE ----------------
# A basic profile (display name, avatar, bio).  It belongs to the account, not to any campaign, and for now only its
# owner can see it (the sidebar and /account); showing it to other people is a separate, later decision.

DISPLAY_NAME_MAX = 40
BIO_MAX = 500


def _profile_error(display_name, bio):
    if len(display_name) > DISPLAY_NAME_MAX or not display_name.isprintable() or any(c in display_name for c in '<>'):
        return f'Display name must be at most {DISPLAY_NAME_MAX} characters, without < or >.'
    if len(bio) > BIO_MAX:
        return f'Bio must be at most {BIO_MAX} characters.'
    return None


def my_profile():
    """The logged-in user's profile row (cached for the request); None when logged out."""
    uid = session.get('user_id')
    if not uid:
        return None
    if '_profile' not in g:
        db = get_db()
        try:
            row = db.execute('SELECT id, username, display_name, avatar_path, bio FROM users WHERE id = ?', (uid,)).fetchone()
            g._profile = dict(row) if row else None
        except pgerr.UndefinedColumn:
            # Code deployed before migration 0022 was applied: every page renders this in the sidebar, so a missing
            # column must not take the whole site down.  Fall back to the username until the migration runs.
            db.rollback()
            app.logger.error('users.display_name is missing: apply migration 0022_user_profile.sql')
            g._profile = {'id': uid, 'username': session.get('username'), 'display_name': None, 'avatar_path': None, 'bio': None}
    return g._profile


app.jinja_env.globals['my_profile'] = my_profile
app.jinja_env.globals['mail_enabled'] = mailer.enabled


EMAIL_MAX = 254
EMAIL_TOKEN_MAX_AGE = 24 * 3600


def _email_error(address):
    """None when `address` looks like a usable email address (the real test is the verification link)."""
    if not address or len(address) > EMAIL_MAX or any(c.isspace() or c in '<>,;"\\' for c in address) or address.count('@') != 1:
        return 'Enter a valid email address.'
    local, domain = address.split('@')
    if not local or len(local) > 64 or '.' not in domain or domain.startswith('.') or domain.endswith('.') or '..' in domain or not domain.isascii():
        return 'Enter a valid email address.'
    return None


def _account_row(db, user_id):
    """The signed-in user's account row as a dict, with `email_ready` telling whether migration 0024 is applied."""
    try:
        row = db.execute('SELECT id, username, display_name, avatar_path, bio, email, email_verified_at FROM users WHERE id = ?', (user_id,)).fetchone()
        ready = True
    except pgerr.UndefinedColumn:
        db.rollback()
        app.logger.error('users.email is missing: apply migration 0024_user_email.sql')
        row = db.execute('SELECT id, username, display_name, avatar_path, bio FROM users WHERE id = ?', (user_id,)).fetchone()
        ready = False
    if row is None:
        return None
    d = dict(row)
    d.setdefault('email', None)
    d.setdefault('email_verified_at', None)
    d['email_ready'] = ready
    d['mail_enabled'] = mailer.enabled()
    return d


def _email_serializer():
    return URLSafeTimedSerializer(app.secret_key, salt='verify-email')


def _send_verification(db, user, address):
    """Rate-limit, then mail a verification link.  Returns an error message or None."""
    ip = client_ip()
    wait = security.mail_blocked_for(db, app.secret_key, user['id'], address, ip)
    if wait:
        return _wait_message(wait)
    token = _email_serializer().dumps({'u': user['id'], 'e': address.lower()})
    link = url_for('verify_email', token=token, _external=True)
    ctx = {'name': user.get('display_name') or user['username'], 'username': user['username'], 'link': link}
    try:
        security.record_mail(db, app.secret_key, user['id'], address, ip)
        db.commit()                                                     # the attempt counts even if sending fails
        mailer.send(address, 'Confirm your email for Campaign Ledger',
                    render_template('email/verify_email.txt', **ctx), render_template('email/verify_email.html', **ctx))
    except mailer.MailError as exc:
        return str(exc)
    return None


@app.route('/account/email', methods=['POST'])
def account_email():
    """Set (or change) the account's email and send the verification link.  Changing it clears the verified mark."""
    db = get_db()
    user = _account_row(db, session['user_id'])
    if user is None:
        session.clear()
        return redirect(url_for('login'))
    if not user['email_ready']:
        return api_error('Email needs a database update that has not been applied yet (migration 0024_user_email.sql).', 503, code='migration_pending')

    def fail(message, status=400):
        return render_template('account.html', profile=user, form=user, error=None, email_error=message), status

    address = request.form.get('email', '').strip()
    error = _email_error(address)
    if error:
        return fail(error)
    if not mailer.enabled():
        return fail('Email is not set up on this server yet, so addresses cannot be confirmed.', 503)
    if (user['email'] or '').lower() != address.lower():
        db.execute('UPDATE users SET email = ?, email_verified_at = NULL WHERE id = ?', (address, user['id']))
        db.commit()
        user['email'], user['email_verified_at'] = address, None
    elif user['email_verified_at']:
        return redirect(url_for('account', mail='already'))
    error = _send_verification(db, user, address)
    if error:
        return fail(error, 429 if error.startswith('Too many') else 502)
    return redirect(url_for('account', mail='sent'))


@app.route('/account/email/resend', methods=['POST'])
def account_email_resend():
    db = get_db()
    user = _account_row(db, session['user_id'])
    if user is None:
        session.clear()
        return redirect(url_for('login'))
    if not user['email_ready'] or not user['email'] or user['email_verified_at']:
        return redirect(url_for('account'))
    error = _send_verification(db, user, user['email'])
    if error:
        return render_template('account.html', profile=user, form=user, error=None, email_error=error), (429 if error.startswith('Too many') else 502)
    return redirect(url_for('account', mail='sent'))


@app.route('/account/email/remove', methods=['POST'])
def account_email_remove():
    db = get_db()
    user = _account_row(db, session['user_id'])
    if user is not None and user['email_ready']:
        db.execute('UPDATE users SET email = NULL, email_verified_at = NULL WHERE id = ?', (user['id'],))
        db.commit()
    return redirect(url_for('account', mail='removed'))


# ---- delete account (DL-38) ----

def _owned_campaigns(db, user_id):
    """Campaigns the user is an owner of, each with whether it would be deleted and who could take it over."""
    out = []
    for c in db.execute('SELECT c.id, c.name FROM campaigns c JOIN campaign_members cm ON cm.campaign_id = c.id '
                        "WHERE cm.user_id = ? AND cm.role = 'owner' ORDER BY c.name", (user_id,)).fetchall():
        others = db.execute('SELECT u.id, u.username, u.display_name, cm.role FROM campaign_members cm JOIN users u ON u.id = cm.user_id '
                            'WHERE cm.campaign_id = ? AND cm.user_id != ? ORDER BY u.username', (c['id'], user_id)).fetchall()
        out.append({'id': c['id'], 'name': c['name'], 'members': [dict(o) for o in others],
                    'has_other_owner': any(o['role'] == 'owner' for o in others)})
    return out


def owned_campaigns_for_page():
    if 'owned' not in g:
        g.owned = _owned_campaigns(get_db(), session['user_id']) if session.get('user_id') else []
    return g.owned


app.jinja_env.globals['owned_campaigns'] = owned_campaigns_for_page


@app.route('/account/delete', methods=['POST'])
def account_delete():
    """Delete the account.  Campaigns the user solely owns are deleted for good unless the user hands each one to a
    member first (the form says so); campaigns with another owner just lose this member."""
    db = get_db()
    user = _account_row(db, session['user_id'])
    if user is None:
        session.clear()
        return redirect(url_for('login'))
    row = db.execute('SELECT password_hash FROM users WHERE id = ?', (user['id'],)).fetchone()
    ip = client_ip()

    def fail(message, status=400):
        return render_template('account.html', profile=user, form=user, error=None, delete_error=message), status

    wait = security.login_blocked_for(db, app.secret_key, user['username'], ip)
    if wait:
        return fail(_wait_message(wait), 429)
    if not row['password_hash'] or not check_password_hash(row['password_hash'], request.form.get('current_password', '')[:1024]):
        security.record_login_failure(db, app.secret_key, user['username'], ip)
        db.commit()
        return fail('Your current password is not correct.')
    if request.form.get('confirm_delete') != 'DELETE':
        return fail('Type DELETE to confirm.')

    plan = []                                      # (campaign, new owner id or None)
    for c in _owned_campaigns(db, user['id']):
        if c['has_other_owner']:
            plan.append((c, None))
            continue
        choice = request.form.get('transfer_%d' % c['id'], '')
        new_owner = None
        if choice:
            if not choice.isdigit() or int(choice) not in {m['id'] for m in c['members']}:
                return fail('Pick one of the campaign\'s members to hand it to.')
            new_owner = int(choice)
        plan.append((c, new_owner))

    files = []
    for c, new_owner in plan:
        if c['has_other_owner']:
            continue                               # membership goes with the user row below
        if new_owner is not None:
            db.execute("UPDATE campaign_members SET role = 'owner', status = 'dm' WHERE campaign_id = ? AND user_id = ?", (c['id'], new_owner))
            notify(db, new_owner, 'ownership_transferred', '%s handed you the campaign %s' % (user['username'], c['name']),
                   link=url_for('characters_list', campaign_id=c['id']))
        else:
            files += _delete_campaign_rows(db, c['id'])
    if user['avatar_path']:
        files.append(user['avatar_path'])
    if user['avatar_path']:
        db.execute('DELETE FROM stored_files WHERE key = ?', (user['avatar_path'],))
    db.execute('UPDATE stored_files SET user_id = NULL WHERE user_id = ?', (user['id'],))       # files stay with their campaigns
    db.execute('DELETE FROM users WHERE id = ?', (user['id'],))    # memberships, invitations, notifications cascade; "made by" fields clear
    db.commit()
    db.close()
    delete_uploads(files)
    if user.get('email') and user.get('email_verified_at'):
        try:
            mailer.send(user['email'], 'Your Campaign Ledger account was deleted',
                        render_template('email/account_deleted.txt', name=user['display_name'] or user['username'], username=user['username']))
        except mailer.MailError:
            pass
    session.clear()
    return redirect(url_for('login', deleted=1))


# ---- password reset by email (DL-38) ----
RESET_TOKEN_MAX_AGE = 3600


def _reset_serializer():
    return URLSafeTimedSerializer(app.secret_key, salt='reset-password')


def _password_fingerprint(password_hash):
    """Ties a reset link to the password it was issued for, so it stops working once the password changes."""
    return hashlib.sha256((password_hash or '').encode()).hexdigest()[:16]


NO_CONFIRMED_ACCOUNT = ('No account has this email address confirmed. Check the spelling, or log in and confirm it on your account page first. '
                        'Accounts without a confirmed email cannot be reset this way.')


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Ask for a reset link.  The address must be one an account has confirmed; if not, the page says so (the throttles
    below keep that from being used to scan for addresses in bulk)."""
    if session.get('user_id'):
        return redirect(url_for('account'))
    if not mailer.enabled():
        return render_template('forgot_password.html', sent=False, email='', error='Password reset by email is not available on this server yet.'), 503
    if request.method == 'GET':
        return render_template('forgot_password.html', sent=False, email='', error=None)
    address = request.form.get('email', '').strip()
    if _email_error(address):
        return render_template('forgot_password.html', sent=False, email=address[:254], error='Enter a valid email address.'), 400
    db = get_db()
    ip = client_ip()
    wait = security.mail_blocked_for(db, app.secret_key, 0, address, ip)
    if wait:
        return render_template('forgot_password.html', sent=False, email=address, error=_wait_message(wait)), 429
    security.record_mail(db, app.secret_key, 0, address, ip)
    db.commit()
    try:
        user = db.execute('SELECT id, username, display_name, password_hash FROM users '
                          'WHERE lower(email) = lower(?) AND email_verified_at IS NOT NULL', (address,)).fetchone()
    except pgerr.UndefinedColumn:
        db.rollback()
        user = None
    if user is None or not user['password_hash']:
        db.close()
        return render_template('forgot_password.html', sent=False, email=address, error=NO_CONFIRMED_ACCOUNT), 404
    token = _reset_serializer().dumps({'u': user['id'], 'f': _password_fingerprint(user['password_hash'])})
    ctx = {'name': user['display_name'] or user['username'], 'username': user['username'],
           'link': url_for('reset_password', token=token, _external=True)}
    try:
        mailer.send(address, 'Reset your Campaign Ledger password',
                    render_template('email/password_reset.txt', **ctx), render_template('email/password_reset.html', **ctx))
    except mailer.MailError:                    # the details are already in the log
        db.close()
        return render_template('forgot_password.html', sent=False, email=address,
                               error='The email could not be sent right now. Try again in a few minutes.'), 502
    db.close()
    return render_template('forgot_password.html', sent=True, email='', error=None)


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """The link from the reset email: choose a new password.  Ends every session of the account."""
    try:
        data = _reset_serializer().loads(token, max_age=RESET_TOKEN_MAX_AGE)
    except BadSignature:                            # includes SignatureExpired
        return render_template('reset_password.html', invalid=True), 400
    db = get_db()
    user = db.execute('SELECT id, username, display_name, email, password_hash FROM users WHERE id = ?', (data.get('u'),)).fetchone()
    if user is None or not user['password_hash'] or _password_fingerprint(user['password_hash']) != data.get('f'):
        return render_template('reset_password.html', invalid=True), 400
    if request.method == 'GET':
        return render_template('reset_password.html', invalid=False, error=None)
    new = request.form.get('new_password', '')
    error = None
    if new != request.form.get('confirm_password', ''):
        error = 'The new passwords do not match.'
    elif len(new) < 8:
        error = 'Password must be at least 8 characters.'
    elif len(new) > 128:
        error = 'Password must be at most 128 characters.'
    if error:
        return render_template('reset_password.html', invalid=False, error=error), 400
    db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (generate_password_hash(new), user['id']))
    security.clear_login_failures(db, app.secret_key, user['username'], client_ip())
    db.commit()
    _bump_session_version(db, user['id'])           # every device is signed out, including whoever had the old password
    if user['email']:
        try:
            mailer.send(user['email'], 'Your Campaign Ledger password was changed',
                        render_template('email/password_changed.txt', name=user['display_name'] or user['username'], username=user['username']))
        except mailer.MailError:
            pass
    session.clear()
    return redirect(url_for('login', reset=1))


@app.route('/verify-email/<token>')
def verify_email(token):
    """The link from the verification email.  Needs the same signed-in account the link was issued for."""
    try:
        data = _email_serializer().loads(token, max_age=EMAIL_TOKEN_MAX_AGE)
    except SignatureExpired:
        return redirect(url_for('account', mail='expired'))
    except BadSignature:
        return redirect(url_for('account', mail='invalid'))
    if data.get('u') != session.get('user_id'):
        return redirect(url_for('account', mail='wrong_account'))
    db = get_db()
    user = _account_row(db, session['user_id'])
    if user is None or not user['email_ready'] or (user['email'] or '').lower() != data.get('e'):
        return redirect(url_for('account', mail='invalid'))        # the address was changed or removed since
    if user['email_verified_at']:
        return redirect(url_for('account', mail='verified'))
    try:
        db.execute('UPDATE users SET email_verified_at = ' + _UTC_NOW + ' WHERE id = ?', (user['id'],))
        db.commit()
    except UniqueViolation:                                        # someone else verified this address first
        db.rollback()
        return redirect(url_for('account', mail='taken'))
    return redirect(url_for('account', mail='verified'))


@app.route('/account', methods=['GET', 'POST'])
def account():
    db = get_db()
    try:
        user = _account_row(db, session['user_id'])
    except pgerr.UndefinedColumn:
        db.rollback()
        return api_error('Profiles need a database update that has not been applied yet (migration 0022_user_profile.sql).', 503, code='migration_pending')
    if user is None:
        session.clear()
        return redirect(url_for('login'))
    if request.method == 'POST':
        display_name = ' '.join(request.form.get('display_name', '').split())
        bio = request.form.get('bio', '').replace('\r\n', '\n').strip()
        error = _profile_error(display_name, bio)
        if error:
            return render_template('account.html', profile=user, form={'display_name': display_name, 'bio': bio}, error=error), 400
        new_avatar = save_upload_field('avatar', 'avatars')      # a full quota is answered by the app-wide banner
        old_avatar = user['avatar_path']
        if new_avatar:
            db.execute('UPDATE users SET avatar_path = ? WHERE id = ?', (new_avatar, user['id']))
            delete_upload(old_avatar)
        elif request.form.get('remove_avatar') == '1' and old_avatar:
            db.execute('UPDATE users SET avatar_path = NULL WHERE id = ?', (user['id'],))
            delete_upload(old_avatar)
        db.execute('UPDATE users SET display_name = ?, bio = ? WHERE id = ?', (display_name or None, bio or None, user['id']))
        db.commit()
        return redirect(url_for('account', saved=1))
    return render_template('account.html', profile=user, form=user, error=None)


@app.route('/account/username', methods=['POST'])
def account_username():
    """Change the username (what you log in with): needs the current password, counted like a login attempt so the form
    cannot be used to guess it.  Sessions stay signed in; a confirmed email address is told about the change."""
    db = get_db()
    user = _account_row(db, session['user_id'])
    if user is None:
        session.clear()
        return redirect(url_for('login'))
    row = db.execute('SELECT password_hash FROM users WHERE id = ?', (user['id'],)).fetchone()
    new = request.form.get('username', '').strip()
    ip = client_ip()

    def fail(message, status=400):
        db.close()
        return render_template('account.html', profile=user, form=user, error=None, username_error=message, new_username=new[:64]), status

    wait = security.login_blocked_for(db, app.secret_key, user['username'], ip)
    if wait:
        return fail(_wait_message(wait), 429)
    if not row['password_hash'] or not check_password_hash(row['password_hash'], request.form.get('current_password', '')[:1024]):
        security.record_login_failure(db, app.secret_key, user['username'], ip)
        db.commit()
        return fail('Your current password is not correct.')
    old = user['username']
    if _username_error(new) or not new:
        return fail(_username_error(new) or 'Enter a new username.')
    if new == old:
        return fail('That is already your username.')
    if db.execute('SELECT id FROM users WHERE lower(username) = lower(?) AND id <> ?', (new, user['id'])).fetchone():
        return fail('That username is already taken.')
    try:
        db.execute('UPDATE users SET username = ? WHERE id = ?', (new, user['id']))
    except UniqueViolation:                       # someone took it a moment ago
        db.rollback()
        return fail('That username is already taken.')
    security.clear_login_failures(db, app.secret_key, old, ip)
    db.commit()
    db.close()
    session['username'] = new
    g.pop('_profile', None)
    if user.get('email') and user.get('email_verified_at') and mailer.enabled():
        try:
            mailer.send(user['email'], 'Your Campaign Ledger username was changed',
                        render_template('email/username_changed.txt', name=user['display_name'] or new, old=old, new=new))
        except mailer.MailError:
            pass                                  # the change is made; the notice is a courtesy and the failure is in the log
    return redirect(url_for('account', un=1))


@app.route('/account/password', methods=['POST'])
def account_password():
    """Change the password: needs the current one.  A wrong current password counts as a failed login
    (same throttle), so this form cannot be used to guess passwords."""
    db = get_db()
    user = db.execute('SELECT id, username, password_hash, display_name, avatar_path, bio FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    if user is None:
        session.clear()
        return redirect(url_for('login'))
    current = request.form.get('current_password', '')[:1024]
    new = request.form.get('new_password', '')
    confirm = request.form.get('confirm_password', '')
    ip = client_ip()

    def fail(message, status=400):
        return render_template('account.html', profile=_account_row(db, user['id']), form=user, error=None, password_error=message), status

    wait = security.login_blocked_for(db, app.secret_key, user['username'], ip)
    if wait:
        return fail(_wait_message(wait), 429)
    if not user['password_hash'] or not check_password_hash(user['password_hash'], current):
        security.record_login_failure(db, app.secret_key, user['username'], ip)
        db.commit()
        return fail('Your current password is not correct.')
    if new != confirm:
        return fail('The new passwords do not match.')
    if len(new) < 8:
        return fail('Password must be at least 8 characters.')
    if len(new) > 128:
        return fail('Password must be at most 128 characters.')
    if new == current:
        return fail('Choose a password different from the current one.')
    db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (generate_password_hash(new), user['id']))
    security.clear_login_failures(db, app.secret_key, user['username'], ip)
    db.commit()
    version = _bump_session_version(db, user['id'])                        # ends every other session of this account
    permanent = session.permanent
    _start_session(user['id'], user['username'], remember=permanent, version=version)   # fresh session and CSRF token
    return redirect(url_for('account', pw=1))


def _bump_session_version(db, user_id):
    """Invalidate all sessions of the account; returns the new version (0 if migration 0023 is not applied yet)."""
    try:
        db.execute('UPDATE users SET session_version = session_version + 1 WHERE id = ?', (user_id,))
        row = db.execute('SELECT session_version FROM users WHERE id = ?', (user_id,)).fetchone()
        db.commit()
        return int(row['session_version'])
    except pgerr.UndefinedColumn:
        db.rollback()
        app.logger.error('users.session_version is missing: apply migration 0023_session_version.sql')
        return 0


@app.route('/account/export', methods=['POST'])
def account_export():
    """Download everything the app holds about the logged-in user as one JSON file: the account, memberships, and the
    characters, factions, maps and dice rolls they made.  Never includes the password hash, session data or anyone
    else's account details."""
    db = get_db()
    user = _account_row(db, session['user_id'])
    if user is None:
        session.clear()
        return redirect(url_for('login'))
    uid = user['id']
    joined = db.execute('SELECT created_at FROM users WHERE id = ?', (uid,)).fetchone()['created_at']
    rows = lambda sql, *a: [dict(r) for r in db.execute(sql, a).fetchall()]
    data = {
        'exported_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'account': {
            'username': user['username'], 'display_name': user['display_name'], 'bio': user['bio'],
            'email': user.get('email'), 'email_verified': bool(user.get('email_verified_at')),
            'created_at': joined,
        },
        'campaign_memberships': rows(
            'SELECT c.name AS campaign, cm.role, cm.status FROM campaign_members cm JOIN campaigns c ON c.id = cm.campaign_id '
            'WHERE cm.user_id = ? ORDER BY c.id', uid),
        'characters_you_made': rows(
            'SELECT c.name AS campaign, ch.* FROM characters ch JOIN campaigns c ON c.id = ch.campaign_id WHERE ch.created_by = ? ORDER BY ch.id', uid),
        'factions_you_made': rows(
            'SELECT c.name AS campaign, g.* FROM groups g JOIN campaigns c ON c.id = g.campaign_id WHERE g.created_by = ? ORDER BY g.id', uid),
        'maps_you_made': rows(
            'SELECT c.name AS campaign, m.id, m.name, m.created_at FROM maps m JOIN campaigns c ON c.id = m.campaign_id WHERE m.created_by = ? ORDER BY m.id', uid),
        'dice_rolls': rows(
            'SELECT c.name AS campaign, d.label, d.dice, d.modifier, d.total, d.mode, d.created_at FROM dice_rolls d '
            'JOIN campaigns c ON c.id = d.campaign_id WHERE d.user_id = ? ORDER BY d.id', uid),
    }
    for item in data['characters_you_made']:
        item.pop('created_by', None)
    for key in ('factions_you_made', 'maps_you_made'):
        for item in data[key]:
            item.pop('created_by', None)
    db.close()
    body = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    return Response(body, mimetype='application/json',
                    headers={'Content-Disposition': 'attachment; filename="campaign-ledger-%s.json"' % re.sub(r'[^A-Za-z0-9_-]+', '_', user['username'])})


@app.route('/account/sessions/logout-all', methods=['POST'])
def account_logout_all():
    """Sign out every other browser/device; this one stays signed in."""
    db = get_db()
    try:
        db.execute('SELECT session_version FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    except pgerr.UndefinedColumn:
        db.rollback()
        return api_error('Signing out everywhere needs a database update that has not been applied yet (migration 0023_session_version.sql).', 503, code='migration_pending')
    version = _bump_session_version(db, session['user_id'])
    _start_session(session['user_id'], session['username'], remember=session.permanent, version=version)
    return redirect(url_for('account', out=1))


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
        g.real_is_dm = g.campaign_status == 'dm'
        # DL-42: a DM who switched on "View as player" is treated as a Player by every route, so
        # what they see is exactly what the server sends a Player (redaction included).
        g.previewing = bool(g.real_is_dm and campaign_id in (session.get('preview_player') or []))
        if g.previewing:
            g.campaign_status = 'player'
        g.is_dm = g.campaign_status == 'dm'
        session['last_campaign_id'] = campaign_id
        return view_func(*args, **kwargs)
    return wrapped


def npcs_hidden_from_viewer():
    """True when this campaign hides its NPCs and the person asking is not the DM (the DM viewing as a player counts as a Player)."""
    c = getattr(g, 'campaign', None)
    return bool(c is not None and c['hide_npcs'] and not g.is_dm)


def npc_is_hidden(is_npc, created_by):
    """One character's visibility: an NPC is hidden from a Player while the switch is on, except from the Player who made it."""
    mine = created_by == session.get('user_id') and not getattr(g, 'previewing', False)     # "View as player" shows what a Player who did not make it sees
    return bool(is_npc) and npcs_hidden_from_viewer() and not mine


def npc_filter(alias='c'):
    """(sql, params) to AND onto a characters query so a Player never gets a hidden NPC; empty when nothing is hidden."""
    if not npcs_hidden_from_viewer():
        return '', []
    return f' AND ({alias}.is_npc = 0 OR {alias}.created_by = ?)', [None if getattr(g, 'previewing', False) else session.get('user_id')]


@app.route('/campaigns/<int:campaign_id>/preview-as-player', methods=['POST'])
@campaign_access_required
def preview_as_player():
    """DM only (judged by their real status, so it also works to switch the preview off while it
    is on).  Turns "View as player" on or off for this campaign in the DM's own session."""
    if not g.real_is_dm:
        abort(403)
    on = request.form.get('on') == '1'
    ids = [i for i in (session.get('preview_player') or []) if i != g.campaign_id]
    if on:
        ids.append(g.campaign_id)
    session['preview_player'] = ids
    # Go back to the page the DM was on (same campaign only), else the battle screen.
    path = urlparse(request.referrer or '').path
    prefix = f'/campaigns/{g.campaign_id}/'
    return redirect(path if path.startswith(prefix) and not path.startswith('//') else url_for('battle_view'))


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


def can_delete(created_by):
    """Deleting is for the campaign's owner and for whoever made the thing -- nobody else, DMs included."""
    return g.campaign_role == 'owner' or (created_by is not None and created_by == session.get('user_id'))


def require_can_delete(created_by):
    if not can_delete(created_by):
        abort(403)


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
        description = sanitize_bio(request.form.get('description'))
        avatar_path = save_upload_field('avatar', 'avatars')
        setting = _resolve_campaign_setting(request.form)
        status = _campaign_status_field(request.form)
        access_mode = _campaign_access_field(request.form)
        db = get_db()
        owned = db.execute("SELECT COUNT(*) AS n FROM campaign_members WHERE user_id = ? AND role = 'owner'", (session['user_id'],)).fetchone()['n']
        wait = 0 if owned >= security.MAX_OWNED_CAMPAIGNS else security.hit(db, app.secret_key, 'campaign-create', session['user_id'])
        if owned >= security.MAX_OWNED_CAMPAIGNS or wait:
            db.commit()
            db.close()
            msg = ('You already own %d campaigns, the most one account can have. Delete one you no longer use first.' % security.MAX_OWNED_CAMPAIGNS
                   if owned >= security.MAX_OWNED_CAMPAIGNS else 'You are creating campaigns too fast. Try again in a little while.')
            resp = Response(msg, status=429, mimetype='text/plain')
            if wait:
                resp.headers['Retry-After'] = str(wait)
            return resp
        db.commit()
        cur = db.execute('''
            INSERT INTO campaigns (name, description, avatar_path, setting, status, access_mode)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (name, description, avatar_path, setting, status, access_mode))
        campaign_id = cur.lastrowid
        db.execute('INSERT INTO campaign_members (campaign_id, user_id, role, status) VALUES (?, ?, ?, ?)',
                   (campaign_id, session['user_id'], 'owner', 'dm'))
        if avatar_path:                                             # the cover image now belongs to this campaign's quota
            db.execute('UPDATE stored_files SET campaign_id = ? WHERE key = ?', (campaign_id, avatar_path))
        db.commit()
        db.close()
        session['last_campaign_id'] = campaign_id
        return redirect(url_for('campaign_home', campaign_id=campaign_id))
    setting_preset, setting_custom = _split_campaign_setting(None)
    return render_template('campaign_form.html', campaign=None, members=None, bio_entries=[{'t': '', 'h': False}], notes_entries=[{'t': '', 'h': False}], assignable=[],
                            setting_preset=setting_preset, setting_custom=setting_custom)


@app.route('/campaigns/<int:campaign_id>/edit', methods=['GET', 'POST'])
@campaign_access_required
def campaign_edit():
    db = get_db()
    if request.method == 'POST':
        if g.campaign_role != 'owner':
            abort(403)
        name = request.form.get('name', '').strip() or 'Untitled Campaign'
        description = sanitize_bio(request.form.get('description'))
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
        if 'notes' in request.form:                         # campaign notes: the same boxes as a character's notes
            db.execute('UPDATE campaigns SET notes = ? WHERE id = ?', (sanitize_notes_payload(request.form.get('notes')), g.campaign_id))
        if access_mode != 'invite_link':           # leaving invite-link mode kills every outstanding link for good
            db.execute('UPDATE campaign_invite_links SET revoked_at = ' + _UTC_NOW + ' WHERE campaign_id = ? AND revoked_at IS NULL',
                       (g.campaign_id,))
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
    invitations = db.execute('''
        SELECT i.invited_user_id AS id, i.status, u.username
        FROM campaign_invitations i JOIN users u ON u.id = i.invited_user_id
        WHERE i.campaign_id = ? AND i.state = 'pending'
        ORDER BY lower(u.username) ASC
    ''', (g.campaign_id,)).fetchall() if g.campaign_role == 'owner' else []
    invite_links = db.execute('''
        SELECT id, created_at, expires_at, max_uses, use_count, revoked_at,
               (expires_at <= ''' + _UTC_NOW + ''') AS expired
        FROM campaign_invite_links WHERE campaign_id = ? ORDER BY id DESC LIMIT 15
    ''', (g.campaign_id,)).fetchall() if g.campaign_role == 'owner' and campaign['access_mode'] == 'invite_link' else []
    storage = quotas.summary(db, session['user_id'], g.campaign_id)
    assignable = db.execute('SELECT id, name, is_npc, player_id FROM characters WHERE campaign_id = ? AND is_temp_familiar = 0 '
                            'ORDER BY is_npc, lower(name)', (g.campaign_id,)).fetchall() if (g.real_is_dm or g.campaign_role == 'owner') else []
    db.close()
    new_invite_link = session.pop('new_invite_link', None) if g.campaign_role == 'owner' else None
    setting_preset, setting_custom = _split_campaign_setting(campaign['setting'])
    return render_template('campaign_form.html', campaign=campaign, members=members, invitations=invitations, assignable=assignable,
                            bio_entries=notes_parse_entries(campaign['description']) or [{'t': '', 'h': False}],
                            notes_entries=notes_parse_entries(campaign['notes']) or [{'t': '', 'h': False}],
                            invite_links=invite_links, new_invite_link=new_invite_link, storage=storage,
                            setting_preset=setting_preset, setting_custom=setting_custom)


_UTC_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"
MAX_PENDING_INVITATIONS = 25          # open invitations per campaign (keeps invites from being used to spam people)
STATUS_LABELS = {'dm': 'DM', 'player': 'Player'}


NOTIFICATION_KEEP = 200        # notifications kept per user; older ones are deleted as new ones arrive
ACTIVITY_KEEP = 200            # events kept per campaign; older ones are deleted as new ones arrive


def log_activity(db, campaign_id, kind, text, link=None):
    """Record one event in the campaign's activity feed by the logged-in user (the caller commits).  `text` says what
    happened without naming the actor: the page adds the name when it is shown, so a deleted account leaves no name."""
    if link and not (link.startswith('/') and not link.startswith('//')):
        link = None
    try:
        db.execute('INSERT INTO campaign_activity (campaign_id, user_id, kind, text, link) VALUES (?, ?, ?, ?, ?)',
                   (campaign_id, session.get('user_id'), kind, text[:300], link))
        db.execute('DELETE FROM campaign_activity WHERE campaign_id = ? AND id NOT IN '
                   '(SELECT id FROM campaign_activity WHERE campaign_id = ? ORDER BY id DESC LIMIT ?)',
                   (campaign_id, campaign_id, ACTIVITY_KEEP))
    except pgerr.UndefinedTable:
        db.rollback()                                       # code deployed before migration 0025: skip rather than fail the action
        app.logger.error('campaign_activity is missing: apply migration 0025_campaign_activity.sql')


def notify(db, user_id, kind, title, body='', link=None, invitation_id=None):
    """Add an in-app notification for one user (the caller commits)."""
    if link and not (link.startswith('/') and not link.startswith('//')):
        link = None                                         # notification links are in-app paths only, never an external URL
    db.execute('INSERT INTO notifications (user_id, kind, title, body, link, invitation_id) VALUES (?, ?, ?, ?, ?, ?)',
               (user_id, kind, title[:200], body[:500], link, invitation_id))
    db.execute('DELETE FROM notifications WHERE user_id = ? AND id NOT IN '
               '(SELECT id FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT ?)',
               (user_id, user_id, NOTIFICATION_KEEP))      # DL-141: a user's inbox stays bounded without a cron job


def unread_notification_count():
    """Unread notifications for the logged-in user (one cheap query per request, shown as the sidebar badge)."""
    if not session.get('user_id'):
        return 0
    if not hasattr(g, '_unread_notifications'):
        db = get_db()
        g._unread_notifications = db.execute('SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND read_at IS NULL',
                                              (session['user_id'],)).fetchone()['n']
        db.close()
    return g._unread_notifications


app.jinja_env.globals['unread_notification_count'] = unread_notification_count


@app.route('/campaigns/<int:campaign_id>/members/add', methods=['POST'])
@campaign_access_required
def campaign_member_add():
    """Owner-only: INVITE a user.  Nobody is added to the campaign here -- the invited user gets a
    notification and joins only if they accept (see invitation_accept)."""
    if g.campaign_role != 'owner':
        abort(403)
    data = request.get_json(silent=True) or request.form
    username = (data.get('username') or '').strip()
    status = data.get('status') if data.get('status') in ('dm', 'player') else 'player'
    db = get_db()
    if not username:
        db.close()
        return _reject_request('Enter a username to invite.', 400)
    user = db.execute('SELECT id, username FROM users WHERE lower(username) = lower(?)', (username,)).fetchone()
    if not user:
        db.close()
        return _reject_request('No user found with that username.', 404)
    if user['id'] == session['user_id']:
        db.close()
        return _reject_request("You're already the owner of this campaign.", 400)
    existing = db.execute('SELECT id FROM campaign_members WHERE campaign_id = ? AND user_id = ?',
                           (g.campaign_id, user['id'])).fetchone()
    if existing:
        db.close()
        return _reject_request('%s is already a member of this campaign.' % user['username'], 400)
    pending = db.execute("SELECT COUNT(*) AS n FROM campaign_invitations WHERE campaign_id = ? AND state = 'pending'",
                         (g.campaign_id,)).fetchone()['n']
    if pending >= MAX_PENDING_INVITATIONS:
        db.close()
        return _reject_request('This campaign already has %d open invitations. Cancel some before inviting more.' % MAX_PENDING_INVITATIONS, 400)
    try:
        # Invitees never join as owner -- ownership is granted separately (see campaign_member_make_owner).
        inv_id = db.execute('INSERT INTO campaign_invitations (campaign_id, invited_user_id, invited_by_id, status) VALUES (?, ?, ?, ?)',
                            (g.campaign_id, user['id'], session['user_id'], status)).lastrowid
    except UniqueViolation:         # the partial unique index: this person already has an open invitation
        db.rollback()
        db.close()
        return _reject_request('%s already has a pending invitation to this campaign.' % user['username'], 400)
    notify(db, user['id'], 'campaign_invite', '%s invited you to %s' % (session['username'], g.campaign['name']),
           'You would join as a %s.' % STATUS_LABELS[status], url_for('notifications_page'), inv_id)
    db.commit()
    db.close()
    if _wants_json():
        return jsonify({'ok': True, 'pending': True, 'username': user['username'], 'status': status,
                        'user_id': user['id'], 'invitation_id': inv_id})
    return redirect(url_for('campaign_edit', campaign_id=g.campaign_id))


@app.route('/campaigns/<int:campaign_id>/members/<int:user_id>/uninvite', methods=['POST'])
@campaign_access_required
def campaign_member_uninvite(user_id):
    """Owner-only: withdraw a pending invitation."""
    if g.campaign_role != 'owner':
        abort(403)
    db = get_db()
    inv = db.execute("SELECT id FROM campaign_invitations WHERE campaign_id = ? AND invited_user_id = ? AND state = 'pending'",
                     (g.campaign_id, user_id)).fetchone()
    if inv:
        db.execute("UPDATE campaign_invitations SET state = 'revoked', responded_at = " + _UTC_NOW + " WHERE id = ? AND state = 'pending'", (inv['id'],))
        db.execute('UPDATE notifications SET read_at = ' + _UTC_NOW + ' WHERE invitation_id = ? AND read_at IS NULL', (inv['id'],))
        db.commit()
    db.close()
    if _wants_json():
        return jsonify({'ok': True})
    return redirect(url_for('campaign_edit', campaign_id=g.campaign_id))


# ---------------- INVITE LINKS ----------------
# The owner creates a link (shown ONCE -- only its hash is stored).  Whoever opens it while logged in can join
# as a Player until it expires, runs out of uses or is revoked.  Links work only while the campaign's access
# mode is "invite_link"; switching to Private revokes them all.  Every way a link can be unusable (unknown,
# expired, revoked, used up, campaign not accepting links) looks identical to the visitor.

INVITE_LINK_LIFETIMES = {'1h': 3600, '1d': 86400, '7d': 7 * 86400, '30d': 30 * 86400}
INVITE_LINK_MAX_USES_CAP = 500
MAX_ACTIVE_INVITE_LINKS = 10
_LINK_LIVE_SQL = ("revoked_at IS NULL AND expires_at > " + _UTC_NOW +
                  " AND (max_uses IS NULL OR use_count < max_uses)")


def _hash_invite_token(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


@app.route('/campaigns/<int:campaign_id>/invite-links', methods=['POST'])
@campaign_access_required
def invite_link_create():
    if g.campaign_role != 'owner':
        abort(403)
    data = request.get_json(silent=True) or request.form
    # Creating a link from the edit page also switches the campaign to Invite Link ("enable"), so the owner can get a
    # link on the spot without saving the whole form first.  Without it, a private campaign refuses.
    enable = str(data.get('enable') or '').lower() in ('1', 'true', 'yes', 'on')
    if g.campaign['access_mode'] != 'invite_link' and not enable:
        return _reject_request('Switch Access & Privacy to "Invite Link" before creating links.', 400)
    lifetime = INVITE_LINK_LIFETIMES.get(data.get('expires_in', '7d'))
    if lifetime is None:
        return _reject_request('Pick how long the link should last.', 400)
    raw_uses = str(data.get('max_uses') or '').strip()
    max_uses = None
    if raw_uses:
        if not raw_uses.isdigit() or not 1 <= int(raw_uses) <= INVITE_LINK_MAX_USES_CAP:
            return _reject_request('Max uses must be a whole number from 1 to %d, or empty for unlimited.' % INVITE_LINK_MAX_USES_CAP, 400)
        max_uses = int(raw_uses)
    db = get_db()
    active = db.execute('SELECT COUNT(*) AS n FROM campaign_invite_links WHERE campaign_id = ? AND ' + _LINK_LIVE_SQL,
                        (g.campaign_id,)).fetchone()['n']
    if active >= MAX_ACTIVE_INVITE_LINKS:
        db.close()
        return _reject_request('This campaign already has %d active links. Revoke one first.' % MAX_ACTIVE_INVITE_LINKS, 400)
    token = secrets.token_urlsafe(32)
    if g.campaign['access_mode'] != 'invite_link':
        db.execute("UPDATE campaigns SET access_mode = 'invite_link' WHERE id = ?", (g.campaign_id,))
    cur = db.execute('INSERT INTO campaign_invite_links (campaign_id, created_by_id, token_hash, expires_at, max_uses) '
                     'VALUES (?, ?, ?, ' + _UTC_NOW + " + (? * interval '1 second'), ?)",
                     (g.campaign_id, session['user_id'], _hash_invite_token(token), lifetime, max_uses))
    link_id = cur.lastrowid
    expires_at = db.execute('SELECT expires_at FROM campaign_invite_links WHERE id = ?', (link_id,)).fetchone()['expires_at']
    db.commit()
    db.close()
    url = url_for('join_via_link', token=token, _external=True)
    if _wants_json():
        return jsonify({'ok': True, 'url': url, 'id': link_id, 'expires_at': str(expires_at), 'max_uses': max_uses})
    session['new_invite_link'] = url                # shown once on the next edit page, then forgotten
    return redirect(url_for('campaign_edit', campaign_id=g.campaign_id))


@app.route('/campaigns/<int:campaign_id>/invite-links/revoke', methods=['POST'])
@campaign_access_required
def invite_link_revoke():
    if g.campaign_role != 'owner':
        abort(403)
    raw = request.form.get('link_id') or (request.get_json(silent=True) or {}).get('link_id')
    if not str(raw or '').isdigit():
        return _reject_request('Unknown link.', 400)
    db = get_db()
    db.execute('UPDATE campaign_invite_links SET revoked_at = ' + _UTC_NOW + ' WHERE id = ? AND campaign_id = ? AND revoked_at IS NULL',
               (int(raw), g.campaign_id))
    db.commit()
    db.close()
    if _wants_json():
        return jsonify({'ok': True})
    return redirect(url_for('campaign_edit', campaign_id=g.campaign_id))


def _usable_link(db, token):
    return db.execute('''
        SELECT l.id, l.campaign_id, l.created_by_id, c.name AS campaign_name, c.avatar_path
        FROM campaign_invite_links l JOIN campaigns c ON c.id = l.campaign_id
        WHERE l.token_hash = ? AND c.access_mode = 'invite_link'
          AND l.revoked_at IS NULL AND l.expires_at > ''' + _UTC_NOW + '''
          AND (l.max_uses IS NULL OR l.use_count < l.max_uses)
    ''', (_hash_invite_token(token),)).fetchone()


def _dead_link_page():
    return render_template('join_link.html', link=None), 404


def _is_member(db, campaign_id):
    return db.execute('SELECT 1 FROM campaign_members WHERE campaign_id = ? AND user_id = ?',
                      (campaign_id, session['user_id'])).fetchone() is not None


@app.route('/join/<token>')
def join_via_link(token):
    db = get_db()
    link = _usable_link(db, token)
    member = link is not None and _is_member(db, link['campaign_id'])
    db.close()
    if link is None:
        return _dead_link_page()
    if member:                                      # already in: just go there (nothing is used up)
        return redirect(url_for('characters_list', campaign_id=link['campaign_id']))
    return render_template('join_link.html', link=link, token=token)


@app.route('/join/<token>', methods=['POST'])
def join_via_link_accept(token):
    db = get_db()
    link = _usable_link(db, token)
    if link is None:
        db.close()
        return _dead_link_page()
    if _is_member(db, link['campaign_id']):
        db.close()
        return redirect(url_for('characters_list', campaign_id=link['campaign_id']))
    # Spend one use atomically: the conditions are re-checked inside the UPDATE, so two people racing for the
    # last use cannot both get in.
    cur = db.execute('UPDATE campaign_invite_links SET use_count = use_count + 1 WHERE id = ? AND ' + _LINK_LIVE_SQL,
                     (link['id'],))
    if cur.rowcount == 0:
        db.rollback()
        db.close()
        return _dead_link_page()
    db.execute("INSERT INTO campaign_members (campaign_id, user_id, role, status) VALUES (?, ?, 'member', 'player') "
               'ON CONFLICT (campaign_id, user_id) DO NOTHING', (link['campaign_id'], session['user_id']))
    if link['created_by_id'] and link['created_by_id'] != session['user_id']:
        notify(db, link['created_by_id'], 'invite_link_joined', '%s joined %s' % (session['username'], link['campaign_name']),
               'They used your invite link and joined as a Player.', url_for('campaign_edit', campaign_id=link['campaign_id']))
    log_activity(db, link['campaign_id'], 'member_joined', 'joined the campaign')
    db.commit()
    db.close()
    return redirect(url_for('characters_list', campaign_id=link['campaign_id']))


# ---------------- NOTIFICATIONS + INVITATION RESPONSES ----------------
# Not campaign-scoped: they belong to the logged-in person.  Every query below filters on
# session['user_id'], so someone else's invitation or notification is simply "not found".

@app.route('/notifications')
def notifications_page():
    db = get_db()
    rows = db.execute('''
        SELECT n.id, n.kind, n.title, n.body, n.link, n.created_at, n.read_at, n.invitation_id,
               i.state AS invite_state, i.status AS invite_status, c.name AS campaign_name
        FROM notifications n
        LEFT JOIN campaign_invitations i ON i.id = n.invitation_id
        LEFT JOIN campaigns c ON c.id = i.campaign_id
        WHERE n.user_id = ?
        ORDER BY n.id DESC LIMIT 100
    ''', (session['user_id'],)).fetchall()
    db.close()
    return render_template('notifications.html', notifications=rows)


@app.route('/notifications/<int:notification_id>/read', methods=['POST'])
def notification_read(notification_id):
    db = get_db()
    db.execute('UPDATE notifications SET read_at = ' + _UTC_NOW + ' WHERE id = ? AND user_id = ? AND read_at IS NULL',
               (notification_id, session['user_id']))
    db.commit()
    db.close()
    return redirect(url_for('notifications_page'))


@app.route('/notifications/read-all', methods=['POST'])
def notifications_read_all():
    db = get_db()
    db.execute('UPDATE notifications SET read_at = ' + _UTC_NOW + ' WHERE user_id = ? AND read_at IS NULL', (session['user_id'],))
    db.commit()
    db.close()
    return redirect(url_for('notifications_page'))


def _respond_to_invitation(invitation_id, accept):
    db = get_db()
    inv = db.execute('''
        SELECT i.*, c.name AS campaign_name FROM campaign_invitations i
        JOIN campaigns c ON c.id = i.campaign_id
        WHERE i.id = ? AND i.invited_user_id = ?
    ''', (invitation_id, session['user_id'])).fetchone()
    if inv is None:
        db.close()
        abort(404)                                         # someone else's (or no such) invitation: indistinguishable
    new_state = 'accepted' if accept else 'declined'
    # The state change is the gate: only one response can ever flip a pending invitation, so a double
    # click or a replay can't add the member twice or notify the owner twice.
    cur = db.execute("UPDATE campaign_invitations SET state = ?, responded_at = " + _UTC_NOW + " WHERE id = ? AND state = 'pending'",
                     (new_state, invitation_id))
    if cur.rowcount == 0:
        db.close()
        if inv['state'] == new_state:                      # already did exactly this: harmless
            return redirect(url_for('characters_list', campaign_id=inv['campaign_id']) if accept else url_for('notifications_page'))
        return _reject_request('This invitation is no longer open.', 409)
    if accept:
        db.execute('''INSERT INTO campaign_members (campaign_id, user_id, role, status) VALUES (?, ?, 'member', ?)
                       ON CONFLICT (campaign_id, user_id) DO NOTHING''', (inv['campaign_id'], session['user_id'], inv['status']))
    db.execute('UPDATE notifications SET read_at = ' + _UTC_NOW + ' WHERE invitation_id = ? AND user_id = ? AND read_at IS NULL',
               (invitation_id, session['user_id']))
    if accept:
        log_activity(db, inv['campaign_id'], 'member_joined', 'joined the campaign')
    if inv['invited_by_id']:
        notify(db, inv['invited_by_id'], 'invite_accepted' if accept else 'invite_declined',
               '%s %s your invitation' % (session['username'], 'accepted' if accept else 'declined'),
               'Campaign: %s' % inv['campaign_name'],
               url_for('campaign_edit', campaign_id=inv['campaign_id']) if accept else None, invitation_id)
    db.commit()
    db.close()
    if accept:
        return redirect(url_for('characters_list', campaign_id=inv['campaign_id']))
    return redirect(url_for('notifications_page'))


@app.route('/invitations/<int:invitation_id>/accept', methods=['POST'])
def invitation_accept(invitation_id):
    return _respond_to_invitation(invitation_id, True)


@app.route('/invitations/<int:invitation_id>/decline', methods=['POST'])
def invitation_decline(invitation_id):
    return _respond_to_invitation(invitation_id, False)


@app.route('/campaigns/<int:campaign_id>/api/members/search')
@campaign_access_required
def api_member_search():
    """Owner-only autocomplete for the invite box: usernames matching the query, excluding
    anyone already a member. Rate-limited and a minimum query length so it can't be used to
    enumerate the platform's usernames one keystroke at a time."""
    if g.campaign_role != 'owner':
        abort(403)
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({'results': []})
    db = get_db()
    secret = app.config['SECRET_KEY']
    retry = security.member_search_blocked_for(db, secret, session['user_id'], g.campaign_id)
    if retry:
        db.close()
        return api_error('Searching too fast -- try again in a moment.', 429)
    security.record_member_search(db, secret, session['user_id'], g.campaign_id)
    db.commit()
    like = q.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_') + '%'
    rows = db.execute('''
        SELECT username FROM users
        WHERE username ILIKE ? ESCAPE '\\'
          AND id != ?
          AND id NOT IN (SELECT user_id FROM campaign_members WHERE campaign_id = ?)
          AND id NOT IN (SELECT invited_user_id FROM campaign_invitations WHERE campaign_id = ? AND state = 'pending')
        ORDER BY username ASC LIMIT 8
    ''', (like, session['user_id'], g.campaign_id, g.campaign_id)).fetchall()
    db.close()
    return jsonify({'results': [r['username'] for r in rows]})


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


@app.route('/campaigns/<int:campaign_id>/npc-visibility', methods=['POST'])
@campaign_access_required
def campaign_npc_visibility():
    """The DM or an owner switches "Hide NPCs from Players" on or off.  Judged by the real status, so a DM previewing as
    a Player can still turn it off."""
    if not (g.real_is_dm or g.campaign_role == 'owner'):
        abort(403)
    hide = request.form.get('hide_npcs') == 'on'
    db = get_db()
    db.execute('UPDATE campaigns SET hide_npcs = ? WHERE id = ?', (hide, g.campaign_id))
    db.commit()
    db.close()
    return redirect(url_for('campaign_edit', campaign_id=g.campaign_id))


@app.route('/campaigns/<int:campaign_id>/members/<int:user_id>/characters', methods=['POST'])
@campaign_access_required
def campaign_member_characters(user_id):
    """The DM or an owner chooses which characters a member plays.  That member can then edit them and move their map
    tokens.  A character has at most one player, so choosing it here takes it from whoever had it."""
    if not (g.real_is_dm or g.campaign_role == 'owner'):
        abort(403)
    db = get_db()
    if db.execute('SELECT 1 FROM campaign_members WHERE campaign_id = ? AND user_id = ?', (g.campaign_id, user_id)).fetchone() is None:
        db.close()
        abort(404)
    wanted = set()
    for raw in request.form.getlist('character_ids'):
        try:
            wanted.add(int(raw))
        except ValueError:
            continue
    valid = {r['id'] for r in db.execute('SELECT id FROM characters WHERE campaign_id = ? AND is_temp_familiar = 0', (g.campaign_id,)).fetchall()}
    wanted &= valid
    db.execute('UPDATE characters SET player_id = NULL WHERE campaign_id = ? AND player_id = ?', (g.campaign_id, user_id))
    for cid in wanted:
        db.execute('UPDATE characters SET player_id = ? WHERE id = ? AND campaign_id = ?', (user_id, cid, g.campaign_id))
    db.commit()
    db.close()
    return redirect(url_for('campaign_edit', campaign_id=g.campaign_id) + '#member-list')


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
    log_activity(db, g.campaign_id, 'member_left', 'left the campaign')
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


def _delete_campaign_rows(db, campaign_id):
    """Delete a campaign and everything in it inside the caller's transaction; returns the uploaded files to remove
    after the caller commits (once the rows are gone the files could no longer be found).  The explicit deletes are
    kept even though Postgres enforces the same ON DELETE CASCADE constraints -- belt and braces."""
    avatar_paths = [r['avatar_path'] for r in db.execute(
        'SELECT avatar_path FROM characters WHERE campaign_id = ? AND avatar_path IS NOT NULL', (campaign_id,))]
    avatar_paths += [r['avatar_path'] for r in db.execute(
        'SELECT avatar_path FROM groups WHERE campaign_id = ? AND avatar_path IS NOT NULL', (campaign_id,))]
    avatar_paths += [r['avatar_path'] for r in db.execute(
        'SELECT avatar_path FROM campaigns WHERE id = ? AND avatar_path IS NOT NULL', (campaign_id,))]
    sheet_paths = [r['image_path'] for r in db.execute('''
        SELECT cs.image_path FROM character_sheets cs
        JOIN characters c ON cs.character_id = c.id
        WHERE c.campaign_id = ?
    ''', (campaign_id,))]
    map_paths = [r['image_path'] for r in db.execute(
        'SELECT image_path FROM maps WHERE campaign_id = ? AND image_path IS NOT NULL', (campaign_id,))]

    db.execute('DELETE FROM battle_participants WHERE character_id IN (SELECT id FROM characters WHERE campaign_id = ?)', (campaign_id,))
    db.execute('DELETE FROM character_sheets WHERE character_id IN (SELECT id FROM characters WHERE campaign_id = ?)', (campaign_id,))
    db.execute('DELETE FROM map_drawings WHERE map_id IN (SELECT id FROM maps WHERE campaign_id = ?)', (campaign_id,))
    db.execute('DELETE FROM map_pins WHERE map_id IN (SELECT id FROM maps WHERE campaign_id = ?)', (campaign_id,))
    db.execute('DELETE FROM characters WHERE campaign_id = ?', (campaign_id,))
    db.execute('DELETE FROM groups WHERE campaign_id = ?', (campaign_id,))
    db.execute('DELETE FROM maps WHERE campaign_id = ?', (campaign_id,))
    db.execute('DELETE FROM campaign_members WHERE campaign_id = ?', (campaign_id,))
    db.execute('DELETE FROM stored_files WHERE campaign_id = ?', (campaign_id,))
    db.execute('DELETE FROM campaigns WHERE id = ?', (campaign_id,))
    return [p for p in avatar_paths + sheet_paths + map_paths if p]


@app.route('/campaigns/<int:campaign_id>/delete', methods=['POST'])
@campaign_access_required
def campaign_delete():
    if g.campaign_role != 'owner':
        abort(403)
    db = get_db()

    files = _delete_campaign_rows(db, g.campaign_id)
    db.commit()
    db.close()

    delete_uploads(files)

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
    # An entry is a plain string (visible to everyone) or {"t": html, "h": 1}
    # (hidden from Players). Anything else is dropped.
    cleaned = []
    for b in blocks:
        if isinstance(b, str):
            fragment, hidden = _sanitize_note_fragment(b), False
        elif isinstance(b, dict) and isinstance(b.get('t'), str):
            fragment, hidden = _sanitize_note_fragment(b['t']), b.get('h') in (1, True)
        else:
            continue
        if not fragment or fragment in ('<br>', '<div><br></div>'):
            continue
        cleaned.append({'t': fragment, 'h': 1} if hidden else fragment)
    return json.dumps(cleaned)


def sanitize_bio(raw):
    """The campaign bio: bold/italic/list text in the same storage format as notes (one box).  Plain text, including
    text that happens to be valid JSON such as 123, is kept as plain text."""
    raw = (raw or '').replace('\x00', '').strip()
    if not raw:
        return ''
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, list):
        out = sanitize_notes_payload(raw)
        return '' if out == '[]' else out
    return json.dumps([_escape_text(raw).replace('\n', '<br>')])


def notes_parse_entries(raw):
    """Parse characters.notes into [{'t': trusted html, 'h': bool hidden-from-Players}, ...],
    one per note box. Plain-string entries (and every note saved before this flag
    existed) are visible; {"t", "h": 1} entries are hidden from Players."""
    if not raw:
        return []
    try:
        blocks = json.loads(raw)
        if isinstance(blocks, list):
            entries = []
            for b in blocks:
                if isinstance(b, str):
                    text, hidden = b, False
                elif isinstance(b, dict) and isinstance(b.get('t'), str):
                    text, hidden = b['t'], b.get('h') in (1, True)
                else:
                    continue
                text = _sanitize_note_fragment(text)
                if text:
                    entries.append({'t': text, 'h': hidden})
            return entries
    except (ValueError, TypeError):
        pass
    # Legacy plain-text notes: escape and turn line breaks into <br> so each
    # line the user typed still shows up as its own line.
    escaped = raw.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return [{'t': escaped.replace('\n', '<br>'), 'h': False}]


def notes_parse_blocks(raw):
    """All note boxes as html strings (hidden ones included -- DM/owner use only)."""
    return [e['t'] for e in notes_parse_entries(raw)]


def owns_character(row):
    """True when the signed-in member made this character or the DM attached it to them ("played by").
    Those two may edit it and read its hidden notes; the DM always may.  Works on any row that carries
    created_by and, when it has one, player_id."""
    me = session.get('user_id')
    if me is None:
        return False
    keys = row.keys() if hasattr(row, 'keys') else row
    return bool(row['created_by'] == me or ('player_id' in keys and row['player_id'] == me))


def can_see_hidden_notes(character):
    """The DM and the character's own creator (or the member it is attached to) see every note; other members
    never receive a note flagged hidden. Single source of truth for the rule."""
    if getattr(g, 'previewing', False):      # "View as player" shows exactly what a Player gets, even to the DM who made the NPC
        return False
    return bool(g.is_dm or owns_character(character))


def notes_for_viewer(raw, can_see_hidden):
    """Raw notes value safe to send to this viewer (hidden entries removed unless allowed)."""
    if can_see_hidden or not raw:
        return raw
    visible = [e['t'] for e in notes_parse_entries(raw) if not e['h']]
    return json.dumps(visible) if visible else ''


def notes_to_html(raw, show_hidden=False):
    """Render stored notes as HTML for detail views. Hidden notes are left out
    unless show_hidden is set (then they carry a visible marker)."""
    entries = [e for e in notes_parse_entries(raw) if show_hidden or not e['h']]
    if not entries:
        return ''
    return ''.join(
        f'<div class="note-block-text note-block-hidden"><span class="note-hidden-label">Hidden from players</span>{e["t"]}</div>'
        if e['h'] else f'<div class="note-block-text">{e["t"]}</div>'
        for e in entries)


def preview_lines(text):
    """Tidy text for a card preview: keep the author's line breaks, but trim each line, collapse runs of
    spaces and drop empty lines (a blank line would otherwise use up a clamped preview line)."""
    lines = (re.sub(r'[ \t\f\v\u00a0]+', ' ', ln).strip() for ln in str(text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n'))
    return '\n'.join(ln for ln in lines if ln)


def notes_plain_preview(raw, show_hidden=False):
    """Flatten stored notes to plain text for compact card previews, keeping their line structure
    (the card shows it with white-space: pre-line)."""
    blocks = [e['t'] for e in notes_parse_entries(raw) if show_hidden or not e['h']]
    if not blocks:
        return ''
    # a line break comes before a block opens as well as after it closes: the editor stores "line 1<div>line 2</div>"
    joined = re.sub(r'<(br|/?div|/?li|/?p|/?ul)\s*/?>', '\n', '\n'.join(blocks), flags=re.I)
    joined = _NOTE_STRIP_TAGS_RE.sub('', joined)
    joined = joined.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
    return preview_lines(joined)


app.jinja_env.filters['notes_preview'] = notes_plain_preview
app.jinja_env.globals['can_see_hidden_notes'] = can_see_hidden_notes
app.jinja_env.filters['preview_lines'] = preview_lines


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


def _put_upload(key, processed, kind):
    """Store one processed image if the user's and the campaign's quota allow it, and record its size.
    The ledger row joins the request's transaction (committed with the record that points at the file)."""
    db = get_db()
    uid, cid = session.get('user_id'), g.get('campaign_id')
    quotas.check(db, uid, cid, len(processed.data))                       # raises QuotaExceeded before anything is written
    get_storage().put(UPLOADS, key, processed.data, processed.content_type)
    quotas.record(db, key, uid, cid, kind, len(processed.data))


def _store_image(data, subfolder, cap_dimension=None):
    """Validate + store; returns Stored or None when the file is not an acceptable image."""
    try:
        p = images.process_image(data, subfolder, cap_dimension)
    except images.ImageError:
        return None
    key = f"{subfolder}/{uuid.uuid4().hex}.{p.ext}"
    _put_upload(key, p, subfolder)
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
        quotas.forget(get_db(), [rel_path])       # frees the quota with the caller's commit
        try:
            get_storage().delete(UPLOADS, rel_path)
        except StorageError:
            pass                       # an orphaned file is better than a failed delete


def delete_uploads(paths):
    paths = [p for p in paths if p]
    if paths:
        quotas.forget(get_db(), paths)
        try:
            get_storage().delete_many(UPLOADS, paths)
        except StorageError:
            pass


def _generate_blank_canvas(width, height):
    """Create a plain white PNG for maps without a background image; returns its key."""
    p = images.blank_canvas(width, height)
    key = f"maps/{uuid.uuid4().hex}.png"
    _put_upload(key, p, 'maps')
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
        return api_error('That kind of upload is not supported.', 400, code='unknown_upload_type')
    max_bytes, allowed_types = UPLOAD_PURPOSES[purpose]
    try:
        size = int(data.get('size', 0))
    except (TypeError, ValueError):
        return api_error('The file size could not be read.', 400, code='invalid_size')
    if size <= 0:
        return api_error('That file is empty.', 400, code='empty_file')
    if size > max_bytes:
        return api_error('That file is too large (limit %d MB).' % (max_bytes // (1024 * 1024)), 413, code='file_too_large')
    if (data.get('content_type') or 'application/octet-stream') not in allowed_types:
        return api_error('That type of file is not supported.', 415, code='unsupported_type')
    if purpose != 'import':
        try:
            quotas.check(get_db(), session['user_id'], None)             # already full: say so before the file travels
        except quotas.QuotaExceeded as exc:
            return api_error(str(exc), 413, code='quota_exceeded', scope=exc.scope)
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


def _csp(nonce=None):
    connect = ["'self'"] + realtime.csp_origins()
    supa = os.environ.get('SUPABASE_URL', '')
    if supa and os.environ.get('STORAGE_BACKEND', 'supabase') == 'supabase':
        p = urlparse(supa)
        if p.scheme and p.netloc:
            connect.append('%s://%s' % (p.scheme, p.netloc))      # browsers upload big files straight to storage
    return '; '.join([
        "default-src 'self'",
        "img-src 'self' data: blob:",
        "media-src 'self' data: blob:",
        "style-src 'self' 'unsafe-inline'",
        "font-src 'self' data:",
        "script-src 'self'" + (" 'nonce-%s'" % nonce if nonce else ''),   # inline scripts run only with this page's one-time token
        "connect-src " + ' '.join(connect),
        "worker-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


_static_hashes = {}


def _static_version(filename):
    """Short content hash of a static file.  Appended as ?v= so a deploy that changes a script
    or stylesheet can never be masked by an older copy cached in the browser or a CDN --
    content-based (not mtime) because a serverless deploy may stamp every file identically."""
    path = safe_join(app.static_folder, filename)
    try:
        st = os.stat(path)
    except (OSError, TypeError):
        return None
    key = (st.st_mtime_ns, st.st_size)
    hit = _static_hashes.get(filename)
    if hit and hit[0] == key:
        return hit[1]
    with open(path, 'rb') as f:
        digest = hashlib.md5(f.read(), usedforsecurity=False).hexdigest()[:10]
    _static_hashes[filename] = (key, digest)
    return digest


@app.url_defaults
def _version_static_urls(endpoint, values):
    if endpoint == 'static' and 'v' not in values and values.get('filename'):
        v = _static_version(values['filename'])
        if v:
            values['v'] = v


@app.after_request
def realtime_ping(resp):
    """After any successful change inside a campaign, tell open pages to refresh (see realtime.py).  Done in one place,
    not per route, so a new route cannot forget it.  Runs after the response has gone out, so it never slows a request."""
    campaign_id = getattr(g, 'campaign_id', None)
    if (campaign_id is not None and request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and resp.status_code < 400
            and realtime.config()):
        scope, secret = realtime.scope_for(request.path), app.secret_key
        resp.call_on_close(lambda: realtime.publish(campaign_id, scope, secret))
    return resp


@app.after_request
def cache_fonts(resp):
    if request.path.startswith('/static/fonts/') and resp.status_code == 200:
        resp.headers['Cache-Control'] = 'public, max-age=2592000'        # the font files never change; a month is plenty
    return resp


@app.after_request
def security_headers(resp):
    h = resp.headers
    h.setdefault('X-Content-Type-Options', 'nosniff')
    h.setdefault('X-Frame-Options', 'DENY')
    h.setdefault('Referrer-Policy', 'same-origin')
    h.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=(), usb=()')
    h.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
    if request.endpoint not in PUBLIC_ENDPOINTS:
        h.setdefault('X-Robots-Tag', 'noindex, nofollow')            # everything behind sign-in stays out of search results
    if resp.mimetype in ('text/html', 'application/json'):
        h.setdefault('Content-Security-Policy', _csp(getattr(g, 'csp_nonce', None)))
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


@app.errorhandler(quotas.QuotaExceeded)
def quota_exceeded(exc):
    """A storage limit was reached: nothing was saved.  Pages get their own banner back, JSON callers the message."""
    app.logger.info('quota refused on %s %s: %s', request.method, request.path, exc.scope)
    if _wants_json():
        return api_error(str(exc), 413, code='quota_exceeded', scope=exc.scope)
    ref = urlparse(request.referrer or '')
    if request.method == 'POST' and ref.netloc == request.host and ref.path:
        sep = '&' if ref.query else '?'
        return redirect(f"{ref.path}{'?' + ref.query if ref.query else ''}{sep}quota_error={exc.scope}")
    return Response(str(exc), status=413, mimetype='text/plain')


@app.errorhandler(StorageError)
def storage_unavailable(exc):
    app.logger.error('storage error: %s', exc)
    if request.is_json or '/api/' in request.path or request.path.startswith('/uploads/sign'):
        return api_error('File storage is temporarily unavailable. Try again in a moment.', 503, code='storage_unavailable')
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
        return redirect(url_for('campaign_home', campaign_id=last_id))
    campaigns = db.execute(
        'SELECT c.id FROM campaigns c JOIN campaign_members cm ON cm.campaign_id = c.id WHERE cm.user_id = ?',
        (session['user_id'],)
    ).fetchall()
    db.close()
    if len(campaigns) == 1:
        return redirect(url_for('campaign_home', campaign_id=campaigns[0]['id']))
    # Zero campaigns, or more than one with no remembered choice: let the
    # person pick (or create their first one) on the campaigns list.
    return redirect(url_for('campaigns_list'))


# ---------------- CHARACTERS ----------------

@app.route('/campaigns/<int:campaign_id>/characters')
@campaign_access_required
def characters_list():
    db = get_db()
    hide_sql, hide_params = npc_filter()
    characters = db.execute('''
        SELECT c.*, g.name AS group_name, g.color AS group_color, mk.username AS made_by
        FROM characters c
        LEFT JOIN groups g ON c.group_id = g.id
        LEFT JOIN users mk ON mk.id = c.created_by
        WHERE c.is_temp_familiar = 0 AND c.campaign_id = ?''' + hide_sql + '''
        ORDER BY lower(c.name) ASC
    ''', [g.campaign_id] + hide_params).fetchall()
    groups = db.execute('SELECT * FROM groups WHERE campaign_id = ? ORDER BY lower(name) ASC', (g.campaign_id,)).fetchall()
    by_char = _factions_by_character(db, g.campaign_id)
    characters = [dict(c, factions=by_char.get(c['id'], [])) for c in characters]
    live = _battle_live_status(db)
    db.close()
    return render_template('characters_list.html', characters=characters, groups=groups, live=live,
                           deletable={c['id'] for c in characters if can_delete(c['created_by'])})


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
    return render_template('character_form.html', character=None, sheets=[], groups=groups, from_battle=from_battle, notes_entries=[{'t': '', 'h': False}])


@app.route('/campaigns/<int:campaign_id>/characters/<int:char_id>')
@campaign_access_required
def character_detail(char_id):
    db = get_db()
    character = db.execute('''
        SELECT c.*, g.name AS group_name, g.color AS group_color, mk.username AS made_by
        FROM characters c LEFT JOIN groups g ON c.group_id = g.id
        LEFT JOIN users mk ON mk.id = c.created_by
        WHERE c.id = ? AND c.campaign_id = ?
    ''', (char_id, g.campaign_id)).fetchone()
    if character is None or npc_is_hidden(character['is_npc'], character['created_by']):
        db.close()
        return redirect(url_for('characters_list'))
    # A Player can open an NPC's dossier for its notes only: stats and sheet
    # images stay withheld (sheets are pictures of the stat block), and the
    # template gets neither -- it renders blurred placeholders instead.
    stats_hidden = bool(character['is_npc']) and not g.is_dm
    sheets = [] if stats_hidden else db.execute(
        'SELECT * FROM character_sheets WHERE character_id = ? ORDER BY sort_order', (char_id,)).fetchall()
    factions = _factions_by_character(db, g.campaign_id).get(char_id, [])
    level_history = [] if stats_hidden else _level_history(db, char_id)
    attacks = [] if stats_hidden else _attacks_for(db, char_id)
    db.close()
    can_edit = bool(g.is_dm or owns_character(character))
    return render_template('character_detail.html', character=character, sheets=sheets, factions=factions, level_history=level_history, attacks=attacks,
                            notes_html=notes_to_html(character['notes'], show_hidden=can_see_hidden_notes(character)), can_edit=can_edit,
                            can_delete=can_delete(character['created_by']), stats_hidden=stats_hidden,
                            sheet=None if stats_hidden else dnd5e.derive(character))


@app.route('/campaigns/<int:campaign_id>/characters/<int:char_id>/edit', methods=['GET', 'POST'])
@campaign_access_required
def character_edit(char_id):
    db = get_db()
    owner_row = db.execute('SELECT created_by, player_id FROM characters WHERE id = ? AND campaign_id = ?',
                            (char_id, g.campaign_id)).fetchone()
    if owner_row is None:
        db.close()
        return redirect(url_for('characters_list'))
    # A Player can only edit a character they personally added or that the DM attached to them; the DM can
    # edit anyone's.
    if not g.is_dm and not owns_character(owner_row):
        db.close()
        abort(403)
    if request.method == 'POST':
        _save_character(db, char_id)
        db.close()
        return redirect(url_for('characters_list'))
    character = db.execute('SELECT * FROM characters WHERE id = ? AND campaign_id = ?', (char_id, g.campaign_id)).fetchone()
    sheets = db.execute('SELECT * FROM character_sheets WHERE character_id = ? ORDER BY sort_order', (char_id,)).fetchall()
    groups = db.execute('SELECT * FROM groups WHERE campaign_id = ? ORDER BY lower(name) ASC', (g.campaign_id,)).fetchall()
    faction_ids = [f['id'] for f in _factions_by_character(db, g.campaign_id).get(char_id, [])]
    level_history = _level_history(db, char_id)
    attacks = _attacks_for(db, char_id)
    db.close()
    if character is None:
        return redirect(url_for('characters_list'))
    notes_entries = notes_parse_entries(character['notes']) or [{'t': '', 'h': False}]
    return render_template('character_form.html', character=character, sheets=sheets, groups=groups, from_battle=False, notes_entries=notes_entries,
                           can_delete_sheets=can_delete(owner_row['created_by']), character_faction_ids=faction_ids,
                           character_save_prof=dnd5e.clean_save_prof(character['save_prof']),
                           character_skill_prof=dnd5e.clean_skill_prof(character['skill_prof']),
                           level_history={str(r['level']): r for r in level_history}, attacks=attacks)


# Character classes (DL-43).  Optional on a character.  The text is the hover guide in the picker.
CHARACTER_CLASSES = [
    ('artificer', 'Artificer', 'An inventor who infuses magic into items and constructs.'),
    ('barbarian', 'Barbarian', 'A fierce warrior who enters a damaging battle rage.'),
    ('bard', 'Bard', 'A performer who weaves magic through art, music, and speech.'),
    ('cleric', 'Cleric', 'A holy servant who channels divine power from a deity.'),
    ('druid', 'Druid', 'A guardian of nature who wields primal magic and wild shapes.'),
    ('fighter', 'Fighter', 'A master of martial combat and weapon mastery.'),
    ('monk', 'Monk', 'A disciplined martial artist who harnesses inner ki energy.'),
    ('paladin', 'Paladin', 'A holy knight bound by an oath and divine smites.'),
    ('ranger', 'Ranger', 'A wilderness tracker blending combat and nature spells.'),
    ('rogue', 'Rogue', 'A stealthy expert in traps, sneak attacks, and trickery.'),
    ('sorcerer', 'Sorcerer', 'A spellcaster born with innate magical bloodlines.'),
    ('warlock', 'Warlock', 'A seeker of secrets who makes a pact with an otherworldly being.'),
    ('wizard', 'Wizard', 'A scholar who masters arcane magic through intense study.'),
]
CLASS_KEYS = [k for k, _l, _d in CHARACTER_CLASSES]
app.jinja_env.globals['character_classes'] = [{'key': k, 'label': l, 'desc': d} for k, l, d in CHARACTER_CLASSES]
def csp_nonce():
    """A fresh random token per request, put on every inline <script> and announced in the CSP header."""
    if not getattr(g, 'csp_nonce', None):
        g.csp_nonce = secrets.token_urlsafe(16)
    return g.csp_nonce


app.jinja_env.globals['csp_nonce'] = csp_nonce


@app.context_processor
def _realtime_settings():
    cid = getattr(g, 'campaign_id', None)
    return {'realtime_settings': realtime.browser_settings(app.secret_key, cid) if cid is not None else None}


app.jinja_env.globals['class_labels'] = {k: l for k, l, _d in CHARACTER_CLASSES}


app.jinja_env.globals['sheet_abilities'] = dnd5e.ABILITIES
app.jinja_env.globals['sheet_skills'] = [{'key': k, 'label': l, 'ability': dnd5e.ABILITY_SHORT[a]} for k, l, a in dnd5e.SKILLS]


def _character_sheet_html(character):
    """The derived-stats block (modifiers, saves, skills ...) as HTML, rendered once for the details page and both popups."""
    return render_template('_character_sheet.html', d=dnd5e.derive(character))


def _clean_class(raw):
    """A known class key, or None (a class is optional)."""
    raw = (raw or '').strip().lower() if isinstance(raw, str) else ''
    return raw if raw in CLASS_KEYS else None


def _faction_ids_from_form(db, form):
    """Faction ids ticked on the character form (the old single `group_id` field still works), campaign-checked."""
    ids = []
    for raw in form.getlist('group_ids') or form.getlist('group_id'):
        gid = _campaign_faction_id(db, raw)
        if gid is not None and gid not in ids:
            ids.append(gid)
    return ids


def _set_character_factions(db, char_id, ids):
    """Replace a character's factions with `ids` and keep the MAIN faction (characters.group_id) one of them:
    the current main faction stays main while it is still ticked, otherwise the first ticked one takes over."""
    row = db.execute('SELECT group_id FROM characters WHERE id = ?', (char_id,)).fetchone()
    main = row['group_id'] if row and row['group_id'] in ids else (ids[0] if ids else None)
    db.execute('DELETE FROM character_groups WHERE character_id = ?', (char_id,))
    for pos, gid in enumerate(([main] + [i for i in ids if i != main]) if main is not None else []):
        db.execute('INSERT INTO character_groups (character_id, group_id, position) VALUES (?, ?, ?) RETURNING character_id', (char_id, gid, pos))
    db.execute('UPDATE characters SET group_id = ? WHERE id = ?', (main, char_id))


def _add_character_to_faction(db, char_id, group_id):
    """One more faction for a character; their other factions are left alone."""
    if db.execute('SELECT 1 FROM character_groups WHERE character_id = ? AND group_id = ?', (char_id, group_id)).fetchone():
        return
    pos = db.execute('SELECT COALESCE(MAX(position), -1) + 1 AS p FROM character_groups WHERE character_id = ?', (char_id,)).fetchone()['p']
    db.execute('INSERT INTO character_groups (character_id, group_id, position) VALUES (?, ?, ?) RETURNING character_id', (char_id, group_id, pos))
    db.execute('UPDATE characters SET group_id = ? WHERE id = ? AND group_id IS NULL', (group_id, char_id))


def _resync_main_faction(db, char_id):
    """After a membership disappeared: if the main faction is no longer one of them, fall back to the next, or none."""
    row = db.execute('SELECT group_id FROM characters WHERE id = ?', (char_id,)).fetchone()
    if row and row['group_id'] is not None and db.execute(
            'SELECT 1 FROM character_groups WHERE character_id = ? AND group_id = ?', (char_id, row['group_id'])).fetchone():
        return
    nxt = db.execute('SELECT group_id FROM character_groups WHERE character_id = ? ORDER BY position, group_id LIMIT 1', (char_id,)).fetchone()
    db.execute('UPDATE characters SET group_id = ? WHERE id = ?', (nxt['group_id'] if nxt else None, char_id))


def _factions_by_character(db, campaign_id):
    """{character id: [{'id', 'name', 'color'}, ...]} for the whole campaign, main faction first."""
    out = {}
    for r in db.execute('''
        SELECT cg.character_id, gr.id, gr.name, gr.color
        FROM character_groups cg JOIN groups gr ON gr.id = cg.group_id
        WHERE gr.campaign_id = ? ORDER BY cg.character_id, cg.position, lower(gr.name)
    ''', (campaign_id,)).fetchall():
        out.setdefault(r['character_id'], []).append({'id': r['id'], 'name': r['name'], 'color': r['color']})
    return out


LEVEL_STAT_KEYS = ('max_hp', 'armor_class', 'str_score', 'dex_score', 'con_score', 'int_score', 'wis_score', 'cha_score', 'speed')


def _save_level_stats(db, char_id, level, max_hp, armor_class, scores, speed, skill_prof, save_prof):
    """Remember the stats the character has at this level (DL-105); saving the same level again replaces them."""
    db.execute('''INSERT INTO character_levels (character_id, level, max_hp, armor_class, str_score, dex_score, con_score, int_score, wis_score, cha_score,
                                                speed, skill_prof, save_prof)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                  ON CONFLICT (character_id, level) DO UPDATE SET max_hp = EXCLUDED.max_hp, armor_class = EXCLUDED.armor_class,
                      str_score = EXCLUDED.str_score, dex_score = EXCLUDED.dex_score, con_score = EXCLUDED.con_score,
                      int_score = EXCLUDED.int_score, wis_score = EXCLUDED.wis_score, cha_score = EXCLUDED.cha_score,
                      speed = EXCLUDED.speed, skill_prof = EXCLUDED.skill_prof, save_prof = EXCLUDED.save_prof
                  RETURNING character_id''',            # (this table has no id column, so the db layer must not add its own RETURNING id)
               (char_id, level, max_hp, armor_class, *scores, speed, skill_prof, save_prof))


def _attacks_for(db, char_id):
    """A character's saved attacks in order, each with what its two roll buttons need (DL-150)."""
    out = []
    for r in db.execute('SELECT name, attack_bonus, damage, damage_type FROM character_attacks WHERE character_id = ? '
                        'ORDER BY position, id', (char_id,)).fetchall():
        a = dict(r)
        spec = dnd5e.damage_roll_spec(a['damage'])
        a['dice_json'] = json.dumps(spec[0]) if spec else ''
        a['damage_bonus'] = spec[1] if spec else 0
        a['to_hit_text'] = '%+d' % a['attack_bonus']
        out.append(a)
    return out


def _save_attacks(db, char_id, attacks):
    """Replace a character's attacks with the (already cleaned) list."""
    db.execute('DELETE FROM character_attacks WHERE character_id = ?', (char_id,))
    for pos, a in enumerate(attacks):
        db.execute('INSERT INTO character_attacks (character_id, position, name, attack_bonus, damage, damage_type) VALUES (?, ?, ?, ?, ?, ?)',
                   (char_id, pos, a['name'], a['attack_bonus'], a['damage'], a['damage_type']))


def _level_history(db, char_id):
    """Every level that has saved stats, lowest first.  Proficiencies come back as real objects (what the form needs)."""
    out = []
    for r in db.execute('SELECT level, ' + ', '.join(LEVEL_STAT_KEYS) + ', skill_prof, save_prof FROM character_levels '
                        'WHERE character_id = ? ORDER BY level', (char_id,)).fetchall():
        r = dict(r)
        r['skill_prof'] = dnd5e.clean_skill_prof(r['skill_prof'])
        r['save_prof'] = dnd5e.clean_save_prof(r['save_prof'])
        out.append(r)
    return out


def _save_character(db, char_id):
    form = request.form
    name = form.get('name', '').strip() or 'Unnamed'
    is_npc = 1 if form.get('is_npc') == 'on' else 0
    level = max(1, min(30, int(form.get('level') or 1)))
    max_hp = int(form.get('max_hp') or 10)
    str_score = int(form.get('str_score') or 10)
    dex_score = int(form.get('dex_score') or 10)
    con_score = int(form.get('con_score') or 10)
    int_score = int(form.get('int_score') or 10)
    wis_score = int(form.get('wis_score') or 10)
    cha_score = int(form.get('cha_score') or 10)
    armor_class = int(form.get('armor_class') or 10)
    notes = sanitize_notes_payload(form.get('notes', ''))
    faction_ids = _faction_ids_from_form(db, form)
    group_id = faction_ids[0] if faction_ids else None
    class_key = _clean_class(form.get('class_key'))
    speed = dnd5e.clean_speed(form.get('speed'))
    skill_prof = json.dumps(dnd5e.clean_skill_prof({k: form.get('skill_' + k) for k in dnd5e.SKILL_KEYS}))
    save_prof = json.dumps(dnd5e.clean_save_prof([a for a in dnd5e.ABILITY_KEYS if form.get('save_' + a) == 'on']))

    avatar_path = save_upload_field('avatar', 'avatars')
    remove_avatar = form.get('remove_avatar') == '1'

    if char_id is None:
        cur = db.execute('''
            INSERT INTO characters
            (name, is_npc, level, max_hp, str_score, dex_score, con_score, int_score,
             wis_score, cha_score, armor_class, avatar_path, notes, group_id, class_key, campaign_id, created_by,
             speed, skill_prof, save_prof)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, is_npc, level, max_hp, str_score, dex_score, con_score, int_score,
              wis_score, cha_score, armor_class, avatar_path, notes, group_id, class_key, g.campaign_id, session['user_id'],
              speed, skill_prof, save_prof))
        char_id = cur.lastrowid
        log_activity(db, g.campaign_id, 'character_created', 'added the %s %s' % ('NPC' if is_npc else 'character', name),
                     url_for('character_detail', campaign_id=g.campaign_id, char_id=char_id))
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
                con_score=?, int_score=?, wis_score=?, cha_score=?, armor_class=?, notes=?, class_key=?,
                speed=?, skill_prof=?, save_prof=?
            WHERE id=?
        ''', (name, is_npc, level, max_hp, str_score, dex_score, con_score, int_score,
              wis_score, cha_score, armor_class, notes, class_key, speed, skill_prof, save_prof, char_id))

    _set_character_factions(db, char_id, faction_ids)
    _save_level_stats(db, char_id, level, max_hp, armor_class, (str_score, dex_score, con_score, int_score, wis_score, cha_score),
                      speed, skill_prof, save_prof)
    if form.get('attacks_present') == '1':            # only a form that shows the Attacks card may replace them (DL-150)
        _save_attacks(db, char_id, dnd5e.clean_attacks(zip(form.getlist('attack_name'), form.getlist('attack_bonus'),
                                                           form.getlist('attack_damage'), form.getlist('attack_type'))))

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
    char = db.execute('SELECT name, avatar_path, created_by FROM characters WHERE id = ? AND campaign_id = ?',
                       (char_id, g.campaign_id)).fetchone()
    if char and not can_delete(char['created_by']):
        db.close()
        abort(403)
    sheets = db.execute('SELECT image_path FROM character_sheets WHERE character_id = ?', (char_id,)).fetchall()
    if char:
        delete_upload(char['avatar_path'])
        for s in sheets:
            delete_upload(s['image_path'])
        db.execute('DELETE FROM characters WHERE id = ? AND campaign_id = ?', (char_id, g.campaign_id))
        log_activity(db, g.campaign_id, 'character_deleted', 'removed the character %s' % char['name'])
        db.commit()
    db.close()
    return redirect(url_for('characters_list'))


@app.route('/campaigns/<int:campaign_id>/characters/<int:char_id>/sheets/<int:sheet_id>/delete', methods=['POST'])
@campaign_access_required
def sheet_delete(char_id, sheet_id):
    db = get_db()
    owner_row = db.execute('SELECT created_by FROM characters WHERE id = ? AND campaign_id = ?',
                            (char_id, g.campaign_id)).fetchone()
    if owner_row and not can_delete(owner_row['created_by']):
        db.close()
        abort(403)
    sheet = db.execute('SELECT image_path FROM character_sheets WHERE id = ?', (sheet_id,)).fetchone()
    if sheet:
        delete_upload(sheet['image_path'])
    db.execute('DELETE FROM character_sheets WHERE id = ?', (sheet_id,))
    db.commit()
    db.close()
    return redirect(url_for('character_edit', char_id=char_id))


# ---------------- FACTIONS ----------------

@app.route('/campaigns/<int:campaign_id>/factions')
@campaign_access_required
def factions_list():
    db = get_db()
    groups = db.execute('''
        SELECT g.*, (SELECT COUNT(*) FROM character_groups cg JOIN characters c ON c.id = cg.character_id WHERE cg.group_id = g.id AND c.is_temp_familiar = 0 AND (? = 0 OR c.is_npc = 0 OR c.created_by = ?)) AS member_count, mk.username AS made_by
        FROM groups g LEFT JOIN users mk ON mk.id = g.created_by
        WHERE g.campaign_id = ? ORDER BY lower(g.name) ASC
    ''', (1 if npcs_hidden_from_viewer() else 0, None if getattr(g, 'previewing', False) else session.get('user_id'), g.campaign_id)).fetchall()
    db.close()
    return render_template('groups_list.html', groups=groups,
                           deletable={x['id'] for x in groups if can_delete(x['created_by'])})


@app.route('/campaigns/<int:campaign_id>/factions/new', methods=['GET', 'POST'])
@campaign_access_required
@dm_required
def faction_new():
    db = get_db()
    if request.method == 'POST':
        _save_faction(db, None)
        db.close()
        return redirect(url_for('factions_list'))
    candidates = _faction_candidates(db)
    db.close()
    return render_template('group_form.html', group=None, candidates=candidates)


@app.route('/campaigns/<int:campaign_id>/factions/<int:group_id>')
@campaign_access_required
def faction_detail(group_id):
    """Read-only 'Details' view of a faction: who is in it. Editing (DM only) is reached from here.
    Shows the same name/avatar/level a member can already see on the roster -- no stats."""
    db = get_db()
    group = db.execute('''SELECT g.*, mk.username AS made_by FROM groups g LEFT JOIN users mk ON mk.id = g.created_by
                          WHERE g.id = ? AND g.campaign_id = ?''', (group_id, g.campaign_id)).fetchone()
    if group is None:
        db.close()
        return redirect(url_for('factions_list'))
    hide_sql, hide_params = npc_filter()
    members = db.execute('''
        SELECT c.id, c.name, c.avatar_path, c.is_npc, c.level, c.class_key
        FROM characters c JOIN character_groups cg ON cg.character_id = c.id
        WHERE cg.group_id = ? AND c.campaign_id = ? AND c.is_temp_familiar = 0''' + hide_sql + '''
        ORDER BY lower(c.name) ASC
    ''', [group_id, g.campaign_id] + hide_params).fetchall()
    db.close()
    return render_template('group_detail.html', group=group, members=members)


@app.route('/campaigns/<int:campaign_id>/factions/<int:group_id>/members/<int:char_id>/remove', methods=['POST'])
@campaign_access_required
@dm_required
def faction_member_remove(group_id, char_id):
    """Take one character out of a faction (their other factions stay). Scoped to this campaign and faction."""
    db = get_db()
    if db.execute('SELECT 1 FROM characters c JOIN groups gr ON gr.campaign_id = c.campaign_id '
                  'WHERE c.id = ? AND gr.id = ? AND c.campaign_id = ?', (char_id, group_id, g.campaign_id)).fetchone():
        db.execute('DELETE FROM character_groups WHERE character_id = ? AND group_id = ?', (char_id, group_id))
        _resync_main_faction(db, char_id)
    db.commit()
    db.close()
    return redirect(url_for('faction_detail', group_id=group_id))


@app.route('/campaigns/<int:campaign_id>/factions/<int:group_id>/edit', methods=['GET', 'POST'])
@campaign_access_required
@dm_required
def faction_edit(group_id):
    db = get_db()
    group = db.execute('SELECT * FROM groups WHERE id = ? AND campaign_id = ?', (group_id, g.campaign_id)).fetchone()
    if group is None:
        db.close()
        return redirect(url_for('factions_list'))
    if request.method == 'POST':
        _save_faction(db, group_id)
        db.close()
        return redirect(url_for('faction_detail', group_id=group_id))
    candidates = _faction_candidates(db)
    db.close()
    return render_template('group_form.html', group=group, candidates=candidates)


def _faction_candidates(db):
    """Everyone in this campaign who could be pulled into a faction (for the search-and-add box)."""
    rows = db.execute('''
        SELECT c.id, c.name, c.is_npc, c.avatar_path, c.group_id, c.class_key
        FROM characters c
        WHERE c.campaign_id = ? AND c.is_temp_familiar = 0
        ORDER BY lower(c.name) ASC
    ''', (g.campaign_id,)).fetchall()
    by_char = _factions_by_character(db, g.campaign_id)
    return [dict(r, group_ids=[f['id'] for f in by_char.get(r['id'], [])],
                 group_names=[f['name'] for f in by_char.get(r['id'], [])]) for r in rows]


_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')


def _campaign_faction_id(db, raw):
    """A character may only join a group of ITS OWN campaign; anything else becomes 'no group'."""
    try:
        gid = int(raw)
    except (TypeError, ValueError):
        return None
    ok = db.execute('SELECT 1 FROM groups WHERE id = ? AND campaign_id = ?', (gid, g.campaign_id)).fetchone()
    return gid if ok else None


def _save_faction(db, group_id):
    form = request.form
    name = form.get('name', '').strip() or 'Unnamed Faction'
    bio = form.get('bio', '')
    color = form.get('color') or '#c9a24b'
    if not _COLOR_RE.match(color):
        color = '#c9a24b'
    avatar_path = save_upload_field('avatar', 'avatars')
    remove_avatar = form.get('remove_avatar') == '1'

    if group_id is None:
        group_id = db.execute('INSERT INTO groups (name, avatar_path, bio, color, campaign_id, created_by) VALUES (?, ?, ?, ?, ?, ?)',
                              (name, avatar_path, bio, color, g.campaign_id, session['user_id'])).lastrowid
        log_activity(db, g.campaign_id, 'faction_created', 'added the faction %s' % name,
                     url_for('faction_detail', campaign_id=g.campaign_id, group_id=group_id))
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
    # Characters picked in the form's search box join this faction (keeping any others they are in),
    # on creation as well as on edit.  Only this campaign's own characters can be added;
    # foreign/garbage ids are ignored.
    for raw_id in dict.fromkeys(request.form.getlist('add_character_ids')):
        try:
            cid_to_add = int(raw_id)
        except (TypeError, ValueError):
            continue
        if db.execute('SELECT 1 FROM characters WHERE id = ? AND campaign_id = ? AND is_temp_familiar = 0',
                      (cid_to_add, g.campaign_id)).fetchone():
            _add_character_to_faction(db, cid_to_add, group_id)
    db.commit()


@app.route('/campaigns/<int:campaign_id>/factions/<int:group_id>/delete', methods=['POST'])
@campaign_access_required
@dm_required
def faction_delete(group_id):
    db = get_db()
    group = db.execute('SELECT name, avatar_path, created_by FROM groups WHERE id = ? AND campaign_id = ?', (group_id, g.campaign_id)).fetchone()
    if group and not can_delete(group['created_by']):
        db.close()
        abort(403)
    if group:
        delete_upload(group['avatar_path'])
        members = [r['character_id'] for r in db.execute('SELECT character_id FROM character_groups WHERE group_id = ?', (group_id,)).fetchall()]
        db.execute('DELETE FROM character_groups WHERE group_id = ?', (group_id,))
        db.execute('DELETE FROM groups WHERE id = ? AND campaign_id = ?', (group_id, g.campaign_id))
        for cid_ in members:                       # anyone whose MAIN faction this was moves on to their next one
            _resync_main_faction(db, cid_)
        log_activity(db, g.campaign_id, 'faction_deleted', 'removed the faction %s' % group['name'])
        db.commit()
    db.close()
    return redirect(url_for('factions_list'))


# ---------------- API (for future battle screen use) ----------------

@app.route('/campaigns/<int:campaign_id>/api/characters')
@campaign_access_required
def api_characters():
    db = get_db()
    hide_sql, hide_params = npc_filter()
    rows = db.execute('''
        SELECT c.*, g.name AS group_name, g.color AS group_color
        FROM characters c LEFT JOIN groups g ON c.group_id = g.id
        WHERE c.is_temp_familiar = 0 AND c.campaign_id = ?''' + hide_sql + '''
        ORDER BY lower(c.name) ASC
    ''', [g.campaign_id] + hide_params).fetchall()
    by_char = _factions_by_character(db, g.campaign_id)
    db.close()
    out = []
    for r in rows:
        d = dict(r, factions=by_char.get(r['id'], []))
        if not g.is_dm:
            if d['is_npc']:
                # Players never receive an NPC's numbers (same allow-list as the detail API).
                d = {k: d[k] for k in ('id', 'name', 'avatar_path', 'level', 'is_npc',
                                       'group_id', 'group_name', 'group_color', 'factions')}
                d['hidden_stats'] = True
            else:
                d['notes'] = notes_for_viewer(d['notes'], can_see_hidden_notes(d))
        out.append(d)
    return jsonify(out)


@app.route('/campaigns/<int:campaign_id>/api/characters/<int:char_id>/detail')
@campaign_access_required
def api_character_detail(char_id):
    db = get_db()
    character = db.execute('''
        SELECT c.*, g.name AS group_name, g.color AS group_color, mk.username AS made_by
        FROM characters c LEFT JOIN groups g ON c.group_id = g.id
        LEFT JOIN users mk ON mk.id = c.created_by
        WHERE c.id = ? AND c.campaign_id = ?
    ''', (char_id, g.campaign_id)).fetchone()
    if character is None or npc_is_hidden(character['is_npc'], character['created_by']):
        db.close()
        return api_error(None, 404)
    if not g.is_dm and character['is_npc']:
        # 5e convention: a Player may read an NPC's notes but never its
        # numbers. This is the real enforcement point -- an allow-list, so a
        # column added to `characters` later can't leak by default. No stat
        # scores, HP, AC, raw notes or sheet images are sent.
        factions = _factions_by_character(db, g.campaign_id).get(char_id, [])
        db.close()
        return jsonify({
            'id': character['id'],
            'name': character['name'],
            'avatar_path': character['avatar_path'],
            'level': character['level'],
            'is_npc': character['is_npc'],
            'group_name': character['group_name'],
            'group_color': character['group_color'],
            'factions': factions,
            'notes_html': notes_to_html(character['notes'], show_hidden=can_see_hidden_notes(character)),
            'sheets': [],
            'made_by': character['made_by'],
            'can_delete': can_delete(character['created_by']),
            'hidden_stats': True,
        })
    sheets = db.execute('SELECT * FROM character_sheets WHERE character_id = ? ORDER BY sort_order', (char_id,)).fetchall()
    factions = _factions_by_character(db, g.campaign_id).get(char_id, [])
    attacks = _attacks_for(db, char_id)
    db.close()
    data = dict(character)
    data['attacks'] = attacks
    data['factions'] = factions
    data['can_delete'] = can_delete(character['created_by'])
    can_see_hidden = can_see_hidden_notes(character)
    data['notes'] = notes_for_viewer(character['notes'], can_see_hidden)   # raw JSON must not carry hidden notes
    data['sheets'] = [dict(s) for s in sheets]
    data['notes_html'] = notes_to_html(character['notes'], show_hidden=can_see_hidden)
    data['sheet_html'] = _character_sheet_html(character)
    data['sheet'] = dnd5e.derive(character)       # the same numbers as sheet_html, as data, for the click-to-roll popup
    return jsonify(data)


# ---------------- BATTLE ----------------

@app.route('/campaigns/<int:campaign_id>/battle')
@campaign_access_required
def battle_view():
    db = get_db()
    hide_sql, hide_params = npc_filter()
    characters = db.execute('''
        SELECT c.*, g.name AS group_name, g.color AS group_color
        FROM characters c LEFT JOIN groups g ON c.group_id = g.id
        WHERE c.is_temp_familiar = 0 AND c.campaign_id = ?''' + hide_sql + '''
        ORDER BY lower(c.name) ASC
    ''', [g.campaign_id] + hide_params).fetchall()
    by_char = _factions_by_character(db, g.campaign_id)
    db.close()
    characters = [dict(c, factions=by_char.get(c['id'], [])) for c in characters]
    if not g.is_dm:
        # A Player can't add anyone to the battle anyway (the "Add Character"
        # modal is DM-only), but don't hand the roster's stat block to the
        # page source either -- keep only what the (hidden) picker UI needs.
        characters = [{
            'id': c['id'], 'name': c['name'], 'avatar_path': c['avatar_path'],
            'group_id': c['group_id'], 'group_name': c['group_name'], 'group_color': c['group_color'],
            'factions': c['factions'], 'is_npc': c['is_npc'],
        } for c in characters]
    return render_template('battle.html', characters=characters,
                            conditions=[{'key': k, 'label': label} for k, label in BATTLE_CONDITIONS],
                            added_id=request.args.get('added'))


# The standard 5e conditions, in the order they are shown.  (Exhaustion is a plain on/off flag here.)
BATTLE_CONDITIONS = [
    ('blinded', 'Blinded'), ('charmed', 'Charmed'), ('deafened', 'Deafened'), ('exhaustion', 'Exhaustion'),
    ('frightened', 'Frightened'), ('grappled', 'Grappled'), ('incapacitated', 'Incapacitated'),
    ('invisible', 'Invisible'), ('paralyzed', 'Paralyzed'), ('petrified', 'Petrified'),
    ('poisoned', 'Poisoned'), ('prone', 'Prone'), ('restrained', 'Restrained'), ('stunned', 'Stunned'),
    ('unconscious', 'Unconscious'),
]
_CONDITION_KEYS = [k for k, _label in BATTLE_CONDITIONS]

# Custom conditions: free text the DM types for one participant ("Hexed", "On fire"...).  Plain text only (the
# screens escape it), short, and capped so a participant row can't turn into a notice board.
MAX_CUSTOM_CONDITIONS = 3
CUSTOM_CONDITION_MAX_LEN = 24


def _clean_custom_conditions(raw):
    """Validated list of custom condition names (whitespace tidied, case-insensitive duplicates dropped).
    Raises ValueError for anything that is not a list of 1..24-character strings, or has too many entries."""
    if not isinstance(raw, list) or len(raw) > MAX_CUSTOM_CONDITIONS:
        raise ValueError('custom_conditions')
    names = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError('custom_conditions')
        name = ' '.join(''.join(ch for ch in item if ch.isprintable()).split())
        if not name or len(name) > CUSTOM_CONDITION_MAX_LEN:
            raise ValueError('custom_conditions')
        if name.lower() not in [n.lower() for n in names]:
            names.append(name)
    return names


def _load_custom_conditions(stored):
    try:
        return _clean_custom_conditions(json.loads(stored or '[]'))
    except (ValueError, TypeError):
        return []


def _battle_live_status(db):
    """What the character cards show for anyone in the running battle: HP bar and conditions, keyed by character id.
    Built from _battle_rows, so a Player never gets a monster's numbers (those rows come back without HP)."""
    labels = dict(BATTLE_CONDITIONS)
    out = {}
    for r in _battle_rows(db, redact_enemies=not g.is_dm):
        cid = r['character_id']
        if r.get('is_temp_familiar') or cid in out or (r.get('is_npc') and not g.is_dm):
            continue
        cur, mx = r.get('current_hp'), r.get('char_max_hp')
        pct = None if cur is None or not mx else max(0, min(100, round(100 * cur / mx)))
        out[cid] = {'hp': cur, 'max': mx, 'pct': pct, 'dead': bool(r.get('is_dead')),
                    'conditions': [labels[k] for k in r['conditions'] if k in labels] + list(r['custom_conditions'])}
    return out


def _battle_rows(db, redact_enemies=False):
    """redact_enemies=True (a Player viewing the battle) strips HP, AC, and
    initiative off every NPC/monster row before it ever reaches the
    response -- this is the actual enforcement point (5e conventions: a
    player shouldn't know a monster's exact numbers), the client-side hiding
    is just presentation on top of it. PCs (is_npc=0) and familiars are
    never redacted -- a party can see its own stats."""
    rows = db.execute('''
        SELECT bp.*, c.name AS char_name, c.avatar_path, c.is_npc, c.max_hp AS char_max_hp,
               c.armor_class AS base_ac, c.is_temp_familiar, c.familiar_icon_key, c.class_key, c.created_by AS owner_id, c.player_id AS player_id,
               g.id AS gid, g.name AS group_name, g.color AS group_color
        FROM battle_participants bp
        JOIN characters c ON bp.character_id = c.id
        LEFT JOIN groups g ON c.group_id = g.id
        WHERE c.campaign_id = ?
        ORDER BY bp.sort_order ASC, bp.id ASC
    ''', (g.campaign_id,)).fetchall()
    rows = [dict(r) for r in rows]

    turn = db.execute('SELECT battle_round, battle_turn_id FROM campaigns WHERE id = ?', (g.campaign_id,)).fetchone()
    for r in rows:
        r['conditions'] = [k for k in _CONDITION_KEYS if k in (r.get('conditions') or '').split(',')]
        r['custom_conditions'] = _load_custom_conditions(r.get('custom_conditions'))
        r['is_active'] = bool(turn and turn['battle_turn_id'] == r['id'])
        r['battle_round'] = turn['battle_round'] if turn else 1

    for r in rows:
        r['effective_ac'] = r['ac_override'] if r['ac_override'] is not None else r['base_ac']
        # A binary "at 0 HP but not yet confirmed dead" flag is safe to send
        # even when the exact HP is redacted -- it's what lets a Player see
        # a hidden enemy go down without ever learning its actual numbers.
        r['is_downed'] = bool(r['current_hp'] is not None and r['current_hp'] <= 0 and not r['is_dead'])

    # Death saves (DL-145) belong to player characters; monsters just die.  Whether a character is stable is visible to
    # everyone, but the 3-and-3 tally only to the DM and to the player who made the character.
    me, dm = session.get('user_id'), getattr(g, 'is_dm', False)
    if getattr(g, 'previewing', False):                       # "View as player" sees what a Player who did not make the character sees
        me = None
    for r in rows:
        mine = me is not None and (r['owner_id'] == me or r.get('player_id') == me)
        r['is_stable'] = bool(r['is_stable'])
        r['can_death_save'] = bool(r['is_downed'] and not r['is_npc'] and not r['is_stable'] and (dm or mine))
        if not (dm or mine):
            r['death_successes'] = r['death_failures'] = None
        r['owner_id_'] = r.pop('owner_id', None)
        r.pop('player_id', None)

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

    if redact_enemies and getattr(g, 'campaign', None) is not None and g.campaign['hide_npcs']:
        rows = [r for r in rows if not r['is_npc'] or (me is not None and r['owner_id_'] == me)]   # gone from a Player's battle unless they made it
    for r in rows:
        r.pop('owner_id_', None)
    if redact_enemies:
        for r in rows:
            # A familiar the DM has marked as an NPC gets the same treatment
            # as any other monster -- only a familiar left as a PC (or any
            # real PC) stays fully visible to its party.
            if r['is_npc']:
                r['hidden_stats'] = True
                # conditions (standard and custom) stay visible: the table can see what is affecting a monster
                r['class_key'] = None                  # ...and so is what class it is
                r['current_hp'] = None
                r['char_max_hp'] = None
                r['temp_hp'] = None
                r['initiative'] = None
                r['ac_override'] = None
                r['effective_ac'] = None
                r['base_ac'] = None
    return rows


BATTLE_LOG_KEEP = 50           # entries kept per campaign; older ones are deleted as new ones arrive


def log_battle(db, text, public=None, kind='info', npc=False):
    """One line in the battle log (the caller commits).  `public` is what Players read: None = the same text, '' = hidden
    from Players (used for enemy numbers they must not see)."""
    try:
        db.execute('INSERT INTO battle_log (campaign_id, kind, text, public_text, about_npc) VALUES (?, ?, ?, ?, ?)',
                   (g.campaign_id, kind, text[:200], None if public is None else public[:200], bool(npc)))
        db.execute('DELETE FROM battle_log WHERE campaign_id = ? AND id NOT IN '
                   '(SELECT id FROM battle_log WHERE campaign_id = ? ORDER BY id DESC LIMIT ?)',
                   (g.campaign_id, g.campaign_id, BATTLE_LOG_KEEP))
    except pgerr.UndefinedTable:
        db.rollback()                                       # code deployed before migration 0026: skip rather than fail the action
        app.logger.error('battle_log is missing: apply migration 0026_battle_log.sql')


def _bp_state(db, pid):
    """What the log needs to know about one participant of THIS campaign: name (with the "#2" suffix when several share
    a name), whether it is an NPC, HP, temp HP, dead flag and conditions.  None if it is not in this campaign's battle."""
    rows = db.execute('''SELECT bp.id, c.name, c.is_npc, bp.current_hp, bp.temp_hp, bp.is_dead, bp.conditions, bp.custom_conditions
                          FROM battle_participants bp JOIN characters c ON c.id = bp.character_id
                          WHERE c.campaign_id = ? ORDER BY bp.sort_order ASC, bp.id ASC''', (g.campaign_id,)).fetchall()
    total, seen, found = {}, {}, None
    for r in rows:
        total[r['name']] = total.get(r['name'], 0) + 1
    for r in rows:
        seen[r['name']] = seen.get(r['name'], 0) + 1
        if r['id'] == pid:
            found = {'name': f"{r['name']} #{seen[r['name']]}" if total[r['name']] > 1 else r['name'], 'npc': bool(r['is_npc']),
                     'hp': r['current_hp'] or 0, 'temp': r['temp_hp'] or 0, 'dead': bool(r['is_dead']),
                     'conditions': [k for k in _CONDITION_KEYS if k in (r['conditions'] or '').split(',')],
                     'custom': _load_custom_conditions(r['custom_conditions'])}
    return found


def _reset_death_saves(db, pid, force=False):
    """Back to 0 successes, 0 failures, not stable (the caller commits).  Without `force` this only happens once the
    participant is above 0 HP, so a heal that still leaves them down keeps the tally."""
    db.execute('UPDATE battle_participants SET death_successes = 0, death_failures = 0, is_stable = 0 '
               'WHERE id = ? AND (? OR current_hp > 0) AND (death_successes <> 0 OR death_failures <> 0 OR is_stable <> 0)',
               (pid, bool(force)))


def _log_hp_change(db, b, a, intent):
    """Log what changed between two _bp_state snapshots: damage or healing (temp HP soaks up damage first), and whether
    the creature went down, got back up or was revived.  Players only learn that an enemy was hurt or healed, never by how much."""
    n, npc = a['name'], a['npc']
    absorbed, lost, gained = b['temp'] - a['temp'], b['hp'] - a['hp'], a['hp'] - b['hp']
    if intent == 'damage' and absorbed + lost > 0:
        extra = f' ({absorbed} absorbed by temp HP)' if absorbed > 0 else ''
        log_battle(db, f'{n} took {absorbed + lost} damage{extra}', f'{n} was hurt' if npc else None, 'damage', npc=npc)
    elif intent == 'heal' and gained > 0:
        log_battle(db, f'{n} healed {gained} HP', f'{n} was healed' if npc else None, 'heal', npc=npc)
    elif intent == 'set' and a['hp'] != b['hp']:
        sign = '+' if gained > 0 else '\u2212'
        log_battle(db, f"{n}'s HP set to {a['hp']} ({sign}{abs(gained)})",
                   (f'{n} was healed' if gained > 0 else f'{n} was hurt') if npc else None, 'heal' if gained > 0 else 'damage', npc=npc)
    if b['dead'] and not a['dead']:
        log_battle(db, f'{n} was revived', None, 'revive', npc=npc)
    elif b['hp'] <= 0 and a['hp'] > 0 and not b['dead']:
        log_battle(db, f'{n} is back on their feet', None, 'revive', npc=npc)
    if b['hp'] > 0 and a['hp'] <= 0 and not a['dead']:
        log_battle(db, f'{n} is down', None, 'down', npc=npc)


def _turn_order(db):
    """Participants in turn order: highest initiative first, ties by the order they were added.
    This is always computed from the real initiative values on the server, whatever order a
    screen happens to sort its list in (and whether or not a Player can see the numbers)."""
    rows = db.execute('''
        SELECT bp.id, bp.is_dead
        FROM battle_participants bp JOIN characters c ON bp.character_id = c.id
        WHERE c.campaign_id = ?
        ORDER BY bp.initiative DESC, bp.sort_order ASC, bp.id ASC
    ''', (g.campaign_id,)).fetchall()
    return [(r['id'], bool(r['is_dead'])) for r in rows]


def _pass_turn(db, skip_id=None, step=1):
    """Move the turn to the next (step=1) or previous (step=-1) living participant.  Dead ones are
    skipped, and so is `skip_id`, someone who is about to be removed.  Going past the end of the
    order starts a new round; going back past the top returns to the previous round (never below 1).
    Returns False if someone else moved the turn first, so a double click never skips a person."""
    camp = db.execute('SELECT battle_round, battle_turn_id FROM campaigns WHERE id = ?', (g.campaign_id,)).fetchone()
    order = _turn_order(db)
    ids = [i for i, _dead in order]
    alive = {i for i, dead in order if not dead and i != skip_id}
    current, rnd, nxt = camp['battle_turn_id'], camp['battle_round'], None
    start = ids.index(current) if current in ids else -1
    if step < 0 and start < 0:
        return True                                        # nothing has started, so there is nothing to go back to
    for k in range(1, len(ids) + 1):
        j = start + step * k
        if ids[j % len(ids)] in alive:
            nxt = ids[j % len(ids)]
            if step > 0 and start >= 0 and j >= len(ids):
                rnd += 1                                   # went past the last participant
            elif step < 0 and j < 0:
                if rnd <= 1:
                    return True                            # already at the very first turn of round 1
                rnd -= 1
            break
    cur = db.execute(
        'UPDATE campaigns SET battle_turn_id = ?, battle_round = ? WHERE id = ? AND battle_turn_id IS NOT DISTINCT FROM CAST(? AS INTEGER)',
        (nxt, rnd if nxt is not None else camp['battle_round'], g.campaign_id, current))
    return cur.rowcount == 1


def _pass_turn_logged(db, step):
    """_pass_turn, plus a log line when combat starts or the round changes (a line per turn would only be noise)."""
    camp = db.execute('SELECT battle_round, battle_turn_id FROM campaigns WHERE id = ?', (g.campaign_id,)).fetchone()
    _pass_turn(db, step=step)
    now = db.execute('SELECT battle_round, battle_turn_id FROM campaigns WHERE id = ?', (g.campaign_id,)).fetchone()
    if camp['battle_turn_id'] is None and now['battle_turn_id'] is not None:
        log_battle(db, 'Combat started: round 1', None, 'round')
    elif now['battle_round'] > camp['battle_round']:
        log_battle(db, f"Round {now['battle_round']} begins", None, 'round')
    elif now['battle_round'] < camp['battle_round']:
        log_battle(db, f"Back to round {now['battle_round']}", None, 'round')


def _create_familiar_participant(db, icon_key, custom_name):
    """A familiar/prop pin gets a lightweight, temporary battle presence: a
    throwaway character (flagged is_temp_familiar so it never shows in the
    real character roster) plus a normal battle_participants row. Deleting
    the character (see _cleanup_familiar_participants) cascades to remove
    the participant row too."""
    display_name = custom_name or (icon_key or 'familiar').capitalize()
    cur = db.execute(
        '''INSERT INTO characters (name, level, max_hp, armor_class, is_npc, is_temp_familiar, familiar_icon_key, campaign_id, created_by)
           VALUES (?, 1, 4, 10, 0, 1, ?, ?, ?)''',
        (display_name, icon_key, g.campaign_id, session['user_id'])
    )
    char_id = cur.lastrowid
    cur2 = db.execute('INSERT INTO battle_participants (character_id, current_hp) VALUES (?, 4)', (char_id,))
    return cur2.lastrowid


def _detach_character_pins(db, map_id):
    """An unlinked map keeps its characters as plain map tokens: they just stop pointing at battle rows."""
    db.execute("UPDATE map_pins SET participant_id = NULL WHERE map_id = ? AND pin_type = 'character'", (map_id,))


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


@app.route('/campaigns/<int:campaign_id>/api/battle/log')
@campaign_access_required
def api_battle_log():
    """The battle log, newest first.  Players get the public wording of enemy events (and none of the hidden ones)."""
    db = get_db()
    try:
        rows = db.execute('SELECT id, kind, text, public_text, about_npc, created_at FROM battle_log WHERE campaign_id = ? ORDER BY id DESC LIMIT ?',
                          (g.campaign_id, BATTLE_LOG_KEEP)).fetchall()
    except pgerr.UndefinedTable:
        db.rollback()
        rows = []                                           # not migrated yet: an empty log, not an error on the battle page
    db.close()
    out = []
    hide_npc_lines = npcs_hidden_from_viewer()
    for r in rows:
        if hide_npc_lines and r['about_npc']:
            continue
        text = r['text'] if g.is_dm or r['public_text'] is None else r['public_text']
        if text:
            out.append({'id': r['id'], 'kind': r['kind'], 'text': text, 'at': str(r['created_at']).replace(' ', 'T') + 'Z'})      # UTC, shown in the viewer's own time zone
    return jsonify(out)


@app.route('/campaigns/<int:campaign_id>/api/battle/add', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_add():
    data = request.get_json(force=True)
    character_id = data.get('character_id')
    db = get_db()
    char = db.execute('SELECT max_hp, name, is_npc FROM characters WHERE id = ? AND campaign_id = ?', (character_id, g.campaign_id)).fetchone()
    if char is None:
        db.close()
        return api_error('That character could not be found.', 404)
    max_order = db.execute('SELECT COALESCE(MAX(sort_order), -1) AS m FROM battle_participants').fetchone()['m']
    db.execute('''
        INSERT INTO battle_participants (character_id, current_hp, initiative, is_dead, sort_order)
        VALUES (?, ?, 0, 0, ?)
    ''', (character_id, char['max_hp'], max_order + 1))
    log_battle(db, f"{char['name']} joined the battle", None, 'joined', npc=bool(char['is_npc']))
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


MONSTER_COUNT_MAX = 12
_HP_ROLL_RE = re.compile(r'^\s*(\d{1,3})\s*d\s*(\d{1,3})\s*(?:([+-])\s*(\d{1,4}))?\s*$', re.I)


def _roll_monster_hp(expr, average):
    """Hit points from the stat block's dice ("3d6+3"); the printed average if it cannot be read."""
    m = _HP_ROLL_RE.match(expr or '')
    if not m:
        return average
    n, sides = int(m.group(1)), int(m.group(2))
    if not (1 <= n <= 100 and 2 <= sides <= 100):
        return average
    bonus = int(m.group(4) or 0) * (-1 if m.group(3) == '-' else 1)
    return max(1, sum(secrets.randbelow(sides) + 1 for _ in range(n)) + bonus)


@app.route('/campaigns/<int:campaign_id>/api/battle/add-monster', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_add_monster():
    """Create NPCs from a 2024 stat block and put them straight into the battle.
    Body: {ref: 'monsters/goblin-warrior', count: 1-12, hp: 'average' | 'rolled'}.  One character per copy
    ("Goblin 1", "Goblin 2"...), so each can have its own rolled hit points; the stat block is a note hidden from Players."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return api_error(None, 400)
    try:
        count = 1 if data.get('count') in (None, '') else int(data['count'])
    except (TypeError, ValueError):
        return api_error(None, 400)
    if not 1 <= count <= MONSTER_COUNT_MAX:
        return api_error(f'Add between 1 and {MONSTER_COUNT_MAX} at a time.', 400)
    rolled = data.get('hp') == 'rolled'
    try:
        entry = srd.detail('monsters', data.get('ref'))
    except srd.SrdError as e:
        return api_error(str(e), _srd_status(e))
    c = entry['prefill']
    notes = sanitize_notes_payload(json.dumps([{'t': n, 'h': 1} for n in entry.get('notes') or []]))
    skill_prof = json.dumps(dnd5e.clean_skill_prof(c['skill_prof']))
    save_prof = json.dumps(dnd5e.clean_save_prof(c['save_prof']))
    db = get_db()
    max_order = db.execute('SELECT COALESCE(MAX(sort_order), -1) AS m FROM battle_participants').fetchone()['m']
    for i in range(1, count + 1):
        hp = _roll_monster_hp(c.get('hp_roll'), c['max_hp']) if rolled else c['max_hp']
        cur = db.execute('''
            INSERT INTO characters
            (name, is_npc, level, max_hp, str_score, dex_score, con_score, int_score, wis_score, cha_score,
             armor_class, notes, campaign_id, created_by, speed, skill_prof, save_prof)
            VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', ((c['name'] if count == 1 else f"{c['name']} {i}")[:120], c['level'], hp, c['str_score'], c['dex_score'],
              c['con_score'], c['int_score'], c['wis_score'], c['cha_score'], c['armor_class'], notes,
              g.campaign_id, session['user_id'], c['speed'], skill_prof, save_prof))
        max_order += 1
        db.execute('INSERT INTO battle_participants (character_id, current_hp, initiative, is_dead, sort_order) VALUES (?, ?, 0, 0, ?)',
                   (cur.lastrowid, hp, max_order))
    log_battle(db, f"{c['name']} joined the battle" if count == 1 else f"{count} \u00d7 {c['name']} joined the battle", None, 'joined', npc=True)
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
    members = db.execute('SELECT c.id, c.max_hp FROM characters c JOIN character_groups cg ON cg.character_id = c.id '
                         'WHERE cg.group_id = ? AND c.campaign_id = ? ORDER BY c.id', (group_id, g.campaign_id)).fetchall()
    max_order = db.execute('SELECT COALESCE(MAX(sort_order), -1) AS m FROM battle_participants').fetchone()['m']
    for i, m in enumerate(members):
        db.execute('''
            INSERT INTO battle_participants (character_id, current_hp, initiative, is_dead, sort_order)
            VALUES (?, ?, 0, 0, ?)
        ''', (m['id'], m['max_hp'], max_order + 1 + i))
    if members:
        gname = db.execute('SELECT name FROM groups WHERE id = ? AND campaign_id = ?', (group_id, g.campaign_id)).fetchone()
        log_battle(db, f"{gname['name'] if gname else 'A faction'} joined the battle ({len(members)})", None, 'joined')
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
    before = _bp_state(db, pid)
    if before is None:
        db.close()
        return api_error(None, 404)
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
        return api_error(None, 404)
    _log_hp_change(db, before, _bp_state(db, pid), 'heal')
    _reset_death_saves(db, pid)
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
    before = _bp_state(db, pid)
    if before is None:
        db.close()
        return api_error(None, 404)
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
        return api_error(None, 404)
    _log_hp_change(db, before, _bp_state(db, pid), 'damage')
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
    before = _bp_state(db, pid)
    if before is None:
        db.close()
        return api_error(None, 404)
    db.execute('UPDATE battle_participants SET temp_hp = ? WHERE id = ?', (value, pid))
    if value != before['temp']:
        log_battle(db, f"{before['name']} has {value} temp HP" if value else f"{before['name']} lost their temp HP",
                   '' if before['npc'] else None, 'temp', npc=before['npc'])            # an enemy's temp HP is not for Players to see
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
    before = _bp_state(db, pid)
    if before is None:
        db.close()
        return api_error(None, 404)
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
        return api_error(None, 404)
    _log_hp_change(db, before, _bp_state(db, pid), 'set')
    _reset_death_saves(db, pid)
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


INITIATIVE_SCOPES = {'all': ('everyone', None), 'pc': ('the player characters', 0), 'npc': ('the NPCs and monsters', 1)}


@app.route('/campaigns/<int:campaign_id>/api/battle/roll-initiative', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_roll_initiative():
    """d20 + Dexterity modifier for everyone still standing, or only for the player characters or only for the NPCs
    ({"scope": "all" | "pc" | "npc"}, default all).  The others keep the numbers they have.  Typing a number by hand
    keeps working afterwards."""
    data = request.get_json(silent=True) or {}
    scope = data.get('scope', 'all')
    if scope not in INITIATIVE_SCOPES:
        return api_error('Roll for everyone, the player characters or the NPCs.', 400)
    who, npc_flag = INITIATIVE_SCOPES[scope]
    db = get_db()
    sql = '''
        SELECT bp.id, c.dex_score FROM battle_participants bp JOIN characters c ON c.id = bp.character_id
        WHERE c.campaign_id = ? AND bp.is_dead = 0'''
    params = [g.campaign_id]
    if npc_flag is not None:
        sql += ' AND c.is_npc = ?'
        params.append(npc_flag)
    for r in db.execute(sql, params).fetchall():
        total = secrets.randbelow(20) + 1 + dnd5e.modifier(r['dex_score'] if r['dex_score'] is not None else 10)
        db.execute('UPDATE battle_participants SET initiative = ? WHERE id = ?', (total, r['id']))
    log_battle(db, 'Initiative rolled for ' + who, None, 'initiative', npc=(scope == 'npc'))
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
    before = _bp_state(db, pid)
    if before is None:
        db.close()
        return api_error(None, 404)
    db.execute('UPDATE battle_participants SET is_dead = 1 WHERE id = ?', (pid,))
    if not before['dead']:
        log_battle(db, f"{before['name']} died", None, 'died', npc=before['npc'])
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/<int:pid>/revive', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_revive(pid):
    db = get_db()
    before = _bp_state(db, pid)
    if before is None:
        db.close()
        return api_error(None, 404)
    db.execute('UPDATE battle_participants SET is_dead = 0 WHERE id = ?', (pid,))
    _reset_death_saves(db, pid, force=True)                 # a revived character starts the count again
    if before['dead']:
        log_battle(db, f"{before['name']} was revived", None, 'revive', npc=before['npc'])
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/<int:pid>/death-save', methods=['POST'])
@campaign_access_required
def api_battle_death_save(pid):
    """One death saving throw for a character who is down at 0 HP (2024 rules).  The DM can roll for any player
    character; a Player only for a character they made.  The d20 is rolled here, never in the browser.
    10 or more is a success, 9 or less a failure, a natural 1 counts as two failures and a natural 20 puts the character
    back on 1 HP.  Three successes make them stable, three failures kill them.  The tally lives on the participant
    (migration 0027) and is cleared by healing, setting HP above 0 or reviving.  A Player's own roll is not announced
    in the log (the table sees only the outcome: stable, back on 1 HP, died); the DM gets every roll."""
    db = get_db()
    row = db.execute('''SELECT bp.current_hp, bp.is_dead, bp.is_stable, bp.death_successes AS ok, bp.death_failures AS bad,
                               c.is_npc, c.created_by, c.player_id
                        FROM battle_participants bp JOIN characters c ON c.id = bp.character_id
                        WHERE bp.id = ? AND c.campaign_id = ?''', (pid, g.campaign_id)).fetchone()
    if row is None:
        db.close()
        return api_error(None, 404)
    if not (g.is_dm or owns_character(row)):
        db.close()
        return api_error(None, 403)
    if row['is_npc']:
        db.close()
        return api_error('Monsters and NPCs do not make death saves.', 400, code='not_applicable')
    if row['current_hp'] > 0 or row['is_dead'] or row['is_stable']:
        db.close()
        return api_error('Only a character who is down and not yet stable can make a death save.', 409, code='not_downed')
    before = _bp_state(db, pid)
    roll = secrets.randbelow(20) + 1
    ok, bad, hp, stable, dead = row['ok'], row['bad'], 0, 0, 0
    if roll == 20:
        outcome, hp, ok, bad = 'revived', 1, 0, 0
    else:
        if roll >= 10:
            ok += 1
        else:
            bad = min(3, bad + (2 if roll == 1 else 1))
        outcome = 'success' if roll >= 10 else 'failure'
    tally = (ok, bad)                                       # as it stands after this roll (before a reset)
    if outcome in ('success', 'failure'):
        if ok >= 3:
            outcome, stable, ok, bad = 'stable', 1, 0, 0
        elif bad >= 3:
            outcome, dead = 'dead', 1
    # compare-and-swap on the tally we read, so two rolls at the same instant cannot overwrite each other
    cur = db.execute('''UPDATE battle_participants SET current_hp = ?, death_successes = ?, death_failures = ?, is_stable = ?, is_dead = ?
                        WHERE id = ? AND current_hp <= 0 AND is_dead = 0 AND is_stable = 0
                          AND death_successes = ? AND death_failures = ?''',
                     (hp, ok, bad, stable, dead, pid, row['ok'], row['bad']))
    if cur.rowcount == 0:
        db.rollback()
        db.close()
        return api_error('Someone else just rolled for this character; look again and retry.', 409, code='conflict')
    n = before['name']
    if outcome != 'revived':
        verdict = 'two failures' if roll == 1 else ('success' if roll >= 10 else 'failure')
        log_battle(db, f"{n} rolled {roll} on a death save: {verdict} ({tally[0]} of 3 successes, {tally[1]} of 3 failures)", '', 'deathsave')
    if outcome == 'revived':
        log_battle(db, f'{n} rolled a natural 20 on a death save and is back on 1 HP', None, 'revive')
    elif outcome == 'stable':
        log_battle(db, f'{n} is stable', None, 'stable')
    elif outcome == 'dead':
        log_battle(db, f'{n} died', None, 'died')
    db.commit()
    rows = _battle_rows(db, redact_enemies=not g.is_dm)
    db.close()
    return jsonify({'roll': roll, 'outcome': outcome, 'rows': rows})


@app.route('/campaigns/<int:campaign_id>/api/battle/<int:pid>/conditions', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_conditions(pid):
    """DM only. Replaces the participant's condition list (unknown names are refused)."""
    payload = request.get_json(silent=True) or {}
    wanted = payload.get('conditions')
    if not isinstance(wanted, list) or any(not isinstance(k, str) or k not in _CONDITION_KEYS for k in wanted):
        return api_error('Unknown condition.', 400, code='unknown_condition')
    custom = None                                          # omitted = leave the participant's custom conditions alone
    if 'custom_conditions' in payload:
        try:
            custom = _clean_custom_conditions(payload['custom_conditions'])
        except ValueError:
            return api_error('Custom conditions: up to %d, each 1-%d characters of plain text.'
                             % (MAX_CUSTOM_CONDITIONS, CUSTOM_CONDITION_MAX_LEN), 400)
    db = get_db()
    before = _bp_state(db, pid)
    if before is None:
        db.close()
        return api_error(None, 404)
    db.execute('UPDATE battle_participants SET conditions = ? WHERE id = ?',
               (','.join(k for k in _CONDITION_KEYS if k in wanted), pid))
    if custom is not None:
        db.execute('UPDATE battle_participants SET custom_conditions = ? WHERE id = ?', (json.dumps(custom), pid))
    labels = dict(BATTLE_CONDITIONS)
    now_std = [k for k in _CONDITION_KEYS if k in wanted]
    for k in now_std:
        if k not in before['conditions']:
            log_battle(db, f"{before['name']} is now {labels[k]}", None, 'condition', npc=before['npc'])
    for k in before['conditions']:
        if k not in now_std:
            log_battle(db, f"{before['name']} is no longer {labels[k]}", None, 'condition', npc=before['npc'])
    if custom is not None:
        for c in custom:
            if c not in before['custom']:
                log_battle(db, f"{before['name']} is now {c}", None, 'condition', npc=before['npc'])
        for c in before['custom']:
            if c not in custom:
                log_battle(db, f"{before['name']} is no longer {c}", None, 'condition', npc=before['npc'])
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/next-turn', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_next_turn():
    """DM only. Starts the fight (first in initiative order) or passes the turn on; the round
    counter goes up each time the order wraps round to the top."""
    db = get_db()
    _pass_turn_logged(db, 1)
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/prev-turn', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_prev_turn():
    """DM only. Steps back one turn (undo a mis-click); going back past the top returns to the previous round."""
    db = get_db()
    _pass_turn_logged(db, -1)
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/restart-turns', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_restart_turns():
    """DM only. Back to round 1 with the first living participant in initiative order acting."""
    db = get_db()
    first = next((i for i, dead in _turn_order(db) if not dead), None)
    db.execute('UPDATE campaigns SET battle_round = 1, battle_turn_id = ? WHERE id = ?', (first, g.campaign_id))
    log_battle(db, 'Turns restarted: round 1', None, 'round')
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


def _remove_participant(db, pid):
    """Take one participant out of this campaign's battle (the caller commits).  Removing whoever is acting
    passes the turn on, and an emptied battle goes back to round 1."""
    row = db.execute(
        'SELECT bp.id FROM battle_participants bp JOIN characters c ON c.id = bp.character_id WHERE bp.id = ? AND c.campaign_id = ?',
        (pid, g.campaign_id)).fetchone()
    if row is None:
        return False
    camp = db.execute('SELECT battle_turn_id FROM campaigns WHERE id = ?', (g.campaign_id,)).fetchone()
    if camp and camp['battle_turn_id'] == pid:
        _pass_turn(db, skip_id=pid)
    db.execute('DELETE FROM battle_participants WHERE id = ?', (pid,))
    if not _turn_order(db):
        db.execute('UPDATE campaigns SET battle_round = 1, battle_turn_id = NULL WHERE id = ?', (g.campaign_id,))
    return True


@app.route('/campaigns/<int:campaign_id>/api/battle/<int:pid>/remove', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_remove(pid):
    db = get_db()
    before = _bp_state(db, pid)
    if _remove_participant(db, pid) and before:
        log_battle(db, f"{before['name']} left the battle", None, 'left', npc=before['npc'])
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/campaigns/<int:campaign_id>/api/battle/clear', methods=['POST'])
@campaign_access_required
@dm_required
def api_battle_clear():
    db = get_db()
    try:
        db.execute('DELETE FROM battle_log WHERE campaign_id = ?', (g.campaign_id,))      # a new encounter starts with a clean log
    except pgerr.UndefinedTable:
        db.rollback()                                       # not migrated yet
    db.execute(
        'DELETE FROM battle_participants WHERE character_id IN (SELECT id FROM characters WHERE campaign_id = ?)',
        (g.campaign_id,)
    )
    db.execute('UPDATE campaigns SET battle_round = 1, battle_turn_id = NULL WHERE id = ?', (g.campaign_id,))
    log_battle(db, 'Battle cleared', None, 'left')
    db.commit()
    db.close()
    return jsonify([])


def _activity_events(db, limit=100):
    """Recent campaign events this person may see (NPC events are left out while NPCs are hidden from Players)."""
    try:
        events = db.execute('''SELECT a.id, a.kind, a.text, a.link, a.created_at, COALESCE(NULLIF(u.display_name, ''), u.username) AS actor
                                FROM campaign_activity a LEFT JOIN users u ON u.id = a.user_id
                                WHERE a.campaign_id = ? ORDER BY a.id DESC LIMIT ?''', (g.campaign_id, limit)).fetchall()
    except pgerr.UndefinedTable:
        db.rollback()
        return []
    if npcs_hidden_from_viewer():                  # nothing about an NPC (its name is in the text) while they are hidden from Players
        hidden_ids = {str(r['id']) for r in db.execute('SELECT id, created_by FROM characters WHERE campaign_id = ? AND is_npc = 1',
                                                      (g.campaign_id,)).fetchall() if npc_is_hidden(1, r['created_by'])}
        events = [e for e in events if not (e['link'] and e['link'].rstrip('/').rsplit('/', 1)[-1] in hidden_ids and '/characters/' in e['link'])
                  and not (e['text'] or '').startswith('added the NPC ')]
    return events


@app.route('/campaigns/<int:campaign_id>/activity')
@campaign_access_required
def activity_view():
    """What has happened in this campaign lately: members joining or leaving, and things added or removed."""
    db = get_db()
    events = _activity_events(db)
    db.close()
    return render_template('activity.html', events=events)


@app.route('/campaigns/<int:campaign_id>/home')
@campaign_access_required
def campaign_home():
    """The campaign's front page: the party, where the battle stands, what happened lately and the latest rolls."""
    db = get_db()
    hide_sql, hide_params = npc_filter()
    party = db.execute('''SELECT c.id, c.name, c.level, c.max_hp, c.armor_class, c.avatar_path, c.class_key
                           FROM characters c WHERE c.campaign_id = ? AND c.is_temp_familiar = 0 AND c.is_npc = 0
                           ORDER BY lower(c.name) ASC''', (g.campaign_id,)).fetchall()
    members = db.execute('''SELECT u.id, u.username, cm.role, cm.status
                             FROM campaign_members cm JOIN users u ON u.id = cm.user_id
                             WHERE cm.campaign_id = ?
                             ORDER BY (cm.status = 'dm') DESC, lower(u.username) ASC''', (g.campaign_id,)).fetchall()
    counts = db.execute('''SELECT (SELECT COUNT(*) FROM characters c WHERE c.campaign_id = ? AND c.is_temp_familiar = 0''' + hide_sql + ''') AS characters,
                                  (SELECT COUNT(*) FROM groups WHERE campaign_id = ?) AS factions,
                                  (SELECT COUNT(*) FROM maps WHERE campaign_id = ?) AS maps''',
                        [g.campaign_id] + hide_params + [g.campaign_id, g.campaign_id]).fetchone()
    rows = _battle_rows(db, redact_enemies=not g.is_dm)
    battle = {'count': len(rows), 'round': rows[0]['battle_round'] if rows else 1,
              'active': next((r['display_name'] for r in rows if r['is_active']), None)}
    events = _activity_events(db, 5)
    rolls = list(reversed(_dice_feed(db, limit=5)))
    played = {}
    for r in db.execute('SELECT c.id, c.name, c.player_id FROM characters c WHERE c.campaign_id = ? AND c.is_temp_familiar = 0 AND c.player_id IS NOT NULL'
                        + hide_sql + ' ORDER BY lower(c.name)', [g.campaign_id] + hide_params).fetchall():
        played.setdefault(r['player_id'], []).append(r)
    next_session = _next_session(db)
    db.close()
    return render_template('campaign_home.html', next_session=next_session, played=played, bio_html=notes_to_html(g.campaign['description']),
                           campaign_notes=notes_to_html(g.campaign['notes'], show_hidden=g.is_dm), members=members, party=party, counts=counts, battle=battle, events=events, rolls=rolls)


# ---------------- SESSION SCHEDULING (DL-152) ----------------
# One "next session" per campaign: a UTC time and a short note.  Members answer yes / maybe / no; changing the time
# clears the answers.  The browser turns the UTC time into the viewer's own time zone (static/js/home.js).

SESSION_STATUSES = ('yes', 'maybe', 'no')
SESSION_NOTE_MAX = 300
SESSION_STALE_AFTER = timedelta(hours=6)       # a session that started more than this long ago no longer counts as "next"


def _parse_session_time(raw):
    """A UTC datetime (naive) from an ISO-8601 string such as 2026-11-07T18:00:00.000Z, or None if it is not one or is
    unreasonable (more than a day ago or more than two years ahead)."""
    raw = (raw or '').strip()
    if not raw or len(raw) > 40:
        return None
    try:
        when = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if when < now - timedelta(days=1) or when > now + timedelta(days=730):
        return None
    return when.replace(microsecond=0)


def _as_utc_datetime(value):
    """A timestamp column as a naive UTC datetime (the database layer may hand back text)."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace(' ', 'T'))


def _next_session(db):
    """The upcoming session for the Home card and the calendar file, or None."""
    c = db.execute('SELECT next_session_at, next_session_note FROM campaigns WHERE id = ?', (g.campaign_id,)).fetchone()
    when = _as_utc_datetime(c['next_session_at']) if c else None
    if when is None or when < datetime.now(timezone.utc).replace(tzinfo=None) - SESSION_STALE_AFTER:
        return None
    answers = {k: [] for k in SESSION_STATUSES}
    for r in db.execute('''SELECT r.status, r.user_id, u.username FROM session_rsvps r JOIN users u ON u.id = r.user_id
                           WHERE r.campaign_id = ? ORDER BY lower(u.username)''', (g.campaign_id,)).fetchall():
        answers[r['status']].append(r['username'])
    mine = db.execute('SELECT status FROM session_rsvps WHERE campaign_id = ? AND user_id = ?', (g.campaign_id, session['user_id'])).fetchone()
    return {'at': when, 'iso': when.strftime('%Y-%m-%dT%H:%M:%SZ'), 'note': c['next_session_note'] or '', 'answers': answers,
            'mine': mine['status'] if mine else None}


@app.route('/campaigns/<int:campaign_id>/session', methods=['POST'])
@campaign_access_required
def campaign_session_set():
    """The DM or an owner sets, changes or clears the next session.  A new time clears the answers and tells the members."""
    if not (g.real_is_dm or g.campaign_role == 'owner'):
        abort(403)
    db = get_db()
    note = ' '.join((request.form.get('note') or '').split())[:SESSION_NOTE_MAX]
    if request.form.get('clear'):
        db.execute('UPDATE campaigns SET next_session_at = NULL, next_session_note = NULL WHERE id = ?', (g.campaign_id,))
        db.execute('DELETE FROM session_rsvps WHERE campaign_id = ?', (g.campaign_id,))
        db.commit(); db.close()
        return redirect(url_for('campaign_home'))
    when = _parse_session_time(request.form.get('starts_at'))
    if when is None:
        db.close()
        abort(400)
    old = _as_utc_datetime(db.execute('SELECT next_session_at FROM campaigns WHERE id = ?', (g.campaign_id,)).fetchone()['next_session_at'])
    db.execute('UPDATE campaigns SET next_session_at = ?, next_session_note = ? WHERE id = ?', (when, note or None, g.campaign_id))
    if old != when:
        db.execute('DELETE FROM session_rsvps WHERE campaign_id = ?', (g.campaign_id,))
        text = when.strftime('%a %d %b %Y, %H:%M UTC')
        for m in db.execute('SELECT user_id FROM campaign_members WHERE campaign_id = ? AND user_id <> ?', (g.campaign_id, session['user_id'])).fetchall():
            notify(db, m['user_id'], 'session_scheduled', 'Next session of %s: %s' % (g.campaign['name'], text),
                   'Open the campaign to say whether you can come.', url_for('campaign_home', campaign_id=g.campaign_id))
        log_activity(db, g.campaign_id, 'session', 'scheduled the next session for ' + text, url_for('campaign_home', campaign_id=g.campaign_id))
    db.commit(); db.close()
    return redirect(url_for('campaign_home'))


@app.route('/campaigns/<int:campaign_id>/session/rsvp', methods=['POST'])
@campaign_access_required
def campaign_session_rsvp():
    """Any member answers yes / maybe / no for the scheduled session."""
    status = request.form.get('status')
    if status not in SESSION_STATUSES:
        abort(400)
    db = get_db()
    if _next_session(db) is None:
        db.close()
        abort(409)                                          # nothing scheduled to answer
    db.execute('''INSERT INTO session_rsvps (campaign_id, user_id, status) VALUES (?, ?, ?)
                  ON CONFLICT (campaign_id, user_id) DO UPDATE SET status = EXCLUDED.status, updated_at = ''' + _UTC_NOW,
               (g.campaign_id, session['user_id'], status))
    db.commit(); db.close()
    return redirect(url_for('campaign_home'))


def _ics_escape(text):
    """RFC 5545 text escaping: backslash, semicolon, comma and line breaks."""
    return str(text).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\r', '').replace('\n', '\\n')


@app.route('/campaigns/<int:campaign_id>/session.ics')
@campaign_access_required
def campaign_session_ics():
    """The next session as a calendar file (a three-hour event; the DM's note goes in the description)."""
    db = get_db()
    nxt = _next_session(db)
    db.close()
    if nxt is None:
        abort(409)                                          # no session scheduled
    start = nxt['at']
    stamp = lambda d: d.strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Campaign Ledger//Session//EN', 'CALSCALE:GREGORIAN', 'BEGIN:VEVENT',
             'UID:session-%d-%s@campaign-ledger' % (g.campaign_id, stamp(start)),
             'DTSTAMP:' + stamp(datetime.now(timezone.utc)), 'DTSTART:' + stamp(start), 'DTEND:' + stamp(start + timedelta(hours=3)),
             'SUMMARY:' + _ics_escape('%s: next session' % g.campaign['name'])]
    if nxt['note']:
        lines.append('DESCRIPTION:' + _ics_escape(nxt['note']))
    lines += ['END:VEVENT', 'END:VCALENDAR']
    return Response('\r\n'.join(lines) + '\r\n', mimetype='text/calendar',
                    headers={'Content-Disposition': 'attachment; filename="session.ics"', 'Cache-Control': 'no-store'})


@app.route('/campaigns/<int:campaign_id>/dice')
@campaign_access_required
def dice_view():
    return render_template('dice.html')


# ---------------- DICE: rolls are made on the server and shared with the whole campaign ----------------

DICE_SIDES = (4, 6, 8, 10, 12, 20, 100)
DICE_MAX_PER_ROLL = 30
DICE_KEEP = 20                  # rolls kept per campaign: older ones are deleted as new ones arrive, so the table stays tiny
DICE_FEED_LIMIT = DICE_KEEP
DICE_NEEDS_MIGRATION = 'The dice log is not set up in this database yet. Run the database migrations (python migrate.py) and try again.'


def _dice_error(message, status=400):
    return api_error(message, status)


def _roll_row_out(r, names):
    """One stored roll as the browsers see it."""
    try:
        dice = json.loads(r['dice'])
    except ValueError:
        dice = []
    try:
        label_ = r['character_label']
    except (KeyError, IndexError):         # a database that has not had migration 0019 yet
        label_ = ''
    modifier, mode = r['modifier'], r['mode']
    # An NPC's roll shows Players only the final number: no dice, modifier or advantage (a bare d20 would give away
    # the creature's bonus).  The DM, and the DM viewing as a player, see the same thing the table does.
    hide_detail = bool(names.get(('npc', r['character_id']))) and not g.is_dm
    if hide_detail:
        dice, modifier, mode = [], 0, 'normal'
    return {'id': r['id'], 'user_id': r['user_id'], 'who': names.get(r['user_id'], 'Someone'),
            'mine': r['user_id'] == session.get('user_id'),
            'character_id': r['character_id'],
            'character': label_ or names.get(('c', r['character_id'])),
            'label': r['label'], 'dice': dice, 'modifier': modifier, 'total': r['total'],
            'mode': mode, 'private': bool(r['private']), 'hidden_detail': hide_detail, 'created_at': str(r['created_at'])}


def _dice_feed(db, after=None, limit=DICE_FEED_LIMIT):
    """Rolls this person may see, oldest first.  Private rolls go to DMs only (the roller always sees their own)."""
    sql = 'SELECT * FROM dice_rolls WHERE campaign_id = ? AND (private = 0 OR user_id = ? OR ? = 1)'
    params = [g.campaign_id, session['user_id'], 1 if g.is_dm else 0]
    if after:
        sql += ' AND id > ?'
        params.append(after)
    rows = db.execute(sql + ' ORDER BY id DESC LIMIT ?', params + [limit]).fetchall()
    rows = list(reversed(rows))
    names = {r['id']: r['username'] for r in db.execute(
        'SELECT u.id, u.username FROM users u JOIN campaign_members cm ON cm.user_id = u.id WHERE cm.campaign_id = ?',
        (g.campaign_id,)).fetchall()}
    for r in db.execute('SELECT id, name, is_npc, created_by FROM characters WHERE campaign_id = ?', (g.campaign_id,)).fetchall():
        names[('c', r['id'])] = r['name']
        names[('npc', r['id'])] = bool(r['is_npc'])
        names[('gone', r['id'])] = npc_is_hidden(r['is_npc'], r['created_by'])      # an NPC the DM hid: its rolls are not for Players
    return [_roll_row_out(r, names) for r in rows if not names.get(('gone', r['character_id']))]


@app.route('/campaigns/<int:campaign_id>/api/dice/roll', methods=['POST'])
@campaign_access_required
def dice_roll():
    """Roll on the server (so everyone sees one honest result), store it, and return it.
    Body: {dice: {"20": 1, "6": 2}, modifier, label, mode: normal|advantage|disadvantage, character_id, participant_id, private}.
    Advantage / disadvantage need exactly one d20 and roll a second one, keeping the higher / lower."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _dice_error('bad request')
    _db = get_db()
    try:
        _wait = security.hit(_db, app.secret_key, 'dice-user', session['user_id'])
        _db.commit()
    except pgerr.UndefinedTable:
        _db.rollback()
        _wait = 0
    finally:
        _db.close()
    if _wait:
        r = api_error('Rolling too fast -- give it a moment.', 429)
        r.headers['Retry-After'] = str(_wait)
        return r
    spec = data.get('dice')
    if not isinstance(spec, dict) or not spec:
        return _dice_error('Pick at least one die.')
    plan = []
    for key, count in spec.items():
        try:
            sides, count = int(key), int(count)
        except (TypeError, ValueError):
            return _dice_error('bad request')
        if sides not in DICE_SIDES or count < 0 or count > DICE_MAX_PER_ROLL:
            return _dice_error('bad request')
        plan += [sides] * count
    if not plan or len(plan) > DICE_MAX_PER_ROLL:
        return _dice_error(f'Roll between 1 and {DICE_MAX_PER_ROLL} dice at a time.')
    try:
        modifier = int(data.get('modifier') or 0)
    except (TypeError, ValueError):
        return _dice_error('bad request')
    if abs(modifier) > 999:
        return _dice_error('That modifier is out of range.')
    mode = data.get('mode') or 'normal'
    if mode not in ('normal', 'advantage', 'disadvantage'):
        return _dice_error('bad request')
    if mode != 'normal' and plan != [20]:
        return _dice_error('Advantage and disadvantage need exactly one d20.')
    label = str(data.get('label') or '').strip()[:80]
    private = bool(data.get('private')) and g.is_dm

    db = get_db()
    character_id = None
    if data.get('character_id') not in (None, ''):
        try:
            cid = int(data['character_id'])
        except (TypeError, ValueError):
            db.close()
            return _dice_error('bad request')
        ch = db.execute('SELECT id, is_npc FROM characters WHERE id = ? AND campaign_id = ?', (cid, g.campaign_id)).fetchone()
        if ch is None:
            db.close()
            return _dice_error('Character not found.', 404)
        if ch['is_npc'] and not g.is_dm:
            db.close()
            return _dice_error('Only the DM rolls for NPCs.', 403)
        character_id = ch['id']
    # Which copy in the battle rolled?  The number comes from the battle menu itself (never from the browser).
    character_label = ''
    if character_id and data.get('participant_id') not in (None, ''):
        try:
            pid_ = int(data['participant_id'])
        except (TypeError, ValueError):
            db.close()
            return _dice_error('bad request')
        for row in _battle_rows(db):
            if row['id'] == pid_ and row['character_id'] == character_id:
                character_label = row['display_name'][:80]
                break

    rolled = [{'sides': s, 'value': secrets.randbelow(s) + 1, 'dropped': False} for s in plan]
    if mode != 'normal':
        rolled.append({'sides': 20, 'value': secrets.randbelow(20) + 1, 'dropped': False})
        drop = min(rolled, key=lambda d: d['value']) if mode == 'advantage' else max(rolled, key=lambda d: d['value'])
        drop['dropped'] = True
    total = sum(d['value'] for d in rolled if not d['dropped']) + modifier
    try:
        db.execute('INSERT INTO dice_rolls (campaign_id, user_id, character_id, character_label, label, dice, modifier, total, mode, private) '
                   'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                   (g.campaign_id, session['user_id'], character_id, character_label, label, json.dumps(rolled), modifier, total, mode,
                    1 if private else 0))
        db.execute('DELETE FROM dice_rolls WHERE campaign_id = ? AND id NOT IN '
                   '(SELECT id FROM dice_rolls WHERE campaign_id = ? ORDER BY id DESC LIMIT ?)',
                   (g.campaign_id, g.campaign_id, DICE_KEEP))
        db.commit()
    except (pgerr.UndefinedTable, pgerr.UndefinedColumn):
        db.rollback()
        db.close()
        return _dice_error(DICE_NEEDS_MIGRATION, 503)
    latest = db.execute('SELECT MAX(id) AS m FROM dice_rolls WHERE campaign_id = ? AND user_id = ?',
                        (g.campaign_id, session['user_id'])).fetchone()['m']
    rolls = _dice_feed(db, after=latest - 1, limit=1)
    db.close()
    return jsonify(rolls[0] if rolls else {})


@app.route('/campaigns/<int:campaign_id>/api/dice/rolls')
@campaign_access_required
def dice_rolls():
    """The roll feed.  ?after=<id> returns rolls newer than that id; ?init=1 returns nothing but the newest id
    (so a page that has just opened does not replay old rolls as toasts); with neither, the most recent rolls."""
    db = get_db()
    try:
        if request.args.get('init'):
            latest = db.execute('SELECT MAX(id) AS m FROM dice_rolls WHERE campaign_id = ?', (g.campaign_id,)).fetchone()['m'] or 0
            return jsonify({'rolls': [], 'latest': latest})
        try:
            after = int(request.args.get('after') or 0)
        except ValueError:
            return _dice_error('bad request')
        rolls = _dice_feed(db, after=after or None)
        latest = db.execute('SELECT MAX(id) AS m FROM dice_rolls WHERE campaign_id = ?', (g.campaign_id,)).fetchone()['m'] or 0
        return jsonify({'rolls': rolls, 'latest': latest})
    except pgerr.UndefinedTable:
        db.rollback()
        return jsonify({'rolls': [], 'latest': 0, 'unavailable': True})      # not migrated yet: an empty feed, not an error on every page
    finally:
        db.close()


# ---------------- RULES REFERENCE (spells, monsters, items: the public 5e API, 2024 rules) ----------------

@app.route('/campaigns/<int:campaign_id>/reference')
@campaign_access_required
def reference_view():
    return render_template('reference.html', kinds=list(srd.KINDS), version=srd.VERSION)


def _srd_status(e):
    return {'bad request': 400, 'not found': 404}.get(str(e), 502)


@app.route('/campaigns/<int:campaign_id>/api/reference/search')
@campaign_access_required
def reference_search():
    try:
        return jsonify(srd.search(request.args.get('kind', ''), request.args.get('q', '')))
    except srd.SrdError as e:
        return api_error(str(e), _srd_status(e))


@app.route('/campaigns/<int:campaign_id>/api/reference/entry')
@campaign_access_required
def reference_detail():
    try:
        return jsonify(srd.detail(request.args.get('kind', ''), request.args.get('ref', '')))
    except srd.SrdError as e:
        return api_error(str(e), _srd_status(e))


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

    if not g.is_dm:
        # Same boundary as the detail API: a Player's export never carries
        # someone else's NPC (stat block + notes).
        characters = [c for c in characters if not c['is_npc'] or c['created_by'] == session.get('user_id')]

    # Pull in any group referenced by an exported character but not already included
    existing_group_ids = {g['id'] for g in groups}
    member_rows = db.execute('SELECT character_id, group_id FROM character_groups ORDER BY character_id, position').fetchall() if characters else []
    char_id_set = {c['id'] for c in characters}
    groups_of = {}
    for r in member_rows:
        if r['character_id'] in char_id_set:
            groups_of.setdefault(r['character_id'], []).append(r['group_id'])
    referenced_group_ids = {gid for ids in groups_of.values() for gid in ids} - existing_group_ids
    if referenced_group_ids:
        placeholders = ','.join('?' * len(referenced_group_ids))
        extra_groups = db.execute(f'SELECT * FROM groups WHERE id IN ({placeholders})', list(referenced_group_ids)).fetchall()
        groups = list(groups) + list(extra_groups)

    char_ids_for_sheets = [c['id'] for c in characters]
    attacks_by_char = {}
    if char_ids_for_sheets:
        placeholders = ','.join('?' * len(char_ids_for_sheets))
        for a in db.execute(f'SELECT character_id, name, attack_bonus, damage, damage_type FROM character_attacks '
                            f'WHERE character_id IN ({placeholders}) ORDER BY character_id, position, id', char_ids_for_sheets).fetchall():
            attacks_by_char.setdefault(a['character_id'], []).append(
                {'name': a['name'], 'attack_bonus': a['attack_bonus'], 'damage': a['damage'], 'damage_type': a['damage_type']})
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
                'class_key': c['class_key'],
                'speed': dnd5e.clean_speed(c['speed']),
                'skill_prof': dnd5e.clean_skill_prof(c['skill_prof']),
                'save_prof': dnd5e.clean_save_prof(c['save_prof']),
                'attacks': attacks_by_char.get(c['id'], []),
                'notes': notes_for_viewer(c['notes'], can_see_hidden_notes(c)),
                'group_name': group_id_to_name.get(c['group_id']),
                'group_names': [group_id_to_name[i] for i in groups_of.get(c['id'], []) if i in group_id_to_name],
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
    row = db.execute('SELECT name, is_npc, created_by FROM characters WHERE id = ? AND campaign_id = ?', (char_id, g.campaign_id)).fetchone()
    db.close()
    if row is None:
        return redirect(url_for('characters_list'))
    if row['is_npc'] and not g.is_dm and row['created_by'] != session['user_id']:
        abort(403)   # an NPC's stat block + notes are not a Player's to export
    return _build_export_zip(g.campaign_id, character_ids=[char_id], download_name=f'{_slugify(row["name"])}.zip')


@app.route('/campaigns/<int:campaign_id>/factions/<int:group_id>/export')
@campaign_access_required
def faction_export(group_id):
    db = get_db()
    row = db.execute('SELECT name FROM groups WHERE id = ? AND campaign_id = ?', (group_id, g.campaign_id)).fetchone()
    db.close()
    if row is None:
        return redirect(url_for('factions_list'))
    return _build_export_zip(g.campaign_id, group_ids=[group_id], character_ids=[], download_name=f'{_slugify(row["name"])}.zip')


@app.route('/campaigns/<int:campaign_id>/import/data', methods=['POST'])
@campaign_access_required
def import_data():
    dest = 'factions_list' if 'groups' in (request.referrer or '') else 'characters_list'
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
        cur = db.execute('INSERT INTO groups (name, avatar_path, bio, color, campaign_id, created_by) VALUES (?, ?, ?, ?, ?, ?)',
                          (grp['name'], avatar_path, grp['bio'], grp['color'], g.campaign_id, session['user_id']))
        group_name_to_id[key] = cur.lastrowid

    # Characters: always inserted as new records (never merged/overwritten)
    for c in manifest['characters']:
        wanted = c.get('group_names') or ([c['group_name']] if c.get('group_name') else [])
        import_group_ids = []
        for nm in wanted:
            gid_ = group_name_to_id.get((nm or '').lower())
            if gid_ is not None and gid_ not in import_group_ids:
                import_group_ids.append(gid_)
        group_id = import_group_ids[0] if import_group_ids else None
        avatar_path = extract_image(c['avatar_file'], 'avatars')
        cur = db.execute('''
            INSERT INTO characters
            (name, is_npc, level, max_hp, str_score, dex_score, con_score, int_score,
             wis_score, cha_score, armor_class, avatar_path, notes, group_id, class_key, campaign_id, created_by,
             speed, skill_prof, save_prof)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (c['name'], c['is_npc'], c['level'], c['max_hp'],
              c['str_score'], c['dex_score'], c['con_score'], c['int_score'],
              c['wis_score'], c['cha_score'], c['armor_class'],
              avatar_path, c['notes'], group_id, _clean_class(c.get('class_key')), g.campaign_id, session['user_id'],
              dnd5e.clean_speed(c.get('speed')), json.dumps(dnd5e.clean_skill_prof(c.get('skill_prof'))),
              json.dumps(dnd5e.clean_save_prof(c.get('save_prof')))))
        new_char_id = cur.lastrowid
        _save_attacks(db, new_char_id, c['attacks'])
        for pos, gid_ in enumerate(import_group_ids):
            db.execute('INSERT INTO character_groups (character_id, group_id, position) VALUES (?, ?, ?) RETURNING character_id', (new_char_id, gid_, pos))

        for i, sheet_rel_path in enumerate(c['sheet_files']):
            sheet_path = extract_image(sheet_rel_path, 'sheets')
            if sheet_path:
                db.execute('INSERT INTO character_sheets (character_id, image_path, sort_order) VALUES (?, ?, ?)',
                           (new_char_id, sheet_path, i))

    # Upload every queued image (in parallel) BEFORE committing, so a storage failure
    # rolls the whole import back instead of leaving rows that point at missing files.
    try:
        quotas.check(db, session['user_id'], g.campaign_id, sum(len(p.data) for _, p in pending))
    except quotas.QuotaExceeded:
        db.rollback()
        db.close()
        return redirect(url_for(dest, import_error='quota'))
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
    for key, processed in pending:
        quotas.record(db, key, session['user_id'], g.campaign_id, key.split('/')[0], len(processed.data))
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
    maps = db.execute('''SELECT m.*, mk.username AS made_by FROM maps m LEFT JOIN users mk ON mk.id = m.created_by
                          WHERE m.campaign_id = ? ORDER BY m.created_at DESC''', (g.campaign_id,)).fetchall()
    db.close()
    return render_template('maps_list.html', maps=[dict(m) for m in maps],
                           deletable={m['id'] for m in maps if can_delete(m['created_by'])})


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
        'INSERT INTO maps (name, image_path, image_width, image_height, campaign_id, created_by) VALUES (?, ?, ?, ?, ?, ?)',
        (name, image_path, width, height, g.campaign_id, session['user_id'])
    )
    map_id = cur.lastrowid
    log_activity(db, g.campaign_id, 'map_created', 'added the map %s' % name, url_for('map_editor', campaign_id=g.campaign_id, map_id=map_id))
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
    init = dict(m)
    # The raw image key goes to nobody's browser: the page loads the map through map_image(),
    # which paints fogged areas out for Players. The mask itself is fetched separately.
    init.pop('image_path', None)
    init.pop('fog_mask', None)
    init['image_url'] = url_for('map_image', map_id=map_id)
    init['fog_active'] = m['fog_mask'] is not None
    roster = []
    if g.is_dm:                                             # the members a token can be assigned to
        db = get_db()
        init['members'] = [dict(r) for r in db.execute(
            'SELECT u.id, u.username FROM campaign_members cm JOIN users u ON u.id = cm.user_id '
            'WHERE cm.campaign_id = ? ORDER BY lower(u.username)', (g.campaign_id,)).fetchall()]
        db.close()
    if g.is_dm:                                             # the DM's "Characters" tool; Players never get the roster
        db = get_db()
        roster = [dict(r) for r in db.execute('''
            SELECT c.id, c.name, c.avatar_path, c.is_npc, c.class_key, gr.name AS group_name, gr.color AS group_color
            FROM characters c LEFT JOIN groups gr ON c.group_id = gr.id
            WHERE c.is_temp_familiar = 0 AND c.campaign_id = ? ORDER BY lower(c.name) ASC
        ''', (g.campaign_id,)).fetchall()]
        db.close()
    return render_template('map_editor.html', map=init, roster=roster)


@app.route('/campaigns/<int:campaign_id>/maps/<int:map_id>/delete', methods=['POST'])
@campaign_access_required
def map_delete(map_id):
    db = get_db()
    m = db.execute('SELECT name, image_path, created_by FROM maps WHERE id = ? AND campaign_id = ?', (map_id, g.campaign_id)).fetchone()
    if m and not can_delete(m['created_by']):
        db.close()
        abort(403)
    if m:
        _cleanup_familiar_participants(db, map_id)
        delete_upload(m['image_path'])
        db.execute('DELETE FROM maps WHERE id = ? AND campaign_id = ?', (map_id, g.campaign_id))
        log_activity(db, g.campaign_id, 'map_deleted', 'removed the map %s' % m['name'])
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
               snap_to_grid, grid_setup_done, locked_for_players, linked_to_battle,
               fog_version, (fog_mask IS NOT NULL) AS fog_active
        FROM maps WHERE id = ? AND campaign_id = ?
    ''', (map_id, g.campaign_id)).fetchone()
    return dict(row) if row is not None else None


def _map_fog_row(db, map_id):
    """(raw mask bytes or None, cols, rows, version, image_path, width, height) for a map of this campaign."""
    m = db.execute('''
        SELECT fog_mask, fog_cols, fog_rows, fog_version, image_path, image_width, image_height
        FROM maps WHERE id = ? AND campaign_id = ?
    ''', (map_id, g.campaign_id)).fetchone()
    if m is None:
        return None
    raw = fog.from_storage(m['fog_mask'], m['fog_cols'], m['fog_rows'])
    return raw, m['fog_cols'], m['fog_rows'], m['fog_version'], m['image_path'], m['image_width'], m['image_height']


@app.route('/campaigns/<int:campaign_id>/maps/<int:map_id>/image')
@campaign_access_required
def map_image(map_id):
    """The map picture. The DM gets the original; a Player gets it with every fogged area painted
    out on the server, so hidden parts of the map never leave it."""
    db = get_db()
    info = _map_fog_row(db, map_id)
    db.close()
    if info is None:
        abort(404)
    raw, cols, rows, version, image_path, _w, _h = info
    km = _UPLOAD_KEY_RE.match(image_path or '')
    data = get_storage().get(UPLOADS, image_path) if km else None
    if data is None:
        abort(404)
    if g.is_dm or raw is None:
        mimetype = images.EXT_TO_CONTENT_TYPE[km.group(2).lower()]
    else:
        try:
            data, mimetype = fog.composite(data, raw, cols, rows)
        except Exception:
            abort(500)      # never fall back to the unmasked image for a Player
    resp = Response(data, mimetype=mimetype)
    # Revalidate every time: the URL is the same before and after the DM changes the fog.
    resp.headers['Cache-Control'] = 'private, no-cache'
    resp.headers['ETag'] = '"%s-%s-%s"' % (version, 'dm' if g.is_dm else 'pl', hashlib.sha1(image_path.encode()).hexdigest()[:8])
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp.make_conditional(request)


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/fog')
@campaign_access_required
def api_map_fog_get(map_id):
    """The fog mask (what is covered -- exactly what the DM chooses to show everyone)."""
    db = get_db()
    info = _map_fog_row(db, map_id)
    db.close()
    if info is None:
        abort(404)
    raw, cols, rows, version, _p, w, h = info
    dims = fog.grid_dims(w, h)
    return jsonify({'version': version, 'cell': fog.CELL, 'cols': dims[0] if dims else 0, 'rows': dims[1] if dims else 0,
                    'mask': fog.to_client(raw) if raw is not None else None})


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/fog', methods=['POST'])
@campaign_access_required
@dm_required
def api_map_fog_set(map_id):
    """DM only. Body: {"fill": true} covers the whole map, {"clear": true} removes all fog,
    {"mask": "<base64 packed bits>"} replaces the mask (the brush sends this when a stroke ends)."""
    data = request.get_json(silent=True) or {}
    db = get_db()
    info = _map_fog_row(db, map_id)
    if info is None:
        db.close()
        abort(404)
    w, h = info[5], info[6]
    dims = fog.grid_dims(w, h)
    if dims is None:
        db.close()
        return api_error('This map cannot have fog.', 400)
    cols, rows = dims
    if data.get('clear'):
        raw = None
    elif data.get('fill'):
        raw = fog.all_fogged(cols, rows)
    else:
        try:
            raw = fog.decode_client(data.get('mask'), cols, rows)
        except ValueError as e:
            db.close()
            return api_error(str(e), 400)
        if not fog.any_fogged(raw):
            raw = None
    db.execute('UPDATE maps SET fog_mask = ?, fog_cols = ?, fog_rows = ?, fog_version = fog_version + 1 WHERE id = ? AND campaign_id = ?',
               (fog.to_storage(raw) if raw is not None else None, cols, rows, map_id, g.campaign_id))
    db.commit()
    new_version = db.execute('SELECT fog_version FROM maps WHERE id = ?', (map_id,)).fetchone()['fog_version']
    db.close()
    return jsonify({'version': new_version, 'active': raw is not None})


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
                _detach_character_pins(db, o['id'])
                db.execute('UPDATE maps SET linked_to_battle = 0 WHERE id = ?', (o['id'],))
        else:
            _cleanup_familiar_participants(db, map_id)
            _detach_character_pins(db, map_id)
        fields.append('linked_to_battle = ?'); values.append(new_val)
    if fields:
        values.append(map_id)
        db.execute(f'UPDATE maps SET {", ".join(fields)} WHERE id = ?', values)
        if data.get('linked_to_battle'):
            _reconcile_map_battle(db, map_id)               # merge what is on the map with what is in the battle
        db.commit()
    m = db.execute('SELECT * FROM maps WHERE id = ?', (map_id,)).fetchone()
    db.close()
    out = dict(m) if m else {}
    out.pop('fog_mask', None)
    if not g.is_dm:
        out.pop('image_path', None)   # a Player gets the map only through the fog-aware image route
    return jsonify(out)


# ---- Drawings ----

@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/drawings')
@campaign_access_required
def api_map_drawings_list(map_id):
    db = get_db()
    result = _map_drawings_payload(db, map_id)
    db.close()
    return jsonify(result)


def _map_drawings_payload(db, map_id):
    # DM-only drawings never leave the server for a Player (the same redaction pattern as hidden notes).
    rows = db.execute('SELECT * FROM map_drawings WHERE map_id = ?' + ('' if g.is_dm else ' AND dm_only = 0')
                      + ' ORDER BY sort_order, id', (map_id,)).fetchall()
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
    dm_only = 1 if (g.is_dm and data.get('dm_only')) else 0        # only the DM can hide an object
    if kind not in ('line', 'rect', 'oval', 'pen', 'angle'):
        return api_error('Unknown kind of drawing.', 400)
    db = get_db()
    max_order = db.execute('SELECT COALESCE(MAX(sort_order), -1) AS m FROM map_drawings WHERE map_id = ?', (map_id,)).fetchone()['m']
    cur = db.execute(
        '''INSERT INTO map_drawings (map_id, kind, data, cx, cy, w, h, rotation, color, fill, fill_opacity, sort_order, locked, dm_only)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (map_id, kind, json.dumps(geometry), cx, cy, w, h, rotation, color, fill, fill_opacity, max_order + 1, locked, dm_only)
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
    if 'dm_only' in data:
        if not g.is_dm:
            db.close()
            abort(403)
        fields.append('dm_only = ?'); values.append(1 if data['dm_only'] else 0)
    if 'data' in data:
        fields.append('data = ?'); values.append(json.dumps(data['data']))
    if fields:
        values.append(drawing_id)
        values.append(map_id)
        # A Player can never touch an object the DM hid, even by guessing its id.
        db.execute(f'UPDATE map_drawings SET {", ".join(fields)} WHERE id = ? AND map_id = ?' + ('' if g.is_dm else ' AND dm_only = 0'), values)
        db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/drawings/<int:drawing_id>/delete', methods=['POST'])
@campaign_access_required
@map_edit_allowed
def api_map_drawing_delete(map_id, drawing_id):
    db = get_db()
    db.execute('DELETE FROM map_drawings WHERE id = ? AND map_id = ?' + ('' if g.is_dm else ' AND dm_only = 0'), (drawing_id, map_id))
    db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/drawings/clear', methods=['POST'])
@campaign_access_required
@map_edit_allowed
def api_map_drawings_clear(map_id):
    db = get_db()
    db.execute('DELETE FROM map_drawings WHERE map_id = ?' + ('' if g.is_dm else ' AND dm_only = 0'), (map_id,))
    db.commit()
    db.close()
    return jsonify({'ok': True})


# ---- Pins ----

def _reconcile_map_battle(db, map_id):
    """While a map is linked to the battle, the two share ONE roster (the caller commits):
      * a battle row that has no token on the map gets one,
      * a token that has no battle row yet is added to the battle (adopting an unplaced row of the same
        character first, so linking a map that already shows the party never doubles anyone),
      * a token whose battle row was removed from the battle disappears from the map.
    Tokens of an unlinked map are plain map tokens (participant_id NULL) and are left alone."""
    parts = db.execute('''
        SELECT bp.id, bp.character_id FROM battle_participants bp
        JOIN characters c ON bp.character_id = c.id
        WHERE c.is_temp_familiar = 0 AND c.campaign_id = ?
        ORDER BY bp.sort_order ASC, bp.id ASC
    ''', (g.campaign_id,)).fetchall()
    valid = {r['id'] for r in parts}
    pins = db.execute(
        "SELECT id, character_id, participant_id FROM map_pins WHERE map_id = ? AND pin_type = 'character' ORDER BY id", (map_id,)
    ).fetchall()
    pinned = set()
    unplaced = []
    for pin in pins:
        if pin['participant_id'] is None:
            unplaced.append(pin)
        elif pin['participant_id'] in valid:
            pinned.add(pin['participant_id'])
        else:                                                   # its battle row is gone: removed from the battle
            db.execute('DELETE FROM map_pins WHERE id = ?', (pin['id'],))
    max_order = db.execute('SELECT COALESCE(MAX(sort_order), -1) AS m FROM battle_participants').fetchone()['m']
    for pin in unplaced:
        char = db.execute('SELECT max_hp, name, is_npc FROM characters WHERE id = ? AND campaign_id = ?', (pin['character_id'], g.campaign_id)).fetchone()
        if char is None:
            continue
        pid = next((r['id'] for r in parts if r['character_id'] == pin['character_id'] and r['id'] not in pinned), None)
        created = pid is None
        if created:
            max_order += 1
            pid = db.execute(
                'INSERT INTO battle_participants (character_id, current_hp, initiative, is_dead, sort_order) VALUES (?, ?, 0, 0, ?)',
                (pin['character_id'], char['max_hp'], max_order)).lastrowid
        won = db.execute(
            'UPDATE map_pins SET participant_id = ? WHERE id = ? AND participant_id IS NULL '
            'AND NOT EXISTS (SELECT 1 FROM map_pins x WHERE x.map_id = ? AND x.participant_id = ?)',
            (pid, pin['id'], map_id, pid)).rowcount == 1
        if won:
            pinned.add(pid)
            if created:
                log_battle(db, f"{char['name']} joined the battle", None, 'joined', npc=bool(char['is_npc']))      # put on the map: now on the field too
        elif created:                                           # another request got there first: don't leave a stray row
            db.execute('DELETE FROM battle_participants WHERE id = ?', (pid,))
    missing = [r for r in parts if r['id'] not in pinned]
    for i, r in enumerate(missing):
        offset = 40 + (i * 30) % 200
        db.execute(
            "INSERT INTO map_pins (map_id, pin_type, character_id, participant_id, x, y, scale) VALUES (?, 'character', ?, ?, ?, ?, 1.0) ON CONFLICT DO NOTHING",
            (map_id, r['character_id'], r['id'], offset, offset))


def _map_pins_payload(db, map_id):
    m = db.execute('SELECT * FROM maps WHERE id = ? AND campaign_id = ?', (map_id, g.campaign_id)).fetchone()
    if m is None:
        return []

    if m['linked_to_battle']:
        _reconcile_map_battle(db, map_id)
        db.commit()

    rows = [dict(r) for r in db.execute('SELECT * FROM map_pins WHERE map_id = ?' + ('' if g.is_dm else ' AND dm_only = 0')
                                        + ' ORDER BY id', (map_id,)).fetchall()]
    # Everything a token shows comes from its character (and, if it has one, its battle row), fetched in two queries.
    char_ids = sorted({r['character_id'] for r in rows if r['pin_type'] == 'character' and r['character_id']})
    chars = {}
    if char_ids:
        marks = ','.join('?' * len(char_ids))
        for c in db.execute(f'''
            SELECT c.id AS character_id, c.name AS char_name, c.avatar_path, c.max_hp AS char_max_hp, c.is_npc,
                   c.is_temp_familiar, c.class_key, g.color AS group_color, c.created_by AS char_made_by,
                   c.player_id AS char_player_id
            FROM characters c LEFT JOIN groups g ON c.group_id = g.id
            WHERE c.campaign_id = ? AND c.id IN ({marks})
        ''', [g.campaign_id] + char_ids).fetchall():
            chars[c['character_id']] = dict(c)
    # Reuse the battle tracker's own duplicate-numbering ("Goblin #1", "Goblin #2") so linked tokens match the battle menu.
    battle_display_names = {r['id']: r['display_name'] for r in _battle_rows(db)}
    # Tokens with no battle row number their own duplicates, in the order they were placed.
    plain_total, plain_seen = {}, {}
    for r in rows:
        if r['pin_type'] == 'character' and r['participant_id'] is None:
            plain_total[r['character_id']] = plain_total.get(r['character_id'], 0) + 1
    result = []
    for p in rows:
        if p['pin_type'] == 'character':
            c = chars.get(p['character_id'])
            if c is None or npc_is_hidden(c['is_npc'], c['char_made_by']):
                continue
            p.update(c)
            p.pop('char_made_by', None)
            live = None
            if p['participant_id']:
                live = db.execute('SELECT current_hp, is_dead FROM battle_participants WHERE id = ?', (p['participant_id'],)).fetchone()
            if live is not None:
                p['current_hp'], p['is_dead'] = live['current_hp'], live['is_dead']
                p['char_name'] = battle_display_names.get(p['participant_id'], p['char_name'])
            else:
                p['current_hp'], p['is_dead'] = None, 0
                if plain_total.get(p['character_id'], 0) > 1:
                    plain_seen[p['character_id']] = plain_seen.get(p['character_id'], 0) + 1
                    p['char_name'] = f"{p['char_name']} #{plain_seen[p['character_id']]}"
            if not g.is_dm and p['is_npc']:
                p['hidden_stats'] = True
                p['current_hp'] = None
                p['char_max_hp'] = None
                p['class_key'] = None
        elif p['participant_id']:
            # A prop pin with a participant_id is a familiar wired into the battle tracker -- fetch its live
            # is_npc/HP too, so a familiar the DM has marked as an NPC gets the same PC/NPC-aware treatment.
            live = db.execute('''
                SELECT bp.current_hp, bp.is_dead, c.id AS character_id, c.name AS char_name, c.avatar_path,
                       c.max_hp AS char_max_hp, c.is_npc, c.is_temp_familiar, c.class_key, g.color AS group_color, c.created_by AS char_made_by
                FROM battle_participants bp
                JOIN characters c ON bp.character_id = c.id
                LEFT JOIN groups g ON c.group_id = g.id
                WHERE bp.id = ?
            ''', (p['participant_id'],)).fetchone()
            if live is None or npc_is_hidden(live['is_npc'], live['char_made_by']):
                continue
            p.update(dict(live))
            p.pop('char_made_by', None)
            p['char_name'] = battle_display_names.get(p['participant_id'], p['char_name'])
            if not g.is_dm and p['is_npc']:
                p['hidden_stats'] = True
                p['current_hp'] = None
                p['char_max_hp'] = None
                p['class_key'] = None
        result.append(p)
    owner_ids = sorted({p['owner_user_id'] for p in result if p.get('owner_user_id') is not None})
    owner_names = {}
    if owner_ids:
        marks = ','.join('?' * len(owner_ids))
        owner_names = {u['id']: u['username'] for u in db.execute(
            f'SELECT id, username FROM users WHERE id IN ({marks})', owner_ids).fetchall()}
    me = session.get('user_id')
    for p in result:                                        # the client greys out what this person may not move
        p['owner_name'] = owner_names.get(p.get('owner_user_id'))
        p['can_move'] = bool(g.is_dm or (not m['locked_for_players'] and me is not None
                                         and me in (p.get('owner_user_id'), p.get('char_player_id'))))
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
    state, the drawings and the pins.
    Serving them together means one serverless invocation and one database connection
    per refresh instead of three."""
    db = get_db()
    state = _map_state_payload(db, map_id)
    if state is None:
        db.close()
        abort(404)
    drawings = _map_drawings_payload(db, map_id)
    pins = _map_pins_payload(db, map_id)
    db.close()
    return jsonify({'state': state, 'drawings': drawings, 'pins': pins})


# Pins can be added by any Player while a map is unlocked, so everything they store must be a known value or plain
# bounded text: it later reaches other members' browsers (the DM's included) through the map and battle screens.
PIN_ICON_KEYS = frozenset(os.path.splitext(n)[0] for n in os.listdir(os.path.join(app.static_folder, 'icons', 'pins')) if n.endswith('.svg'))
PIN_NAME_MAX = 80


def _pin_name(value):
    """A pin / familiar name: None or bounded plain text. Raises ValueError for anything else."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('name')
    value = ''.join(ch for ch in value if ch.isprintable()).strip()
    if len(value) > PIN_NAME_MAX:
        raise ValueError('name')
    return value or None


def _pin_owned_by_me(db, map_id, pin_id):
    """Token ownership: the DM may touch any token; a Player only the ones the DM attached to them (or that they
    placed themselves).  Returns the pin row, or None when there is no such token on this map."""
    return db.execute('SELECT p.owner_user_id, c.player_id AS char_player_id FROM map_pins p LEFT JOIN characters c ON c.id = p.character_id '
                      'WHERE p.id = ? AND p.map_id = ?' + ('' if g.is_dm else ' AND p.dm_only = 0'),
                      (pin_id, map_id)).fetchone()


def _may_move_pin(pin_row):
    me = session.get('user_id')
    return bool(g.is_dm or (pin_row is not None and me is not None and me in (pin_row['owner_user_id'], pin_row['char_player_id'])))


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/pins', methods=['POST'])
@campaign_access_required
@map_edit_allowed
def api_map_pins_add(map_id):
    data = request.get_json(force=True)
    pin_type = data.get('pin_type', 'prop')
    icon_key = data.get('icon_key')
    character_id = None
    if pin_type == 'character':                             # only the DM puts characters on the map
        if not g.is_dm:
            abort(403)
        icon_key = None
        try:
            character_id = int(data.get('character_id'))
        except (TypeError, ValueError):
            return api_error('Unknown character.', 400)
    elif pin_type != 'prop':
        return api_error('Unknown pin type.', 400)
    if icon_key is not None and icon_key not in PIN_ICON_KEYS:
        return api_error('Unknown pin icon.', 400)
    try:
        custom_name = _pin_name(data.get('custom_name'))
    except ValueError:
        return api_error('Pin names must be plain text of at most %d characters.' % PIN_NAME_MAX, 400)
    x = float(data.get('x', 0))
    y = float(data.get('y', 0))
    scale = float(data.get('scale', 1.0))
    rotation = float(data.get('rotation', 0))
    locked = 1 if data.get('locked') else 0
    dm_only = 1 if (g.is_dm and data.get('dm_only')) else 0        # only the DM can hide a token
    db = get_db()
    participant_id = None
    m = db.execute('SELECT linked_to_battle FROM maps WHERE id = ?', (map_id,)).fetchone()
    linked = bool(m and m['linked_to_battle'])
    if pin_type == 'character':
        if db.execute('SELECT 1 FROM characters WHERE id = ? AND campaign_id = ? AND is_temp_familiar = 0',
                      (character_id, g.campaign_id)).fetchone() is None:
            db.close()
            return api_error('Character not found.', 404)
    elif linked:
        participant_id = _create_familiar_participant(db, icon_key, custom_name)
    cur = db.execute(
        "INSERT INTO map_pins (map_id, pin_type, icon_key, custom_name, x, y, scale, rotation, locked, participant_id, character_id, owner_user_id, dm_only) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        # A marker a Player places is theirs to move; everything the DM places starts out DM-only until assigned.
        (map_id, pin_type, icon_key, custom_name if pin_type == 'prop' else None, x, y, scale, rotation, locked, participant_id, character_id,
         None if g.is_dm else session['user_id'], dm_only)
    )
    new_id = cur.lastrowid
    if pin_type == 'character' and linked:
        _reconcile_map_battle(db, map_id)                   # the new token joins the battle straight away
        row = db.execute('SELECT participant_id FROM map_pins WHERE id = ?', (new_id,)).fetchone()
        participant_id = row['participant_id'] if row else None
    db.commit()
    db.close()
    return jsonify({'id': new_id, 'participant_id': participant_id})


@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/pins/<int:pin_id>/update', methods=['POST'])
@campaign_access_required
@map_edit_allowed
def api_map_pin_update(map_id, pin_id):
    data = request.get_json(force=True)
    db = get_db()
    if not g.is_dm and not _may_move_pin(_pin_owned_by_me(db, map_id, pin_id)):
        db.close()
        abort(403)                                          # a Player moves only the tokens assigned to them
    fields = []
    values = []
    if 'owner_user_id' in data:
        # Who a token is attached to is the DM's call alone, and only a member of this campaign can hold one.
        if not g.is_dm:
            db.close()
            abort(403)
        new_owner = data['owner_user_id']
        if new_owner in (None, ''):
            new_owner = None
        else:
            try:
                new_owner = int(new_owner)
            except (TypeError, ValueError):
                db.close()
                return api_error('Unknown member.', 400)
            if db.execute('SELECT 1 FROM campaign_members WHERE campaign_id = ? AND user_id = ?',
                          (g.campaign_id, new_owner)).fetchone() is None:
                db.close()
                return api_error('That person is not a member of this campaign.', 400)
        fields.append('owner_user_id = ?'); values.append(new_owner)
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
    if 'dm_only' in data:
        if not g.is_dm:
            db.close()
            abort(403)
        fields.append('dm_only = ?'); values.append(1 if data['dm_only'] else 0)
    if 'custom_name' in data:
        try:
            new_custom_name = _pin_name(data['custom_name'])
        except ValueError:
            db.close()
            return api_error('Pin names must be plain text of at most %d characters.' % PIN_NAME_MAX, 400)
        fields.append('custom_name = ?'); values.append(new_custom_name)
        # Keep a familiar's battle-tracker name in sync with its pin name.
        pin_row = db.execute('SELECT participant_id, icon_key FROM map_pins WHERE id = ? AND map_id = ?', (pin_id, map_id)).fetchone()
        if pin_row and pin_row['participant_id']:
            new_name = new_custom_name or (pin_row['icon_key'] or 'familiar').capitalize()
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
    pin = db.execute('SELECT p.participant_id, p.pin_type, p.owner_user_id, p.dm_only, c.player_id AS char_player_id FROM map_pins p '
                     'LEFT JOIN characters c ON c.id = p.character_id WHERE p.id = ? AND p.map_id = ?', (pin_id, map_id)).fetchone()
    if pin and not g.is_dm and (pin['dm_only'] or not _may_move_pin(pin)):
        db.close()
        abort(403)
    if pin and pin['pin_type'] == 'character':
        # Taking a character off a linked map takes them out of the battle too, so it is the DM's call alone.
        if not g.is_dm:
            db.close()
            abort(403)
        m = db.execute('SELECT linked_to_battle FROM maps WHERE id = ?', (map_id,)).fetchone()
        if pin['participant_id'] and m and m['linked_to_battle']:
            _remove_participant(db, pin['participant_id'])
    elif pin and pin['participant_id']:
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
