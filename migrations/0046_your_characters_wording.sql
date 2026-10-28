-- The character library is now called "Your characters" in the app.  Copies made from it say where they came from in plain words
-- that read well to everyone at the table.
UPDATE characters SET added_from = 'their own characters' WHERE added_from = 'a personal library';
