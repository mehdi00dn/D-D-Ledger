"""Fill the storage ledger (stored_files) for images uploaded before quotas existed.

    DATABASE_URL=...  python scripts/backfill_storage_usage.py [--dry-run]

Reads every image key the database points at (avatars, sheets, maps), asks storage for its size and inserts a
ledger row for keys that have none.  Safe to re-run.  Attribution: maps/characters/groups use their `created_by`
and campaign; campaign covers use the campaign owner.  Without it old images simply count as 0 bytes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_db            # noqa: E402
from storage import get_storage, UPLOADS   # noqa: E402
import quotas                          # noqa: E402

SOURCES = [
    ('avatars', "SELECT avatar_path AS k, created_by AS u, id AS cid FROM campaigns c, "
                "LATERAL (SELECT user_id AS created_by FROM campaign_members m WHERE m.campaign_id = c.id AND m.role = 'owner' LIMIT 1) o "
                "WHERE avatar_path IS NOT NULL"),
    ('avatars', 'SELECT avatar_path AS k, created_by AS u, campaign_id AS cid FROM characters WHERE avatar_path IS NOT NULL'),
    ('avatars', 'SELECT avatar_path AS k, created_by AS u, campaign_id AS cid FROM groups WHERE avatar_path IS NOT NULL'),
    ('sheets', 'SELECT cs.image_path AS k, c.created_by AS u, c.campaign_id AS cid FROM character_sheets cs JOIN characters c ON c.id = cs.character_id'),
    ('maps', 'SELECT image_path AS k, created_by AS u, campaign_id AS cid FROM maps WHERE image_path IS NOT NULL'),
]


def main(dry_run=False):
    db, st = get_db(), get_storage()
    known = {r['key'] for r in db.execute('SELECT key FROM stored_files')}
    added = missing = 0
    for kind, sql in SOURCES:
        for row in db.execute(sql).fetchall():
            if row['k'] in known:
                continue
            data = st.get(UPLOADS, row['k'])
            if data is None:
                missing += 1
                continue
            if not dry_run:
                quotas.record(db, row['k'], row['u'], row['cid'], kind, len(data))
            known.add(row['k'])
            added += 1
    if not dry_run:
        db.commit()
    print(f"{'would add' if dry_run else 'added'} {added} ledger rows; {missing} referenced files were not found in storage")
    return added, missing


if __name__ == '__main__':
    main('--dry-run' in sys.argv)
