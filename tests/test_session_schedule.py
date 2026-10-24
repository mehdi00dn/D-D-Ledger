"""DL-152: the next session, RSVPs, the calendar file and the notification."""
from datetime import datetime, timedelta, timezone
import pytest
from conftest import q, BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only')


def _iso(days=3, hours=0):
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=hours)).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def _setup(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, pl)
    return dm, pl, cid


def test_dm_schedules_members_answer_and_a_new_time_resets_the_answers(make_user):
    dm, pl, cid = _setup(make_user)
    assert pl.post(f'/campaigns/{cid}/session', data={'starts_at': _iso()}).status_code == 403
    assert 'Set the date' in dm.get(f'/campaigns/{cid}/home').data.decode()
    assert 'Next session' not in pl.get(f'/campaigns/{cid}/home').data.decode()          # nothing to show a Player yet
    assert pl.post(f'/campaigns/{cid}/session/rsvp', data={'status': 'yes'}).status_code == 409
    r = dm.post(f'/campaigns/{cid}/session', data={'starts_at': _iso(), 'note': '  Bring   snacks, dice  '})
    assert r.status_code == 302
    assert q('SELECT next_session_note FROM campaigns WHERE id = ?', cid)[0]['next_session_note'] == 'Bring snacks, dice'
    html = pl.get(f'/campaigns/{cid}/home').data.decode()
    assert 'data-local-time' in html and 'Bring snacks, dice' in html and 'Add to calendar' in html
    assert pl.post(f'/campaigns/{cid}/session/rsvp', data={'status': 'bogus'}).status_code == 400
    pl.post(f'/campaigns/{cid}/session/rsvp', data={'status': 'maybe'})
    pl.post(f'/campaigns/{cid}/session/rsvp', data={'status': 'yes'})                    # answering again replaces it
    dm.post(f'/campaigns/{cid}/session/rsvp', data={'status': 'no'})
    assert [r['status'] for r in q('SELECT status FROM session_rsvps WHERE campaign_id = ? ORDER BY status', cid)] == ['no', 'yes']
    assert 'is-on' in pl.get(f'/campaigns/{cid}/home').data.decode()
    # same time again keeps the answers; a different time clears them
    dm.post(f'/campaigns/{cid}/session', data={'starts_at': str(q('SELECT next_session_at FROM campaigns WHERE id = ?', cid)[0]['next_session_at']).replace(' ', 'T')[:19] + 'Z', 'note': 'x'})
    assert len(q('SELECT id FROM session_rsvps WHERE campaign_id = ?', cid)) == 2
    dm.post(f'/campaigns/{cid}/session', data={'starts_at': _iso(5)})
    assert q('SELECT id FROM session_rsvps WHERE campaign_id = ?', cid) == []


def test_members_are_told_once_when_the_time_changes_and_it_is_in_the_activity_feed(make_user):
    dm, pl, cid = _setup(make_user)
    uid = q('SELECT id FROM users WHERE username = ?', pl.name)[0]['id']
    dm.post(f'/campaigns/{cid}/session', data={'starts_at': _iso()})
    dm_id = q('SELECT id FROM users WHERE username = ?', dm.name)[0]['id']
    assert len(q("SELECT id FROM notifications WHERE user_id = ? AND kind = 'session_scheduled'", uid)) == 1
    assert q("SELECT id FROM notifications WHERE user_id = ? AND kind = 'session_scheduled'", dm_id) == []      # not the one who set it
    dm.post(f'/campaigns/{cid}/session', data={'starts_at': _iso(), 'note': 'only the note changed'})
    assert len(q("SELECT id FROM notifications WHERE user_id = ? AND kind = 'session_scheduled'", uid)) == 1
    assert 'scheduled the next session' in dm.get(f'/campaigns/{cid}/activity').data.decode()


def test_bad_times_are_refused_and_the_session_can_be_cancelled(make_user):
    dm, pl, cid = _setup(make_user)
    for bad in ('', 'tomorrow', '2001-01-01T00:00:00Z', _iso(days=900), 'x' * 80):
        assert dm.post(f'/campaigns/{cid}/session', data={'starts_at': bad}).status_code == 400
    dm.post(f'/campaigns/{cid}/session', data={'starts_at': _iso()})
    pl.post(f'/campaigns/{cid}/session/rsvp', data={'status': 'yes'})
    assert pl.post(f'/campaigns/{cid}/session', data={'clear': '1'}).status_code == 403
    dm.post(f'/campaigns/{cid}/session', data={'clear': '1'})
    row = q('SELECT next_session_at FROM campaigns WHERE id = ?', cid)[0]
    assert row['next_session_at'] is None and q('SELECT id FROM session_rsvps WHERE campaign_id = ?', cid) == []
    assert pl.get(f'/campaigns/{cid}/session.ics').status_code == 409


def test_calendar_file_is_valid_and_members_only(make_user):
    dm, pl, cid = _setup(make_user)
    outsider = make_user()
    dm.post(f'/campaigns/{cid}/session', data={'starts_at': '2099-01-01T00:00:00Z', 'note': 'a'})   # far future: refused, so use a real one
    dm.post(f'/campaigns/{cid}/session', data={'starts_at': _iso(), 'note': 'Bring snacks; dice, please'})
    r = pl.get(f'/campaigns/{cid}/session.ics')
    assert r.status_code == 200 and r.mimetype == 'text/calendar'
    body = r.data.decode()
    assert body.startswith('BEGIN:VCALENDAR\r\n') and 'BEGIN:VEVENT' in body and 'DTSTART:' in body and 'DTEND:' in body
    assert r'DESCRIPTION:Bring snacks\; dice\, please' in body
    assert outsider.get(f'/campaigns/{cid}/session.ics').status_code == 404
