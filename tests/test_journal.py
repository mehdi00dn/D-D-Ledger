"""DL-121: the session journal - the DM writes recaps (some boxes hidden), members read what is shared."""
import json
import pubid
import pytest
from conftest import q, BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only')


def _setup(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, pl)
    return dm, pl, cid


def _body(*boxes):
    return json.dumps([{'t': t, 'h': 1} if h else t for t, h in boxes])


def _add(dm, cid, title='The bridge', boxes=(('We crossed the bridge.', False),), date='2026-10-31'):
    r = dm.post(f'/campaigns/{cid}/journal/new', data={'title': title, 'played_on': date, 'body': _body(*boxes)})
    assert r.status_code == 302, r.data.decode()[:300]
    return q('SELECT id FROM session_journal WHERE campaign_id = ? ORDER BY id DESC', cid)[0]['id']


def _get(u, url):
    return u.get(url)


def test_dm_writes_and_everyone_reads_the_shared_notes(make_user):
    dm, pl, cid = _setup(make_user)
    jid = _add(dm, cid, boxes=(('We crossed the bridge.', False), ('The vizier is a lich.', True)))
    page = pl.get(f'/campaigns/{cid}/journal/{jid}').data.decode()
    assert 'We crossed the bridge.' in page and 'lich' not in page                               # a hidden box never reaches a player
    assert 'lich' in dm.get(f'/campaigns/{cid}/journal/{jid}').data.decode()
    assert 'The bridge' in pl.get(f'/campaigns/{cid}/journal').data.decode()
    assert 'lich' not in pl.get(f'/campaigns/{cid}/journal').data.decode()                       # nor in the list preview
    assert 'New session' not in pl.get(f'/campaigns/{cid}/journal').data.decode()


def test_an_entry_with_only_hidden_notes_does_not_exist_for_players(make_user):
    dm, pl, cid = _setup(make_user)
    jid = _add(dm, cid, title='Plans', boxes=(('Next: the ambush.', True),))
    assert pl.get(f'/campaigns/{cid}/journal/{jid}').status_code == 404
    assert 'Plans' not in pl.get(f'/campaigns/{cid}/journal').data.decode()
    listing = dm.get(f'/campaigns/{cid}/journal').data.decode()
    assert 'Plans' in listing and 'DM only' in listing


def test_only_the_dm_can_write_edit_or_delete(make_user):
    dm, pl, cid = _setup(make_user)
    jid = _add(dm, cid)
    data = {'title': 'x', 'played_on': '2026-10-31', 'body': _body(('x', False))}
    assert pl.post(f'/campaigns/{cid}/journal/new', data=data).status_code == 403
    assert pl.get(f'/campaigns/{cid}/journal/new').status_code == 403
    assert pl.post(f'/campaigns/{cid}/journal/{jid}/edit', data=data).status_code == 403
    assert pl.post(f'/campaigns/{cid}/journal/{jid}/delete').status_code == 403
    assert dm.post(f'/campaigns/{cid}/journal/{jid}/edit', data=dict(data, title='Renamed')).status_code == 302
    assert q('SELECT title FROM session_journal WHERE id = ?', jid)[0]['title'] == 'Renamed'
    assert dm.post(f'/campaigns/{cid}/journal/{jid}/delete').status_code == 302
    assert q('SELECT id FROM session_journal WHERE id = ?', jid) == []


def test_bad_forms_are_refused_and_html_is_sanitised(make_user):
    dm, pl, cid = _setup(make_user)
    ok = {'title': 'T', 'played_on': '2026-10-31', 'body': _body(('hello', False))}
    for bad in ({'title': '  '}, {'title': '<b>x</b>'}, {'title': 'x' * 121}, {'played_on': 'yesterday'}, {'body': '[]'}, {'body': ''}):
        assert dm.post(f'/campaigns/{cid}/journal/new', data=dict(ok, **bad)).status_code == 400, bad
    assert q('SELECT id FROM session_journal WHERE campaign_id = ?', cid) == []
    jid = _add(dm, cid, boxes=(('<script>alert(1)</script><b>ok</b>', False),))
    page = pl.get(f'/campaigns/{cid}/journal/{jid}').data.decode()
    assert '<script>alert(1)' not in page and '<b>ok</b>' in page


def test_an_entry_of_another_campaign_is_not_reachable(make_user):
    dm, pl, cid = _setup(make_user)
    other = make_user(); ocid = other.new_campaign()
    jid = _add(other, ocid)
    assert dm.get(f'/campaigns/{cid}/journal/{jid}').status_code == 404
    assert dm.post(f'/campaigns/{cid}/journal/{jid}/delete').status_code == 404
    assert q('SELECT id FROM session_journal WHERE id = ?', jid)


def test_a_new_shared_recap_is_in_the_activity_feed_but_a_secret_one_is_not(make_user):
    dm, pl, cid = _setup(make_user)
    _add(dm, cid, title='Shared one')
    _add(dm, cid, title='Secret one', boxes=(('shh', True),))
    feed = pl.get(f'/campaigns/{cid}/activity').data.decode()
    assert 'Shared one' in feed and 'Secret one' not in feed


def test_list_groups_by_month_and_gives_the_dm_edit_and_delete_on_each_row(make_user):
    dm, pl, cid = _setup(make_user)
    a = _add(dm, cid, title='Old one', date='2026-09-12')
    b = _add(dm, cid, title='New one', boxes=(('Shared', False), ('Secret', True)), date='2026-10-31')
    page = dm.get(f'/campaigns/{cid}/journal').data.decode()
    assert page.index('October 2026') < page.index('New one') < page.index('September 2026') < page.index('Old one')
    assert f'href="/campaigns/%s/journal/%s/edit?from=list"' % (pubid.short('campaigns', cid), pubid.short('journal', b)) in page and 'aria-label="Edit New one"' in page
    assert f'action="/campaigns/%s/journal/%s/delete"' % (pubid.short('campaigns', cid), pubid.short('journal', a)) in page and 'aria-label="Delete Old one"' in page
    assert '2 notes' in page and '1 note<' in page
    pl_page = pl.get(f'/campaigns/{cid}/journal').data.decode()
    assert 'journal-actions' not in pl_page and '/journal/' + str(b) + '/edit' not in pl_page and '1 note<' in pl_page     # a player counts only the shared boxes


def test_editing_from_the_list_returns_to_the_list(make_user):
    dm, pl, cid = _setup(make_user)
    jid = _add(dm, cid)
    url = f'/campaigns/{cid}/journal/{jid}/edit'
    data = {'title': 'Renamed', 'played_on': '2026-10-31', 'body': _body(('We crossed.', False))}
    assert dm.post(url + '?from=list', data=data).headers['Location'].endswith(f'/campaigns/%s/journal' % pubid.short('campaigns', cid))
    assert dm.post(url, data=data).headers['Location'].endswith(f'/journal/%s' % pubid.short('journal', jid))
    assert 'href="/campaigns/%s/journal"' % pubid.short('campaigns', cid) in dm.get(url + '?from=list').data.decode()
