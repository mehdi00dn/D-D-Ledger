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

-- Maps
CREATE TABLE IF NOT EXISTS maps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    image_path TEXT NOT NULL,
    image_width INTEGER DEFAULT 0,
    image_height INTEGER DEFAULT 0,
    grid_size INTEGER DEFAULT 50,
    grid_offset_x INTEGER DEFAULT 0,
    grid_offset_y INTEGER DEFAULT 0,
    grid_color TEXT DEFAULT 'gray',
    grid_visible INTEGER DEFAULT 1,
    grid_setup_done INTEGER DEFAULT 0,
    linked_to_battle INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Freeform drawings (lines, rects, ovals, pen strokes) on a map, all
-- coordinates stored in the map image's natural pixel space.
-- Shapes are stored as object-model transforms: a bounding box (cx, cy, w, h)
-- in natural-image pixel space plus a rotation in degrees. Kind-specific
-- geometry that lives *inside* that box (line endpoints, pen strokes) is
-- stored in `data` as fractions in the range -0.5..0.5, so resizing/rotating
-- the box automatically carries the inner geometry with it.
CREATE TABLE IF NOT EXISTS map_drawings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    map_id INTEGER NOT NULL,
    kind TEXT NOT NULL,        -- 'line' | 'rect' | 'oval' | 'pen'
    data TEXT NOT NULL,        -- JSON, kind-specific local geometry (fractions of w/h)
    cx REAL DEFAULT 0,
    cy REAL DEFAULT 0,
    w REAL DEFAULT 0,
    h REAL DEFAULT 0,
    rotation REAL DEFAULT 0,
    color TEXT DEFAULT '#c9a24b',
    fill INTEGER DEFAULT 0,
    fill_opacity REAL DEFAULT 0.4,
    sort_order INTEGER DEFAULT 0,
    locked INTEGER DEFAULT 0,
    FOREIGN KEY (map_id) REFERENCES maps(id) ON DELETE CASCADE
);

-- Pins on a map: either a live character (tied to a battle participant)
-- or a static prop (e.g. a summoned familiar marker).
CREATE TABLE IF NOT EXISTS map_pins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    map_id INTEGER NOT NULL,
    pin_type TEXT NOT NULL DEFAULT 'character',  -- 'character' | 'prop'
    participant_id INTEGER,
    icon_key TEXT,
    x REAL NOT NULL,   -- natural image pixels
    y REAL NOT NULL,
    scale REAL DEFAULT 1.0,
    rotation REAL DEFAULT 0,
    locked INTEGER DEFAULT 0,
    FOREIGN KEY (map_id) REFERENCES maps(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_drawings_map ON map_drawings(map_id);
CREATE INDEX IF NOT EXISTS idx_pins_map ON map_pins(map_id);
