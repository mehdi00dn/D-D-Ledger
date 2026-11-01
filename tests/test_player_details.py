"""DL-97: optional player details (favourite classes, play style, timezone) on the profile, with their own visibility."""
import pytest
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _id(u):
    return q('SELECT id FROM users WHERE username = ?', u.name)[0]['id']


def _save(c, **form):
    base = {'display_name': '', 'bio': '', 'profile_visibility': 'members', 'details_present': '1'}
    base.update(form)
    return c.post('/account', data=base)


def _befriend(a, b):
    a.post(f'/friends/{b.name}/request'); b.post(f'/friends/{a.name}/accept')


def test_details_are_saved_and_shown_with_counts(make_user):
    a, b = make_user(), make_user()
    r = _save(a, favourite_classes=['wizard', 'rogue'], play_style='roleplay', timezone='Europe/London')
    assert r.status_code == 302
    row = q('SELECT favourite_classes, play_style, timezone FROM users WHERE id = ?', _id(a))[0]
    assert (row['favourite_classes'], row['play_style'], row['timezone']) == ('wizard,rogue', 'roleplay', 'Europe/London')
    page = b.get(f'/u/{a.name}').get_data(as_text=True)
    assert 'As a player' in page and 'Wizard' in page and 'Roleplay and story' in page and 'Europe/London' in page
    assert 'Player characters' in page


def test_bad_values_are_refused(make_user):
    a = make_user()
    assert _save(a, favourite_classes=['not-a-class']).status_code == 400
    assert _save(a, favourite_classes=['wizard', 'rogue', 'bard', 'monk']).status_code == 400
    assert _save(a, play_style='chaos').status_code == 400
    assert _save(a, timezone='<script>').status_code == 400
    assert q('SELECT favourite_classes FROM users WHERE id = ?', _id(a))[0]['favourite_classes'] is None


def test_details_visibility_is_enforced_on_the_server(make_user):
    a, friend, stranger = make_user(), make_user(), make_user()
    _befriend(a, friend)
    _save(a, play_style='combat', details_visibility='friends')
    assert 'Combat and tactics' in friend.get(f'/u/{a.name}').get_data(as_text=True)
    assert 'Combat and tactics' not in stranger.get(f'/u/{a.name}').get_data(as_text=True)
    assert 'As a player' not in stranger.get(f'/u/{a.name}').get_data(as_text=True)
    _save(a, play_style='combat', details_visibility='private')
    assert 'Combat and tactics' not in friend.get(f'/u/{a.name}').get_data(as_text=True)
    assert 'Combat and tactics' in a.get(f'/u/{a.name}').get_data(as_text=True)


def test_clearing_details_and_the_download(make_user):
    a = make_user()
    _save(a, play_style='balanced', timezone='Asia/Tehran')
    assert 'Asia/Tehran' in a.post('/account/export').get_data(as_text=True)
    _save(a)
    row = q('SELECT favourite_classes, play_style, timezone FROM users WHERE id = ?', _id(a))[0]
    assert row['favourite_classes'] is None and row['play_style'] is None and row['timezone'] is None


def test_a_form_without_the_details_block_leaves_them_alone(make_user):
    a = make_user()
    _save(a, play_style='combat')
    a.post('/account', data={'display_name': 'X', 'bio': '', 'profile_visibility': 'members'})
    assert q('SELECT play_style FROM users WHERE id = ?', _id(a))[0]['play_style'] == 'combat'
