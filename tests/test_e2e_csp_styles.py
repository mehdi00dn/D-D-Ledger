"""DL-172: with the strict policy, the main screens raise no policy violations and the styles that come from data still show."""
import pytest
from conftest import BACKEND
from test_e2e_browser import Api, pw, _open_map  # noqa: F401

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='browser tests need Postgres')

WATCH = """() => { window.__csp = []; document.addEventListener('securitypolicyviolation', e => window.__csp.push(e.violatedDirective + ' ' + (e.blockedURI || '') + ' ' + (e.sample || ''))); }"""


def test_main_screens_have_no_policy_violations_and_data_styles_apply(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Strict styles')
    assert dm.post(f'/campaigns/{cid}/factions/new', data={'name': 'Red Hand', 'color': '#c0392b'}, files={'x': ('x', b'')}).status_code in (302, 200)
    hero = dm.character(cid, 'Mira', level=3)
    pid = dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': hero}).json()[-1]['id']
    assert dm.post(f'/campaigns/{cid}/api/battle/{pid}/conditions', json={'conditions': ['blinded', 'poisoned']}).status_code == 200
    dm.post(f'/campaigns/{cid}/api/battle/{pid}/damage', json={'amount': 7})
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    page.add_init_script('(' + WATCH + ')()')
    errors = []
    page.on('pageerror', lambda x: errors.append(str(x)))
    for path in ('/campaigns', f'/campaigns/{cid}/home', f'/campaigns/{cid}/characters', f'/campaigns/{cid}/characters/{hero}',
                 f'/campaigns/{cid}/characters/new', f'/campaigns/{cid}/factions', f'/campaigns/{cid}/factions/new',
                 f'/campaigns/{cid}/maps', f'/campaigns/{cid}/battle', '/account'):
        page.goto(base + path)
        page.wait_for_load_state('networkidle')
        page.wait_for_timeout(300)
        assert page.evaluate('window.__csp') == [], (path, page.evaluate('window.__csp'))

    page.goto(f'{base}/campaigns/{cid}/battle')                                   # the data styles on the battle row
    page.wait_for_selector('.battle-row .hp-bar-fill')
    fill = page.locator('.battle-row .hp-bar-fill').first
    assert fill.evaluate("e => e.style.getPropertyValue('--p')") not in ('', '1'), 'the HP fill (--p) was not applied'
    assert fill.evaluate("e => getComputedStyle(e).width") != '0px'
    assert page.locator('.battle-row .cond-icon').first.evaluate("e => getComputedStyle(e.querySelector('use') || e).fill") != ''
    page.goto(f'{base}/campaigns/{cid}/factions')                                 # a faction colour dot
    dot = page.locator('.group-dot').first
    dot.wait_for()
    assert dot.evaluate("e => e.style.background") != ''
    assert page.evaluate('window.__csp') == []

    mid, mctx, mpage, bad, errs = _open_map(pw, base, dm, cid)                    # a map pin's place and colour
    mpage.add_init_script('(' + WATCH + ')()')
    mpage.reload(); mpage.wait_for_load_state('networkidle')
    mpage.click('#characters-trigger'); mpage.wait_for_selector('.roster-item')
    mpage.drag_and_drop('.roster-item', '#map-canvas', target_position={'x': 220, 'y': 180})
    mpage.wait_for_selector('.map-pin')
    pin = mpage.locator('.map-pin').first
    assert pin.evaluate("e => e.style.left") not in ('', '0%') and pin.evaluate("e => e.style.width") != ''
    assert mpage.locator('.map-pin-visual').first.evaluate("e => e.style.borderColor") != ''
    assert mpage.evaluate('window.__csp') == []
    mctx.close()
    ctx.close()
    assert not errors, errors
