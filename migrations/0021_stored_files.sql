-- Storage ledger: one row per image kept in the 'uploads' bucket, with its stored size, so the server can
-- enforce per-user and per-campaign upload quotas (see quotas.py) without listing storage on every upload.
-- Rows are written in the same transaction as the record that points at the file and removed when the file is deleted.
-- Images uploaded before this migration have no row (counted as 0 bytes) until scripts/backfill_storage_usage.py runs.
CREATE TABLE stored_files (
    id          BIGSERIAL PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    user_id     INTEGER,
    campaign_id INTEGER,
    kind        TEXT NOT NULL,
    bytes       BIGINT NOT NULL CHECK (bytes >= 0),
    created_at  TIMESTAMP(0) NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);
CREATE INDEX idx_stored_files_user ON stored_files (user_id);
CREATE INDEX idx_stored_files_campaign ON stored_files (campaign_id);

-- same lock-down as every other table (see 0002): closed to Supabase's public Data API
ALTER TABLE stored_files ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE stored_files FROM anon;
        REVOKE ALL ON SEQUENCE stored_files_id_seq FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE stored_files FROM authenticated;
        REVOKE ALL ON SEQUENCE stored_files_id_seq FROM authenticated;
    END IF;
END
$$;
