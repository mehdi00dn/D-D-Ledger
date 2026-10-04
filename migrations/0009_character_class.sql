-- Optional character class (one of the 13 keys listed in app.py, CHARACTER_CLASSES).
-- Unknown values are rejected by the app; the column itself stays free text so adding a class never needs a migration.
ALTER TABLE characters ADD COLUMN class_key TEXT;
