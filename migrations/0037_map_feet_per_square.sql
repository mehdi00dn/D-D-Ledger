-- How far one grid square is on a map, in feet, for the distance ruler.  5 is the D&D default.
ALTER TABLE maps ADD COLUMN feet_per_square INTEGER NOT NULL DEFAULT 5;
