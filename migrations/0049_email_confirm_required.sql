-- DL-165: accounts made with a password while the site can send email must confirm their address before they can create
-- or join a campaign.  The flag is set at sign-up only, so accounts that already existed are never locked out.
ALTER TABLE users ADD COLUMN email_confirm_required BOOLEAN NOT NULL DEFAULT FALSE;
