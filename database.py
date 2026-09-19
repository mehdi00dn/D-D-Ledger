import os
import sqlite3
import shutil
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Vercel instances have an ephemeral writable filesystem.  Keep the bundled
# SQLite database as a read-only seed and work on a writable copy in /tmp.
# For production, set DATABASE_PATH to a persistent database and do not use
# SQLite on Vercel.
if os.environ.get('VERCEL'):
    RUNTIME_DIR = os.environ.get('DND_RUNTIME_DIR', '/tmp/dnd-ledger')
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    DB_PATH = os.environ.get('DATABASE_PATH', os.path.join(RUNTIME_DIR, 'dnd.db'))
else:
    RUNTIME_DIR = BASE_DIR
    DB_PATH = os.environ.get('DATABASE_PATH', os.path.join(BASE_DIR, 'dnd.db'))

SCHEMA_PATH = os.path.join(BASE_DIR, 'schema.sql')


def _ensure_runtime_db():
    """Create the writable runtime DB from the bundled DB on Vercel."""
    if not os.environ.get('VERCEL'):
        return
    if os.path.exists(DB_PATH):
        return

    seed_path = os.path.join(BASE_DIR, 'dnd.db')
    if os.path.exists(seed_path):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        shutil.copy2(seed_path, DB_PATH)


def get_db():
    _ensure_runtime_db()
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    conn = get_db()
    with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
        conn.executescript(f.read())
    _migrate(conn)
    conn.commit()
    conn.close()


def _migrate(conn):
    """Add columns to existing databases that predate them."""
    existing_cols = {row['name'] for row in conn.execute('PRAGMA table_info(battle_participants)')}
    if 'temp_hp' not in existing_cols:
        conn.execute('ALTER TABLE battle_participants ADD COLUMN temp_hp INTEGER DEFAULT 0')

    char_cols = {row['name'] for row in conn.execute('PRAGMA table_info(characters)')}
    for col, ddl in (('is_temp_familiar', 'INTEGER DEFAULT 0'), ('familiar_icon_key', 'TEXT'),
                     ('created_by', 'INTEGER')):
        if col not in char_cols:
            conn.execute(f'ALTER TABLE characters ADD COLUMN {col} {ddl}')
            if col == 'created_by':
                conn.execute('''
                    UPDATE characters SET created_by = (
                        SELECT cm.user_id FROM campaign_members cm
                        WHERE cm.campaign_id = characters.campaign_id AND cm.role = 'owner'
                        LIMIT 1
                    ) WHERE created_by IS NULL
                ''')

    drawing_cols = {row['name'] for row in conn.execute('PRAGMA table_info(map_drawings)')}
    for col, ddl in (('cx', 'REAL DEFAULT 0'), ('cy', 'REAL DEFAULT 0'),
                     ('w', 'REAL DEFAULT 0'), ('h', 'REAL DEFAULT 0'),
                     ('rotation', 'REAL DEFAULT 0'), ('locked', 'INTEGER DEFAULT 0')):
        if col not in drawing_cols:
            conn.execute(f'ALTER TABLE map_drawings ADD COLUMN {col} {ddl}')

    pin_cols = {row['name'] for row in conn.execute('PRAGMA table_info(map_pins)')}
    for col, ddl in (('rotation', 'REAL DEFAULT 0'), ('locked', 'INTEGER DEFAULT 0'), ('custom_name', 'TEXT')):
        if col not in pin_cols:
            conn.execute(f'ALTER TABLE map_pins ADD COLUMN {col} {ddl}')

    map_cols = {row['name'] for row in conn.execute('PRAGMA table_info(maps)')}
    for col, ddl in (('grid_color', "TEXT DEFAULT 'gray'"), ('grid_visible', 'INTEGER DEFAULT 1'),
                     ('grid_setup_done', 'INTEGER DEFAULT 0'), ('snap_to_grid', 'INTEGER DEFAULT 0'),
                     ('locked_for_players', 'INTEGER DEFAULT 0')):
        if col not in map_cols:
            conn.execute(f'ALTER TABLE maps ADD COLUMN {col} {ddl}')

    campaign_cols = {row['name'] for row in conn.execute('PRAGMA table_info(campaigns)')}
    if 'avatar_path' not in campaign_cols:
        conn.execute('ALTER TABLE campaigns ADD COLUMN avatar_path TEXT')
    for col, ddl in (('setting', 'TEXT'), ('status', "TEXT DEFAULT 'active'"), ('access_mode', "TEXT DEFAULT 'private'")):
        if col not in campaign_cols:
            conn.execute(f'ALTER TABLE campaigns ADD COLUMN {col} {ddl}')

    member_cols = {row['name'] for row in conn.execute('PRAGMA table_info(campaign_members)')}
    if 'status' not in member_cols:
        conn.execute("ALTER TABLE campaign_members ADD COLUMN status TEXT DEFAULT 'player'")
        conn.execute("UPDATE campaign_members SET status = 'dm' WHERE role = 'owner'")

    _migrate_campaigns(conn)

    if conn.execute('PRAGMA user_version').fetchone()[0] < 1:
        conn.execute('UPDATE characters SET is_npc = 0 WHERE is_temp_familiar = 1 AND is_npc = 1')
        conn.execute('PRAGMA user_version = 1')


def _migrate_campaigns(conn):
    char_cols = {row['name'] for row in conn.execute('PRAGMA table_info(characters)')}
    needs_migration = 'campaign_id' not in char_cols
    if needs_migration:
        _backup_db()

    for table in ('groups', 'characters', 'maps'):
        cols = {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}
        if 'campaign_id' not in cols:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN campaign_id INTEGER')

    conn.execute('CREATE INDEX IF NOT EXISTS idx_groups_campaign ON groups(campaign_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_characters_campaign ON characters(campaign_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_maps_campaign ON maps(campaign_id)')

    unassigned = conn.execute(
        'SELECT COUNT(*) AS n FROM ('
        '  SELECT campaign_id FROM groups WHERE campaign_id IS NULL'
        '  UNION ALL SELECT campaign_id FROM characters WHERE campaign_id IS NULL'
        '  UNION ALL SELECT campaign_id FROM maps WHERE campaign_id IS NULL'
        ')'
    ).fetchone()['n']
    if unassigned == 0:
        return

    existing = conn.execute('SELECT id FROM campaigns ORDER BY id LIMIT 1').fetchone()
    if existing:
        default_id = existing['id']
    else:
        cur = conn.execute(
            "INSERT INTO campaigns (name, description) VALUES (?, ?)",
            ('Default Campaign', 'Automatically created to hold your existing data.')
        )
        default_id = cur.lastrowid

    conn.execute('UPDATE groups SET campaign_id = ? WHERE campaign_id IS NULL', (default_id,))
    conn.execute('UPDATE characters SET campaign_id = ? WHERE campaign_id IS NULL', (default_id,))
    conn.execute('UPDATE maps SET campaign_id = ? WHERE campaign_id IS NULL', (default_id,))


def _backup_db():
    if not os.path.exists(DB_PATH):
        return
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_path = os.path.join(os.path.dirname(DB_PATH), f'dnd.db.bak-{stamp}')
    shutil.copy2(DB_PATH, backup_path)
