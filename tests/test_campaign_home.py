"""DL-144: the front page inside a campaign."""
import pytest
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only')


def test_home_shows_party_battle_activity_and_rolls(make_user):
    dm = make_user(); cid = dm.new_campaign()
    hero = dm.new_character(cid, name='Kael Thorne', level=3, max_hp=24, armor_class=16)
    dm.new_character(cid, name='Goblin Scout', is_npc='on')
    html = dm.get(f'/campaigns/{cid}/home').data.decode()
    assert 'Kael Thorne' in html and 'Lvl 3' in html and '24 HP' in html
    assert 'Goblin Scout' not in html.split('The party')[1].split('Battle')[0]      # the party is player characters only
    assert 'No battle running' in html
    dm.add_to_battle(cid, hero)
    html = dm.get(f'/campaigns/{cid}/home').data.decode()
    assert 'Round 1' in html and '1 on the field' in html
    dm.json(f'/campaigns/{cid}/api/dice/roll', {'dice': {'20': 1}, 'modifier': 3, 'label': 'Perception', 'character_id': hero})
    assert 'Perception' in dm.get(f'/campaigns/{cid}/home').data.decode()


def test_opening_the_site_lands_on_home(make_user):
    dm = make_user(); cid = dm.new_campaign()
    dm.get(f'/campaigns/{cid}/home')                      # remembers the campaign
    r = dm.get('/')
    assert r.status_code == 302 and r.headers['Location'].endswith(f'/campaigns/{cid}/home')


def test_home_shows_the_bio_and_the_player_list(make_user):
    dm = make_user(); cid = dm.new_campaign()
    html = dm.get(f'/campaigns/{cid}/home').data.decode()
    assert 'class="home-bio"' in html                       # new_campaign saves description 'd'
    assert 'aria-label="Players"' in html and 'role-badge-dm' in html
    assert 'home-banner' not in html                        # no image uploaded, so no empty frame
