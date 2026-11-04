-- Short public ids for the pages people open and share (DL-109): /campaigns/3f9a0c7be214 instead of /campaigns/1.
-- The integer keys stay the primary keys and every foreign key keeps pointing at them; the short id is only what shows
-- in the address bar.  Old numeric addresses keep working (the app redirects pages to the short form).
-- The code is 12 lowercase hex characters (48 random bits) with at least one letter a-f, so it can never be read as a
-- number.  gen_random_uuid() is a core function from Postgres 13 and is cryptographically random.
-- The separate UUID public_id columns from 0004 are not touched: they are the host-portable identifiers.

CREATE OR REPLACE FUNCTION gen_short_id() RETURNS TEXT LANGUAGE plpgsql VOLATILE AS $$
DECLARE s TEXT;
BEGIN
    LOOP
        s := substr(replace(gen_random_uuid()::text, '-', ''), 1, 12);
        EXIT WHEN s ~ '[a-f]';
    END LOOP;
    RETURN s;
END
$$;

-- Adding a volatile default rewrites the table once and gives every existing row its own code.
ALTER TABLE campaigns       ADD COLUMN short_id TEXT NOT NULL DEFAULT gen_short_id();
ALTER TABLE characters      ADD COLUMN short_id TEXT NOT NULL DEFAULT gen_short_id();
ALTER TABLE maps            ADD COLUMN short_id TEXT NOT NULL DEFAULT gen_short_id();
ALTER TABLE session_journal ADD COLUMN short_id TEXT NOT NULL DEFAULT gen_short_id();
ALTER TABLE quests          ADD COLUMN short_id TEXT NOT NULL DEFAULT gen_short_id();

CREATE UNIQUE INDEX idx_campaigns_short_id       ON campaigns (short_id);
CREATE UNIQUE INDEX idx_characters_short_id      ON characters (short_id);
CREATE UNIQUE INDEX idx_maps_short_id            ON maps (short_id);
CREATE UNIQUE INDEX idx_session_journal_short_id ON session_journal (short_id);
CREATE UNIQUE INDEX idx_quests_short_id          ON quests (short_id);

