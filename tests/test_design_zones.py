"""DL-180 part 30: two design systems, never mixed.

Outside a campaign (campaign list, new campaign, account, profile, friends, notifications, invite link, your characters, the rules
reference) every page is built on site_base.html: the home page's header, painted stage and footer, no sidebar.  Inside a campaign every page
is built on base.html: the sidebar shell, no public header.  The pages that exist in both places (the reference, the character form, the
campaign form) take whichever shell the address says."""
import re

import pytest

from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _body_class(html):
    m = re.search(r'<body class="([^"]*)"', html)
    return m.group(1).split() if m else []


def _site_pages(u, cid_char):
    return ['/campaigns', '/campaigns/new', '/account', '/friends', '/notifications', f'/u/{u.name}', '/library', '/library/new',
            f'/library/{cid_char}', f'/library/{cid_char}/edit', '/reference', '/join/not-a-real-token']


def _campaign_pages(cid):
    return [f'/campaigns/{cid}/{p}' for p in ('home', 'characters', 'characters/new', 'edit', 'factions', 'battle', 'maps', 'dice',
                                              'journal', 'activity', 'treasury', 'reference')]


@pytest.fixture
def world(make_user):
    u = make_user()
    cid = u.new_campaign()
    r = u.post('/library/new', data={'name': 'Kept Aria', 'level': '1', 'max_hp': '9', 'armor_class': '12'}, content_type='multipart/form-data')
    assert r.status_code == 302, r.status_code
    char = r.headers['Location'].rstrip('/').split('/')[-1]
    return u, cid, char


def test_every_page_outside_a_campaign_is_the_home_pages_design(world):
    u, _cid, char = world
    for path in _site_pages(u, char):
        r = u.get(path)
        assert r.status_code == (404 if path.startswith('/join/') else 200), (path, r.status_code)       # a dead invite link answers 404 inside the site's design
        html = r.get_data(as_text=True)
        assert _body_class(html) == ['pub', 'site'], path
        assert 'class="pub-header"' in html and 'class="pub-footer"' in html, path
        assert 'css/site.css' in html and 'css/public.css' in html, path
        assert 'app-shell' not in html and 'class="sidebar"' not in html and 'sidebar-nav' not in html, path    # nothing of the campaign shell
        assert html.count('<h1') == 1, path
        assert ' style="' not in html, path


def test_every_page_inside_a_campaign_keeps_the_campaign_design(world):
    u, cid, _char = world
    for path in _campaign_pages(cid):
        r = u.get(path)
        assert r.status_code == 200, (path, r.status_code)
        html = r.get_data(as_text=True)
        assert _body_class(html) == [], path
        assert 'class="app-shell"' in html and 'class="sidebar"' in html, path
        assert 'pub-header' not in html and 'pub-footer' not in html, path                            # nothing of the public shell
        assert 'css/site.css' not in html and 'css/public.css' not in html, path
        assert 'campaign-switcher' in html, path


def test_the_site_header_marks_where_you_are(world):
    u, _cid, char = world
    for path, label in (('/campaigns', 'Campaigns'), ('/library', 'Your characters'), (f'/library/{char}', 'Your characters'),
                        ('/reference', 'Reference'), ('/notifications', 'Notifications')):
        html = u.get(path).get_data(as_text=True)
        marked = re.findall(r'<a href="[^"]*" aria-current="page">([^<]+)', html)
        assert [m.strip() for m in marked if m.strip() in ('Campaigns', 'Your characters', 'Reference', 'Notifications')] == [label], path


def test_the_site_header_has_the_theme_switch_and_the_theme_script(world):
    u, _cid, _char = world
    html = u.get('/campaigns').get_data(as_text=True)
    assert 'id="theme-toggle"' in html and 'js/theme.js' in html
    assert 'pub-theme-sun' in html and 'pub-theme-moon' in html


def test_a_visitor_still_gets_the_public_header_without_the_signed_in_extras(make_user, appmod):
    html = appmod.app.test_client().get('/').get_data(as_text=True)
    assert 'id="theme-toggle"' not in html and 'Get started' in html


def test_the_forms_that_live_in_both_places_follow_the_address(world):
    u, cid, _char = world
    inside = u.get(f'/campaigns/{cid}/reference').get_data(as_text=True)
    outside = u.get('/reference').get_data(as_text=True)
    assert 'class="app-shell"' in inside and 'pub-header' not in inside
    assert 'pub-header' in outside and 'app-shell' not in outside
    inside = u.get(f'/campaigns/{cid}/edit').get_data(as_text=True)
    outside = u.get('/campaigns/new').get_data(as_text=True)
    assert 'app-shell' in inside and 'pub-header' not in inside
    assert 'pub-header' in outside and 'app-shell' not in outside
    inside = u.get(f'/campaigns/{cid}/characters/new').get_data(as_text=True)
    outside = u.get('/library/new').get_data(as_text=True)
    assert 'app-shell' in inside and 'pub-header' not in inside
    assert 'pub-header' in outside and 'app-shell' not in outside


def test_site_css_is_scoped_to_the_site():
    css = re.sub(r'/\*.*?\*/', '', open('static/css/site.css', encoding='utf-8').read(), flags=re.S)
    for prelude in re.findall(r'([^{}]+)\{', css):
        prelude = prelude.strip()
        if prelude.startswith('@') or prelude in ('from', 'to'):
            continue
        for part in prelude.split(','):
            part = part.strip()
            assert part.startswith(('.site-page', 'body.site')), part          # never a bare selector that could reach a campaign page
