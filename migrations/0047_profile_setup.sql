-- DL-161: a new account is sent to a one-time "Set up your profile" page.  profile_setup_at is set when the person saves
-- or skips it; accounts that already exist count as done, so only new sign-ups see it.
ALTER TABLE users ADD COLUMN profile_setup_at TIMESTAMP(0);
UPDATE users SET profile_setup_at = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') WHERE profile_setup_at IS NULL;
