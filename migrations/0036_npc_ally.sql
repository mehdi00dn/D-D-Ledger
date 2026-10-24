-- NPC allies: an NPC the table treats as a party member. It stays an NPC (own tag, DM-rolled dice, initiative scope),
-- but its stats are shown to Players and the "Hide NPCs" switch does not hide it.
ALTER TABLE characters ADD COLUMN is_ally INTEGER NOT NULL DEFAULT 0;
