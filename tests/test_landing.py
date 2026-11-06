"""DL-177: the public home page at / (signed-out visitors); signed-in people are still sent to their campaign."""
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
    ctas = re.findall(r'<a class="btn btn-primary[^"]*" href="([^"]+)">([^<]+)</a>', html)
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


def test_signed_in_people_still_go_to_their_campaign(make_user):
    u = make_user(); cid = u.new_campaign()
    r = u.get('/')
    assert r.status_code == 302 and f'/campaigns/' in r.headers['Location']
    u2 = make_user()                                                              # no campaign yet: the campaigns list
    assert u2.get('/').status_code == 302


def test_a_signed_out_session_is_a_visitor_again(make_user):
    u = make_user()
    assert u.get('/').status_code == 302
    u.get('/logout'); u.c.post('/logout')
    assert u.get('/').status_code == 200


def test_landing_assets_are_served(visitor):
    for path in ('/static/css/landing.css', '/static/js/landing.js'):
        assert visitor.get(path).status_code == 200
    css = visitor.get('/static/css/landing.css').get_data(as_text=True)
    for font in re.findall(r"url\('\.\./fonts/([^']+)'\)", css):
        assert visitor.get('/static/fonts/' + font).status_code == 200, font
