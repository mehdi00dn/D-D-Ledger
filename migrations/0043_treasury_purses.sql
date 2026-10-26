-- Character purses (DL-148 follow-up): an entry now belongs to one purse.  character_id NULL is the party's purse, a
-- character id is that character's own purse.  `batch` ties together the entries of one share between purses, so they
-- are removed together.  Entries that already named a character ("for Aria") now count in that character's purse.
ALTER TABLE treasury_entries ADD COLUMN IF NOT EXISTS batch TEXT;
CREATE INDEX IF NOT EXISTS idx_treasury_holder ON treasury_entries (campaign_id, character_id);
