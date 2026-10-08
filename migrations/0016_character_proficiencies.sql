-- Extra character settings: walking speed, saving-throw proficiencies and skill proficiencies.
-- Modifiers, proficiency bonus, skill bonuses and passive perception are worked out from the existing scores
-- and level, so only the choices themselves are stored.
--   skill_prof: JSON object, skill key -> 1 (proficient) or 2 (expertise), e.g. {"stealth": 2, "arcana": 1}
--   save_prof:  JSON array of ability keys, e.g. ["str", "con"]
ALTER TABLE characters ADD COLUMN speed      INTEGER NOT NULL DEFAULT 30;
ALTER TABLE characters ADD COLUMN skill_prof TEXT    NOT NULL DEFAULT '{}';
ALTER TABLE characters ADD COLUMN save_prof  TEXT    NOT NULL DEFAULT '[]';
