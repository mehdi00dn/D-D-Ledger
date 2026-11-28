"""DL-109: page addresses use a short random code (/campaigns/3f9a0c7be214) instead of the integer key; old numeric addresses
keep working, and a browser that opens one is sent to the short form once the page itself was allowed."""
import re

import pytest
import pubid
from conftest import q, BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only')

BROWSER = {'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8'}
CODE = re.compile(r'^[0-9a-f]{12}$')
TABLES = ('campaigns', 'characters', 'maps', 'session_journal', 'quests')


def _world(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, pl)
    ch = dm.new_character(cid, 'Mira')
    mid = dm.new_map(cid, 'Cave')
    dm.post(f'/campaigns/{cid}/quests/add', data={'title': 'Find it'})
    qid = q('SELECT id FROM quests WHERE campaign_id = ?', cid)[0]['id']
    dm.post(f'/campaigns/{cid}/journal/new', data={'title': 'Night one', 'played_on': '2026-10-31', 'body': '["We crossed."]'})
    jid = q('SELECT id FROM session_journal WHERE campaign_id = ?', cid)[0]['id']
    return dm, pl, cid, ch, mid, qid, jid


def test_every_row_has_a_unique_code_that_cannot_be_read_as_a_number(make_user):
    _world(make_user)
    for table in TABLES:
        rows = q(f'SELECT short_id FROM {table}')
        codes = [r['short_id'] for r in rows]
        assert codes and all(CODE.match(c) and not c.isdigit() for c in codes), table
        assert len(codes) == len(set(codes)), table


def test_new_pages_redirect_to_the_short_address(make_user):
    dm = make_user()
    r = dm.post('/campaigns/new', data={'name': 'Short'})
    cid = pubid.pk('campaigns', r.headers['Location'])
    assert f'/campaigns/{pubid.short("campaigns", cid)}/' in r.headers['Location']
    assert not re.search(r'/campaigns/\d+/', r.headers['Location'])


def test_both_forms_open_the_page_and_a_browser_is_moved_to_the_short_one(make_user):
    dm, pl, cid, ch, mid, qid, jid = _world(make_user)
    code, ccode = pubid.short('campaigns', cid), pubid.short('characters', ch)
    assert dm.get(f'/campaigns/{code}/characters/{ccode}').status_code == 200
    assert dm.get(f'/campaigns/{cid}/characters/{ch}').status_code == 200          # scripts and old links without an HTML Accept
    r = dm.get(f'/campaigns/{cid}/characters/{ch}', headers=BROWSER)
    assert r.status_code == 302 and r.headers['Location'] == f'/campaigns/{code}/characters/{ccode}'
    assert dm.get(f'/campaigns/{code}/characters/{ccode}', headers=BROWSER).status_code == 200      # no loop on the short form
    r = dm.get(f'/campaigns/{cid}/journal/{jid}/edit?from=list', headers=BROWSER)
    assert r.headers['Location'] == f'/campaigns/{code}/journal/{pubid.short("journal", jid)}/edit?from=list'   # query kept
    assert dm.get(f'/campaigns/{cid}/maps/{mid}', headers=BROWSER).headers['Location'].endswith(pubid.short('maps', mid))


def test_scripts_and_fetch_calls_keep_working_on_numbers_without_a_redirect(make_user):
    dm, pl, cid, ch, mid, qid, jid = _world(make_user)
    r = dm.get(f'/campaigns/{cid}/api/characters', headers={'Accept': 'application/json'})     # what the page scripts send
    assert r.status_code == 200
    assert dm.get(f'/campaigns/{cid}/api/maps/{mid}/sync').status_code == 200
    assert dm.json(f'/campaigns/{cid}/api/battle/add', {'character_id': ch}).status_code == 200
    assert dm.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'done'}).status_code == 302
    assert dm.post(f'/campaigns/{pubid.short("campaigns", cid)}/quests/{pubid.short("quests", qid)}', data={'action': 'reopen'}).status_code == 302


def test_the_api_answers_the_same_on_a_code_and_on_a_number(make_user):
    dm, pl, cid, ch, mid, qid, jid = _world(make_user)
    code = pubid.short('campaigns', cid)
    assert dm.get(f'/campaigns/{cid}/api/characters').get_json() == dm.get(f'/campaigns/{code}/api/characters').get_json()


def test_a_number_never_gives_a_stranger_the_code(make_user):
    dm, pl, cid, ch, mid, qid, jid = _world(make_user)
    stranger = make_user()
    for path in (f'/campaigns/{cid}/characters', f'/campaigns/{cid}/characters/{ch}', f'/campaigns/{cid}/home'):
        r = stranger.get(path, headers=BROWSER)
        assert r.status_code in (403, 404), path
        assert pubid.short('campaigns', cid) not in (r.headers.get('Location') or '') + r.get_data(as_text=True), path
    import app as appmod
    c = appmod.app.test_client()
    r = c.get(f'/campaigns/{cid}/home', headers=BROWSER)
    assert r.status_code == 302 and '/login' in r.headers['Location'] and pubid.short('campaigns', cid) not in r.headers['Location']


def test_unknown_or_mismatched_codes_are_a_404(make_user):
    dm, pl, cid, ch, mid, qid, jid = _world(make_user)
    code, ccode = pubid.short('campaigns', cid), pubid.short('characters', ch)
    for bad in ('000000000000', 'ffffffffffff', 'ABCDEF123456', 'abc', 'abcdef1234567', ccode):      # unknown, upper case, wrong length, a character's code as a campaign
        assert dm.get(f'/campaigns/{bad}/home').status_code == 404, bad
    assert dm.get(f'/campaigns/{code}/characters/{code}').status_code == 404                           # a campaign's code as a character
    assert dm.get(f'/campaigns/{code}/maps/{ccode}').status_code == 404                                # a character's code as a map


def test_a_code_from_another_campaign_does_not_open_anything(make_user):
    dm, pl, cid, ch, mid, qid, jid = _world(make_user)
    other = make_user(); ocid = other.new_campaign(); och = other.new_character(ocid, 'Elsewhere')
    assert dm.get(f'/campaigns/{cid}/characters/{pubid.short("characters", och)}').status_code == 404
    assert dm.get(f'/campaigns/{pubid.short("campaigns", ocid)}/characters').status_code in (403, 404)


def test_url_for_builds_the_short_form_and_falls_back_for_a_row_that_is_gone(make_user):
    import app as appmod
    import public_ids
    dm, pl, cid, ch, mid, qid, jid = _world(make_user)
    with appmod.app.test_request_context('/'):
        from flask import url_for
        assert url_for('characters_list', campaign_id=cid).endswith(pubid.short('campaigns', cid) + '/characters')
        assert url_for('characters_list', campaign_id=99999999) == '/campaigns/99999999/characters'
    public_ids.clear_cache()
    with appmod.app.test_request_context('/'):
        from flask import url_for
        assert pubid.short('campaigns', cid) in url_for('characters_list', campaign_id=cid)          # found again after the cache is emptied


def test_the_old_integer_keys_and_foreign_keys_are_untouched(make_user):
    dm, pl, cid, ch, mid, qid, jid = _world(make_user)
    assert q('SELECT campaign_id FROM characters WHERE id = ?', ch)[0]['campaign_id'] == cid
    assert q('SELECT campaign_id FROM maps WHERE id = ?', mid)[0]['campaign_id'] == cid
    assert q('SELECT public_id::text AS u FROM campaigns WHERE id = ?', cid)[0]['u'].count('-') == 4      # the UUID from 0004 is still there
