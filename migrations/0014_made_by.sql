-- Every faction and map now remembers who made it (characters already did), so only its maker or the campaign
-- owner can delete it, and the details can say "Made by ...".
ALTER TABLE groups ADD COLUMN created_by INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE maps   ADD COLUMN created_by INTEGER REFERENCES users(id) ON DELETE SET NULL;

-- Everything that exists today was made by its campaign's owner (the longest-standing owner if there are several).
UPDATE groups SET created_by = (SELECT cm.user_id FROM campaign_members cm
                                 WHERE cm.campaign_id = groups.campaign_id AND cm.role = 'owner' ORDER BY cm.id LIMIT 1);
UPDATE maps   SET created_by = (SELECT cm.user_id FROM campaign_members cm
                                 WHERE cm.campaign_id = maps.campaign_id AND cm.role = 'owner' ORDER BY cm.id LIMIT 1);
UPDATE characters SET created_by = (SELECT cm.user_id FROM campaign_members cm
                                     WHERE cm.campaign_id = characters.campaign_id AND cm.role = 'owner' ORDER BY cm.id LIMIT 1)
 WHERE created_by IS NULL;

CREATE INDEX idx_groups_created_by ON groups(created_by);
CREATE INDEX idx_maps_created_by   ON maps(created_by);
