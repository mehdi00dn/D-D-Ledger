-- Custom battle conditions: up to three free-text conditions per participant, typed by the DM
-- (the standard ones live in battle_participants.conditions as fixed keys).  Stored as a JSON array of strings.
ALTER TABLE battle_participants ADD COLUMN custom_conditions TEXT NOT NULL DEFAULT '[]';
