"""DL-177/DL-179: the home page at /, for visitors and for signed-in people (who get the app menu in its header)."""
import re

import pytest

from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


@pytest.fixture
def visitor(appmod):
    return appmod.app.test_client()


def test_a_visitor_sees_the_landing_page(visitor):
    r = visitor.get('/')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert '<title>Oaken' in html
    assert 'Your world. Your party. Your adventure.' in html
    for heading in ('Every campaign has a story. Keep yours together.', 'More than a character sheet.',
                    'Less time managing the encounter. More time in the adventure.', 'Give your adventures a place to unfold.',
                    'Everything else your table needs.', 'Start with a campaign. Build from there.', 'Your next adventure starts here.'):
        assert heading in html


def test_every_button_goes_somewhere_real(visitor):
    html = visitor.get('/').get_data(as_text=True)
    hrefs = set(re.findall(r'href="([^"]+)"', html))
    for want in ('/register', '/login', '/about', '/contact', '/privacy', '/terms', '/cookies', '#features', '#how-it-works'):
        assert any(h == want or h.startswith(want + '?') for h in hrefs) or want in html, want
    assert 'href="#"' not in html and 'href=""' not in html
    for h in hrefs:                                                               # internal links all answer for a visitor
        if h.startswith('/') and not h.startswith('/static/'):
            code = visitor.get(h.split('#')[0]).status_code
            assert code == 200, (h, code)
    for anchor in ('features', 'how-it-works', 'main'):                           # and every in-page link has its target
        assert f'id="{anchor}"' in html


def test_one_primary_label_and_the_cta_links_to_registration(visitor):
    html = visitor.get('/').get_data(as_text=True)
    ctas = re.findall(r'<a class="pub-btn pub-btn--primary[^"]*" href="([^"]+)">([^<]+)</a>', html)
    assert len(ctas) >= 3 and {label for _h, label in ctas} == {'Get started'}
    assert {h for h, _l in ctas} == {'/register'}


def test_page_follows_the_content_security_policy(visitor):
    r = visitor.get('/')
    html = r.get_data(as_text=True)
    assert ' style="' not in html and '<style' not in html                        # no inline styles: the CSP forbids them
    csp = r.headers['Content-Security-Policy']
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    for tag in re.findall(r'<script\b([^>]*)>', html):                            # inline scripts must carry the nonce
        assert 'src=' in tag or f'nonce="{nonce}"' in tag
    assert 'onclick=' not in html and 'javascript:' not in html


def test_headings_in_order_and_images_have_alt_or_are_hidden(visitor):
    html = visitor.get('/').get_data(as_text=True)
    assert html.count('<h1') == 1
    levels = [int(n) for n in re.findall(r'<h([1-6])\b', html)]
    assert all(b - a <= 1 for a, b in zip(levels, levels[1:]))                    # never skips a level going down
    for img in re.findall(r'<img\b[^>]*>', html):
        assert 'alt=' in img
    assert 'aria-label="Oaken, home"' in html and 'Skip to content' in html


def test_empty_frames_name_their_slot(visitor):
    html = visitor.get('/').get_data(as_text=True)
    slots = re.findall(r'data-slot="(IMG-[0-9a-c]+)"', html)
    assert {'IMG-01', 'IMG-02', 'IMG-03', 'IMG-04', 'IMG-05', 'IMG-06'} <= set(slots)
    assert all('aria-hidden="true"' in f for f in re.findall(r'<figure[^>]*data-slot[^>]*>', html))


def test_signed_in_people_get_the_same_page_with_the_app_menu(make_user):
    """DL-179: / is the home page of the whole site; signed in, the header carries the out-of-campaign menu."""
    u = make_user(); cid = u.new_campaign('Frostmaiden')
    r = u.get('/')
    html = r.data.decode()
    assert r.status_code == 200 and 'Your world. Your party. Your adventure.' in html
    nav = html[html.index('<nav class="pub-menu"'):html.index('</nav>')]
    for path in ('/campaigns', '/library', '/reference', '/notifications', '/account'):
        assert f'href="{path}' in nav, path
    assert 'action="/logout"' in nav and 'name="csrf_token"' in nav
    assert 'href="/register"' not in html and 'Get started' not in html and 'Log in' not in html
    for path in ('/campaigns', '/library', '/reference', '/notifications', '/account'):
        assert u.get(path).status_code == 200, path


def test_the_main_button_resumes_or_opens_the_list(make_user):
    u = make_user()
    assert 'Open your campaigns' in u.get('/').data.decode()                      # no campaign yet
    cid = u.new_campaign('Frostmaiden')
    assert 'Continue to Frostmaiden' in u.get('/').data.decode()                  # the only campaign
    u.new_campaign('Second')
    u.get(f'/campaigns/{cid}/home')
    assert 'Continue to Frostmaiden' in u.get('/').data.decode()                  # the remembered one
    other = make_user()
    assert 'Frostmaiden' not in other.get('/').data.decode()                      # never someone else's campaign


def test_the_unread_count_shows_in_the_header(make_user):
    import database
    u = make_user()
    assert 'class="pub-badge"' not in u.get('/').data.decode()
    db = database.get_db()
    try:
        uid = db.execute('SELECT id FROM users WHERE username = ?', (u.name,)).fetchone()['id']
        db.execute("INSERT INTO notifications (user_id, kind, title, body, link) VALUES (?, 'info', 'Hello', 'b', '/campaigns')", (uid,))
        db.commit()
    finally:
        db.close()
    html = u.get('/').data.decode()
    assert 'class="pub-badge" aria-label="1 unread"' in html


def test_a_signed_out_session_is_a_visitor_again(make_user):
    u = make_user()
    assert 'Log out' in u.get('/').data.decode()
    u.get('/logout'); u.c.post('/logout')
    html = u.get('/').data.decode()
    assert 'Get started' in html and 'Log out' not in html


def test_the_sidebar_links_back_to_home_outside_a_campaign(make_user):
    u = make_user(); cid = u.new_campaign()
    out = u.get('/campaigns').data.decode()
    assert 'class="brand-link" href="/"' in out and 'class="nav-tab nav-home"' in out
    assert 'nav-home' not in u.get(f'/campaigns/{cid}/home').data.decode()         # inside a campaign the tabs are the campaign's


def test_landing_assets_are_served(visitor):
    for path in ('/static/css/public.css', '/static/css/landing.css', '/static/js/public.js'):
        assert visitor.get(path).status_code == 200
    css = visitor.get('/static/css/landing.css').get_data(as_text=True)
    for font in re.findall(r"url\('\.\./fonts/([^']+)'\)", css):
        assert visitor.get('/static/fonts/' + font).status_code == 200, font
    for path in re.findall(r'href="(/static/[^"]+)"', visitor.get('/').get_data(as_text=True)):
        assert visitor.get(path).status_code == 200, path                             # every stylesheet the page links, in order


def test_the_header_and_footer_are_the_shared_ones(visitor):
    """DL-181: the home page and the text and form pages use the same header and footer partials."""
    for path in ('/', '/about', '/contact', '/privacy', '/terms', '/cookies', '/login', '/register', '/forgot-password'):
        html = visitor.get(path).get_data(as_text=True)
        assert html.count('<header class="pub-header"') == 1 and html.count('<footer class="pub-footer"') == 1, path
        assert 'class="pub-skip" href="#main"' in html and 'id="main"' in html, path
        assert 'css/public.css' in html and 'js/public.js' in html, path
        assert html.count('<h1') == 1, path
    home = visitor.get('/').get_data(as_text=True)
    other = visitor.get('/about').get_data(as_text=True)
    assert 'href="#features"' in home and 'href="/#features"' in other               # anchors on the home page, a way back from the others
    assert 'aria-current="page">About' in other and 'aria-current="page"' not in home
