import os
import uuid
import io
import json
import zipfile
from flask import (
    Flask, render_template, request, redirect, url_for,
    send_from_directory, jsonify, flash, send_file
)
from werkzeug.utils import secure_filename
from PIL import Image
from database import get_db, init_db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
OPTIMIZE_THRESHOLD_BYTES = 1 * 1024 * 1024  # 1MB
OPTIMIZE_MAX_DIMENSION = 2000  # px, on the longest edge

app = Flask(__name__)
app.secret_key = 'dnd-campaign-manager-dev-key'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB uploads


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


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
    return redirect(url_for('characters_list'))


# ---------------- CHARACTERS ----------------

@app.route('/characters')
def characters_list():
    db = get_db()
    characters = db.execute('''
        SELECT c.*, g.name AS group_name, g.color AS group_color
        FROM characters c
        LEFT JOIN groups g ON c.group_id = g.id
        ORDER BY c.name COLLATE NOCASE ASC
    ''').fetchall()
    groups = db.execute('SELECT * FROM groups ORDER BY name COLLATE NOCASE ASC').fetchall()
    db.close()
    return render_template('characters_list.html', characters=characters, groups=groups)


@app.route('/characters/new', methods=['GET', 'POST'])
def character_new():
    db = get_db()
    if request.method == 'POST':
        new_id = _save_character(db, None)
        db.close()
        if request.form.get('from_battle') == '1':
            return redirect(url_for('battle_view', added=new_id))
        return redirect(url_for('characters_list'))
    groups = db.execute('SELECT * FROM groups ORDER BY name COLLATE NOCASE ASC').fetchall()
    db.close()
    from_battle = request.args.get('from') == 'battle'
    return render_template('character_form.html', character=None, sheets=[], groups=groups, from_battle=from_battle)


@app.route('/characters/<int:char_id>/edit', methods=['GET', 'POST'])
def character_edit(char_id):
    db = get_db()
    if request.method == 'POST':
        _save_character(db, char_id)
        db.close()
        return redirect(url_for('characters_list'))
    character = db.execute('SELECT * FROM characters WHERE id = ?', (char_id,)).fetchone()
    sheets = db.execute('SELECT * FROM character_sheets WHERE character_id = ? ORDER BY sort_order', (char_id,)).fetchall()
    groups = db.execute('SELECT * FROM groups ORDER BY name COLLATE NOCASE ASC').fetchall()
    db.close()
    if character is None:
        return redirect(url_for('characters_list'))
    return render_template('character_form.html', character=character, sheets=sheets, groups=groups, from_battle=False)


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
    notes = form.get('notes', '')
    group_id = form.get('group_id') or None

    avatar_file = request.files.get('avatar')
    avatar_path = save_upload(avatar_file, 'avatars')
    remove_avatar = form.get('remove_avatar') == '1'

    if char_id is None:
        cur = db.execute('''
            INSERT INTO characters
            (name, is_npc, level, max_hp, str_score, dex_score, con_score, int_score,
             wis_score, cha_score, armor_class, avatar_path, notes, group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, is_npc, level, max_hp, str_score, dex_score, con_score, int_score,
              wis_score, cha_score, armor_class, avatar_path, notes, group_id))
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


@app.route('/characters/<int:char_id>/delete', methods=['POST'])
def character_delete(char_id):
    db = get_db()
    char = db.execute('SELECT avatar_path FROM characters WHERE id = ?', (char_id,)).fetchone()
    sheets = db.execute('SELECT image_path FROM character_sheets WHERE character_id = ?', (char_id,)).fetchall()
    if char:
        delete_upload(char['avatar_path'])
    for s in sheets:
        delete_upload(s['image_path'])
    db.execute('DELETE FROM characters WHERE id = ?', (char_id,))
    db.commit()
    db.close()
    return redirect(url_for('characters_list'))


@app.route('/characters/<int:char_id>/sheets/<int:sheet_id>/delete', methods=['POST'])
def sheet_delete(char_id, sheet_id):
    db = get_db()
    sheet = db.execute('SELECT image_path FROM character_sheets WHERE id = ?', (sheet_id,)).fetchone()
    if sheet:
        delete_upload(sheet['image_path'])
    db.execute('DELETE FROM character_sheets WHERE id = ?', (sheet_id,))
    db.commit()
    db.close()
    return redirect(url_for('character_edit', char_id=char_id))


# ---------------- GROUPS ----------------

@app.route('/groups')
def groups_list():
    db = get_db()
    groups = db.execute('''
        SELECT g.*, (SELECT COUNT(*) FROM characters c WHERE c.group_id = g.id) AS member_count
        FROM groups g ORDER BY g.name COLLATE NOCASE ASC
    ''').fetchall()
    db.close()
    return render_template('groups_list.html', groups=groups)


@app.route('/groups/new', methods=['GET', 'POST'])
def group_new():
    db = get_db()
    if request.method == 'POST':
        _save_group(db, None)
        db.close()
        return redirect(url_for('groups_list'))
    db.close()
    return render_template('group_form.html', group=None)


@app.route('/groups/<int:group_id>/edit', methods=['GET', 'POST'])
def group_edit(group_id):
    db = get_db()
    if request.method == 'POST':
        _save_group(db, group_id)
        db.close()
        return redirect(url_for('groups_list'))
    group = db.execute('SELECT * FROM groups WHERE id = ?', (group_id,)).fetchone()
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
        db.execute('INSERT INTO groups (name, avatar_path, bio, color) VALUES (?, ?, ?, ?)',
                   (name, avatar_path, bio, color))
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


@app.route('/groups/<int:group_id>/delete', methods=['POST'])
def group_delete(group_id):
    db = get_db()
    group = db.execute('SELECT avatar_path FROM groups WHERE id = ?', (group_id,)).fetchone()
    if group:
        delete_upload(group['avatar_path'])
    db.execute('DELETE FROM groups WHERE id = ?', (group_id,))
    db.commit()
    db.close()
    return redirect(url_for('groups_list'))


# ---------------- API (for future battle screen use) ----------------

@app.route('/api/characters')
def api_characters():
    db = get_db()
    rows = db.execute('''
        SELECT c.*, g.name AS group_name, g.color AS group_color
        FROM characters c LEFT JOIN groups g ON c.group_id = g.id
        ORDER BY c.name COLLATE NOCASE ASC
    ''').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/characters/<int:char_id>/detail')
def api_character_detail(char_id):
    db = get_db()
    character = db.execute('''
        SELECT c.*, g.name AS group_name, g.color AS group_color
        FROM characters c LEFT JOIN groups g ON c.group_id = g.id
        WHERE c.id = ?
    ''', (char_id,)).fetchone()
    if character is None:
        db.close()
        return jsonify({'error': 'not found'}), 404
    sheets = db.execute('SELECT * FROM character_sheets WHERE character_id = ? ORDER BY sort_order', (char_id,)).fetchall()
    db.close()
    data = dict(character)
    data['sheets'] = [dict(s) for s in sheets]
    return jsonify(data)


# ---------------- BATTLE ----------------

@app.route('/battle')
def battle_view():
    db = get_db()
    characters = db.execute('''
        SELECT c.*, g.name AS group_name, g.color AS group_color
        FROM characters c LEFT JOIN groups g ON c.group_id = g.id
        ORDER BY c.name COLLATE NOCASE ASC
    ''').fetchall()
    db.close()
    return render_template('battle.html', characters=[dict(c) for c in characters],
                            added_id=request.args.get('added'))


def _battle_rows(db):
    rows = db.execute('''
        SELECT bp.*, c.name AS char_name, c.avatar_path, c.is_npc, c.max_hp AS char_max_hp,
               c.armor_class AS base_ac, g.id AS gid, g.name AS group_name, g.color AS group_color
        FROM battle_participants bp
        JOIN characters c ON bp.character_id = c.id
        LEFT JOIN groups g ON c.group_id = g.id
        ORDER BY bp.sort_order ASC, bp.id ASC
    ''').fetchall()
    rows = [dict(r) for r in rows]

    for r in rows:
        r['effective_ac'] = r['ac_override'] if r['ac_override'] is not None else r['base_ac']

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
    return rows


@app.route('/api/battle')
def api_battle_list():
    db = get_db()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/api/battle/add', methods=['POST'])
def api_battle_add():
    data = request.get_json(force=True)
    character_id = data.get('character_id')
    db = get_db()
    char = db.execute('SELECT max_hp FROM characters WHERE id = ?', (character_id,)).fetchone()
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


@app.route('/api/battle/add-group', methods=['POST'])
def api_battle_add_group():
    data = request.get_json(force=True)
    group_id = data.get('group_id')
    db = get_db()
    members = db.execute('SELECT id, max_hp FROM characters WHERE group_id = ?', (group_id,)).fetchall()
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


@app.route('/api/battle/<int:pid>/heal', methods=['POST'])
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


@app.route('/api/battle/<int:pid>/damage', methods=['POST'])
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


@app.route('/api/battle/<int:pid>/temphp', methods=['POST'])
def api_battle_temphp(pid):
    data = request.get_json(force=True)
    value = max(0, int(data.get('value', 0)))
    db = get_db()
    db.execute('UPDATE battle_participants SET temp_hp = ? WHERE id = ?', (value, pid))
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/api/battle/<int:pid>/set_hp', methods=['POST'])
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


@app.route('/api/battle/<int:pid>/initiative', methods=['POST'])
def api_battle_initiative(pid):
    data = request.get_json(force=True)
    value = int(data.get('value', 0))
    db = get_db()
    db.execute('UPDATE battle_participants SET initiative = ? WHERE id = ?', (value, pid))
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/api/battle/<int:pid>/ac', methods=['POST'])
def api_battle_ac(pid):
    data = request.get_json(force=True)
    value = int(data.get('value', 0))
    db = get_db()
    db.execute('UPDATE battle_participants SET ac_override = ? WHERE id = ?', (value, pid))
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/api/battle/<int:pid>/die', methods=['POST'])
def api_battle_die(pid):
    db = get_db()
    db.execute('UPDATE battle_participants SET is_dead = 1 WHERE id = ?', (pid,))
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/api/battle/<int:pid>/revive', methods=['POST'])
def api_battle_revive(pid):
    db = get_db()
    db.execute('UPDATE battle_participants SET is_dead = 0 WHERE id = ?', (pid,))
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/api/battle/<int:pid>/remove', methods=['POST'])
def api_battle_remove(pid):
    db = get_db()
    db.execute('DELETE FROM battle_participants WHERE id = ?', (pid,))
    db.commit()
    rows = _battle_rows(db)
    db.close()
    return jsonify(rows)


@app.route('/api/battle/clear', methods=['POST'])
def api_battle_clear():
    db = get_db()
    db.execute('DELETE FROM battle_participants')
    db.commit()
    db.close()
    return jsonify([])


@app.route('/dice')
def dice_view():
    return render_template('dice.html')


# ---------------- EXPORT / IMPORT ----------------

def _build_export_zip(group_ids=None, character_ids=None, download_name='campaign_export.zip'):
    """Build a zip export. group_ids/character_ids of None means 'all';
    an explicit list (including empty) restricts to just those rows.
    Groups referenced by an exported character are always pulled in too,
    so relinking on import still works even for a single-character export."""
    db = get_db()

    if group_ids is None:
        groups = db.execute('SELECT * FROM groups ORDER BY id').fetchall()
    elif group_ids:
        placeholders = ','.join('?' * len(group_ids))
        groups = db.execute(f'SELECT * FROM groups WHERE id IN ({placeholders}) ORDER BY id', group_ids).fetchall()
    else:
        groups = []

    if character_ids is None:
        characters = db.execute('SELECT * FROM characters ORDER BY id').fetchall()
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
        for g in manifest['groups']:
            if g['avatar_file']:
                referenced_paths.add(g['avatar_file'])
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
                      download_name=download_name)


def _slugify(name):
    slug = ''.join(ch.lower() if ch.isalnum() else '-' for ch in name).strip('-')
    while '--' in slug:
        slug = slug.replace('--', '-')
    return slug or 'export'


@app.route('/export/data')
def export_data():
    return _build_export_zip(download_name='campaign_export.zip')


@app.route('/characters/<int:char_id>/export')
def character_export(char_id):
    db = get_db()
    row = db.execute('SELECT name FROM characters WHERE id = ?', (char_id,)).fetchone()
    db.close()
    if row is None:
        return redirect(url_for('characters_list'))
    return _build_export_zip(character_ids=[char_id], download_name=f'{_slugify(row["name"])}.zip')


@app.route('/groups/<int:group_id>/export')
def group_export(group_id):
    db = get_db()
    row = db.execute('SELECT name FROM groups WHERE id = ?', (group_id,)).fetchone()
    db.close()
    if row is None:
        return redirect(url_for('groups_list'))
    return _build_export_zip(group_ids=[group_id], character_ids=[], download_name=f'{_slugify(row["name"])}.zip')


@app.route('/import/data', methods=['POST'])
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
    existing_groups = {row['name'].lower(): row['id'] for row in db.execute('SELECT id, name FROM groups')}
    group_name_to_id = dict(existing_groups)

    for g in manifest.get('groups', []):
        key = (g.get('name') or '').lower()
        if key in group_name_to_id:
            continue
        avatar_path = extract_image(g.get('avatar_file'), 'avatars')
        cur = db.execute('INSERT INTO groups (name, avatar_path, bio, color) VALUES (?, ?, ?, ?)',
                          (g.get('name') or 'Unnamed Group', avatar_path, g.get('bio', ''), g.get('color', '#c9a24b')))
        group_name_to_id[key] = cur.lastrowid

    # Characters: always inserted as new records (never merged/overwritten)
    for c in manifest.get('characters', []):
        group_id = group_name_to_id.get((c.get('group_name') or '').lower())
        avatar_path = extract_image(c.get('avatar_file'), 'avatars')
        cur = db.execute('''
            INSERT INTO characters
            (name, is_npc, level, max_hp, str_score, dex_score, con_score, int_score,
             wis_score, cha_score, armor_class, avatar_path, notes, group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (c.get('name', 'Unnamed'), c.get('is_npc', 0), c.get('level', 1), c.get('max_hp', 10),
              c.get('str_score', 10), c.get('dex_score', 10), c.get('con_score', 10), c.get('int_score', 10),
              c.get('wis_score', 10), c.get('cha_score', 10), c.get('armor_class', 10),
              avatar_path, c.get('notes', ''), group_id))
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

@app.route('/maps')
def maps_list():
    db = get_db()
    maps = db.execute('SELECT * FROM maps ORDER BY created_at DESC').fetchall()
    db.close()
    return render_template('maps_list.html', maps=[dict(m) for m in maps])


@app.route('/maps/new', methods=['POST'])
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
        width = max(200, min(6000, int(request.form.get('blank_width', 1500) or 1500)))
        height = max(200, min(6000, int(request.form.get('blank_height', 1000) or 1000)))
        image_path = _generate_blank_canvas(width, height)

    db = get_db()
    cur = db.execute(
        'INSERT INTO maps (name, image_path, image_width, image_height) VALUES (?, ?, ?, ?)',
        (name, image_path, width, height)
    )
    map_id = cur.lastrowid
    db.commit()
    db.close()
    return redirect(url_for('map_editor', map_id=map_id))


@app.route('/maps/<int:map_id>')
def map_editor(map_id):
    db = get_db()
    m = db.execute('SELECT * FROM maps WHERE id = ?', (map_id,)).fetchone()
    db.close()
    if m is None:
        return redirect(url_for('maps_list'))
    return render_template('map_editor.html', map=dict(m))


@app.route('/maps/<int:map_id>/delete', methods=['POST'])
def map_delete(map_id):
    db = get_db()
    m = db.execute('SELECT image_path FROM maps WHERE id = ?', (map_id,)).fetchone()
    if m:
        delete_upload(m['image_path'])
    db.execute('DELETE FROM maps WHERE id = ?', (map_id,))
    db.commit()
    db.close()
    return redirect(url_for('maps_list'))


@app.route('/api/maps/<int:map_id>/settings', methods=['POST'])
def api_map_settings(map_id):
    data = request.get_json(force=True)
    db = get_db()
    fields = []
    values = []
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
    if 'linked_to_battle' in data:
        fields.append('linked_to_battle = ?'); values.append(1 if data['linked_to_battle'] else 0)
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

@app.route('/api/maps/<int:map_id>/drawings')
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


@app.route('/api/maps/<int:map_id>/drawings', methods=['POST'])
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
    if kind not in ('line', 'rect', 'oval', 'pen'):
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


@app.route('/api/maps/<int:map_id>/drawings/<int:drawing_id>/update', methods=['POST'])
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


@app.route('/api/maps/<int:map_id>/drawings/<int:drawing_id>/delete', methods=['POST'])
def api_map_drawing_delete(map_id, drawing_id):
    db = get_db()
    db.execute('DELETE FROM map_drawings WHERE id = ? AND map_id = ?', (drawing_id, map_id))
    db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/api/maps/<int:map_id>/drawings/clear', methods=['POST'])
def api_map_drawings_clear(map_id):
    db = get_db()
    db.execute('DELETE FROM map_drawings WHERE map_id = ?', (map_id,))
    db.commit()
    db.close()
    return jsonify({'ok': True})


# ---- Pins ----

@app.route('/api/maps/<int:map_id>/pins')
def api_map_pins_list(map_id):
    db = get_db()
    m = db.execute('SELECT * FROM maps WHERE id = ?', (map_id,)).fetchone()
    if m is None:
        db.close()
        return jsonify([])

    if m['linked_to_battle']:
        # Auto-place a pin for any battle participant that doesn't have one yet
        participants = db.execute('SELECT id FROM battle_participants').fetchall()
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
    result = []
    for r in rows:
        p = dict(r)
        if p['pin_type'] == 'character' and p['participant_id']:
            live = db.execute('''
                SELECT bp.current_hp, bp.is_dead, c.id AS character_id, c.name AS char_name, c.avatar_path,
                       c.max_hp AS char_max_hp, g.color AS group_color
                FROM battle_participants bp
                JOIN characters c ON bp.character_id = c.id
                LEFT JOIN groups g ON c.group_id = g.id
                WHERE bp.id = ?
            ''', (p['participant_id'],)).fetchone()
            if live is None:
                continue
            p.update(dict(live))
        result.append(p)
    db.close()
    return jsonify(result)


@app.route('/api/maps/<int:map_id>/pins', methods=['POST'])
def api_map_pins_add(map_id):
    data = request.get_json(force=True)
    pin_type = data.get('pin_type', 'prop')
    icon_key = data.get('icon_key')
    x = float(data.get('x', 0))
    y = float(data.get('y', 0))
    scale = float(data.get('scale', 1.0))
    rotation = float(data.get('rotation', 0))
    locked = 1 if data.get('locked') else 0
    db = get_db()
    cur = db.execute(
        "INSERT INTO map_pins (map_id, pin_type, icon_key, x, y, scale, rotation, locked) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (map_id, pin_type, icon_key, x, y, scale, rotation, locked)
    )
    db.commit()
    new_id = cur.lastrowid
    db.close()
    return jsonify({'id': new_id})


@app.route('/api/maps/<int:map_id>/pins/<int:pin_id>/update', methods=['POST'])
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
    if fields:
        values.append(pin_id)
        values.append(map_id)
        db.execute(f'UPDATE map_pins SET {", ".join(fields)} WHERE id = ? AND map_id = ?', values)
        db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/api/maps/<int:map_id>/pins/<int:pin_id>/delete', methods=['POST'])
def api_map_pin_delete(map_id, pin_id):
    db = get_db()
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
