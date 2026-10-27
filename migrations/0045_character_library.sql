-- Personal character library (DL-160).  A library character is an ordinary row in `characters` that belongs to a person
-- instead of a campaign: campaign_id is NULL and owner_id is the person.  Everything that reads characters by campaign
-- therefore never sees it; only the library pages (and the profile showcase, which already works from created_by) do.
--
-- owner_id   the person whose library holds the character.  Deleting the account deletes the library with it.
-- library_id links the two halves of a copy: on a campaign character it is the library character it was added from (or
--            saved to), so the same character is not added to the same campaign twice.  Deleting the library character
--            only clears the link; the campaign keeps its character.
ALTER TABLE characters ADD COLUMN IF NOT EXISTS owner_id   INTEGER REFERENCES users(id)      ON DELETE CASCADE;
ALTER TABLE characters ADD COLUMN IF NOT EXISTS library_id INTEGER REFERENCES characters(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_characters_owner   ON characters (owner_id)   WHERE owner_id   IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_characters_library ON characters (library_id) WHERE library_id IS NOT NULL;

-- A character belongs to a campaign or to a person, never to nothing.
ALTER TABLE characters ADD CONSTRAINT characters_has_a_home CHECK (campaign_id IS NOT NULL OR owner_id IS NOT NULL);
