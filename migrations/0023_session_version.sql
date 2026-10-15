-- "Log out everywhere": every login session remembers the version it was issued under, and changing the password
-- or pressing the button bumps the account's version, which ends all other sessions on their next request.
-- Existing sessions carry no version and count as 0, so nobody is logged out by this migration.
ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0;
