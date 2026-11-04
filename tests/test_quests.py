"""DL-151: the quest list on the campaign Home page - the DM keeps it, Players read the quests that are not hidden."""
import pytest
import pubid
from conftest import q, BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only')


def _setup(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, pl)
    return dm, pl, cid


def _home(user, cid):
    return user.get(f'/campaigns/{cid}/home').data.decode()


def test_dm_adds_finishes_hides_and_deletes_and_players_only_read(make_user):
    dm, pl, cid = _setup(make_user)
    assert 'No quests yet' in _home(dm, cid) and 'Quests' not in _home(pl, cid)                 # nothing to show a Player yet
    assert pl.post(f'/campaigns/{cid}/quests/add', data={'title': 'Sneaky'}).status_code == 403
    assert dm.post(f'/campaigns/{cid}/quests/add', data={'title': '   '}).status_code == 400
    dm.post(f'/campaigns/{cid}/quests/add', data={'title': 'Find the  caravan', 'details': 'Last seen near the ford'})
    dm.post(f'/campaigns/{cid}/quests/add', data={'title': 'The vizier is a lich', 'hidden': '1'})
    qid, secret = [r['id'] for r in q('SELECT id FROM quests WHERE campaign_id = ? ORDER BY id', cid)]
    assert 'Find the caravan' in _home(pl, cid) and 'Last seen near the ford' in _home(pl, cid)
    assert 'lich' not in _home(pl, cid) and 'lich' in _home(dm, cid)                            # hidden quests are the DM's alone
    assert pl.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'done'}).status_code == 403
    dm.post(f'/campaigns/{cid}/quests/{secret}', data={'action': 'show'})
    assert 'lich' in _home(pl, cid)
    dm.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'done'})
    assert q('SELECT done FROM quests WHERE id = ?', qid)[0]['done'] is True and 'Done' in _home(pl, cid)
    dm.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'reopen'})
    assert q('SELECT done FROM quests WHERE id = ?', qid)[0]['done'] is False
    assert dm.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'explode'}).status_code == 400
    dm.post(f'/campaigns/{cid}/quests/{secret}', data={'action': 'delete'})
    assert len(q('SELECT id FROM quests WHERE campaign_id = ?', cid)) == 1


def test_a_quest_of_another_campaign_cannot_be_touched(make_user):
    dm, pl, cid = _setup(make_user)
    other = dm.new_campaign()
    dm.post(f'/campaigns/{other}/quests/add', data={'title': 'Elsewhere'})
    qid = q('SELECT id FROM quests WHERE campaign_id = ?', other)[0]['id']
    assert dm.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'delete'}).status_code == 404
    assert len(q('SELECT id FROM quests WHERE id = ?', qid)) == 1


def test_dm_edits_a_quest_and_players_cannot(make_user):
    dm, pl, cid = _setup(make_user)
    dm.post(f'/campaigns/{cid}/quests/add', data={'title': 'Find the caravan', 'details': 'Seen near the ford', 'hidden': '1'})
    qid = q('SELECT id FROM quests WHERE campaign_id = ?', cid)[0]['id']
    assert pl.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'edit', 'title': 'Hacked'}).status_code == 403
    assert dm.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'edit', 'title': '   '}).status_code == 400
    assert dm.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'edit', 'title': 'Find the  caravan  at dawn', 'details': ''}).status_code == 302
    row = q('SELECT title, details, hidden FROM quests WHERE id = ?', qid)[0]
    assert row['title'] == 'Find the caravan at dawn' and row['details'] is None and row['hidden'] is True   # editing never reveals a hidden quest
    assert 'Find the caravan at dawn' not in _home(pl, cid)
    dm.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'edit', 'title': 'x' * 300, 'details': 'y' * 900})
    row = q('SELECT title, details FROM quests WHERE id = ?', qid)[0]
    assert len(row['title']) == 120 and len(row['details']) == 600                                           # same limits as adding
    assert f'name="title" maxlength="120" required value="' in _home(dm, cid)                               # the form is on the page for the DM


def test_a_quest_of_another_campaign_cannot_be_edited(make_user):
    dm, pl, cid = _setup(make_user)
    other = make_user(); ocid = other.new_campaign()
    other.post(f'/campaigns/{ocid}/quests/add', data={'title': 'Theirs'})
    oqid = q('SELECT id FROM quests WHERE campaign_id = ?', ocid)[0]['id']
    assert dm.post(f'/campaigns/{cid}/quests/{oqid}', data={'action': 'edit', 'title': 'Mine now'}).status_code == 404
    assert q('SELECT title FROM quests WHERE id = ?', oqid)[0]['title'] == 'Theirs'


def test_quest_buttons_are_icons_with_names_and_players_get_none(make_user):
    dm, pl, cid = _setup(make_user)
    dm.post(f'/campaigns/{cid}/quests/add', data={'title': 'Find the caravan'})
    html = _home(dm, cid)
    for name in ('Mark done: Find the caravan', 'Hide from players: Find the caravan', 'Edit: Find the caravan'):
        assert f'aria-label="{name}"' in html, name
    assert '>Mark done<' not in html and '>Hide<' not in html and 'Edit</summary>' not in html     # icon only
    qid = q('SELECT id FROM quests WHERE campaign_id = ?', cid)[0]['id']
    dm.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'done'})
    dm.post(f'/campaigns/{cid}/quests/{qid}', data={'action': 'hide'})
    html = _home(dm, cid)
    assert 'aria-label="Reopen: Find the caravan"' in html and 'aria-label="Show to players: Find the caravan"' in html
    assert 'quest-btn' not in _home(pl, cid)


def test_party_tile_and_character_card_name_link_to_the_page(make_user):
    dm = make_user(); cid = dm.new_campaign()
    dm.post(f'/campaigns/{cid}/characters/new', data={'name': 'Mira', 'max_hp': '8'})
    cid_char = q('SELECT id FROM characters WHERE campaign_id = ?', cid)[0]['id']
    assert f'class="dossier-name-link" href="/campaigns/%s/characters/%s"' % (pubid.short('campaigns', cid), pubid.short('characters', cid_char)) in dm.get(f'/campaigns/{cid}/characters').data.decode()
