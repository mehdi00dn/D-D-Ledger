-- Battle conditions (Blinded, Prone, ...): kept on the participant as a comma-separated list of
-- condition keys, in a fixed order.  The allowed keys live in app.py (BATTLE_CONDITIONS).
ALTER TABLE battle_participants ADD COLUMN conditions TEXT NOT NULL DEFAULT '';
