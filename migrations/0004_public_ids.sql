-- Add stable, host-portable public identifiers to the four entities the app
-- addresses directly by id in routes and exports: campaigns, characters,
-- groups and maps. These are not used to replace the internal integer ids
-- yet (no route or query changes here) -- they exist so a later change (e.g.
-- exposing IDs in URLs or across a host migration) never has to invent them
-- retroactively or touch historical data.
--
-- gen_random_uuid() is a Postgres core function since version 13 (no
-- pgcrypto/uuid-ossp extension required). ADD COLUMN with this default
-- rewrites the table once, assigning every existing row its own random UUID;
-- new rows get one automatically on insert.

ALTER TABLE campaigns  ADD COLUMN public_id UUID NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE characters ADD COLUMN public_id UUID NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE groups     ADD COLUMN public_id UUID NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE maps       ADD COLUMN public_id UUID NOT NULL DEFAULT gen_random_uuid();

CREATE UNIQUE INDEX idx_campaigns_public_id  ON campaigns(public_id);
CREATE UNIQUE INDEX idx_characters_public_id ON characters(public_id);
CREATE UNIQUE INDEX idx_groups_public_id     ON groups(public_id);
CREATE UNIQUE INDEX idx_maps_public_id       ON maps(public_id);
