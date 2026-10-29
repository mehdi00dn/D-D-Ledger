-- DL-79: the moment a person made their account counts as agreeing to the Terms and the Privacy page they were shown
-- next to the sign-up button (or the Google button).  Accounts that already existed stay empty.
ALTER TABLE users ADD COLUMN terms_accepted_at TIMESTAMP(0);
