-- DL-141: covering indexes for foreign keys that had none (Supabase performance advisor).  Deletes on the parent
-- tables (users, characters, invitations, battle participants) no longer scan the child table.
CREATE INDEX IF NOT EXISTS idx_campaign_activity_user       ON campaign_activity (user_id);
CREATE INDEX IF NOT EXISTS idx_invitations_invited_by       ON campaign_invitations (invited_by_id);
CREATE INDEX IF NOT EXISTS idx_invite_links_created_by      ON campaign_invite_links (created_by_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_battle_turn        ON campaigns (battle_turn_id);
CREATE INDEX IF NOT EXISTS idx_dice_rolls_character         ON dice_rolls (character_id);
CREATE INDEX IF NOT EXISTS idx_dice_rolls_user              ON dice_rolls (user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_invitation     ON notifications (invitation_id);
