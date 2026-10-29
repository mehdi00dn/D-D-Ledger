-- A shape can be attached to a token (DL-159): when the token moves, the shape moves with it by the same amount.
-- No alignment is forced; the shape keeps whatever position it had when it was attached.  If the token is removed the
-- shape stays where it is and simply lets go.
ALTER TABLE map_drawings ADD COLUMN IF NOT EXISTS attached_pin_id INTEGER REFERENCES map_pins(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_map_drawings_attached ON map_drawings (attached_pin_id);
