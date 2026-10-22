-- Hide NPCs from Players: a campaign switch (the DM and the owner flip it), plus a flag on battle log lines that are about
-- an NPC so Players can be shown none of them while it is on.
ALTER TABLE campaigns   ADD COLUMN hide_npcs BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE battle_log  ADD COLUMN about_npc BOOLEAN NOT NULL DEFAULT FALSE;
