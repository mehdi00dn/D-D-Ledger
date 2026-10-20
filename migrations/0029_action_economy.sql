-- DL-146: per-turn action economy and concentration on a battle participant.
-- `spent` is a comma-separated list of what has been used this turn, in a fixed order (see ECONOMY_KEYS in app.py),
-- like `conditions`; it is emptied when the participant's turn starts.
ALTER TABLE battle_participants ADD COLUMN spent TEXT NOT NULL DEFAULT '';
ALTER TABLE battle_participants ADD COLUMN concentrating INTEGER NOT NULL DEFAULT 0 CHECK (concentrating IN (0, 1));
