-- The action-economy and concentration toggles (migration 0029) were taken out again: the battle rows stay simple.
ALTER TABLE battle_participants DROP COLUMN IF EXISTS spent;
ALTER TABLE battle_participants DROP COLUMN IF EXISTS concentrating;
