"""Real-time wake-ups in a real browser against a real server process.

The Realtime service itself is replaced by the fake REST endpoint (server -> Realtime) and by Playwright's
WebSocket mock (Realtime -> browser), so what is proved here is OUR half of the chain: the server pings the
channel the page subscribed to, and the page refreshes the moment a ping arrives.  Polling is set to once a
minute, so a refresh within a couple of seconds can only have come from the push."""
import json, re, time
import pytest
from conftest import BACKEND, Server
from fake_supabase import FakeSupabase
from test_e2e_browser import Api, pw            # fixtures/helpers reused from the browser suite

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')

SLOW_POLL = "window.__LEDGER_POLL__ = {base: 60000, max: 60000, skip: 60000};"


@pytest.fixture
def rt_server():
    fake = FakeSupabase(key='sb_secret_E2E').start()
    srv = Server({'SUPABASE_URL': fake.url, 'SUPABASE_SECRET_KEY': 'sb_secret_E2E', 'SUPABASE_PUBLISHABLE_KEY': 'sb_publishable_E2E',
                  'STORAGE_BACKEND': 'local'})
    yield srv, fake
    srv.stop(); fake.stop()


class FakeRealtimeSocket:
    """Plays the Realtime service for one browser tab: accepts the join, lets the test push pings, can drop the link."""
    def __init__(self, page):
        self.page = page
        self.sockets, self.joins, self.heartbeats = [], [], 0
        page.route_web_socket(re.compile(r'.*/realtime/v1/websocket.*'), self._on_socket)

    def _on_socket(self, ws):
        self.sockets.append(ws)
        def on_message(raw):
            m = json.loads(raw)
            if m['event'] == 'phx_join':
                self.joins.append(m)
                ws.send(json.dumps({'topic': m['topic'], 'event': 'phx_reply', 'ref': m['ref'], 'payload': {'status': 'ok', 'response': {}}}))
            elif m['event'] == 'heartbeat':
                self.heartbeats += 1
        ws.on_message(on_message)

    def ping(self, channel, scope='both'):
        self.sockets[-1].send(json.dumps({'topic': 'realtime:' + channel, 'event': 'broadcast',
                                          'payload': {'type': 'broadcast', 'event': 'changed', 'payload': {'s': scope}}}))

    def wait_joined(self, n=1, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end and len(self.joins) < n:
            self.page.wait_for_timeout(50)            # Playwright only delivers browser events while we are inside one of its calls
        return len(self.joins) >= n


def _record(page, pattern):
    hits, rx = [], re.compile(pattern)
    page.on('request', lambda r: hits.append(time.time()) if rx.search(r.url) else None)
    return hits


def _wait(page, fn, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        page.wait_for_timeout(50)                     # (time.sleep would starve Playwright's event delivery)
    return False


def _setup(srv):
    base = srv.url
    dm, pl = Api(base), Api(base); cid = dm.campaign('Push')
    hero = dm.character(cid, 'Hero'); dm.add_member(cid, pl, 'player')
    ctx = pl.context(pw_browser(), base); ctx.add_init_script(SLOW_POLL)
    return base, dm, pl, cid, hero, ctx


_browser = {}
def pw_browser():
    return _browser['b']


def test_a_battle_change_reaches_an_open_player_page_at_once(pw, rt_server):
    srv, fake = rt_server; _browser['b'] = pw
    base, dm, pl, cid, hero, ctx = _setup(srv)
    page = ctx.new_page(); sock = FakeRealtimeSocket(page); battle_reqs = _record(page, r'/api/battle$')
    page.goto(f'{base}/campaigns/{cid}/battle'); page.wait_for_load_state('networkidle')
    assert sock.wait_joined(), 'the page never joined its Realtime channel'
    channel = page.evaluate("() => window.LEDGER_REALTIME.channel")
    assert sock.joins[0]['topic'] == 'realtime:' + channel

    dm.s.post(f'{base}/campaigns/{cid}/api/battle/add', json={'character_id': hero})
    assert _wait(page, lambda: fake.broadcasts), 'the server never pinged Realtime'
    assert fake.broadcasts[0]['topic'] == channel, 'the server pinged a different channel than the page listens on'
    page.wait_for_timeout(1500)
    assert 'Hero' not in page.inner_text('body'), 'without the push, a once-a-minute poll must not have shown it'

    before = len(battle_reqs); sock.ping(channel, 'battle')
    page.wait_for_function("() => document.body.innerText.includes('Hero')", timeout=4000)
    assert len(battle_reqs) > before
    ctx.close()


def test_the_map_page_refreshes_on_a_ping_too(pw, rt_server):
    srv, fake = rt_server; _browser['b'] = pw
    base, dm, pl, cid, hero, ctx = _setup(srv)
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'M', 'blank_width': '600', 'blank_height': '400'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    dm.post(f'/campaigns/{cid}/api/maps/{mid}/settings', json={'grid_setup_done': 1})
    page = ctx.new_page(); sock = FakeRealtimeSocket(page); syncs = _record(page, r'/api/maps/\d+/sync$')
    page.goto(f'{base}/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle')
    assert sock.wait_joined()
    channel = page.evaluate("() => window.LEDGER_REALTIME.channel")
    page.wait_for_timeout(500); before = len(syncs)
    sock.ping(channel, 'map')
    assert _wait(page, lambda: len(syncs) > before, 3.0), 'a ping must trigger an immediate /sync'
    sock.ping(channel, 'battle')                                         # battle changes move linked pins, so maps refresh for them too
    n = len(syncs)
    assert _wait(page, lambda: len(syncs) > n, 3.0)
    ctx.close()


def test_a_battle_page_ignores_map_only_pings(pw, rt_server):
    srv, fake = rt_server; _browser['b'] = pw
    base, dm, pl, cid, hero, ctx = _setup(srv)
    page = ctx.new_page(); sock = FakeRealtimeSocket(page); reqs = _record(page, r'/api/battle$')
    page.goto(f'{base}/campaigns/{cid}/battle'); page.wait_for_load_state('networkidle')
    assert sock.wait_joined(); channel = page.evaluate("() => window.LEDGER_REALTIME.channel")
    page.wait_for_timeout(400); n = len(reqs)
    sock.ping(channel, 'map'); page.wait_for_timeout(1200)
    assert len(reqs) == n, 'drawing on a map should not make battle pages refetch'
    sock.ping(channel, 'both')
    assert _wait(page, lambda: len(reqs) > n, 3.0)
    ctx.close()


def test_pings_for_another_channel_are_ignored(pw, rt_server):
    srv, fake = rt_server; _browser['b'] = pw
    base, dm, pl, cid, hero, ctx = _setup(srv)
    page = ctx.new_page(); sock = FakeRealtimeSocket(page); reqs = _record(page, r'/api/battle$')
    page.goto(f'{base}/campaigns/{cid}/battle'); page.wait_for_load_state('networkidle')
    assert sock.wait_joined(); page.wait_for_timeout(400); n = len(reqs)
    sock.ping('lc-someone-elses-campaign'); page.wait_for_timeout(1200)
    assert len(reqs) == n
    ctx.close()


def test_the_page_reconnects_after_the_socket_drops(pw, rt_server):
    srv, fake = rt_server; _browser['b'] = pw
    base, dm, pl, cid, hero, ctx = _setup(srv)
    page = ctx.new_page(); sock = FakeRealtimeSocket(page)
    page.goto(f'{base}/campaigns/{cid}/battle'); page.wait_for_load_state('networkidle')
    assert sock.wait_joined(1)
    sock.sockets[-1].close()
    assert sock.wait_joined(2, timeout=8.0), 'the page should rejoin on its own after a dropped connection'
    ctx.close()


def test_without_realtime_the_page_just_polls(pw):
    srv = Server({'SUPABASE_URL': '', 'SUPABASE_SECRET_KEY': '', 'SUPABASE_PUBLISHABLE_KEY': '', 'STORAGE_BACKEND': 'local'})
    try:
        base = srv.url; dm = Api(base); cid = dm.campaign('NoRT')
        ctx = dm.context(pw, base); ctx.add_init_script("window.__LEDGER_POLL__ = {base: 250, max: 500, skip: 250};")
        page = ctx.new_page(); sockets = []
        page.on('websocket', lambda w: sockets.append(w)); reqs = _record(page, r'/api/battle$')
        page.goto(f'{base}/campaigns/{cid}/battle'); page.wait_for_load_state('networkidle'); page.wait_for_timeout(1500)
        assert not page.evaluate("() => 'LEDGER_REALTIME' in window") and not sockets
        assert len(reqs) >= 3
        ctx.close()
    finally:
        srv.stop()


def test_a_poke_during_a_request_in_flight_triggers_another_refresh(pw, shared_server):
    """The answer already on its way may predate the change, so a ping that lands mid-request must cause a second fetch."""
    base = shared_server.url; api = Api(base)
    ctx = api.context(pw, base); page = ctx.new_page()
    page.goto(f'{base}/campaigns')
    calls = page.evaluate("""() => new Promise((resolve) => {
        let n = 0;
        const poll = LedgerPoll.every(() => new Promise((done) => { n += 1; setTimeout(() => done(true), 500); }), {base: 60000, max: 60000});
        poll.poke();                                   // first fetch starts ~150 ms later and takes 500 ms
        setTimeout(() => poll.poke(), 400);            // ping lands while it is still in flight
        setTimeout(() => { poll.stop(); resolve(n); }, 1800);
    })""")
    assert calls == 2, f'expected a follow-up refresh, saw {calls} fetch(es)'
    ctx.close()
