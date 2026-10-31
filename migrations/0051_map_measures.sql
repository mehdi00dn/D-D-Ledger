-- Shared measurements (DL-153): the ruler and the cone / sphere / line / cube templates a person is showing the table.
-- One row per person per map, replaced on every update and ignored once expires_at has passed, so it is a short-lived
-- "pointing at the map" signal, not saved map data.  Coordinates are natural map pixels.
CREATE TABLE IF NOT EXISTS map_measures (
    map_id     INTEGER NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('ruler', 'cone', 'sphere', 'line', 'cube')),
    ax         DOUBLE PRECISION NOT NULL,
    ay         DOUBLE PRECISION NOT NULL,
    bx         DOUBLE PRECISION NOT NULL,
    by         DOUBLE PRECISION NOT NULL,
    expires_at TIMESTAMP(3) NOT NULL,
    PRIMARY KEY (map_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_map_measures_expires ON map_measures (expires_at);

ALTER TABLE map_measures ENABLE ROW LEVEL SECURITY;              -- like every table: no direct access through Supabase's data API
