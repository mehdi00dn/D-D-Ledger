-- A character can belong to several factions.  character_groups holds every membership; characters.group_id stays
-- as the character's MAIN faction (the one whose colour shows on tokens and battle rows) and is kept in step by the
-- app: it is always one of the character's memberships, or NULL when there are none.  Existing single factions
-- are carried over as one membership each.
CREATE TABLE character_groups (
    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    group_id     INTEGER NOT NULL REFERENCES groups(id)     ON DELETE CASCADE,
    position     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (character_id, group_id)
);

CREATE INDEX idx_character_groups_group ON character_groups(group_id);

ALTER TABLE character_groups ENABLE ROW LEVEL SECURITY;      -- like every table: no direct access through Supabase's data API

INSERT INTO character_groups (character_id, group_id, position)
SELECT id, group_id, 0 FROM characters WHERE group_id IS NOT NULL;
