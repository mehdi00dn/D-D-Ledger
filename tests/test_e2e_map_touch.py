"""The map editor on a touch screen, driven with real touch events (Chrome DevTools input), not mouse events:
the four hold-and-drag buttons, tokens that are easy to hit, the docked buttons clearing the dock, and the wording."""
import os
import re

import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from playwright.sync_api import expect
from test_e2e_browser import Api, SESSION_COOKIE, pw                      # noqa: F401
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


class Finger:
    def __init__(self, page):
        self.page, self.cdp = page, page.context.new_cdp_session(page)

    def _send(self, kind, points):
        self.cdp.send('Input.dispatchTouchEvent', {'type': kind, 'touchPoints': [{'x': x, 'y': y, 'id': i} for i, (x, y) in enumerate(points)]})

    def tap(self, x, y):
        self._send('touchStart', [(x, y)]); self.page.wait_for_timeout(40); self._send('touchEnd', []); self.page.wait_for_timeout(200)

    def drag(self, a, b, steps=8):
        self._send('touchStart', [a]); self.page.wait_for_timeout(30)
        for i in range(1, steps + 1):
            self._send('touchMove', [(a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps)]); self.page.wait_for_timeout(16)
        self._send('touchEnd', []); self.page.wait_for_timeout(300)


def _centre(box):
    return box['x'] + box['width'] / 2, box['y'] + box['height'] / 2


def _map(pw, shared_server, name, width=390, height=844):
    base = shared_server.url; dm = Api(base); cid = dm.campaign(name)
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Arena', 'blank_width': '1000', 'blank_height': '700'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    api = f'/campaigns/{cid}/api/maps/{mid}'
    dm.post(f'{api}/settings', json={'grid_setup_done': 1, 'grid_size': 50, 'snap_to_grid': 0})
    ctx = pw.new_context(viewport={'width': width, 'height': height}, has_touch=True, is_mobile=True)
    ctx.add_cookies([{'name': SESSION_COOKIE, 'value': dm.s.cookies.get(SESSION_COOKIE), 'url': base}])
    return base, dm, api, mid, ctx


def _open(page, base, api):
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(f'{base}{api.replace("/api", "")}'); page.wait_for_load_state('networkidle')
    return errors


def test_token_resize_and_rotate_buttons_work_with_a_finger(pw, shared_server):
    base, dm, api, mid, ctx = _map(pw, shared_server, 'Touch token')
    dm.post(f'{api}/pins', json={'pin_type': 'prop', 'icon_key': 'paw', 'x': 500, 'y': 350})
    page = ctx.new_page(); errors = _open(page, base, api); page.wait_for_selector('.map-pin')
    finger = Finger(page)
    finger.tap(*_centre(page.locator('.map-pin').bounding_box()))
    expect(page.locator('#pin-resize-btn')).to_be_visible()
    before = dm.get(f'{api}/pins').json()[0]
    finger.drag(_centre(page.locator('#pin-resize-btn').bounding_box()), (200, 300))
    after = dm.get(f'{api}/pins').json()[0]
    assert (after['scale'] or 1) != (before['scale'] or 1), 'dragging the resize button changes the token size'
    finger.drag(_centre(page.locator('#pin-rotate-btn').bounding_box()), (60, 200))
    assert (dm.get(f'{api}/pins').json()[0]['rotation'] or 0) != 0, 'dragging the rotate button turns the token'
    assert not errors, errors
    ctx.close()


def test_shape_resize_and_rotate_buttons_work_with_a_finger(pw, shared_server):
    base, dm, api, mid, ctx = _map(pw, shared_server, 'Touch shape')
    dm.post(f'{api}/drawings', json={'kind': 'rect', 'cx': 500, 'cy': 350, 'w': 300, 'h': 200, 'fill': True, 'data': {}})
    page = ctx.new_page(); errors = _open(page, base, api); page.wait_for_timeout(500)
    finger = Finger(page)
    box = page.locator('#map-canvas').bounding_box(); k = box['width'] / 1000
    finger.tap(box['x'] + 500 * k, box['y'] + 350 * k)
    expect(page.locator('#shape-action-bubble')).to_be_visible()
    before = dm.get(f'{api}/drawings').json()[0]
    finger.drag(_centre(page.locator('#shape-resize-toggle').bounding_box()), (300, 350))
    assert dm.get(f'{api}/drawings').json()[0]['w'] != before['w']
    finger.drag(_centre(page.locator('#shape-rotate-toggle').bounding_box()), (320, 250))
    assert (dm.get(f'{api}/drawings').json()[0]['rotation'] or 0) != 0
    assert not errors, errors
    ctx.close()


def test_a_tiny_token_can_be_hit_beside_its_centre_and_a_tap_on_a_button_still_clicks(pw, shared_server):
    base, dm, api, mid, ctx = _map(pw, shared_server, 'Touch target')
    dm.post(f'{api}/pins', json={'pin_type': 'prop', 'icon_key': 'paw', 'x': 500, 'y': 350})
    page = ctx.new_page(); _open(page, base, api); page.wait_for_selector('.map-pin')
    finger = Finger(page)
    cx, cy = _centre(page.locator('.map-pin').bounding_box())
    finger.tap(cx + 13, cy - 9)                                              # 13 px off a 12 px token
    expect(page.locator('#pin-resize-btn')).to_be_visible()
    finger.tap(*_centre(page.locator('#pin-resize-btn').bounding_box()))      # no drag: the click still toggles the handles
    ctx.close()


def test_docked_buttons_sit_above_the_open_tray_and_wording_matches_touch(pw, shared_server):
    base, dm, api, mid, ctx = _map(pw, shared_server, 'Touch dock')
    dm.post(f'{api}/drawings', json={'kind': 'rect', 'cx': 500, 'cy': 350, 'w': 300, 'h': 200, 'fill': True, 'data': {}})
    page = ctx.new_page(); _open(page, base, api); page.wait_for_timeout(500)
    finger = Finger(page)
    box = page.locator('#map-canvas').bounding_box(); k = box['width'] / 1000
    finger.tap(box['x'] + 500 * k, box['y'] + 350 * k)
    expect(page.locator('#shape-action-bubble')).to_be_visible()
    bubble = page.locator('#shape-action-bubble').bounding_box(); dock = page.locator('#map-toolbar').bounding_box()
    assert bubble['y'] + bubble['height'] <= dock['y'] + 1, 'the shape buttons clear the dock, including its style row'
    page.locator('#map-toolbar-toggle').click(); page.wait_for_timeout(500)
    bubble = page.locator('#shape-action-bubble').bounding_box(); dock = page.locator('#map-toolbar').bounding_box()
    assert bubble['y'] + bubble['height'] <= dock['y'] + 1, 'and still do when the tray is open'
    assert 'Esc' not in page.locator('#map-attach-hint').text_content()
    ctx.close()


def test_measure_picker_stays_on_screen_on_a_phone(pw, shared_server):
    base, dm, api, mid, ctx = _map(pw, shared_server, 'Touch measure')
    page = ctx.new_page(); _open(page, base, api)
    page.locator('#map-toolbar [data-tool="ruler"]').click(); page.wait_for_timeout(300)
    box = page.locator('#measure-picker').bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= 390 + 0.5, box
    ctx.close()


def test_painting_fog_with_a_finger_leaves_no_brush_ring(pw, shared_server):
    base, dm, api, mid, ctx = _map(pw, shared_server, 'Touch fog')
    page = ctx.new_page(); errors = _open(page, base, api)
    page.locator('#map-toolbar-toggle').click(); page.wait_for_timeout(300)
    page.locator('#fog-flyout [data-flyout-trigger]').click(); page.wait_for_timeout(250)
    page.locator('#fog-flyout [data-tool="fog-add"]').click(); page.wait_for_timeout(250)
    box = page.locator('#map-stage-wrap').bounding_box()
    Finger(page).drag((box['x'] + 60, box['y'] + 60), (box['x'] + 200, box['y'] + 110))
    page.wait_for_timeout(300)
    expect(page.locator('#fog-brush-ring')).to_be_hidden()
    assert not errors, errors
    ctx.close()
