"""DL-163 / DL-164: the Rest menu opens on hover like Roll Initiative, both show a caret, the short rest boxes use the
form field look; the campaign image preview tells its size, and the saved image is at most 1200 px."""
import io
import json
import re

import pytest
from conftest import BACKEND, q, png_bytes
from test_e2e_browser import Api, pw  # noqa: F401

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='browser tests need Postgres')


def test_rest_menu_hover_carets_and_short_rest_fields(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign()
    hero = dm.character(cid, 'Mira', level=3)
    dm.s.post(f'{base}/campaigns/{cid}/api/battle/add', json={'character_id': hero})
    ctx = dm.context(pw, base)
    page = ctx.new_page(); page.set_viewport_size({'width': 1280, 'height': 800})
    errors = []
    page.on('pageerror', lambda x: errors.append(str(x)))
    page.goto(f'{base}/campaigns/{cid}/battle')
    for btn in ('#roll-initiative-btn', '#rest-menu-btn'):
        assert page.locator(btn + ' svg').count() == 2                      # the icon and the dropdown caret
    page.hover('#rest-menu-btn')
    page.wait_for_selector('#rest-menu-list .roll-menu-panel', state='visible')
    assert page.locator('#short-rest-btn').is_visible() and page.locator('#long-rest-btn').is_visible()
    page.click('#short-rest-btn')
    page.wait_for_selector('#short-rest-dialog[open]')
    inp = page.locator('#short-rest-list input').first
    style = inp.evaluate("e => { const s = getComputedStyle(e); return [s.borderTopWidth, s.borderTopStyle, s.borderRadius, s.backgroundColor]; }")
    assert style[0] == '1px' and style[2] != '0px'                          # the styled field, not the browser default
    page.fill('#short-rest-list input', '0')
    page.click('#short-rest-go')
    page.wait_for_selector('#short-rest-dialog[open]', state='detached')
    ctx.close()
    assert not errors, errors


def test_campaign_image_is_capped_at_1200_and_the_preview_says_so(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    r = dm.post('/campaigns/new', data={'name': 'Wide', 'description': json.dumps([])},
                files={'avatar': ('c.png', png_bytes(2400, 1400, (120, 60, 30)), 'image/png')})
    cid = int(re.search(r'/campaigns/(\d+)/', r.headers['Location']).group(1))
    from PIL import Image
    path = q('SELECT avatar_path FROM campaigns WHERE id = ?', cid)[0]['avatar_path']
    stored = Image.open(io.BytesIO(dm.get('/uploads/' + path).content))
    assert max(stored.size) <= 1200
    ctx = dm.context(pw, base)
    page = ctx.new_page(); page.set_viewport_size({'width': 1280, 'height': 800})
    page.goto(f'{base}/campaigns/{cid}/home')
    page.click('.home-banner-view')
    page.wait_for_selector('#lightbox-size', state='visible')
    text = page.inner_text('#lightbox-size')
    assert 'largest allowed edge 1200 px' in text and ' × ' in text
    ctx.close()
