-- Usernames are unique case-insensitively while display case is preserved.
DO $$
DECLARE conflict RECORD;
BEGIN
    SELECT lower(username) AS lu, array_agg(username ORDER BY id) AS variants
      INTO conflict FROM users GROUP BY lower(username) HAVING count(*) > 1 LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'Cannot make usernames case-insensitive: % already exist as %. Rename one of them, then re-run this migration.',
            array_length(conflict.variants, 1), conflict.variants;
    END IF;
END $$;
CREATE UNIQUE INDEX idx_users_username_ci ON users (lower(username));
