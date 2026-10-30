-- DL-167: the sign-up form now has a required "I accept the cookies" tick box next to the Terms and Privacy one.  This
-- records when it was ticked (a Google sign-up records it when the person ticked the boxes before choosing Google).
-- Accounts that already existed stay empty.
ALTER TABLE users ADD COLUMN cookies_accepted_at TIMESTAMP(0);
