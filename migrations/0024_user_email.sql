-- Email on accounts (DL-132).  Optional: nobody is asked for one yet, and login still uses the username.
-- An address is only unique once it is verified, so nobody can block someone else's address by typing it in first.
ALTER TABLE users ADD COLUMN email             TEXT;
ALTER TABLE users ADD COLUMN email_verified_at TIMESTAMP(0);
CREATE UNIQUE INDEX idx_users_verified_email ON users (lower(email)) WHERE email_verified_at IS NOT NULL;
