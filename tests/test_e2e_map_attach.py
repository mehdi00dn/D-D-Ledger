"""DL-159 in a real browser: select a shape, press Attach, click a token; dragging the token carries the shape."""
import os
import re

import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from playwright.sync_api import expect
from test_e2e_browser import Api, pw                      # noqa: F401
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def test_attach_a_shape_then_drag_the_token(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Attach')
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Arena', 'blank_width': '1000', 'blank_height': '700'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    api = f'/campaigns/{cid}/api/maps/{mid}'
    dm.post(f'{api}/settings', json={'grid_setup_done': 1, 'grid_size': 50, 'snap_to_grid': 0})
    dm.post(f'{api}/drawings', json={'kind': 'rect', 'cx': 700, 'cy': 200, 'w': 160, 'h': 120, 'fill': True, 'data': {}})
    dm.post(f'{api}/pins', json={'pin_type': 'prop', 'icon_key': 'paw', 'x': 300, 'y': 400})
    ctx = dm.context(pw, base); page = ctx.new_page(); errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.goto(f'{base}{api.replace("/api", "")}'); page.wait_for_load_state('networkidle'); page.wait_for_selector('.map-pin')
    box = page.locator('#map-canvas').bounding_box(); k = box['width'] / 1000
    page.mouse.click(box['x'] + 700 * k, box['y'] + 200 * k)
    expect(page.locator('#shape-action-bubble')).to_be_visible()
    page.click('#shape-attach-btn')
    expect(page.locator('#map-attach-hint')).to_be_visible()
    page.locator('.map-pin').click()
    expect(page.locator('#map-attach-hint')).to_be_hidden()
    page.wait_for_timeout(500)
    shape = dm.get(f'{api}/drawings').json()[0]
    pin = dm.get(f'{api}/pins').json()[0]
    assert shape['attached_pin_id'] == pin['id'] and (shape['cx'], shape['cy']) == (700, 200)   # no auto-alignment
    pb = page.locator('.map-pin').bounding_box(); cx, cy = pb['x'] + pb['width'] / 2, pb['y'] + pb['height'] / 2
    page.mouse.move(cx, cy); page.mouse.down(); page.mouse.move(cx + 100 * k, cy + 50 * k, steps=8); page.mouse.up(); page.wait_for_timeout(700)
    moved_pin = dm.get(f'{api}/pins').json()[0]; moved = dm.get(f'{api}/drawings').json()[0]
    dx, dy = moved_pin['x'] - pin['x'], moved_pin['y'] - pin['y']
    assert dx > 50 and dy > 20
    assert (moved['cx'] - 700, moved['cy'] - 200) == (dx, dy)
    assert not errors, errors
    ctx.close()


def test_locked_shape_behind_its_token_is_unlocked_from_the_token_menu(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Hidden lock')
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Arena', 'blank_width': '1000', 'blank_height': '700'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    api = f'/campaigns/{cid}/api/maps/{mid}'
    dm.post(f'{api}/settings', json={'grid_setup_done': 1, 'grid_size': 50, 'snap_to_grid': 0})
    pin = dm.post(f'{api}/pins', json={'pin_type': 'prop', 'icon_key': 'paw', 'x': 500, 'y': 350}).json()
    dm.post(f'{api}/drawings', json={'kind': 'rect', 'cx': 500, 'cy': 350, 'w': 160, 'h': 120, 'fill': True, 'data': {}})
    shape = dm.get(f'{api}/drawings').json()[0]
    pid = pin.get('id') or dm.get(f'{api}/pins').json()[0]['id']
    dm.post(f'{api}/drawings/{shape["id"]}/update', json={'attached_pin_id': pid, 'locked': True})
    ctx = dm.context(pw, base); page = ctx.new_page(); errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.goto(f'{base}{api.replace("/api", "")}'); page.wait_for_load_state('networkidle'); page.wait_for_selector('.map-pin')
    page.locator('.map-pin').click()
    expect(page.locator('#pin-shapes-btn')).to_be_visible()
    page.click('#pin-shapes-btn')
    expect(page.locator('#pin-shapes-menu')).to_be_visible()
    page.locator('#pin-shapes-menu [data-act=lock]').click()
    page.wait_for_timeout(600)
    assert not dm.get(f'{api}/drawings').json()[0]['locked']
    assert not errors, errors
    ctx.close()
