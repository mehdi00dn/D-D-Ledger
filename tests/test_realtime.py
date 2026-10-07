"""Real-time wake-ups: the server pings a secret per-campaign Realtime channel after every successful change,
pings carry no data, a broken Realtime never harms a request, and pages only get the channel when it is configured."""
import time
import pytest
from conftest import q
from fake_supabase import FakeSupabase
import realtime

SECRET_KEY = 'sb_secret_RT'
PUBLIC_KEY = 'sb_publishable_RT'


@pytest.fixture
def rt(monkeypatch):
    fake = FakeSupabase(key=SECRET_KEY).start()
    monkeypatch.setenv('SUPABASE_URL', fake.url)
    monkeypatch.setenv('SUPABASE_SECRET_KEY', SECRET_KEY)
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', PUBLIC_KEY)
    monkeypatch.delenv('REALTIME_ENABLED', raising=False)
    realtime.reset_breaker()
    yield fake
    fake.stop()
    realtime.reset_breaker()


def wait_for(fake, n, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end and len(fake.broadcasts) < n:
        time.sleep(0.02)
    return fake.broadcasts


# ---- configuration and names -------------------------------------------------------------------------------

@pytest.mark.parametrize('env', [
    {}, {'SUPABASE_URL': 'https://x.supabase.co', 'SUPABASE_SECRET_KEY': 's'},              # no browser key
    {'SUPABASE_URL': 'https://x.supabase.co', 'SUPABASE_PUBLISHABLE_KEY': 'p'},             # no server key
    {'SUPABASE_SECRET_KEY': 's', 'SUPABASE_PUBLISHABLE_KEY': 'p'},                          # no url
    {'SUPABASE_URL': 'https://x.supabase.co', 'SUPABASE_SECRET_KEY': 's', 'SUPABASE_PUBLISHABLE_KEY': 'p', 'REALTIME_ENABLED': '0'},
    {'SUPABASE_URL': 'not-a-url', 'SUPABASE_SECRET_KEY': 's', 'SUPABASE_PUBLISHABLE_KEY': 'p'},
])
def test_realtime_is_off_unless_fully_configured(env):
    assert realtime.config(env) is None


def test_fully_configured_realtime_is_on_and_trims_the_url():
    cfg = realtime.config({'SUPABASE_URL': 'https://x.supabase.co/', 'SUPABASE_SECRET_KEY': 's', 'SUPABASE_PUBLISHABLE_KEY': 'p'})
    assert cfg == {'url': 'https://x.supabase.co', 'secret_key': 's', 'publishable_key': 'p'}


def test_channel_names_are_stable_secret_and_per_campaign():
    a, b = realtime.channel_name('k1', 1), realtime.channel_name('k1', 2)
    assert a == realtime.channel_name('k1', 1) and a != b
    assert a != realtime.channel_name('k2', 1)                      # unguessable without the app secret
    assert a.startswith('lc-') and len(a) == 43 and '1' != a[3:]


def test_scope_tells_pages_what_changed():
    assert realtime.scope_for('/campaigns/1/api/battle/3/heal') == 'battle'
    assert realtime.scope_for('/campaigns/1/api/maps/2/drawings') == 'map'
    assert realtime.scope_for('/campaigns/1/api/maps/2/fog') == 'map'
    assert realtime.scope_for('/campaigns/1/api/maps/2/pins') == 'both'            # a linked map's pins are battle rows too
    assert realtime.scope_for('/campaigns/1/characters/5/edit') == 'both'


# ---- publishing --------------------------------------------------------------------------------------------

def test_publish_sends_one_empty_ping_to_the_campaigns_channel(rt):
    assert realtime.publish(7, 'battle', 'appsecret') is True
    [m] = rt.broadcasts
    assert m == {'topic': realtime.channel_name('appsecret', 7), 'event': 'changed', 'payload': {'s': 'battle'}, 'private': False}


def test_a_rejected_key_fails_quietly_and_pauses_pings(rt, monkeypatch):
    monkeypatch.setenv('SUPABASE_SECRET_KEY', 'sb_secret_WRONG')
    assert realtime.publish(1, 'both', 's') is False
    monkeypatch.setenv('SUPABASE_SECRET_KEY', SECRET_KEY)
    assert realtime.publish(1, 'both', 's') is False                # breaker: not retried for a while
    assert rt.broadcasts == []
    realtime.reset_breaker()
    assert realtime.publish(1, 'both', 's') is True


def test_an_unreachable_realtime_never_raises_and_does_not_hold_things_up(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'http://127.0.0.1:9')       # nothing listens here
    monkeypatch.setenv('SUPABASE_SECRET_KEY', 'k'); monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'p')
    realtime.reset_breaker()
    t0 = time.time()
    assert realtime.publish(1, 'both', 's') is False
    assert realtime.publish(1, 'both', 's') is False
    assert time.time() - t0 < 2.5
    realtime.reset_breaker()


# ---- the app hooks -----------------------------------------------------------------------------------------

def test_every_successful_change_pings_only_its_own_campaign(rt, appmod, make_user):
    alice, bob = make_user(), make_user()
    ca, cb = alice.new_campaign(), bob.new_campaign()
    ch = alice.new_character(ca, 'Hero')
    rt.broadcasts.clear()
    r = alice.json(f'/campaigns/{ca}/api/battle/add', {'character_id': ch})
    assert r.status_code == 200
    r.close()                                  # a real WSGI server closes the response after sending it; the test client does not
    [m] = wait_for(rt, 1)
    assert m['topic'] == realtime.channel_name(appmod.app.secret_key, ca) and m['payload'] == {'s': 'battle'}
    r = bob.post(f'/campaigns/{cb}/maps/new', data={'name': 'Bob Map', 'blank_width': '300', 'blank_height': '300'}, content_type='multipart/form-data')
    assert r.status_code == 302
    r.close()
    msgs = wait_for(rt, 2)
    assert msgs[-1]['topic'] == realtime.channel_name(appmod.app.secret_key, cb)
    assert {x['topic'] for x in msgs} == {realtime.channel_name(appmod.app.secret_key, c) for c in (ca, cb)}


def test_reads_and_refused_requests_send_nothing(rt, appmod, make_user):
    owner, player, stranger = make_user(), make_user(), make_user()
    cid = owner.new_campaign(); owner.add_member(cid, player, 'player')
    rt.broadcasts.clear()
    for who, code in ((player, 403), (stranger, 404)):                                          # a Player / an outsider is refused
        r = who.json(f'/campaigns/{cid}/api/battle/clear'); assert r.status_code == code; r.close()
    for url in (f'/campaigns/{cid}/api/battle', f'/campaigns/{cid}/characters'):
        owner.get(url).close()
    time.sleep(0.4)
    assert rt.broadcasts == []


def test_a_dead_realtime_does_not_break_changes(appmod, make_user, monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'http://127.0.0.1:9')
    monkeypatch.setenv('SUPABASE_SECRET_KEY', 'k'); monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'p')
    realtime.reset_breaker()
    u = make_user(); cid = u.new_campaign(); ch = u.new_character(cid, 'Hero')
    assert u.json(f'/campaigns/{cid}/api/battle/add', {'character_id': ch}).status_code == 200
    assert len(u.battle(cid)) == 1
    realtime.reset_breaker()


# ---- what pages receive ------------------------------------------------------------------------------------

def test_members_get_their_campaigns_channel_and_nobody_else_does(rt, appmod, make_user):
    owner, player, stranger = make_user(), make_user(), make_user()
    c1, c2 = owner.new_campaign(), owner.new_campaign()
    owner.add_member(c1, player, 'player')
    chan = realtime.channel_name(appmod.app.secret_key, c1)
    for who in (owner, player):
        html = who.get(f'/campaigns/{c1}/battle').get_data(as_text=True)
        assert 'window.LEDGER_REALTIME' in html and chan in html and PUBLIC_KEY in html
        assert SECRET_KEY not in html                                                 # the server key never reaches a browser
    assert chan not in owner.get(f'/campaigns/{c2}/battle').get_data(as_text=True)
    assert stranger.get(f'/campaigns/{c1}/battle').status_code == 404                  # no page, so no channel name
    assert 'LEDGER_REALTIME' not in owner.get('/campaigns').get_data(as_text=True)     # not on pages outside a campaign


def test_pages_have_no_realtime_when_it_is_not_configured(appmod, make_user, monkeypatch):
    monkeypatch.delenv('SUPABASE_PUBLISHABLE_KEY', raising=False)
    u = make_user(); cid = u.new_campaign()
    assert 'LEDGER_REALTIME' not in u.get(f'/campaigns/{cid}/battle').get_data(as_text=True)


def test_the_csp_allows_the_realtime_socket_only_when_configured(rt, appmod, make_user, monkeypatch):
    u = make_user(); cid = u.new_campaign()
    csp = u.get(f'/campaigns/{cid}/battle').headers['Content-Security-Policy']
    assert rt.url in csp and rt.url.replace('http', 'ws', 1) in csp
    monkeypatch.setenv('REALTIME_ENABLED', '0')
    assert 'ws://' not in u.get(f'/campaigns/{cid}/battle').headers['Content-Security-Policy']
