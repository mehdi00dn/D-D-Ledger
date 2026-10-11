-- DM-only map objects: a drawing or token flagged dm_only is never sent to Players (the server filters it out
-- of every read path and refuses Player writes to it).  Everything that exists today stays visible.
ALTER TABLE map_drawings ADD COLUMN dm_only INTEGER NOT NULL DEFAULT 0;
ALTER TABLE map_pins ADD COLUMN dm_only INTEGER NOT NULL DEFAULT 0;
