"""Map ruler (DL-153): feet per square is a per-map setting, and the ruler measures in squares and feet."""
import os
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def test_feet_per_square_is_a_clamped_per_map_setting(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, pl)
    mid = dm.new_map(cid)
    assert q('SELECT feet_per_square FROM maps WHERE id = ?', mid)[0]['feet_per_square'] == 5
    url = f'/campaigns/{cid}/api/maps/{mid}/settings'
    assert dm.json(url, {'feet_per_square': 10}).get_json()['feet_per_square'] == 10
    assert dm.json(url, {'feet_per_square': 0}).get_json()['feet_per_square'] == 1
    assert dm.json(url, {'feet_per_square': 9999}).get_json()['feet_per_square'] == 100
    assert dm.json(url, {'feet_per_square': 'far'}).status_code == 400
    assert q('SELECT feet_per_square FROM maps WHERE id = ?', mid)[0]['feet_per_square'] == 100
    dm.json(url, {'feet_per_square': 10})
    assert pl.get(f'/campaigns/{cid}/api/maps/{mid}/sync').get_json()['state']['feet_per_square'] == 10      # everyone's ruler follows it live
    assert '"feet_per_square": 10' in pl.get(f'/campaigns/{cid}/maps/{mid}').data.decode()


playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, pw                      # noqa: F401,E402
import re


def _new_map(api, cid):
    r = api.post(f'/campaigns/{cid}/maps/new', data={'name': 'Ruler map', 'blank_width': '400', 'blank_height': '300'})
    return int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))


def _open(dm, pw, base, cid, mid):
    dm.post(f'/campaigns/{cid}/api/maps/{mid}/settings', json={'grid_setup_done': 1, 'grid_size': 50})
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    page.goto(f'{base}/campaigns/{cid}/maps/{mid}')
    page.wait_for_selector('#map-canvas')
    return ctx, page


def _drag(page, squares_x, squares_y=0, start=(60, 60)):
    box = page.locator('#map-canvas').bounding_box()
    k = box['width'] / 400                                           # the blank map is 400 wide; 50px grid
    x0, y0 = box['x'] + start[0] * k, box['y'] + start[1] * k
    page.mouse.move(x0, y0); page.mouse.down()
    page.mouse.move(x0 + squares_x * 50 * k, y0 + squares_y * 50 * k, steps=5)
    page.mouse.up()


def test_ruler_reads_squares_and_feet_and_follows_the_setting(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Ruler')
    mid = _new_map(dm, cid)
    ctx, page = _open(dm, pw, base, cid, mid)
    readout = page.locator('#ruler-readout')
    page.click('[data-tool="ruler"]')
    _drag(page, 4)
    if os.environ.get('SHOT_DIR'):
        page.screenshot(path=os.path.join(os.environ['SHOT_DIR'], 'ruler.png'))
    playwright.expect(readout).to_have_text('4 squares · 20 ft')
    _drag(page, 3, 2)                                                # a diagonal step counts as one square (5e)
    playwright.expect(readout).to_have_text('3 squares · 15 ft')
    _drag(page, 1)
    playwright.expect(readout).to_have_text('1 square · 5 ft')
    page.keyboard.press('Escape')
    playwright.expect(readout).to_have_text('')
    # the DM changes the scale in the grid settings
    page.click('[data-tool="select"]')
    page.click('#grid-settings-btn')
    if os.environ.get('SHOT_DIR'):
        page.wait_for_timeout(700)
        page.screenshot(path=os.path.join(os.environ['SHOT_DIR'], 'ruler-settings.png'))
    page.fill('#setup-feet-per-square', '10')
    page.click('#setup-confirm-btn')
    page.wait_for_function("() => document.getElementById('map-setup-overlay').hidden")
    assert q('SELECT feet_per_square FROM maps WHERE id = ?', mid)[0]['feet_per_square'] == 10
    page.click('[data-tool="ruler"]')
    _drag(page, 4)
    playwright.expect(readout).to_have_text('4 squares · 40 ft')
    page.click('[data-tool="select"]')                               # another tool puts the ruler away
    playwright.expect(readout).to_have_text('')
    ctx.close()


def test_players_can_measure_on_a_locked_map(shared_server, pw):
    base = shared_server.url
    dm, pl = Api(base), Api(base)
    cid = dm.campaign('Locked ruler')
    dm.add_member(cid, pl)
    mid = _new_map(dm, cid)
    dm.post(f'/campaigns/{cid}/api/maps/{mid}/settings', json={'grid_setup_done': 1, 'locked_for_players': 1})
    ctx = pl.context(pw, base)
    page = ctx.new_page()
    page.goto(f'{base}/campaigns/{cid}/maps/{mid}')
    page.wait_for_selector('#map-canvas')
    page.click('[data-tool="ruler"]')
    _drag(page, 2)
    playwright.expect(page.locator('#ruler-readout')).to_have_text('2 squares · 10 ft')
    ctx.close()
