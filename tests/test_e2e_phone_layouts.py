"""DL-180 part 15 in a real browser: campaign card pictures, quests and the public header on a phone; the Treasury guide on dark; the map guide."""
import io
import os
import pubid
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from playwright.sync_api import expect
from test_e2e_browser import Api, SESSION_COOKIE, pw                      # noqa: F401
from conftest import BACKEND, png_bytes

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _ctx(pw, api, base, width, height, phone):
    ctx = pw.new_context(viewport={'width': width, 'height': height}, device_scale_factor=1, has_touch=phone, is_mobile=phone)
    ctx.add_cookies([{'name': SESSION_COOKIE, 'value': api.s.cookies.get(SESSION_COOKIE), 'url': base}])
    return ctx


def _luma(rgb):
    nums = [float(x) for x in rgb[rgb.index('(') + 1:rgb.index(')')].replace('/', ',').replace(' ', ',').split(',') if x][:3]
    return sum(nums) / 3


def test_a_campaign_card_picture_has_the_same_shape_on_a_phone_and_a_desktop(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Shape')
    dm.post(f'/campaigns/{cid}/edit', data={'name': 'Shape'}, files={'avatar': ('b.png', png_bytes(1200, 720, (30, 90, 60)), 'image/png')})
    ratios = []
    for w, h, phone in [(390, 844, True), (1400, 900, False)]:
        ctx = _ctx(pw, dm, base, w, h, phone); page = ctx.new_page()
        page.goto(base + '/campaigns'); page.wait_for_load_state('networkidle')
        box = page.locator('.campaign-grid .group-image-block').first.bounding_box()
        ratios.append(box['width'] / box['height'])
        ctx.close()
    assert all(abs(r - 2.0) < 0.03 for r in ratios), ratios


def test_quests_on_a_phone_are_text_then_one_row_of_buttons(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Quests phone')
    dm.post(f'/campaigns/{cid}/quests/add', data={'title': 'Find the lost crown beneath the harbour before the tide returns', 'details': 'Last seen with the baron'})
    ctx = _ctx(pw, dm, base, 390, 844, True); page = ctx.new_page()
    page.goto(base + '/campaigns'); page.locator('.group-actions a.btn-primary').first.click()
    page.wait_for_selector('.quest')
    title = page.locator('.quest-title').first.bounding_box()
    main = page.locator('.quest-main').first.bounding_box()
    buttons = [b.bounding_box() for b in page.locator('.quest .quest-btn').all()]
    assert len(buttons) == 4
    assert max(b['y'] for b in buttons) - min(b['y'] for b in buttons) <= 2, 'all four buttons share one row'
    assert buttons[0]['y'] > main['y'] + main['height'] - 1, 'the row sits under the text'
    assert main['width'] > 280, 'the text uses the full width of the card'
    assert all(b['width'] >= 40 and b['height'] >= 40 for b in buttons)
    delete = page.locator('.quest .quest-delete').first.bounding_box()
    assert delete['x'] + delete['width'] > 390 - 60, 'delete is set apart at the far end'
    ctx.close()


def test_the_public_header_on_a_phone_is_centred_with_no_dead_band(pw, shared_server):
    ctx = pw.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
    page = ctx.new_page(); page.goto(shared_server.url + '/'); page.wait_for_load_state('networkidle')
    header = page.locator('.pub-header').bounding_box()
    brand = page.locator('.pub-header .pub-brand').bounding_box()
    toggle = page.locator('.pub-toggle').bounding_box()
    assert 66 <= header['height'] <= 72, header
    for box in (brand, toggle):
        assert abs((box['y'] + box['height'] / 2) - (header['y'] + header['height'] / 2)) <= 2, (box, header)
    page.locator('.pub-toggle').click()
    page.wait_for_timeout(500)
    assert page.locator('.pub-menu').is_visible() and page.locator('.pub-header').bounding_box()['height'] > header['height'] + 60
    ctx.close()


def test_the_landing_buttons_stack_at_one_width_on_a_phone(pw, shared_server):
    ctx = pw.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
    page = ctx.new_page(); page.goto(shared_server.url + '/'); page.wait_for_load_state('networkidle')
    boxes = [b.bounding_box() for b in page.locator('.hero .cta-row .pub-btn').all()]
    assert len(boxes) == 2 and abs(boxes[0]['width'] - boxes[1]['width']) < 1 and boxes[1]['y'] > boxes[0]['y']
    ctx.close()


def test_the_treasury_guide_is_dark_with_light_text(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Dark guide')
    ctx = _ctx(pw, dm, base, 1400, 900, False); page = ctx.new_page()
    page.goto(base + '/campaigns'); page.locator('.group-actions a.btn-primary').first.click()
    page.locator('a[href*="/treasury"]').first.click(); page.wait_for_load_state('networkidle')
    guide = page.locator('details.guide-card')
    bg = guide.evaluate('e => getComputedStyle(e).backgroundColor')
    guide.locator('summary').click()
    fg = guide.locator('.guide-steps li').first.evaluate('e => getComputedStyle(e).color')
    assert _luma(bg) < 70 and _luma(fg) > 150, (bg, fg)
    ctx.close()


def test_the_map_guide_opens_from_a_help_button_and_fits_the_screen(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Map guide')
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Arena', 'blank_width': '800', 'blank_height': '600'})
    mid = pubid.pk('maps', r.headers['Location'])
    dm.post(f'/campaigns/{cid}/api/maps/{mid}/settings', json={'grid_setup_done': 1})
    for w, h, phone in [(390, 844, True), (1400, 900, False)]:
        ctx = _ctx(pw, dm, base, w, h, phone); page = ctx.new_page()
        page.goto(f'{base}/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle')
        title = page.locator('.page-title').bounding_box(); help_btn = page.locator('.map-help-btn').bounding_box(); back = page.locator('.map-back-btn').bounding_box()
        assert abs((help_btn['y'] + help_btn['height'] / 2) - (back['y'] + back['height'] / 2)) < 3 and help_btn['x'] + help_btn['width'] <= back['x'] + 1, (help_btn, back)
        if phone:
            assert abs(help_btn['y'] - title['y']) < 40, 'on a phone the help button shares the title row'
        guide = page.locator('#map-guide')
        expect(guide).to_be_hidden()
        page.locator('.map-help-btn').tap() if phone else page.locator('.map-help-btn').click()
        expect(guide).to_be_visible()
        expect(guide.locator('.guide-steps li').first).to_be_visible()
        box = guide.bounding_box()
        assert box['x'] >= 0 and box['x'] + box['width'] <= w and box['y'] >= 0 and box['y'] + box['height'] <= h, box
        assert page.evaluate('document.documentElement.scrollWidth') <= w + 1
        page.keyboard.press('Escape'); expect(guide).to_be_hidden()
    ctx.close()


def test_the_shapes_menu_with_its_picture_button_stays_on_a_phone_screen(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Shapes menu')
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Arena', 'blank_width': '800', 'blank_height': '600'})
    mid = pubid.pk('maps', r.headers['Location'])
    dm.post(f'/campaigns/{cid}/api/maps/{mid}/settings', json={'grid_setup_done': 1})
    for w, h in [(390, 844), (360, 740), (844, 390)]:
        ctx = _ctx(pw, dm, base, w, h, True); page = ctx.new_page()
        page.goto(f'{base}/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle')
        if page.locator('[data-tool="eraser"]').is_hidden():                    # the tools tray starts closed upright, open sideways
            page.locator('#map-toolbar-toggle').tap(); page.wait_for_timeout(500)
        page.locator('#shapes-flyout [data-flyout-trigger]').tap(); page.wait_for_timeout(400)
        box = page.locator('#shapes-flyout .tool-flyout-menu-inner').bounding_box()
        assert box['x'] >= 0 and box['x'] + box['width'] <= w and box['y'] >= 0, (w, h, box)
        assert len(page.locator('#shapes-flyout .tool-flyout-menu-inner .map-tool-btn').all()) == 6
        ctx.close()
