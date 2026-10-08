-- Token ownership: the DM can attach a map token to one campaign member, and a Player can then move only the
-- tokens attached to them.  NULL means "nobody": a DM-only token.  Tokens a Player places themselves are
-- attached to that Player by the app.  Everything that exists today stays unassigned (DM-only).
ALTER TABLE map_pins ADD COLUMN owner_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX idx_map_pins_owner ON map_pins(owner_user_id);
