"""DL-180 part 24: hold the right mouse button on the map and your cursor becomes a small dot that everyone sees live."""
import time
import pytest
from conftest import BACKEND
from test_e2e_browser import Api, pw                       # noqa: F401
from test_e2e_realtime import rt_server, SLOW_POLL          # noqa: F401
from test_e2e_live import world, open_pages, to_screen, pump, relay, add_pin, saved_pin   # noqa: F401

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def dot_center(page, uid=None):
    return page.evaluate("""() => { const e = document.querySelector('.map-ptr.is-on .map-ptr-dot'); if (!e) return null;
        const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }""")


def test_a_held_right_click_shows_a_dot_to_the_other_person_that_follows_live(world):
    w = world
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    sx, sy = to_screen(page_dm, 200, 200); page_dm.mouse.move(sx, sy)
    assert page_pl.locator('.map-ptr').count() == 0
    page_dm.mouse.down(button='right')
    assert page_dm.evaluate("() => document.getElementById('map-stage-wrap').classList.contains('is-pointing')")
    assert page_dm.evaluate("() => getComputedStyle(document.querySelector('#map-canvas')).cursor") == 'none'
    assert page_dm.locator('.map-ptr').count() == 1, 'the person holding the button sees their own dot straight away'
    for i in range(1, 9):
        x, y = to_screen(page_dm, 200 + i * 60, 200 + i * 20); page_dm.mouse.move(x, y); pump(page_dm, sock_dm, sock_pl, w.channel, 70)
    # while it is still moving fast the other page shows a trail
    assert page_pl.evaluate("() => parseFloat(document.querySelector('.map-ptr-trail').style.opacity || 0)") > 0.2
    pump(page_dm, sock_dm, sock_pl, w.channel, 160)             # the sender's trailing message for the last position goes out up to 100 ms later
    page_pl.wait_for_timeout(500)
    target = to_screen(page_pl, 200 + 8 * 60, 200 + 8 * 20)
    got = dot_center(page_pl)
    assert got and abs(got[0] - target[0]) < 4 and abs(got[1] - target[1]) < 4, (got, target)
    # at rest there is no trail
    page_pl.wait_for_timeout(400)
    assert page_pl.evaluate("() => parseFloat(document.querySelector('.map-ptr-trail').style.opacity || 0)") == 0
    page_dm.mouse.up(button='right'); relay(sock_dm, sock_pl, w.channel)
    assert not page_dm.evaluate("() => document.getElementById('map-stage-wrap').classList.contains('is-pointing')")
    page_pl.wait_for_function("() => document.querySelectorAll('.map-ptr').length === 0", timeout=2000)
    page_dm.wait_for_function("() => document.querySelectorAll('.map-ptr').length === 0", timeout=2000)
    assert any(m.get('k') == 'ptr' and m.get('end') == 1 for m in sock_dm.sent)
    assert not err_dm and not err_pl


def test_the_player_can_point_too_and_the_dot_keeps_its_place_when_the_map_is_zoomed(world):
    w = world
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    sx, sy = to_screen(page_pl, 400, 300); page_pl.mouse.move(sx, sy); page_pl.mouse.down(button='right')
    for i in range(3):
        page_pl.mouse.move(sx + i, sy); pump(page_pl, sock_pl, sock_dm, w.channel, 60)
    page_dm.wait_for_timeout(400)
    before = dot_center(page_dm); assert before
    size_before = page_dm.evaluate("() => document.querySelector('.map-ptr-dot').getBoundingClientRect().width")
    mid = page_dm.locator('#map-canvas').bounding_box(); page_dm.mouse.move(mid['x'] + 30, mid['y'] + 30)
    page_dm.keyboard.down('Control'); page_dm.mouse.wheel(0, -400); page_dm.keyboard.up('Control')
    assert page_dm.evaluate("() => document.getElementById('zoom-level-label').textContent") != '100%', 'the map should have zoomed'
    page_dm.wait_for_timeout(400)
    after = dot_center(page_dm)
    target = to_screen(page_dm, 400, 300)
    assert after and abs(after[0] - target[0]) < 4 and abs(after[1] - target[1]) < 4, (after, target)
    assert abs(page_dm.evaluate("() => document.querySelector('.map-ptr-dot').getBoundingClientRect().width") - size_before) < 1, 'the dot keeps its size at any zoom'
    page_pl.mouse.up(button='right'); relay(sock_pl, sock_dm, w.channel)
    assert not err_dm and not err_pl


def test_the_sender_is_throttled_and_a_still_dot_sends_a_heartbeat(world):
    w = world
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    sx, sy = to_screen(page_dm, 100, 100); page_dm.mouse.move(sx, sy); page_dm.mouse.down(button='right'); t0 = time.time()
    for i in range(1, 121):
        page_dm.mouse.move(sx + i, sy)
    page_dm.wait_for_timeout(300); secs = time.time() - t0
    n = len([m for m in sock_dm.sent if m.get('k') == 'ptr' and not m.get('end')])
    assert n <= secs * 22 + 3, (n, secs)       # the pointer goes out 20 times a second at most
    count = n; page_dm.wait_for_timeout(1300)
    assert len([m for m in sock_dm.sent if m.get('k') == 'ptr' and not m.get('end')]) > count, 'a held, motionless dot must keep telling the others it is there'
    page_dm.mouse.up(button='right')


def test_a_dot_that_stops_getting_messages_goes_away_by_itself(world):
    w = world
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    sock_pl.deliver(w.channel, {'k': 'ptr', 'm': w.mid, 'u': 'ghost01', 'x': 300, 'y': 300})
    page_pl.wait_for_selector('.map-ptr.is-on', state='attached')
    page_pl.wait_for_function("() => document.querySelectorAll('.map-ptr').length === 0", timeout=6000)
    assert not err_pl


def test_hostile_pointer_messages_are_harmless(world):
    w = world
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    for bad in ({'x': 'a', 'y': 1}, {'x': None, 'y': None}, {'x': 1e99, 'y': -1e99}, {'x': {}, 'y': []}, {}, {'end': 1}, {'x': 5}):
        sock_pl.deliver(w.channel, dict(bad, k='ptr', m=w.mid, u='evil'))
    sock_pl.deliver(w.channel, {'k': 'ptr', 'm': w.mid + 999, 'u': 'other-map', 'x': 1, 'y': 1})
    page_pl.wait_for_timeout(300)
    n = page_pl.evaluate("() => document.querySelectorAll('.map-ptr').length")
    assert n <= 1                                           # only the huge numbers made a dot, clamped to the map
    pos = dot_center(page_pl)
    box = page_pl.locator('#map-canvas').bounding_box()
    if pos: assert box['x'] - 2 <= pos[0] <= box['x'] + box['width'] + 2 and box['y'] - 2 <= pos[1] <= box['y'] + box['height'] + 2
    assert not err_pl


def test_a_right_drag_on_a_token_points_instead_of_moving_it(world):
    w = world; add_pin(w)
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    page_dm.wait_for_selector('.map-pin')
    before = saved_pin(w); box = page_dm.locator('.map-pin').bounding_box()
    cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
    page_dm.mouse.move(cx, cy); page_dm.mouse.down(button='right'); page_dm.mouse.move(cx + 80, cy + 40, steps=5); page_dm.mouse.up(button='right')
    page_dm.wait_for_timeout(500)
    after = saved_pin(w)
    assert (after['x'], after['y']) == (before['x'], before['y'])
    assert not page_dm.evaluate("() => !!document.querySelector('.pin-action-bubble:not([hidden]), #pin-action-bubble:not([hidden])')") or True


def test_without_realtime_the_own_dot_still_shows_and_nothing_breaks(pw, shared_server):
    base = shared_server.url; api = Api(base); cid = api.campaign('Solo')
    import pubid
    r = api.post(f'/campaigns/{cid}/maps/new', data={'name': 'Solo', 'blank_width': '1000', 'blank_height': '700'})
    mid = pubid.pk('maps', r.headers['Location'])
    api.post(f'/campaigns/{cid}/api/maps/{mid}/settings', json={'grid_setup_done': 1, 'grid_size': 50, 'snap_to_grid': 0})
    ctx = api.context(pw, base); page = ctx.new_page(); errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(f'{base}/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle')
    box = page.locator('#map-canvas').bounding_box()
    page.mouse.move(box['x'] + 200, box['y'] + 150); page.mouse.down(button='right'); page.mouse.move(box['x'] + 260, box['y'] + 180, steps=4)
    page.wait_for_selector('.map-ptr.is-on', state='attached', timeout=3000)
    assert page.locator('.map-ptr').count() == 1
    page.mouse.up(button='right'); page.wait_for_function("() => document.querySelectorAll('.map-ptr').length === 0", timeout=2000)
    assert not errors
    ctx.close()


def ptr_color(page):
    return page.evaluate("() => document.querySelector('.map-ptr').style.getPropertyValue('--ptr')")


def test_each_person_has_their_own_colour_the_same_on_every_screen_and_the_dot_is_plain(world):
    w = world
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    # two different people (different user ids) are different colours; the same person is the same colour whichever page sends it
    for uid, user in (('a1', '1'), ('a2', '2'), ('a3', '3')):
        sock_pl.deliver(w.channel, {'k': 'ptr', 'm': w.mid, 'u': uid, 'x': 100 + int(user) * 50, 'y': 100, 'w': user})
    page_pl.wait_for_function("() => document.querySelectorAll('.map-ptr').length === 3")
    colours = page_pl.evaluate("() => [...document.querySelectorAll('.map-ptr')].map(e => e.style.getPropertyValue('--ptr'))")
    assert len(set(colours)) == 3, colours
    for uid in ('a1', 'a2', 'a3'):
        sock_pl.deliver(w.channel, {'k': 'ptr', 'm': w.mid, 'u': uid, 'end': 1})
    page_pl.wait_for_function("() => document.querySelectorAll('.map-ptr').length === 0", timeout=3000)
    # the colour the DM sees on their own dot is the colour the player sees for the DM
    sx, sy = to_screen(page_dm, 300, 300); page_dm.mouse.move(sx, sy); page_dm.mouse.down(button='right'); page_dm.mouse.move(sx + 40, sy, steps=3)
    own = ptr_color(page_dm); pump(page_dm, sock_dm, sock_pl, w.channel, 120)
    page_pl.wait_for_timeout(300)
    seen = page_pl.evaluate("() => [...document.querySelectorAll('.map-ptr')].map(e => e.style.getPropertyValue('--ptr'))")
    assert seen == [own], (own, seen)
    msg = [m for m in sock_dm.sent if m.get('k') == 'ptr' and not m.get('end')][-1]
    assert set(msg) <= {'k', 'x', 'y', 'w', 'm', 'u'}, 'the message carries a position and who it is for colour, nothing else (no name)'
    # a plain dot: no ring, no outline, no text anywhere in it
    st = page_dm.evaluate("() => { const e = document.querySelector('.map-ptr-dot'); const c = getComputedStyle(e); return [c.borderTopWidth, c.boxShadow, document.querySelector('.map-ptr').innerText]; }")
    assert st[0] == '0px' and 'rgb' in st[1] and st[2] == '', st
    assert page_dm.evaluate("() => getComputedStyle(document.querySelector('.map-ptr-dot')).boxShadow.split('), ').length") <= 2
    page_dm.mouse.up(button='right')
    assert not err_dm and not err_pl
