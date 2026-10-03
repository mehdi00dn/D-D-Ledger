-- Turn tracker: which participant is acting and which round the fight is in.
-- Kept on the campaign (one battle per campaign today). battle_turn_id clears itself if that
-- participant leaves the battle.
ALTER TABLE campaigns ADD COLUMN battle_round   INTEGER NOT NULL DEFAULT 1;
ALTER TABLE campaigns ADD COLUMN battle_turn_id INTEGER REFERENCES battle_participants(id) ON DELETE SET NULL;
