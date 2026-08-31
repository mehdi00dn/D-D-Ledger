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
