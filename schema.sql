-- D&D Campaign Manager Database Schema

CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    avatar_path TEXT,
    bio TEXT,
    color TEXT DEFAULT '#c9a24b',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    is_npc INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    max_hp INTEGER DEFAULT 10,
    str_score INTEGER DEFAULT 10,
    dex_score INTEGER DEFAULT 10,
    con_score INTEGER DEFAULT 10,
    int_score INTEGER DEFAULT 10,
    wis_score INTEGER DEFAULT 10,
    cha_score INTEGER DEFAULT 10,
    armor_class INTEGER DEFAULT 10,
    avatar_path TEXT,
    notes TEXT,
    group_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS character_sheets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL,
    image_path TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
);

-- Battle tables (used in a later stage, created now so schema is stable)
CREATE TABLE IF NOT EXISTS battle_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL,
    display_suffix INTEGER DEFAULT 0,
    current_hp INTEGER NOT NULL,
    initiative INTEGER DEFAULT 0,
    ac_override INTEGER,
    temp_hp INTEGER DEFAULT 0,
    is_dead INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_characters_group ON characters(group_id);
CREATE INDEX IF NOT EXISTS idx_sheets_character ON character_sheets(character_id);
CREATE INDEX IF NOT EXISTS idx_battle_character ON battle_participants(character_id);
