-- A roll made for one of several copies of a character in battle ("Goblin Minion #2") keeps that numbered name,
-- so the roll log reads the same as the battle menu even after the battle changes.
ALTER TABLE dice_rolls ADD COLUMN character_label TEXT NOT NULL DEFAULT '';
