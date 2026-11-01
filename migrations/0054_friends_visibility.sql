-- DL-98 follow-up: who can see the friends list on a profile (default: friends only).
ALTER TABLE users ADD COLUMN IF NOT EXISTS friends_visibility TEXT NOT NULL DEFAULT 'friends' CHECK (friends_visibility IN ('profile', 'friends', 'private'));
