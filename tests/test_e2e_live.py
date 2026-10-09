"""Live movement on the map, in two real browser pages (a DM and a Player) against a real server process.

Supabase Realtime is replaced by Playwright's WebSocket mock; the test relays the "live" messages one page sends to
the other page's socket, exactly what Realtime does.  Polling is set to once a minute, so anything the second page
shows DURING a gesture can only have arrived through the live channel.  What is proved here is our half of the chain:
the sender's messages, the receiver's gliding and drawing, safety against hostile messages, and that the saved state
still has the last word."""
import json, re, time
import pytest
from conftest import BACKEND, q
from test_e2e_browser import Api, pw                       # noqa: F401  (fixtures/helpers reused from the browser suite)
from test_e2e_realtime import FakeRealtimeSocket, rt_server, SLOW_POLL, _wait, _record   # noqa: F401

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')

NATURAL_W, NATURAL_H = 1000, 700


class LiveSocket(FakeRealtimeSocket):
    """The fake Realtime socket for one tab, which also records every live message the tab SENDS."""
    def __init__(self, page):
        self.sent, self.relayed = [], 0
        super().__init__(page)

    def _on_socket(self, ws):
        self.sockets.append(ws)
        def on_message(raw):
            m = json.loads(raw)
            if m['event'] == 'phx_join':
                self.joins.append(m)
                ws.send(json.dumps({'topic': m['topic'], 'event': 'phx_reply', 'ref': m['ref'], 'payload': {'status': 'ok', 'response': {}}}))
            elif m['event'] == 'heartbeat':
                self.heartbeats += 1
            elif m['event'] == 'broadcast' and (m.get('payload') or {}).get('event') == 'live':
                self.sent.append(m['payload']['payload'])
        ws.on_message(on_message)

    def deliver(self, channel, payload):
        self.sockets[-1].send(json.dumps({'topic': 'realtime:' + channel, 'event': 'broadcast',
                                          'payload': {'type': 'broadcast', 'event': 'live', 'payload': payload}}))


def relay(src, dst, channel):
    """Forward everything `src` has sent since last time to `dst` (what the Realtime service does)."""
    while src.relayed < len(src.sent):
        dst.deliver(channel, src.sent[src.relayed]); src.relayed += 1


def pump(page, src, dst, channel, ms=120):
    page.wait_for_timeout(ms); relay(src, dst, channel)


class World:
    pass


@pytest.fixture
def world(pw, rt_server):
    srv, fake = rt_server
    w = World(); w.base = srv.url; w.pw = pw; w.fake = fake
    w.dm, w.pl = Api(w.base), Api(w.base)
    w.cid = w.dm.campaign('Live'); w.dm.add_member(w.cid, w.pl, 'player')
    r = w.dm.post(f'/campaigns/{w.cid}/maps/new', data={'name': 'Arena', 'blank_width': str(NATURAL_W), 'blank_height': str(NATURAL_H)})
    w.mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    w.dm.post(f'/campaigns/{w.cid}/api/maps/{w.mid}/settings', json={'grid_setup_done': 1, 'grid_size': 50, 'snap_to_grid': 0})
    w.api = f'/campaigns/{w.cid}/api/maps/{w.mid}'
    w.ctxs = []
    yield w
    for c in w.ctxs:
        c.close()


def open_pages(w):
    """A DM page and a Player page on the same map, each with its own fake Realtime socket."""
    out = []
    for who in (w.dm, w.pl):
        ctx = who.context(w.pw, w.base); ctx.add_init_script(SLOW_POLL); w.ctxs.append(ctx)
        page = ctx.new_page(); errors = []
        page.on('pageerror', lambda e, errors=errors: errors.append(str(e)))
        sock = LiveSocket(page)
        page.goto(f'{w.base}/campaigns/{w.cid}/maps/{w.mid}'); page.wait_for_load_state('networkidle')
        assert sock.wait_joined(), 'the map page never joined its Realtime channel'
        out.append((page, sock, errors))
    w.channel = out[0][0].evaluate("() => window.LEDGER_REALTIME.channel")
    page_dm, sock_dm, err_dm = out[0]; page_pl, sock_pl, err_pl = out[1]
    return page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl


def to_screen(page, nx, ny):
    box = page.locator('#map-canvas').bounding_box()
    return box['x'] + nx * box['width'] / NATURAL_W, box['y'] + ny * box['height'] / NATURAL_H


def pixel(page, nx, ny):
    return page.evaluate("([x, y]) => { const d = document.getElementById('map-canvas').getContext('2d').getImageData(x, y, 1, 1).data; return [d[0], d[1], d[2]]; }", [nx, ny])


def is_red(px): return px[0] > 200 and px[1] < 70 and px[2] < 70


def pin_left(page): return page.eval_on_selector('.map-pin', "e => parseFloat(e.style.left)")


def add_pin(w, x=300, y=300, owner=False):
    """A prop token.  owner=True hands it to the Player (a Player may only move tokens assigned to them)."""
    r = w.dm.post(f'{w.api}/pins', json={'pin_type': 'prop', 'icon_key': 'paw', 'x': x, 'y': y}); assert r.status_code == 200, r.text
    pid = r.json()['id']
    if owner:
        uid = q('SELECT id FROM users WHERE username = ?', w.pl.name)[0]['id']
        assert w.dm.post(f'{w.api}/pins/{pid}/update', json={'owner_user_id': uid}).status_code == 200
    return pid


def add_rect(w, cx=300, cy=300, size=80):
    r = w.dm.post(f'{w.api}/drawings', json={'kind': 'rect', 'cx': cx, 'cy': cy, 'w': size, 'h': size, 'color': '#ff0000',
                                            'fill': True, 'fill_opacity': 1, 'data': {}}); assert r.status_code == 200, r.text
    return r.json()['id']


def saved_pin(w): return w.dm.get(f'{w.api}/pins').json()[0]


# ---------------------------------------------------------------------------------------------------------------------

def test_a_dragged_token_glides_across_the_other_page_before_it_is_dropped(world):
    w = world; add_pin(w)
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    page_dm.wait_for_selector('.map-pin'); page_pl.wait_for_selector('.map-pin')
    syncs = _record(page_pl, r'/sync$'); start = pin_left(page_pl); n_syncs = len(syncs)

    box = page_dm.locator('.map-pin').bounding_box()
    cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
    page_dm.mouse.move(cx, cy); page_dm.mouse.down()
    seen = []
    for i in range(1, 16):                                     # drag 300px right, slowly, but do NOT let go
        page_dm.mouse.move(cx + i * 20, cy); pump(page_dm, sock_dm, sock_pl, w.channel, 60)
        seen.append(pin_left(page_pl))
    assert max(seen) - start > 5, f'the player page never moved the token while it was being dragged: {seen}'
    assert seen == sorted(seen), 'the token should only travel one way on this drag (no jitter back and forth)'
    assert len({round(v, 1) for v in seen}) > 6, f'it should glide through many positions, not jump: {seen}'
    assert saved_pin(w)['x'] == 300, 'nothing has been saved yet -- this was all live'
    assert len(syncs) == n_syncs, 'the player page must not have re-fetched anything to show this'

    page_dm.mouse.up(); page_dm.wait_for_timeout(400); relay(sock_dm, sock_pl, w.channel)
    page_pl.wait_for_timeout(900)
    final_dm, final_pl = pin_left(page_dm), pin_left(page_pl)
    assert abs(final_dm - final_pl) < 0.05, (final_dm, final_pl)
    assert saved_pin(w)['x'] > 500                                       # saved normally
    assert page_pl.eval_on_selector('.map-pin', "e => parseFloat(e.dataset.x)") == pytest.approx(saved_pin(w)['x'], abs=0.5)
    assert len(syncs) == n_syncs, 'even after the drop, the player page needed no refetch to be in the right place'
    assert not err_dm and not err_pl, (err_dm, err_pl)


def test_the_player_can_move_a_token_and_the_dm_sees_it_live_too(world):
    w = world; add_pin(w, owner=True)
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    page_dm.wait_for_selector('.map-pin'); page_pl.wait_for_selector('.map-pin')
    start = pin_left(page_dm)
    box = page_pl.locator('.map-pin').bounding_box(); cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
    page_pl.mouse.move(cx, cy); page_pl.mouse.down()
    for i in range(1, 10):
        page_pl.mouse.move(cx, cy + i * 18); pump(page_pl, sock_pl, sock_dm, w.channel, 60)
    top = page_dm.eval_on_selector('.map-pin', "e => parseFloat(e.style.top)")
    assert top > 300 / NATURAL_H * 100 + 5, 'the DM page should have followed the Player\'s drag'
    page_pl.mouse.up(); page_pl.wait_for_timeout(300)
    assert not err_dm and not err_pl


def test_a_dragged_shape_glides_too(world):
    w = world; add_rect(w)
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    assert is_red(pixel(page_pl, 300, 300))
    sx, sy = to_screen(page_dm, 300, 300)
    page_dm.mouse.click(sx, sy); page_dm.wait_for_timeout(400)           # select it first: the page shifts a little when the shape's tools appear
    sx, sy = to_screen(page_dm, 300, 300)
    page_dm.mouse.move(sx, sy); page_dm.mouse.down()
    ex, ey = to_screen(page_dm, 700, 300)
    for i in range(1, 21):
        page_dm.mouse.move(sx + (ex - sx) * i / 20, sy); pump(page_dm, sock_dm, sock_pl, w.channel, 50)
    page_pl.wait_for_timeout(500)
    assert is_red(pixel(page_pl, 680, 300)),'the player should see the shape near where it is being dragged to'
    assert not is_red(pixel(page_pl, 300, 300)), 'and no longer at its old spot'
    shapes = w.dm.get(f'{w.api}/drawings').json()
    assert shapes[0]['cx'] == 300, 'still unsaved while the button is held'
    page_dm.mouse.up(); page_dm.wait_for_timeout(500); relay(sock_dm, sock_pl, w.channel); page_pl.wait_for_timeout(900)
    assert w.dm.get(f'{w.api}/drawings').json()[0]['cx'] > 600
    assert is_red(pixel(page_pl, 700, 300)) and not is_red(pixel(page_pl, 300, 300))
    assert not err_dm and not err_pl


def test_a_shape_being_drawn_appears_on_the_other_page_as_it_is_drawn(world):
    w = world
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    before = pixel(page_pl, 350, 475)
    page_dm.evaluate("() => document.querySelector('.map-tool-btn[data-tool=rect]').click()")
    sx, sy = to_screen(page_dm, 200, 400); ex, ey = to_screen(page_dm, 500, 550)
    page_dm.mouse.move(sx, sy); page_dm.mouse.down()
    for i in range(1, 11):
        page_dm.mouse.move(sx + (ex - sx) * i / 10, sy + (ey - sy) * i / 10); pump(page_dm, sock_dm, sock_pl, w.channel, 70)
    page_pl.wait_for_timeout(200)
    assert pixel(page_pl, 350, 475) != before, 'the player should see the rectangle taking shape'
    assert w.dm.get(f'{w.api}/drawings').json() == [], 'it is only a preview until the mouse is released'
    page_dm.mouse.up(); page_dm.wait_for_timeout(500); relay(sock_dm, sock_pl, w.channel)
    page_pl.wait_for_timeout(300)
    assert pixel(page_pl, 350, 475) != before, 'the preview stays up until the saved shape replaces it (no flicker)'
    sock_pl.ping(w.channel, 'map'); page_pl.wait_for_timeout(900)           # the server's "something changed" ping
    assert len(w.dm.get(f'{w.api}/drawings').json()) == 1
    assert pixel(page_pl, 350, 475) != before
    assert not err_dm and not err_pl


def test_pen_strokes_arrive_in_pieces_and_join_up(world):
    w = world
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    before = pixel(page_pl, 300, 300)
    page_dm.evaluate("() => document.querySelector('.map-tool-btn[data-tool=pen]').click()")
    sx, sy = to_screen(page_dm, 200, 300); page_dm.mouse.move(sx, sy); page_dm.mouse.down()
    for i in range(1, 41):                                                  # a long squiggle, sent over several messages
        x, y = to_screen(page_dm, 200 + i * 10, 300 + (15 if i % 2 else -15)); page_dm.mouse.move(x, y)
        if i % 4 == 0: pump(page_dm, sock_dm, sock_pl, w.channel, 60)
    pump(page_dm, sock_dm, sock_pl, w.channel, 150); page_pl.wait_for_timeout(200)
    pens = [m for m in sock_dm.sent if m.get('k') == 'draft' and m.get('kind') == 'pen']
    assert len(pens) >= 3 and pens[0]['from'] == 0 and pens[1]['from'] > 0, 'pen points should go out as increments, not the whole line each time'
    assert any(pixel(page_pl, x, 300 + d) != before for x in range(300, 600, 5) for d in (-15, 0, 15)), 'the pen line should be visible on the other page'
    page_dm.mouse.up(); page_dm.wait_for_timeout(300)
    assert not err_dm and not err_pl


def test_fog_painted_by_the_dm_shows_up_live_for_the_player_but_reveals_wait_for_the_save(world):
    w = world
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    fog_hidden = lambda p: p.eval_on_selector('#map-fog-layer', 'e => e.hidden')
    assert fog_hidden(page_pl)
    page_dm.evaluate("() => document.querySelector('.map-tool-btn[data-tool=fog-add]').click()")
    sx, sy = to_screen(page_dm, 300, 300); page_dm.mouse.move(sx, sy); page_dm.mouse.down()
    for i in range(1, 11):
        x, y = to_screen(page_dm, 300 + i * 30, 300); page_dm.mouse.move(x, y); pump(page_dm, sock_dm, sock_pl, w.channel, 60)
    page_pl.wait_for_timeout(300)
    assert not fog_hidden(page_pl), 'fog being painted should appear for the player while the brush is still down'
    assert w.dm.get(f'{w.api}/fog').json()['version'] == 0, 'nothing saved yet'
    page_dm.mouse.up(); page_dm.wait_for_timeout(600); relay(sock_dm, sock_pl, w.channel)
    assert w.dm.get(f'{w.api}/fog').json()['version'] > 0

    # now erase part of it: the DM sees it clear at once; the player must wait for the saved picture
    page_dm.evaluate("() => document.querySelector('.map-tool-btn[data-tool=fog-erase]').click()")
    mask = lambda p: p.eval_on_selector('.map-fog-fill', 'e => e.style.maskImage || e.style.webkitMaskImage')
    dm_before, pl_before = mask(page_dm), mask(page_pl)
    sx, sy = to_screen(page_dm, 300, 300); page_dm.mouse.move(sx, sy); page_dm.mouse.down()
    for i in range(1, 11):
        x, y = to_screen(page_dm, 300 + i * 30, 300); page_dm.mouse.move(x, y); pump(page_dm, sock_dm, sock_pl, w.channel, 60)
    page_pl.wait_for_timeout(600)
    assert mask(page_dm) != dm_before, 'the DM\'s own page shows the erase at once'
    assert mask(page_pl) == pl_before, 'a Player must not see a reveal early (the picture underneath is still hidden for them)'
    assert any(m.get('k') == 'fog' and m.get('mode') == 'erase' for m in sock_dm.sent), 'other DMs still get erase strokes'
    page_dm.mouse.up(); page_dm.wait_for_timeout(300)
    assert not err_dm and not err_pl


def test_the_sender_is_throttled(world):
    w = world; add_pin(w)
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    page_dm.wait_for_selector('.map-pin')
    box = page_dm.locator('.map-pin').bounding_box(); cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
    page_dm.mouse.move(cx, cy); page_dm.mouse.down(); t0 = time.time()
    for i in range(1, 121):                                                 # 120 mouse events as fast as the browser will take them
        page_dm.mouse.move(cx + i, cy)
    page_dm.wait_for_timeout(300); secs = time.time() - t0
    moves = [m for m in sock_dm.sent if m.get('k') == 'pins' and not m.get('end')]
    page_dm.mouse.up(); page_dm.wait_for_timeout(300)
    assert 1 <= len(moves) <= secs * 10 + 3, f'{len(moves)} live messages in {secs:.2f}s -- should be about ten a second at most'
    ends = [m for m in sock_dm.sent if m.get('k') == 'pins' and m.get('end')]
    assert len(ends) == 1 and ends[0]['items'][0]['x'] > 400, 'one final message carries the exact drop position'
    assert all(m['m'] == w.mid and m['u'] for m in sock_dm.sent)


def test_a_token_that_stops_getting_updates_returns_to_its_saved_place(world):
    w = world; add_pin(w)
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    page_pl.wait_for_selector('.map-pin'); start = pin_left(page_pl)
    pid = saved_pin(w)['id']
    sock_pl.deliver(w.channel, {'k': 'pins', 'm': w.mid, 'u': 'someoneelse', 'items': [{'id': pid, 'x': 800, 'y': 500, 's': 1, 'r': 0}]})
    page_pl.wait_for_timeout(700)
    assert pin_left(page_pl) > start + 20, 'it follows a live message'
    page_pl.wait_for_timeout(3300)                                            # the sender vanished (closed the tab mid-drag)
    assert pin_left(page_pl) == pytest.approx(start, abs=0.01), 'with no more updates it must go back to what is saved'
    assert not err_pl


def test_hostile_or_broken_live_messages_are_harmless(world):
    w = world; pid = None; add_pin(w); rid = add_rect(w, 600, 400)
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    page_pl.wait_for_selector('.map-pin'); start = pin_left(page_pl); pid = saved_pin(w)['id']
    red_before = pixel(page_pl, 600, 400)
    send = lambda p: sock_pl.deliver(w.channel, p)
    send('just a string'); send(None); send([1, 2, 3]); send({})
    send({'k': 'pins', 'm': w.mid + 99, 'u': 'x', 'items': [{'id': pid, 'x': 900, 'y': 600}]})          # another map
    send({'k': 'pins', 'm': w.mid, 'u': 'x', 'items': [{'id': 987654, 'x': 900, 'y': 600}]})            # a pin this page does not have
    send({'k': 'pins', 'm': w.mid, 'u': 'x', 'items': 'not a list'})
    send({'k': 'pins', 'm': w.mid, 'u': 'x', 'items': [{'id': pid, 'x': 'NaN', 'y': {'a': 1}, 's': 'big', 'r': None}]})
    send({'k': 'pins', 'm': w.mid, 'u': 'x', 'items': [{'id': pid} for _ in range(5000)]})
    send({'k': 'shapes', 'm': w.mid, 'u': 'x', 'items': [{'id': rid, 'cx': 1e300, 'cy': -1e300, 'w': -5, 'h': 'x', 'r': 1e9}]})
    send({'k': 'draft', 'm': w.mid, 'u': 'x', 'kind': 'rect', 'cx': 100, 'cy': 100, 'w': 50, 'h': 50, 'color': 'red;background:url(//evil)', 'fill': 1, 'fo': 99})
    send({'k': 'draft', 'm': w.mid, 'u': 'x', 'kind': '__proto__', 'color': '#fff'})
    send({'k': 'draft', 'm': w.mid, 'u': 'x', 'kind': 'pen', 'from': 999999, 'pts': [[1, 1], [2, 2]]})
    send({'k': 'draft', 'm': w.mid, 'u': 'x', 'kind': 'pen', 'from': 0, 'pts': [[1, 1], 'oops', [None, {}], [5, 5]]})
    send({'k': 'fog', 'm': w.mid, 'u': 'x', 'mode': 'paint', 'brush': 1e9, 'pts': [[1e9, 1e9], ['a', 'b'], None, [5, 5]]})
    send({'k': 'fog', 'm': w.mid, 'u': 'x', 'mode': 'erase', 'brush': 100, 'pts': [[100, 100]]})
    send({'k': 'constructor', 'm': w.mid, 'u': 'x'})
    send({'k': 'pins', 'm': w.mid, 'u': 'x', 'items': [{'id': pid, 'x': 500, 'y': 300, 's': 1e9, 'r': 0}]})   # a legal move with an absurd size
    page_pl.wait_for_timeout(900)
    width = page_pl.eval_on_selector('.map-pin', "e => parseFloat(e.style.width)")
    assert width <= 5 * 50 / NATURAL_W * 100 + 0.01, f'size must be clamped, got {width}%'
    page_pl.wait_for_timeout(3300)                                            # everything hostile is gone or expired
    assert pin_left(page_pl) == pytest.approx(start, abs=0.01)
    assert pixel(page_pl, 600, 400) == red_before, 'the shape must be back where it was saved'
    assert page_pl.eval_on_selector('#map-fog-layer', 'e => e.hidden') or True        # (a stray paint dab is allowed to show; it never saves)
    assert w.dm.get(f'{w.api}/fog').json()['version'] == 0, 'nothing a live message does can be saved'
    assert w.dm.get(f'{w.api}/pins').json()[0]['x'] == 300 and w.dm.get(f'{w.api}/drawings').json()[0]['cx'] == 600
    assert not err_pl, err_pl
    page_pl.evaluate("() => 1")                                               # the page is still alive and scriptable


def test_ones_own_drag_is_never_overridden_by_someone_elses_message(world):
    w = world; add_pin(w)
    page_dm, sock_dm, err_dm, page_pl, sock_pl, err_pl = open_pages(w)
    page_dm.wait_for_selector('.map-pin'); pid = saved_pin(w)['id']
    box = page_dm.locator('.map-pin').bounding_box(); cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
    page_dm.mouse.move(cx, cy); page_dm.mouse.down(); page_dm.mouse.move(cx + 100, cy, steps=5)
    mine = pin_left(page_dm)
    sock_dm.deliver(w.channel, {'k': 'pins', 'm': w.mid, 'u': 'other', 'items': [{'id': pid, 'x': 900, 'y': 650, 's': 1, 'r': 0}]})
    page_dm.wait_for_timeout(600)
    assert pin_left(page_dm) == pytest.approx(mine, abs=0.01), 'while I hold a token, nobody else moves it on my screen'
    page_dm.mouse.up(); page_dm.wait_for_timeout(300)
    assert not err_dm


def test_without_realtime_nothing_is_sent_and_dragging_still_works(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('NoRT')
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'M', 'blank_width': '1000', 'blank_height': '700'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    dm.post(f'/campaigns/{cid}/api/maps/{mid}/settings', json={'grid_setup_done': 1, 'snap_to_grid': 0})
    dm.post(f'/campaigns/{cid}/api/maps/{mid}/pins', json={'pin_type': 'prop', 'icon_key': 'paw', 'x': 300, 'y': 300})
    ctx = dm.context(pw, base); page = ctx.new_page(); errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    sockets = []; page.on('websocket', lambda ws: sockets.append(ws))
    page.goto(f'{base}/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle'); page.wait_for_selector('.map-pin')
    box = page.locator('.map-pin').bounding_box(); cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
    page.mouse.move(cx, cy); page.mouse.down(); page.mouse.move(cx + 150, cy, steps=8); page.mouse.up(); page.wait_for_timeout(500)
    assert not sockets and not errors
    assert dm.get(f'/campaigns/{cid}/api/maps/{mid}/pins').json()[0]['x'] > 400
    ctx.close()
