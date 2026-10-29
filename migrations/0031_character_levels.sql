-- DL-105: stats saved level by level.  A character's own columns (level, max_hp, armor_class, the six scores) stay the
-- stats of its CURRENT level, so battle, the sheet and every export keep reading them unchanged; this table remembers
-- what each level looked like (HP, AC, the six scores, speed and the skill and saving-throw proficiencies), so moving a character to another level can bring that level's stats back.
CREATE TABLE character_levels (
    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    level        INTEGER NOT NULL CHECK (level BETWEEN 1 AND 30),
    max_hp       INTEGER NOT NULL,
    armor_class  INTEGER NOT NULL,
    str_score    INTEGER NOT NULL,
    dex_score    INTEGER NOT NULL,
    con_score    INTEGER NOT NULL,
    int_score    INTEGER NOT NULL,
    wis_score    INTEGER NOT NULL,
    cha_score    INTEGER NOT NULL,
    speed        INTEGER NOT NULL DEFAULT 30,
    skill_prof   TEXT    NOT NULL DEFAULT '{}',
    save_prof    TEXT    NOT NULL DEFAULT '[]',
    PRIMARY KEY (character_id, level)
);

INSERT INTO character_levels (character_id, level, max_hp, armor_class, str_score, dex_score, con_score, int_score, wis_score, cha_score, speed, skill_prof, save_prof)
SELECT id, level, COALESCE(max_hp, 10), COALESCE(armor_class, 10), COALESCE(str_score, 10), COALESCE(dex_score, 10),
       COALESCE(con_score, 10), COALESCE(int_score, 10), COALESCE(wis_score, 10), COALESCE(cha_score, 10),
       COALESCE(speed, 30), COALESCE(skill_prof, '{}'), COALESCE(save_prof, '[]')
FROM characters WHERE level BETWEEN 1 AND 30;

ALTER TABLE character_levels ENABLE ROW LEVEL SECURITY;         -- like every table: no direct access through Supabase's data API
