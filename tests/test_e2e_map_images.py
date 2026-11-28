"""DL-180 part 15 (reworked in part 27) in a real browser: a picture is added to the canvas beside the map, the grid runs over it, and it has only a position, a size, a turn and a remove."""
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


def _open_map(pw, shared_server, name, snap=0):
    base = shared_server.url; dm = Api(base); cid = dm.campaign(name)
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Arena', 'blank_width': '1000', 'blank_height': '700'})
    mid = pubid.pk('maps', r.headers['Location'])
    api = f'/campaigns/{cid}/api/maps/{mid}'
    dm.post(f'{api}/settings', json={'grid_setup_done': 1, 'grid_size': 50, 'snap_to_grid': snap})
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(f'{base}/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle')
    return dm, api, ctx, page, errors


def _add_picture(page, dm, api, w=300, h=200, rgb=(220, 20, 20)):
    before = len(dm.get(f'{api}/drawings').json())
    page.set_input_files('#map-image-input', {'name': 'clue.png', 'mimeType': 'image/png', 'buffer': png_bytes(w, h, rgb)})
    for _ in range(40):
        rows = dm.get(f'{api}/drawings').json()
        if len(rows) > before:
            break
        page.wait_for_timeout(150)
    page.wait_for_timeout(500)
    return rows[-1]


def _screen(page, x, y):
    """Map pixels -> screen pixels (the map's own box, wherever the canvas reaches)."""
    r = page.evaluate("() => { const b = document.getElementById('map-stage-inner').getBoundingClientRect(); return [b.x, b.y, b.width, b.height]; }")
    return r[0] + r[2] * x / 1000, r[1] + r[3] * y / 700


def _canvas_pixel(page, x, y):
    """The colour of the canvas at a map pixel (read from the canvas itself, so a thin grid line is not lost to scaling)."""
    return page.evaluate("""([x, y]) => {
      const c = document.getElementById('map-canvas'), inner = document.getElementById('map-stage-inner').getBoundingClientRect(), r = c.getBoundingClientRect();
      const nx = (r.x - inner.x) / inner.width * 1000, ny = (r.y - inner.y) / inner.height * 700, nw = r.width / inner.width * 1000, nh = r.height / inner.height * 700;
      const px = Math.round((x - nx) / nw * c.width), py = Math.round((y - ny) / nh * c.height);
      return Array.from(c.getContext('2d').getImageData(px, py, 1, 1).data);
    }""", [x, y])


def test_add_image_goes_beside_the_map_draws_selects_and_undoes(pw, shared_server):
    dm, api, ctx, page, errors = _open_map(pw, shared_server, 'Pictures')
    assert page.evaluate("() => document.getElementById('zoom-level-label').textContent") == '100%'
    row = _add_picture(page, dm, api)
    assert row['kind'] == 'image' and row['url']
    left, top = row['cx'] - row['w'] / 2, row['cy'] - row['h'] / 2
    assert left >= 1000 and top == 0, (left, top)                          # beside the map, not on it
    # the canvas grew to hold it, and the view pulled back so it is on screen
    zoom = int(page.evaluate("() => document.getElementById('zoom-level-label').textContent").rstrip('%'))
    assert zoom < 100
    wrap = page.locator('#map-stage-wrap').bounding_box()
    sx, sy = _screen(page, row['cx'], row['cy'])
    assert wrap['x'] < sx < wrap['x'] + wrap['width'] and wrap['y'] < sy < wrap['y'] + wrap['height']
    r, g, b, _a = _canvas_pixel(page, row['cx'] + 3, row['cy'] + 3)         # a point of the picture that is clear of grid lines
    assert r > 150 and g < 90 and b < 90, (r, g, b)
    # the grid runs over the picture: along a grid line the red is crossed by the line
    line = _canvas_pixel(page, (int(row['cx'] // 50) + 1) * 50, row['cy'] + 3)
    assert line[:3] != [r, g, b], line
    # it is selected right away, and Delete removes it; undo brings it back
    page.keyboard.press('Delete'); page.wait_for_timeout(400)
    assert dm.get(f'{api}/drawings').json() == []
    page.keyboard.press('Control+z'); page.wait_for_timeout(600)
    back = dm.get(f'{api}/drawings').json()
    assert [r['kind'] for r in back] == ['image'] and back[0]['url'] == row['url']
    assert not errors, errors
    ctx.close()


def test_the_canvas_is_just_the_map_again_when_the_picture_is_removed(pw, shared_server):
    dm, api, ctx, page, errors = _open_map(pw, shared_server, 'Pictures gone')
    plain = page.evaluate("() => { const c = document.getElementById('map-canvas'); return [c.width, c.height, c.style.width, c.style.left]; }")
    assert plain == [1000, 700, '', '']
    _add_picture(page, dm, api)
    grown = page.evaluate("() => document.getElementById('map-canvas').width")
    assert grown > 1000
    page.keyboard.press('Delete'); page.wait_for_timeout(500)
    assert page.evaluate("() => { const c = document.getElementById('map-canvas'); return [c.width, c.height, c.style.width, c.style.left]; }") == plain
    assert not errors, errors
    ctx.close()


def test_a_picture_menu_has_only_resize_rotate_and_remove(pw, shared_server):
    dm, api, ctx, page, errors = _open_map(pw, shared_server, 'Picture menu')
    _add_picture(page, dm, api)
    expect(page.locator('#shape-action-bubble')).to_be_visible()
    for hidden in ('#shape-hide-btn', '#shape-attach-btn', '#shape-lock-btn', '#shape-angle-toggle'):
        expect(page.locator(hidden)).to_be_hidden()
    for shown in ('#shape-resize-toggle', '#shape-rotate-toggle', '#shape-delete-btn'):
        expect(page.locator(shown)).to_be_visible()
    expect(page.locator('#shape-style-group')).to_be_hidden()              # no colour or fill for a picture
    assert not errors, errors
    ctx.close()


def test_a_picture_moves_and_resizes_on_the_grid_and_keeps_its_proportions(pw, shared_server):
    dm, api, ctx, page, errors = _open_map(pw, shared_server, 'Picture grid', snap=1)
    row = _add_picture(page, dm, api, 300, 200)
    assert (row['cx'] - row['w'] / 2) % 50 == 0 and (row['cy'] - row['h'] / 2) % 50 == 0      # it landed on the grid
    # move it: its corner meets a grid line again
    sx, sy = _screen(page, row['cx'], row['cy'])
    page.mouse.move(sx, sy); page.mouse.down(); page.mouse.move(sx + 37, sy + 23, steps=6); page.mouse.up(); page.wait_for_timeout(500)
    moved = dm.get(f'{api}/drawings').json()[0]
    assert (moved['cx'] - moved['w'] / 2) % 50 == 0 and (moved['cy'] - moved['h'] / 2) % 50 == 0
    assert (moved['cx'], moved['cy']) != (row['cx'], row['cy'])
    # resize from the bottom right corner: still 3:2, and the corner is on the grid
    page.click('#shape-resize-toggle')
    cx, cy = _screen(page, moved['cx'] + moved['w'] / 2, moved['cy'] + moved['h'] / 2)
    page.mouse.move(cx, cy); page.mouse.down(); page.mouse.move(cx + 60, cy + 10, steps=6); page.mouse.up(); page.wait_for_timeout(500)
    big = dm.get(f'{api}/drawings').json()[0]
    assert abs(big['w'] / big['h'] - 1.5) < 0.01 and big['w'] > moved['w']
    assert (big['cx'] + big['w'] / 2) % 50 == 0 or (big['cy'] + big['h'] / 2) % 50 == 0
    assert (big['cx'] - big['w'] / 2) == pytest.approx(moved['cx'] - moved['w'] / 2, abs=0.5)     # the opposite corner stayed put
    assert not errors, errors
    ctx.close()


def test_a_picture_turns_and_stays_in_reach(pw, shared_server):
    dm, api, ctx, page, errors = _open_map(pw, shared_server, 'Picture turn')
    row = _add_picture(page, dm, api)
    page.click('#shape-rotate-toggle')
    sx, sy = _screen(page, row['cx'], row['cy'])
    top = _screen(page, row['cx'], row['cy'] - row['h'] / 2)
    page.mouse.move(top[0], top[1] - 30); page.mouse.down(); page.mouse.move(sx + 80, sy, steps=8); page.mouse.up(); page.wait_for_timeout(500)
    turned = dm.get(f'{api}/drawings').json()[0]
    assert abs(turned['rotation']) > 30
    assert not errors, errors
    ctx.close()


def test_the_right_click_dot_goes_anywhere_on_the_canvas(pw, shared_server):
    dm, api, ctx, page, errors = _open_map(pw, shared_server, 'Picture dot')
    row = _add_picture(page, dm, api)
    sx, sy = _screen(page, row['cx'], row['cy'])
    page.mouse.move(sx, sy); page.mouse.down(button='right'); page.mouse.move(sx + 2, sy + 2); page.wait_for_timeout(300)
    got = page.evaluate("() => { const e = document.querySelector('.map-ptr.is-on .map-ptr-dot'); const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }")
    page.mouse.up(button='right')
    assert abs(got[0] - (sx + 2)) < 3 and abs(got[1] - (sy + 2)) < 3, (got, (sx, sy))      # on the picture, beyond the map's edge
    assert not errors, errors
    ctx.close()


def test_a_player_sees_the_picture_and_the_canvas_around_it(pw, shared_server):
    dm, api, ctx, page, errors = _open_map(pw, shared_server, 'Picture player')
    row = _add_picture(page, dm, api)
    cid = api.split('/')[2]
    player = Api(shared_server.url); dm.add_member(cid, player)
    pctx = player.context(pw, shared_server.url); ppage = pctx.new_page()
    ppage.goto(f'{shared_server.url}{api.replace("/api", "")}'); ppage.wait_for_load_state('networkidle'); ppage.wait_for_timeout(600)
    assert ppage.evaluate("() => document.getElementById('map-canvas').width") > 1000
    r, g, b, _a = _canvas_pixel(ppage, row['cx'] + 3, row['cy'] + 3)
    assert r > 150 and g < 90 and b < 90, (r, g, b)
    pctx.close(); ctx.close()




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
