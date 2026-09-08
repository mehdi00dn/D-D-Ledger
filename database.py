import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'dnd.db')
SCHEMA_PATH = os.path.join(BASE_DIR, 'schema.sql')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    conn = get_db()
    with open(SCHEMA_PATH, 'r') as f:
        conn.executescript(f.read())
    _migrate(conn)
    conn.commit()
    conn.close()


def _migrate(conn):
    """Add columns to existing databases that predate them. Safe to run
    every startup: each ALTER is wrapped so an existing column is a no-op."""
    existing_cols = {row['name'] for row in conn.execute('PRAGMA table_info(battle_participants)')}
    if 'temp_hp' not in existing_cols:
        conn.execute('ALTER TABLE battle_participants ADD COLUMN temp_hp INTEGER DEFAULT 0')

    drawing_cols = {row['name'] for row in conn.execute('PRAGMA table_info(map_drawings)')}
    for col, ddl in (('cx', 'REAL DEFAULT 0'), ('cy', 'REAL DEFAULT 0'),
                     ('w', 'REAL DEFAULT 0'), ('h', 'REAL DEFAULT 0'),
                     ('rotation', 'REAL DEFAULT 0'), ('locked', 'INTEGER DEFAULT 0')):
        if col not in drawing_cols:
            conn.execute(f'ALTER TABLE map_drawings ADD COLUMN {col} {ddl}')

    pin_cols = {row['name'] for row in conn.execute('PRAGMA table_info(map_pins)')}
    for col, ddl in (('rotation', 'REAL DEFAULT 0'), ('locked', 'INTEGER DEFAULT 0')):
        if col not in pin_cols:
            conn.execute(f'ALTER TABLE map_pins ADD COLUMN {col} {ddl}')

    map_cols = {row['name'] for row in conn.execute('PRAGMA table_info(maps)')}
    for col, ddl in (('grid_color', "TEXT DEFAULT 'gray'"), ('grid_visible', 'INTEGER DEFAULT 1'),
                     ('grid_setup_done', 'INTEGER DEFAULT 0'), ('snap_to_grid', 'INTEGER DEFAULT 0')):
        if col not in map_cols:
            conn.execute(f'ALTER TABLE maps ADD COLUMN {col} {ddl}')
