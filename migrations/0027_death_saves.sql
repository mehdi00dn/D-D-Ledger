-- Death saving throws (DL-145): the three-and-three tally for a character who is down at 0 HP.
-- Successes and failures each run 0..3; is_stable means three successes landed (still 0 HP and unconscious, no more saves).
-- Healing, setting HP above 0 or reviving puts all three back to zero.
ALTER TABLE battle_participants ADD COLUMN death_successes INTEGER NOT NULL DEFAULT 0 CHECK (death_successes BETWEEN 0 AND 3);
ALTER TABLE battle_participants ADD COLUMN death_failures  INTEGER NOT NULL DEFAULT 0 CHECK (death_failures  BETWEEN 0 AND 3);
ALTER TABLE battle_participants ADD COLUMN is_stable       INTEGER NOT NULL DEFAULT 0 CHECK (is_stable IN (0, 1));
