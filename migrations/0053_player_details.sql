-- DL-97: optional "about me as a player" details on the public profile.
ALTER TABLE users ADD COLUMN IF NOT EXISTS favourite_classes TEXT;       -- up to 3 class keys, comma-separated
ALTER TABLE users ADD COLUMN IF NOT EXISTS play_style TEXT CHECK (play_style IN ('roleplay', 'combat', 'exploration', 'balanced'));
ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone TEXT;                -- an IANA name such as Asia/Tehran
ALTER TABLE users ADD COLUMN IF NOT EXISTS details_visibility TEXT NOT NULL DEFAULT 'profile' CHECK (details_visibility IN ('profile', 'friends', 'private'));
