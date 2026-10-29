-- Basic profile: an optional display name, avatar and short bio on the account.
-- display_name is only a label (login still uses the unique username); avatar_path is a key in the 'uploads' bucket.
-- The users table already has row level security and no public grants (see 0002), so nothing more is needed here.
ALTER TABLE users ADD COLUMN display_name TEXT;
ALTER TABLE users ADD COLUMN avatar_path  TEXT;
ALTER TABLE users ADD COLUMN bio          TEXT;
