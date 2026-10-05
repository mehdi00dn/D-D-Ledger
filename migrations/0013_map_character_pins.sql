-- Characters on the map no longer depend on the battle: a character pin now remembers WHICH character it shows
-- (character_id), and its battle row (participant_id) is only filled in while the map is linked to the battle.
ALTER TABLE map_pins ADD COLUMN character_id INTEGER REFERENCES characters(id) ON DELETE CASCADE;

UPDATE map_pins SET character_id = (SELECT bp.character_id FROM battle_participants bp WHERE bp.id = map_pins.participant_id)
 WHERE pin_type = 'character' AND participant_id IS NOT NULL;
DELETE FROM map_pins WHERE pin_type = 'character' AND character_id IS NULL;   -- orphans of removed battle rows

-- Maps that are not linked keep their character pins as plain map tokens (no battle row behind them).
UPDATE map_pins SET participant_id = NULL
 WHERE pin_type = 'character' AND map_id IN (SELECT id FROM maps WHERE linked_to_battle = 0);

CREATE INDEX idx_map_pins_character ON map_pins(character_id);
-- One token per battle row per map, so two clients syncing at once can never double-place someone.
CREATE UNIQUE INDEX uq_map_pins_participant ON map_pins(map_id, participant_id)
 WHERE pin_type = 'character' AND participant_id IS NOT NULL;
