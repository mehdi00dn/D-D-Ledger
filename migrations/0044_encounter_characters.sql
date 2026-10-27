-- Monsters added from an encounter (or the monster manual) become ONE shared character per kind of monster, however many
-- copies fight.  `added_from` says where the character came from (shown on its card); `added_sig` is a fingerprint of the
-- character as it was created, so a later edit (stats, notes, pictures, group...) is noticed and that character is left alone.
ALTER TABLE characters ADD COLUMN IF NOT EXISTS added_from TEXT;
ALTER TABLE characters ADD COLUMN IF NOT EXISTS added_sig  TEXT;
