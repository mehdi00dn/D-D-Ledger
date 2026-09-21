"""Live-refresh behaviour in a real browser: consolidated requests, pause while hidden, backoff, live updates."""
import re, time
import pytest
from conftest import BACKEND
from test_e2e_browser import Api, pw            # fixtures/helpers reused from the browser suite

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')

FAST = "window.__LEDGER_POLL__ = {base: 250, max: 2000, step: 2, skip: 250};"       # test-only speed-up


def _ctx(api, browser, base, fast=True):
    ctx = api.context(browser, base)
    if fast:
        ctx.add_init_script(FAST)
    return ctx


def _record(page, pattern):
    """Timestamps of requests whose URL matches the regex `pattern` (API calls only, not static assets)."""
    hits, rx = [], re.compile(pattern)
    page.on('request', lambda r: hits.append((time.time(), r.url)) if rx.search(r.url) else None)
    return hits


def _set_hidden(page, hidden):
    page.evaluate("""(h) => {
        Object.defineProperty(document, 'hidden', {configurable: true, get: () => h});
        Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => h ? 'hidden' : 'visible'});
        document.dispatchEvent(new Event('visibilitychange'));
    }""", hidden)


def _map_setup(base):
    api = Api(base); cid = api.campaign('Poll')
    r = api.post(f'/campaigns/{cid}/maps/new', data={'name': 'M', 'blank_width': '600', 'blank_height': '400'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    api.post(f'/campaigns/{cid}/api/maps/{mid}/settings', json={'grid_setup_done': 1})
    return api, cid, mid


def test_map_page_refreshes_through_one_sync_request_not_three(pw, shared_server):
    base = shared_server.url
    api, cid, mid = _map_setup(base)
    ctx = _ctx(api, pw, base); page = ctx.new_page()
    sync, state = _record(page, r'/api/maps/\d+/sync$'), _record(page, r'/api/maps/\d+/state$')
    drawings, pins = _record(page, r'/api/maps/\d+/drawings$'), _record(page, r'/api/maps/\d+/pins$')
    page.goto(f'{base}/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle')
    page.wait_for_timeout(4000)
    assert len(sync) >= 4, f'expected steady /sync refreshes, saw {len(sync)}'
    assert len(state) == 0, 'the old state poller must be gone'
    assert len(drawings) <= 1 and len(pins) <= 1, 'drawings/pins are fetched once at load, never polled'
    ctx.close()


def test_nothing_is_requested_while_the_tab_is_hidden_and_it_catches_up_when_shown(pw, shared_server):
    base = shared_server.url
    api, cid, mid = _map_setup(base)
    ctx = _ctx(api, pw, base); page = ctx.new_page()
    sync = _record(page, r'/api/maps/\d+/sync$')
    page.goto(f'{base}/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle')
    page.wait_for_timeout(1500)
    assert len(sync) >= 2                                            # polling while visible
    _set_hidden(page, True); page.wait_for_timeout(600)              # let any in-flight request finish
    frozen = len(sync)
    page.wait_for_timeout(3000)                                      # ~12 poll periods at the test speed
    assert len(sync) == frozen, f'{len(sync) - frozen} requests were made while hidden'
    t0 = time.time(); _set_hidden(page, False)
    page.wait_for_timeout(900)
    assert len(sync) > frozen and sync[frozen][0] - t0 < 1.0, 'must refresh promptly when the tab is shown again'
    ctx.close()


def test_battle_screen_also_pauses_when_hidden(pw, shared_server):
    base = shared_server.url; api = Api(base); cid = api.campaign('BPoll')
    ctx = _ctx(api, pw, base); page = ctx.new_page(); reqs = _record(page, r'/api/battle$')
    page.goto(f'{base}/campaigns/{cid}/battle'); page.wait_for_load_state('networkidle'); page.wait_for_timeout(1200)
    assert len(reqs) >= 2
    _set_hidden(page, True); page.wait_for_timeout(600); frozen = len(reqs)
    page.wait_for_timeout(2500)
    assert len(reqs) == frozen
    _set_hidden(page, False); page.wait_for_timeout(900)
    assert len(reqs) > frozen
    ctx.close()


def test_idle_polling_backs_off_and_snaps_back_when_something_changes(pw, shared_server):
    base = shared_server.url; api = Api(base); cid = api.campaign('Backoff')
    hero = api.character(cid, 'Hero')
    ctx = _ctx(api, pw, base); page = ctx.new_page(); reqs = _record(page, r'/api/battle$')
    page.goto(f'{base}/campaigns/{cid}/battle'); page.wait_for_load_state('networkidle')
    page.wait_for_timeout(9000)                                      # idle: nothing changes
    times = [t for t, _ in reqs]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert len(gaps) >= 5
    assert gaps[-1] > gaps[0] * 2.5, f'polling should slow down while idle: {[round(g, 2) for g in gaps]}'
    assert max(gaps) <= 2.6                                          # ...but never beyond the configured ceiling (2 s + jitter)
    # someone else changes the battle -> the very next poll sees it and the pace resets
    api.s.post(f'{base}/campaigns/{cid}/api/battle/add', json={'character_id': hero})
    page.wait_for_function("() => document.body.innerText.includes('Hero')", timeout=6000)
    seen_at = time.time()
    page.wait_for_timeout(2500)
    after = [t for t, _ in reqs if t > seen_at]
    assert len(after) >= 3, 'after a change polling must return to full speed'
    ctx.close()


def test_other_users_edits_appear_live_without_reload(pw, shared_server):
    base = shared_server.url
    dm, pl = Api(base), Api(base); cid = dm.campaign('Live')
    dm.post(f'/campaigns/{cid}/members/add', data={'username': pl.name, 'status': 'player'})
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'M', 'blank_width': '600', 'blank_height': '400'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    dm.post(f'/campaigns/{cid}/api/maps/{mid}/settings', json={'grid_setup_done': 1})
    hero = dm.character(cid, 'Hero')
    pctx = _ctx(pl, pw, base); page = pctx.new_page()
    page.goto(f'{base}/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle')
    red = "() => { const c = document.getElementById('map-canvas'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i] > 200 && d[i+1] < 60 && d[i+2] < 60 && d[i+3] > 200) n++; return n; }"
    assert page.evaluate(red) == 0
    # 1) a drawing made by the DM shows up on the player's canvas
    dm.s.post(f'{base}/campaigns/{cid}/api/maps/{mid}/drawings', json={'kind': 'rect', 'data': {}, 'cx': 300, 'cy': 200, 'w': 240, 'h': 160, 'color': '#ff0000', 'fill': 1, 'fill_opacity': 1})
    page.wait_for_function(f"() => ({red})() > 500", timeout=8000)
    # 2) the DM locks the map -> the player's banner appears
    assert page.locator('#map-locked-banner').is_hidden()
    dm.s.post(f'{base}/campaigns/{cid}/api/maps/{mid}/settings', json={'locked_for_players': 1})
    page.wait_for_selector('#map-locked-banner:not([hidden])', timeout=8000)
    # 3) linking the map to the battle brings pins in for the player
    dm.s.post(f'{base}/campaigns/{cid}/api/battle/add', json={'character_id': hero})
    dm.s.post(f'{base}/campaigns/{cid}/api/maps/{mid}/settings', json={'linked_to_battle': 1})
    page.wait_for_selector('.map-pin', timeout=8000)
    pctx.close()
