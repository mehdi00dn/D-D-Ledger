"""DL-184: every account gets its own copy of the Tutorial campaign, once, as its owner, with no activity or other logs."""
import time

import pytest
from conftest import q


@pytest.fixture(autouse=True)
def tutorial_on(monkeypatch, appmod):
    monkeypatch.setenv('OAKEN_TUTORIAL_CAMPAIGN', '1')
    appmod._tutorial_retry.clear()
    yield
    appmod._tutorial_retry.clear()


def mine(u):
    return q('SELECT c.id, c.name, cm.role, cm.status FROM campaigns c JOIN campaign_members cm ON cm.campaign_id = c.id '
             'JOIN users x ON x.id = cm.user_id WHERE x.username = ? ORDER BY c.id', u.name)


def flag(u):
    return q('SELECT tutorial_campaign_at FROM users WHERE username = ?', u.name)[0]['tutorial_campaign_at']


def test_opening_the_list_gives_the_account_the_tutorial_as_owner_and_dm(user):
    assert mine(user) == [] and flag(user) is None
    page = user.get('/campaigns')
    assert page.status_code == 200 and 'Tutorial' in page.get_data(as_text=True)
    rows = mine(user)
    assert len(rows) == 1 and rows[0]['name'] == 'Tutorial' and rows[0]['role'] == 'owner' and rows[0]['status'] == 'dm'
    assert flag(user) is not None
    assert q('SELECT COUNT(*) AS n FROM campaign_members WHERE campaign_id = ?', rows[0]['id'])[0]['n'] == 1


def test_the_copy_holds_what_the_file_holds(user):
    user.get('/campaigns')
    cid = mine(user)[0]['id']
    n = lambda table: q(f'SELECT COUNT(*) AS n FROM {table} WHERE campaign_id = ?', cid)[0]['n']
    assert n('characters') == 6 and n('groups') == 1 and n('maps') == 1 and n('quests') == 1 and n('session_journal') == 0
    assert q('SELECT COUNT(*) AS n FROM characters WHERE campaign_id = ? AND is_npc = 1', cid)[0]['n'] == 2
    assert q('SELECT COUNT(*) AS n FROM map_pins WHERE map_id IN (SELECT id FROM maps WHERE campaign_id = ?)', cid)[0]['n'] > 0
    assert q('SELECT access_mode FROM campaigns WHERE id = ?', cid)[0]['access_mode'] == 'private'
    # every picture was stored for this account
    assert q('SELECT COUNT(*) AS n FROM stored_files WHERE campaign_id = ? AND user_id = (SELECT id FROM users WHERE username = ?)', cid, user.name)[0]['n'] >= 13


def test_the_copy_starts_without_records_or_logs(user):
    user.get('/campaigns')
    cid = mine(user)[0]['id']
    for table in ('campaign_activity', 'dice_rolls', 'battle_log', 'battle_participants', 'campaign_invitations', 'campaign_invite_links', 'session_rsvps'):
        assert q(f'SELECT COUNT(*) AS n FROM {table} WHERE campaign_id = ?', cid)[0]['n'] == 0, table
    assert q('SELECT COUNT(*) AS n FROM notifications')[0]['n'] >= 0
    # the four starting purses are there, dated now and not on the day the DM wrote them
    rows = q('SELECT gp, note, created_at FROM treasury_entries WHERE campaign_id = ?', cid)
    assert len(rows) == 4 and all(r['gp'] == 50 and r['note'] == 'Income' for r in rows)
    assert all(not str(r['created_at']).startswith('2026-10-28') for r in rows)
    assert user.get(f'/campaigns/{cid}/activity').status_code == 200


def test_it_is_made_once_and_does_not_come_back_when_deleted(user):
    user.get('/campaigns'); user.get('/campaigns')
    assert len(mine(user)) == 1
    cid = mine(user)[0]['id']
    assert user.post(f'/campaigns/{cid}/delete').status_code == 302
    assert mine(user) == []
    user.get('/campaigns')
    assert mine(user) == [] and flag(user) is not None


def test_every_account_has_its_own_copy_and_its_own_files(user, make_user):
    other = make_user()
    user.get('/campaigns'); other.get('/campaigns')
    a, b = mine(user)[0]['id'], mine(other)[0]['id']
    assert a != b
    keys = lambda cid: {r['key'] for r in q('SELECT key FROM stored_files WHERE campaign_id = ?', cid)}
    assert keys(a) and keys(b) and not keys(a) & keys(b)
    assert user.get(f'/campaigns/{b}/characters').status_code == 404            # nobody else can open it


def test_an_account_that_is_over_its_storage_limit_still_gets_it(user, monkeypatch):
    monkeypatch.setenv('OAKEN_QUOTA_USER_MB', '1')
    user.get('/campaigns')
    assert len(mine(user)) == 1 and flag(user) is not None


def test_it_does_not_count_as_creating_a_campaign(user):
    user.get('/campaigns')
    cid = user.new_campaign()                          # the tutorial used none of the creation allowance
    assert cid and len(mine(user)) == 2


def test_the_switch_turns_it_off(user, monkeypatch):
    monkeypatch.setenv('OAKEN_TUTORIAL_CAMPAIGN', '0')
    assert user.get('/campaigns').status_code == 200
    assert mine(user) == [] and flag(user) is None


def test_a_missing_file_never_breaks_the_list(user, appmod, monkeypatch):
    monkeypatch.setattr(appmod, 'TUTORIAL_FILE', '/nonexistent/tutorial.zip')
    appmod._tutorial_cache.clear()
    assert user.get('/campaigns').status_code == 200
    assert mine(user) == [] and flag(user) is None


def test_a_failed_copy_leaves_the_account_unclaimed_and_is_retried_later(user, appmod, monkeypatch):
    real = appmod._import_campaign
    def boom(*a, **k): raise RuntimeError('storage went away')
    monkeypatch.setattr(appmod, '_import_campaign', boom)
    assert user.get('/campaigns').status_code == 200
    assert mine(user) == [] and flag(user) is None
    monkeypatch.setattr(appmod, '_import_campaign', real)
    user.get('/campaigns')                              # inside the quiet period: left alone
    assert mine(user) == []
    appmod._tutorial_retry.clear()
    user.get('/campaigns')
    assert len(mine(user)) == 1 and flag(user) is not None


def test_two_visits_at_once_make_one_copy(user, appmod):
    import threading
    out = []
    def visit():
        c = user.fresh_client()
        out.append(c.get('/campaigns').status_code)
    threads = [threading.Thread(target=visit) for _ in range(3)]
    for t in threads: t.start()
    for t in threads: t.join(60)
    assert out == [200, 200, 200] and len(mine(user)) == 1
