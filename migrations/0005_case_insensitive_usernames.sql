-- Usernames become case-insensitive: 'Bob' and 'bob' are the same account from here on.
-- Display case is preserved (we don't rewrite anyone's stored username) -- only lookups and
-- the uniqueness check become case-insensitive.
--
-- Guard first: if two existing accounts already differ only by case (not possible today since
-- the column has always been case-sensitive-unique, but this migration must still be safe to
-- run against any real, possibly-messy production data), refuse to proceed with a clear error
-- naming them, rather than let CREATE UNIQUE INDEX fail with an opaque constraint violation or
-- -- worse -- silently pick one arbitrarily. Whoever runs this fixes the conflict by renaming
-- one of the accounts, then re-runs.
DO $$
DECLARE
    conflict RECORD;
BEGIN
    SELECT lower(username) AS lu, array_agg(username ORDER BY id) AS variants
      INTO conflict
      FROM users
      GROUP BY lower(username)
      HAVING count(*) > 1
      LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'Cannot make usernames case-insensitive: % already exist as %. Rename one of them, then re-run this migration.',
            array_length(conflict.variants, 1), conflict.variants;
    END IF;
END $$;

CREATE UNIQUE INDEX idx_users_username_ci ON users (lower(username));
