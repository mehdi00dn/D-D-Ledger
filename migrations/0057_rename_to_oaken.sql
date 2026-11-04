-- DL-176: the app is now called Oaken.  Existing text that names the old product (the tutorial campaign, its quest
-- and the activity lines written about that quest) is rewritten.  Safe to re-run; touches only rows that match.
UPDATE campaigns SET description = REPLACE(REPLACE(REPLACE(description, 'D&amp;D Ledger', 'Oaken'), 'D&D Ledger', 'Oaken'), 'Campaign Ledger', 'Oaken')
 WHERE description LIKE '%Ledger%';
UPDATE campaigns SET notes = REPLACE(REPLACE(REPLACE(notes, 'D&amp;D Ledger', 'Oaken'), 'D&D Ledger', 'Oaken'), 'Campaign Ledger', 'Oaken')
 WHERE notes LIKE '%Ledger%';
UPDATE quests SET title = REPLACE(REPLACE(REPLACE(title, 'D&amp;D Ledger', 'Oaken'), 'D&D Ledger', 'Oaken'), 'Campaign Ledger', 'Oaken')
 WHERE title LIKE '%Ledger%';
UPDATE campaign_activity SET text = REPLACE(REPLACE(REPLACE(text, 'D&amp;D Ledger', 'Oaken'), 'D&D Ledger', 'Oaken'), 'Campaign Ledger', 'Oaken')
 WHERE text LIKE '%Ledger%';
