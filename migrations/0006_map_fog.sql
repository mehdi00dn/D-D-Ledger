-- Map fog of war. The mask is one bit per FOG_CELL x FOG_CELL block of the map image
-- (1 = fogged), packed row by row, zlib-compressed and base64-encoded (see fog.py).
-- NULL means "no fog". fog_version bumps on every change so open tabs know when to refetch.
ALTER TABLE maps ADD COLUMN fog_mask    TEXT;
ALTER TABLE maps ADD COLUMN fog_cols    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE maps ADD COLUMN fog_rows    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE maps ADD COLUMN fog_version INTEGER NOT NULL DEFAULT 0;
