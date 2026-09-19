import os
import re
import uuid
import io
import json
import zipfile
from datetime import datetime
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    send_from_directory, jsonify, flash, send_file, g, abort, session
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image
from database import get_db, init_db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.environ.get(
    'UPLOAD_DIR',
    os.path.join(os.environ.get('DND_RUNTIME_DIR', '/tmp/dnd-ledger'), 'uploads')
    if os.environ.get('VERCEL') else os.path.join(BASE_DIR, 'uploads')
)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
OPTIMIZE_THRESHOLD_BYTES = 1 * 1024 * 1024  # 1MB
OPTIMIZE_MAX_DIMENSION = 2000  # px, on the longest edge

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dnd-campaign-manager-dev-key')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB uploads

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


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ---------------- AUTH (Phase F) ----------------
# Full accounts: anyone can self-register, and every route requires a
# logged-in session except the handful that must stay reachable to get one.

PUBLIC_ENDPOINTS = {'login', 'register', 'static', 'uploaded_file'}


@app.before_request
def require_login():
    if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS:
        return
    if not session.get('user_id'):
        return redirect(url_for('login', next=request.path))


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


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        error = None
        if not username or not password:
            error = 'Username and password are required.'
        elif password != confirm:
            error = 'Passwords do not match.'
        elif len(password) < 6:
            error = 'Password must be at least 6 characters.'

        db = get_db()
        if error is None and db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone():
            error = 'That username is already taken.'
        if error:
            db.close()
            return render_template('register.html', error=error, username=username)

        is_first_user = db.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n'] == 0
        cur = db.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)',
                          (username, generate_password_hash(password)))
        user_id = cur.lastrowid
        if is_first_user:
            _claim_orphaned_campaigns(db, user_id)
        db.commit()
        db.close()

        session['user_id'] = user_id
        session['username'] = username
        return redirect(url_for('campaigns_list'))
    return render_template('register.html', error=None, username='')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        db.close()
        if user is None or not check_password_hash(user['password_hash'] or '', password):
            return render_template('login.html', error='Incorrect username or password.', username=username,
                                    next=request.form.get('next', ''))
        session['user_id'] = user['id']
        session['username'] = user['username']
        next_path = request.form.get('next') or request.args.get('next')
        if next_path and next_path.startswith('/') and not next_path.startswith('//'):
            return redirect(next_path)
        return redirect(url_for('campaigns_list'))
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
        avatar_path = save_upload(request.files.get('avatar'), 'avatars')
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
        avatar_path = save_upload(request.files.get('avatar'), 'avatars')
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
        ORDER BY cm.role DESC, u.username COLLATE NOCASE ASC
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
    # rows. The cascade below is done by hand rather than relying on the
    # DB's FK cascade: databases that went through the Phase A column
    # migration don't actually carry that constraint, since SQLite can't
    # attach an enforced FOREIGN KEY via ALTER TABLE ADD COLUMN -- only a
    # brand-new install's schema.sql-created tables have it.
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

    for p in avatar_paths + sheet_paths + map_paths:
        delete_upload(p)

    if session.get('last_campaign_id') == g.campaign_id:
        session.pop('last_campaign_id', None)

    return redirect(url_for('campaigns_list'))


# ---------------- CHARACTER NOTES (multi-box rich text) ----------------
# Notes are stored in characters.notes as a JSON array of small HTML fragments,
# one per note box, produced by the note editor in character_form.html.
# Characters saved before this feature existed have notes as a plain string,
# which is treated as a single legacy block wherever notes are read back.

_NOTE_ALLOWED_TAGS = {'b', 'strong', 'i', 'em', 'ul', 'li', 'br', 'div'}
_NOTE_TAG_RE = re.compile(r'<(/?)([a-zA-Z0-9]+)[^>]*>')
_NOTE_STRIP_TAGS_RE = re.compile(r'<[^>]+>')


def _sanitize_note_fragment(fragment):
    """Strip a note fragment down to a small safe-tag whitelist and drop all
    attributes, so a hand-crafted POST can't smuggle in scripts or styles."""
    def repl(m):
        closing, tag = m.group(1), m.group(2).lower()
        if tag not in _NOTE_ALLOWED_TAGS:
            return ''
        if tag == 'br':
            return '<br>'
        return f'</{tag}>' if closing else f'<{tag}>'
    return _NOTE_TAG_RE.sub(repl, fragment or '')


def sanitize_notes_payload(raw):
    """Sanitize the notes field submitted by the note editor before storing it."""
    if not raw:
        return ''
    try:
        blocks = json.loads(raw)
    except (ValueError, TypeError):
        return raw  # not JSON (shouldn't happen via the UI) - leave as-is
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
            return [b for b in blocks if isinstance(b, str) and b]
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


def optimize_image_if_needed(full_path, max_bytes=OPTIMIZE_THRESHOLD_BYTES, max_dimension=OPTIMIZE_MAX_DIMENSION):
    """Downscale and recompress an image in place if it's over max_bytes.
    Never raises: a failure here just leaves the original upload as-is."""
    try:
        if os.path.getsize(full_path) <= max_bytes:
            return
        img = Image.open(full_path)
        fmt = img.format

        if max(img.size) > max_dimension:
            img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

        if fmt == 'JPEG':
            img = img.convert('RGB')
            quality = 85
            while True:
                img.save(full_path, format='JPEG', quality=quality, optimize=True)
                if os.path.getsize(full_path) <= max_bytes or quality <= 40:
                    break
                quality -= 10
        elif fmt == 'WEBP':
            quality = 85
            while True:
                img.save(full_path, format='WEBP', quality=quality)
                if os.path.getsize(full_path) <= max_bytes or quality <= 40:
                    break
                quality -= 10
        elif fmt == 'PNG':
            img.save(full_path, format='PNG', optimize=True)
            if os.path.getsize(full_path) > max_bytes:
                # Drastic fallback: reduce to a 256-color adaptive palette
                palette_img = img.convert('RGBA').convert('P', palette=Image.ADAPTIVE, colors=256)
                palette_img.save(full_path, format='PNG', optimize=True)
        else:
            img.save(full_path, format=fmt)
    except Exception:
        pass


def enforce_map_image_max_dimension(full_path, max_dimension=OPTIMIZE_MAX_DIMENSION):
    """Map background images are always capped at max_dimension on their
    longest edge, regardless of file size (unlike the generic byte-size
    optimizer above). Returns the resulting (width, height)."""
    with Image.open(full_path) as img:
        width, height = img.size
        if max(width, height) <= max_dimension:
            return width, height
        fmt = img.format or 'PNG'
        img_copy = img.copy()
        img_copy.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
        if fmt == 'JPEG':
            img_copy = img_copy.convert('RGB')
            img_copy.save(full_path, format='JPEG', quality=90, optimize=True)
        else:
            img_copy.save(full_path, format=fmt)
        return img_copy.size


def save_upload(file_storage, subfolder):
    if not file_storage or file_storage.filename == '':
        return None
    if not allowed_file(file_storage.filename):
        return None
    ext = secure_filename(file_storage.filename).rsplit('.', 1)[1].lower()
    fname = f"{uuid.uuid4().hex}.{ext}"
    folder = os.path.join(UPLOAD_DIR, subfolder)
    os.makedirs(folder, exist_ok=True)
    full_path = os.path.join(folder, fname)
    file_storage.save(full_path)
    optimize_image_if_needed(full_path)
    return f"{subfolder}/{fname}"


def delete_upload(rel_path):
    if not rel_path:
        return
    full = os.path.join(UPLOAD_DIR, rel_path)
    if os.path.exists(full):
        try:
            os.remove(full)
        except OSError:
            pass


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


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
        ORDER BY c.name COLLATE NOCASE ASC
    ''', (g.campaign_id,)).fetchall()
    groups = db.execute('SELECT * FROM groups WHERE campaign_id = ? ORDER BY name COLLATE NOCASE ASC', (g.campaign_id,)).fetchall()
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
    groups = db.execute('SELECT * FROM groups WHERE campaign_id = ? ORDER BY name COLLATE NOCASE ASC', (g.campaign_id,)).fetchall()
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
    groups = db.execute('SELECT * FROM groups WHERE campaign_id = ? ORDER BY name COLLATE NOCASE ASC', (g.campaign_id,)).fetchall()
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
    group_id = form.get('group_id') or None

    avatar_file = request.files.get('avatar')
    avatar_path = save_upload(avatar_file, 'avatars')
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
    sheet_files = request.files.getlist('sheets')
    max_order = db.execute('SELECT COALESCE(MAX(sort_order), -1) AS m FROM character_sheets WHERE character_id = ?', (char_id,)).fetchone()['m']
    for i, sf in enumerate(sheet_files):
        path = save_upload(sf, 'sheets')
        if path:
            max_order += 1
            db.execute('INSERT INTO character_sheets (character_id, image_path, sort_order) VALUES (?, ?, ?)',
                       (char_id, path, max_order))

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
        FROM groups g WHERE g.campaign_id = ? ORDER BY g.name COLLATE NOCASE ASC
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


def _save_group(db, group_id):
    form = request.form
    name = form.get('name', '').strip() or 'Unnamed Group'
    bio = form.get('bio', '')
    color = form.get('color') or '#c9a24b'
    avatar_file = request.files.get('avatar')
    avatar_path = save_upload(avatar_file, 'avatars')
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
        ORDER BY c.name COLLATE NOCASE ASC
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
        ORDER BY c.name COLLATE NOCASE ASC
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
    row = db.execute('''
        SELECT bp.current_hp, c.max_hp FROM battle_participants bp
        JOIN characters c ON bp.character_id = c.id WHERE bp.id = ?
    ''', (pid,)).fetchone()
    if row is None:
        db.close()
        return jsonify({'error': 'not found'}), 404
    new_hp = min(row['max_hp'], row['current_hp'] + amount)
    is_dead = 0 if new_hp > 0 else db.execute('SELECT is_dead FROM battle_participants WHERE id=?', (pid,)).fetchone()['is_dead']
    db.execute('UPDATE battle_participants SET current_hp = ?, is_dead = ? WHERE id = ?', (new_hp, is_dead, pid))
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
    row = db.execute('SELECT current_hp, temp_hp FROM battle_participants WHERE id = ?', (pid,)).fetchone()
    if row is None:
        db.close()
        return jsonify({'error': 'not found'}), 404
    temp_hp = row['temp_hp'] or 0
    # Temporary HP absorbs damage first (standard D&D rule); any leftover spills onto real HP
    absorbed = min(temp_hp, amount)
    new_temp = temp_hp - absorbed
    remaining = amount - absorbed
    new_hp = max(0, row['current_hp'] - remaining)
    db.execute('UPDATE battle_participants SET current_hp = ?, temp_hp = ? WHERE id = ?', (new_hp, new_temp, pid))
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
    row = db.execute('''
        SELECT bp.is_dead, c.max_hp FROM battle_participants bp
        JOIN characters c ON bp.character_id = c.id WHERE bp.id = ?
    ''', (pid,)).fetchone()
    if row is None:
        db.close()
        return jsonify({'error': 'not found'}), 404
    value = max(0, min(row['max_hp'], int(data.get('value', 0))))
    is_dead = 0 if value > 0 else row['is_dead']
    db.execute('UPDATE battle_participants SET current_hp = ?, is_dead = ? WHERE id = ?', (value, is_dead, pid))
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
        for rel_path in referenced_paths:
            full = os.path.join(UPLOAD_DIR, rel_path)
            if os.path.exists(full) and rel_path not in seen:
                zf.write(full, arcname=f'images/{rel_path}')
                seen.add(rel_path)

    buf.seek(0)
    return send_file(buf, mimetype='application/zip', as_attachment=True,
                      download_name=_dated_filename(download_name))


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
    file = request.files.get('import_file')
    if not file or file.filename == '':
        return redirect(request.referrer or url_for('characters_list'))

    try:
        zf = zipfile.ZipFile(io.BytesIO(file.read()))
        manifest = json.loads(zf.read('manifest.json'))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError):
        dest = 'groups_list' if 'groups' in (request.referrer or '') else 'characters_list'
        return redirect(url_for(dest, import_error=1))

    def extract_image(rel_path, subfolder):
        """Pull an image out of the zip and save it under a fresh filename."""
        if not rel_path:
            return None
        arcname = f'images/{rel_path}'
        if arcname not in zf.namelist():
            return None
        ext = rel_path.rsplit('.', 1)[-1].lower() if '.' in rel_path else 'png'
        if ext not in ALLOWED_EXTENSIONS:
            ext = 'png'
        new_name = f"{uuid.uuid4().hex}.{ext}"
        folder = os.path.join(UPLOAD_DIR, subfolder)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, new_name), 'wb') as out:
            out.write(zf.read(arcname))
        return f"{subfolder}/{new_name}"

    db = get_db()

    # Groups: dedupe by name (case-insensitive) so re-importing doesn't duplicate factions
    # -- scoped to this campaign only, so importing into campaign B never
    # matches a same-named group that only exists in campaign A.
    existing_groups = {
        row['name'].lower(): row['id']
        for row in db.execute('SELECT id, name FROM groups WHERE campaign_id = ?', (g.campaign_id,))
    }
    group_name_to_id = dict(existing_groups)

    for grp in manifest.get('groups', []):
        key = (grp.get('name') or '').lower()
        if key in group_name_to_id:
            continue
        avatar_path = extract_image(grp.get('avatar_file'), 'avatars')
        cur = db.execute('INSERT INTO groups (name, avatar_path, bio, color, campaign_id) VALUES (?, ?, ?, ?, ?)',
                          (grp.get('name') or 'Unnamed Group', avatar_path, grp.get('bio', ''), grp.get('color', '#c9a24b'), g.campaign_id))
        group_name_to_id[key] = cur.lastrowid

    # Characters: always inserted as new records (never merged/overwritten)
    for c in manifest.get('characters', []):
        group_id = group_name_to_id.get((c.get('group_name') or '').lower())
        avatar_path = extract_image(c.get('avatar_file'), 'avatars')
        cur = db.execute('''
            INSERT INTO characters
            (name, is_npc, level, max_hp, str_score, dex_score, con_score, int_score,
             wis_score, cha_score, armor_class, avatar_path, notes, group_id, campaign_id, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (c.get('name', 'Unnamed'), c.get('is_npc', 0), c.get('level', 1), c.get('max_hp', 10),
              c.get('str_score', 10), c.get('dex_score', 10), c.get('con_score', 10), c.get('int_score', 10),
              c.get('wis_score', 10), c.get('cha_score', 10), c.get('armor_class', 10),
              avatar_path, c.get('notes', ''), group_id, g.campaign_id, session['user_id']))
        new_char_id = cur.lastrowid

        for i, sheet_rel_path in enumerate(c.get('sheet_files', [])):
            sheet_path = extract_image(sheet_rel_path, 'sheets')
            if sheet_path:
                db.execute('INSERT INTO character_sheets (character_id, image_path, sort_order) VALUES (?, ?, ?)',
                           (new_char_id, sheet_path, i))

    db.commit()
    db.close()

    dest = 'groups_list' if 'groups' in (request.referrer or '') else 'characters_list'
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

    if image_file and image_file.filename != '':
        image_path = save_upload(image_file, 'maps')
        if not image_path:
            return redirect(url_for('maps_list'))
        full_path = os.path.join(UPLOAD_DIR, image_path)
        try:
            width, height = enforce_map_image_max_dimension(full_path)
        except Exception:
            width, height = 1500, 1000
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
    row = db.execute('''
        SELECT grid_size, grid_color, grid_visible, grid_offset_x, grid_offset_y,
               snap_to_grid, grid_setup_done, locked_for_players, linked_to_battle
        FROM maps WHERE id = ? AND campaign_id = ?
    ''', (map_id, g.campaign_id)).fetchone()
    db.close()
    if row is None:
        abort(404)
    return jsonify(dict(row))


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


def _generate_blank_canvas(width, height):
    """Create a plain white PNG in the maps upload folder and return its relative path."""
    img = Image.new('RGB', (width, height), color=(255, 255, 255))
    fname = f"{uuid.uuid4().hex}.png"
    folder = os.path.join(UPLOAD_DIR, 'maps')
    os.makedirs(folder, exist_ok=True)
    img.save(os.path.join(folder, fname), format='PNG')
    return f"maps/{fname}"


# ---- Drawings ----

@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/drawings')
@campaign_access_required
def api_map_drawings_list(map_id):
    db = get_db()
    rows = db.execute('SELECT * FROM map_drawings WHERE map_id = ? ORDER BY sort_order, id', (map_id,)).fetchall()
    db.close()
    result = []
    for r in rows:
        d = dict(r)
        d['data'] = json.loads(d['data'])
        result.append(d)
    return jsonify(result)


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

@app.route('/campaigns/<int:campaign_id>/api/maps/<int:map_id>/pins')
@campaign_access_required
def api_map_pins_list(map_id):
    db = get_db()
    m = db.execute('SELECT * FROM maps WHERE id = ? AND campaign_id = ?', (map_id, g.campaign_id)).fetchone()
    if m is None:
        db.close()
        return jsonify([])

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
    db.close()
    return jsonify(result)


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
