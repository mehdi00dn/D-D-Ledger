"""DL-141: foreign-key indexes (migration 0028) and bounded notification inboxes."""
import pytest
from conftest import q, BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only (indexes, pg_indexes)')

EXPECTED = {
    'idx_campaign_activity_user', 'idx_invitations_invited_by', 'idx_invite_links_created_by',
    'idx_campaigns_battle_turn', 'idx_dice_rolls_character', 'idx_dice_rolls_user', 'idx_notifications_invitation',
}


def test_foreign_key_indexes_exist(appmod):
    have = {r['indexname'] for r in q("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()")}
    assert EXPECTED <= have, sorted(EXPECTED - have)


def test_notification_inbox_is_capped_and_keeps_the_newest(appmod, make_user):
    import database
    u = make_user(); uid = q('SELECT id FROM users WHERE username = ?', u.name)[0]['id']
    other = make_user(); oid = q('SELECT id FROM users WHERE username = ?', other.name)[0]['id']
    db = database.get_db()
    appmod.notify(db, oid, 'keep', 'other user')
    for i in range(appmod.NOTIFICATION_KEEP + 5):
        appmod.notify(db, uid, 'cap', f'n{i}')
    db.commit(); db.close()
    titles = [r['title'] for r in q("SELECT title FROM notifications WHERE user_id = ? AND kind = 'cap' ORDER BY id", uid)]
    assert len(titles) == appmod.NOTIFICATION_KEEP
    assert titles[-1] == f'n{appmod.NOTIFICATION_KEEP + 4}' and titles[0] == 'n5'
    assert len(q("SELECT 1 FROM notifications WHERE user_id = ? AND kind = 'keep'", oid)) == 1      # another user's inbox is untouched
