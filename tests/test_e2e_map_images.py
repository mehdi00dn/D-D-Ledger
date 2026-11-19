"""DL-180 part 15 in a real browser: the Add image button puts a picture on the map, it is drawn, can be picked up and undone."""
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


def _open_map(pw, shared_server, name):
    base = shared_server.url; dm = Api(base); cid = dm.campaign(name)
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Arena', 'blank_width': '1000', 'blank_height': '700'})
    mid = pubid.pk('maps', r.headers['Location'])
    api = f'/campaigns/{cid}/api/maps/{mid}'
    dm.post(f'{api}/settings', json={'grid_setup_done': 1, 'grid_size': 50, 'snap_to_grid': 0})
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(f'{base}/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle')
    return dm, api, ctx, page, errors


def test_add_image_places_draws_selects_and_undoes(pw, shared_server):
    dm, api, ctx, page, errors = _open_map(pw, shared_server, 'Pictures')
    page.set_input_files('#map-image-input', {'name': 'clue.png', 'mimeType': 'image/png', 'buffer': png_bytes(300, 200, (220, 20, 20))})
    for _ in range(40):
        rows = dm.get(f'{api}/drawings').json()
        if rows:
            break
        page.wait_for_timeout(150)
    assert [r['kind'] for r in rows] == ['image'] and rows[0]['url']
    # it is on the canvas: the red picture shows where the map was white
    page.wait_for_timeout(500)
    canvas = page.locator('#map-canvas')
    box = canvas.bounding_box()
    px, py = box['x'] + box['width'] * rows[0]['cx'] / 1000, box['y'] + box['height'] * rows[0]['cy'] / 700
    shot = page.screenshot(clip={'x': px - 4, 'y': py - 4, 'width': 8, 'height': 8})
    from PIL import Image
    r, g, b = Image.open(io.BytesIO(shot)).convert('RGB').getpixel((4, 4))
    assert r > 150 and g < 90 and b < 90, (r, g, b)
    # it is selected right away, and Delete removes it; undo brings it back
    page.keyboard.press('Delete'); page.wait_for_timeout(400)
    assert dm.get(f'{api}/drawings').json() == []
    page.keyboard.press('Control+z'); page.wait_for_timeout(600)
    back = dm.get(f'{api}/drawings').json()
    assert [r['kind'] for r in back] == ['image'] and back[0]['url'] == rows[0]['url']
    assert not errors, errors
    ctx.close()


def test_a_player_on_a_locked_map_has_no_add_image_button(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Locked pictures')
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Arena', 'blank_width': '800', 'blank_height': '600'})
    mid = pubid.pk('maps', r.headers['Location'])
    dm.post(f'/campaigns/{cid}/api/maps/{mid}/settings', json={'locked_for_players': 1, 'grid_setup_done': 1})
    player = Api(base); dm.add_member(cid, player)
    ctx = player.context(pw, base); page = ctx.new_page()
    page.goto(f'{base}/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle')
    expect(page.locator('#map-add-image-btn')).to_be_hidden()
    ctx.close()
