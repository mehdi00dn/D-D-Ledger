"""DL-186: the image preview on a phone.  The picture fills the screen, the arrows sit in a bar underneath it with the count, a
pinch or a double-tap zooms, a swipe turns the page, and a swipe up or down closes it.  Touches are sent through the browser's own
touch input, so the page gets real pointer events.  Set SHOT_DIR to save screenshots."""
import os, time
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, pw, png_bytes, SESSION_COOKIE          # noqa: F401  (pw is a fixture)
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _shot(page, name):
    d = os.environ.get('SHOT_DIR')
    if d:
        os.makedirs(d, exist_ok=True)
        page.screenshot(path=os.path.join(d, name + '.png'))


class Finger:
    """Touch input through the DevTools protocol."""
    def __init__(self, page):
        self.cdp = page.context.new_cdp_session(page)

    def _send(self, kind, points):
        self.cdp.send('Input.dispatchTouchEvent', {'type': kind, 'touchPoints': [{'x': x, 'y': y, 'id': i} for i, (x, y) in enumerate(points)]})

    def swipe(self, x0, y0, x1, y1, steps=8, pause=0.012, lift=True):
        self._send('touchStart', [(x0, y0)])
        for i in range(1, steps + 1):
            self._send('touchMove', [(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)])
            time.sleep(pause)
        if lift:
            self._send('touchEnd', [])

    def tap(self, x, y):
        self._send('touchStart', [(x, y)]); time.sleep(0.03); self._send('touchEnd', [])

    def pinch(self, x, y, start, end, steps=8, pause=0.012):
        self._send('touchStart', [(x - start, y), (x + start, y)])
        for i in range(1, steps + 1):
            r = start + (end - start) * i / steps
            self._send('touchMove', [(x - r, y), (x + r, y)])
            time.sleep(pause)
        self._send('touchEnd', [])


def _scale(page):
    return page.evaluate("() => { const m = new DOMMatrix(getComputedStyle(document.getElementById('lightbox-img')).transform); return Math.hypot(m.a, m.b); }")


def _settled_scale(page, wait=0.6):
    time.sleep(wait)
    return _scale(page)


def test_phone_lightbox(pw, shared_server):
    base = shared_server.url
    api = Api(base)
    cid = api.campaign('Phone gallery')
    files = [('sheets', (f's{i}.png', png_bytes(500, 800, (60 * i + 30, 90, 140)), 'image/png')) for i in range(3)]
    api.post(f'/campaigns/{cid}/characters/new', data={'name': 'Gallery', 'max_hp': '10'}, files=files)
    char_id = next(c['id'] for c in api.get(f'/campaigns/{cid}/api/characters').json() if c['name'] == 'Gallery')
    ctx = pw.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    ctx.add_cookies([{'name': SESSION_COOKIE, 'value': api.s.cookies.get(SESSION_COOKIE), 'url': base}])
    page = ctx.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(base + f'/campaigns/{cid}/characters/{char_id}')
    page.wait_for_load_state('networkidle')
    finger = Finger(page)
    page.tap('#sheet-viewer-img')
    page.wait_for_selector('#image-lightbox:not([hidden])')
    page.wait_for_function("() => document.getElementById('lightbox-img').complete && document.getElementById('lightbox-img').naturalWidth > 0")
    time.sleep(0.4)

    # layout: the arrows are in a bar under the picture, the count between them, and nothing overflows sideways
    stage = page.locator('.lightbox-stage').bounding_box()
    prev, nxt = page.locator('#lightbox-prev-btn').bounding_box(), page.locator('#lightbox-next-btn').bounding_box()
    assert prev['y'] >= stage['y'] + stage['height'] - 1 and nxt['y'] >= stage['y'] + stage['height'] - 1, (stage, prev, nxt)
    assert prev['x'] < 195 < nxt['x'] and prev['width'] >= 44 and prev['height'] >= 44
    assert page.locator('#lightbox-count').inner_text() == '1 of 3'
    assert page.evaluate('() => document.documentElement.scrollWidth <= window.innerWidth')
    assert page.locator('#lightbox-img').bounding_box()['width'] <= 390
    _shot(page, 'lightbox-phone-open')

    # a short, slow drag springs back; a real swipe turns the page; a swipe back returns
    src0 = page.locator('#lightbox-img').get_attribute('src')
    finger.swipe(200, 400, 160, 400, steps=6, pause=0.03)
    time.sleep(0.5)
    assert page.locator('#lightbox-img').get_attribute('src') == src0 and abs(page.locator('#lightbox-img').bounding_box()['x'] - (195 - page.locator('#lightbox-img').bounding_box()['width'] / 2)) < 2
    finger.swipe(320, 400, 60, 400)
    page.wait_for_function("() => document.getElementById('lightbox-count').textContent === '2 of 3'")
    assert page.locator('#lightbox-img').get_attribute('src') != src0
    time.sleep(0.4)
    finger.swipe(60, 400, 330, 400)
    page.wait_for_function("() => document.getElementById('lightbox-count').textContent === '1 of 3'")
    assert page.locator('#lightbox-img').get_attribute('src') == src0
    time.sleep(0.4)
    # the first picture has nothing before it: the swipe stretches and comes back
    finger.swipe(60, 400, 330, 400)
    time.sleep(0.6)
    assert page.locator('#lightbox-count').inner_text() == '1 of 3' and page.locator('#lightbox-img').get_attribute('src') == src0

    # the arrow buttons still work, with a proper tap
    page.tap('#lightbox-next-btn')
    page.wait_for_function("() => document.getElementById('lightbox-count').textContent === '2 of 3'")
    page.tap('#lightbox-prev-btn')
    page.wait_for_function("() => document.getElementById('lightbox-count').textContent === '1 of 3'")
    time.sleep(0.4)

    # double-tap zooms in at the finger, double-tap again zooms out
    assert _scale(page) == 1
    finger.tap(195, 300); time.sleep(0.08); finger.tap(195, 300)
    z = _settled_scale(page)
    assert 2.2 < z < 2.6, z
    _shot(page, 'lightbox-phone-zoomed')
    finger.tap(195, 300); time.sleep(0.08); finger.tap(195, 300)
    assert _settled_scale(page) == pytest.approx(1, abs=0.01)

    # pinch zooms; letting go of a pinch that went too small returns to 1
    finger.pinch(195, 400, 30, 110)
    z = _settled_scale(page)
    assert z > 2, z
    finger.pinch(195, 400, 120, 20)
    assert _settled_scale(page) == pytest.approx(1, abs=0.01)
    finger.pinch(195, 400, 20, 140)
    zoomed = _settled_scale(page)
    assert zoomed > 3
    # a zoomed picture pans with one finger and stops at its edge instead of leaving a gap
    finger.swipe(200, 400, 40, 400, steps=6, pause=0.02)
    time.sleep(0.8)
    box = page.locator('#lightbox-img').bounding_box()
    assert box['x'] + box['width'] >= 390 - 2, box           # the right edge of the picture is not pulled inside the screen
    # and a swipe on a zoomed picture pans it, it does not turn the page
    assert page.locator('#lightbox-count').inner_text() == '1 of 3'
    finger.tap(195, 400); time.sleep(0.08); finger.tap(195, 400)
    assert _settled_scale(page) == pytest.approx(1, abs=0.01)

    # a swipe down closes it
    finger.swipe(195, 300, 195, 640)
    page.wait_for_selector('#image-lightbox', state='hidden')
    assert not errors, errors
    ctx.close()


def test_single_image_has_no_arrow_bar_on_a_phone(pw, shared_server):
    base = shared_server.url
    api = Api(base)
    cid = api.campaign('One picture')
    files = [('sheets', ('only.png', png_bytes(600, 400, (80, 120, 60)), 'image/png'))]
    api.post(f'/campaigns/{cid}/characters/new', data={'name': 'Solo', 'max_hp': '10'}, files=files)
    char_id = next(c['id'] for c in api.get(f'/campaigns/{cid}/api/characters').json() if c['name'] == 'Solo')
    ctx = pw.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    ctx.add_cookies([{'name': SESSION_COOKIE, 'value': api.s.cookies.get(SESSION_COOKIE), 'url': base}])
    page = ctx.new_page()
    page.goto(base + f'/campaigns/{cid}/characters/{char_id}')
    page.wait_for_load_state('networkidle')
    page.tap('#sheet-viewer-img')
    page.wait_for_selector('#image-lightbox:not([hidden])')
    time.sleep(0.4)
    assert not page.locator('#lightbox-prev-btn').is_visible() and not page.locator('#lightbox-next-btn').is_visible()
    assert not page.locator('#lightbox-count').is_visible()
    _shot(page, 'lightbox-phone-single')
    page.tap('#lightbox-close-btn')
    page.wait_for_selector('#image-lightbox', state='hidden')
    ctx.close()
