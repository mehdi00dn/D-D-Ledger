"""DL-155: character cards and campaign covers."""
import pytest
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only')


def _card(html, name):
    return html.split(name, 1)[1].split('dossier-card', 1)[0]


def test_cards_show_class_art_live_hp_and_conditions_and_skip_untouched_scores(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, pl)
    hero = dm.new_character(cid, name='Kael Thorne', class_key='fighter', max_hp='40')
    plain = dm.new_character(cid, name='Plain Jane')
    html = dm.get(f'/campaigns/{cid}/characters').data.decode()
    assert 'classes.svg#fighter' in html and 'avatar-initial' in html     # class art, else the initial
    assert 'class="dossier-stats"' not in html and 'dossier-live' not in html   # all-10 scores hidden, nobody in battle
    pid = dm.add_to_battle(cid, hero)
    dm.json(f'/campaigns/{cid}/api/battle/{pid}/damage', {'amount': 30})
    dm.json(f'/campaigns/{cid}/api/battle/{pid}/conditions', {'conditions': ['prone'], 'custom_conditions': ['Hexed']})
    for who in (dm, pl):
        html = who.get(f'/campaigns/{cid}/characters').data.decode()
        assert '10 / 40 HP' in html and 'is-low' in html and 'Prone' in html and 'Hexed' in html


def test_a_players_cards_never_carry_a_monsters_numbers(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, pl)
    ogre = dm.new_character(cid, name='Ogre', is_npc='on', max_hp='59', str_score='19')
    dm.add_to_battle(cid, ogre)
    assert '59' in dm.get(f'/campaigns/{cid}/characters').data.decode()
    assert 'dossier-live' not in pl.get(f'/campaigns/{cid}/characters').data.decode()
    assert 'dossier-live' in dm.get(f'/campaigns/{cid}/characters').data.decode()


def test_campaign_without_an_image_gets_a_monogram_cover(make_user):
    dm = make_user()
    dm.post('/campaigns/new', data={'name': 'Ashfall Chronicles'})
    html = dm.get('/campaigns').data.decode()
    assert 'cover-monogram' in html and '>AC<' in html and 'is-generated' in html
