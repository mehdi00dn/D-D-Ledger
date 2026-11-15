-- DL-184: every account gets a copy of the Tutorial campaign, once.
-- tutorial_campaign_at is set when the account has been given its copy, so deleting it does not bring it back.
-- NULL means "not given yet": every existing account gets its copy the next time it opens its campaign list.
ALTER TABLE users ADD COLUMN IF NOT EXISTS tutorial_campaign_at TIMESTAMP(0);
