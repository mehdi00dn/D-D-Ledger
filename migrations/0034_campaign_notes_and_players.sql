-- Campaign notes (same multi-box format as character notes, shown on the campaign Home page) and "played by":
-- the campaign member a character is attached to.  That member can edit the character and move its map token.
ALTER TABLE campaigns  ADD COLUMN notes     TEXT;
ALTER TABLE characters ADD COLUMN player_id INTEGER REFERENCES users(id) ON DELETE SET NULL;
CREATE INDEX idx_characters_player ON characters(player_id);
