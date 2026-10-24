"""The sidebar in a real browser: grouped links, a Settings link inside a campaign, and the Hide NPCs switch on that page.
Set SHOT_DIR to save screenshots."""
import os
import re
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, pw                      # noqa: F401  (pw is a fixture)
from conftest import BACKEND, q, png_bytes

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _shot(page, name):
    d = os.environ.get('SHOT_DIR')
    if d:
        os.makedirs(d, exist_ok=True)
        page.screenshot(path=os.path.join(d, name + '.png'), full_page=True)


def test_settings_link_and_hide_npcs_switch(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Sidebar')
    ctx = dm.context(pw, base)
    page = ctx.new_page(); page.set_viewport_size({'width': 1280, 'height': 800})
    errors = []
    page.on('pageerror', lambda x: errors.append(str(x)))
    page.goto(f'{base}/campaigns/{cid}/characters')
    assert page.locator('.sidebar-nav .nav-group').all_text_contents() == ['Table', 'Encounters', 'Lore', 'Campaign']
    page.get_by_role('link', name='Settings').first.click()
    page.wait_for_url(f'**/campaigns/{cid}/edit')
    assert page.locator('.sidebar-nav a.nav-tab.active').inner_text().strip() == 'Settings'
    assert page.locator('#npc-visibility-form input[name=hide_npcs]').is_checked() is False
    page.locator('#npc-visibility-form .toggle-switch').click()
    page.wait_for_load_state()
    page.wait_for_selector('#npc-visibility-form')
    assert page.locator('#npc-visibility-form input[name=hide_npcs]').is_checked()
    assert q('SELECT hide_npcs FROM campaigns WHERE id = ?', cid)[0]['hide_npcs'] is True
    _shot(page, 'settings-hide-npcs')
    # phone: Settings is in the More sheet
    page.set_viewport_size({'width': 390, 'height': 780})
    page.click('#nav-more-btn')
    playwright.expect(page.get_by_role('link', name='Campaign settings')).to_be_visible()
    _shot(page, 'sidebar-phone-more')
    ctx.close()
    assert not errors, errors


def test_campaign_home_page(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    r = dm.post('/campaigns/new', data={'name': 'The Ashfall Chronicles', 'description': 'Ash falls on the Cinder Coast.\nThe party hunts the Ember King.'},
                files={'avatar': ('c.png', png_bytes(640, 384, (120, 60, 30)), 'image/png')})
    cid = int(re.search(r'/campaigns/(\d+)/', r.headers['Location']).group(1))
    ids = [dm.character(cid, n, level=lv, max_hp=hp, armor_class=ac) for n, lv, hp, ac in (('Kael Thorne', 3, 28, 16), ('Nyx', 3, 21, 14), ('Sera Veyl', 3, 24, 14))]
    r = dm.post(f'/campaigns/{cid}/api/dice/roll', json={'dice': {'20': 1}, 'modifier': 4, 'label': 'Perception', 'character_id': ids[1]})
    ctx = dm.context(pw, base)
    page = ctx.new_page(); page.set_viewport_size({'width': 1280, 'height': 800})
    errors = []
    page.on('pageerror', lambda x: errors.append(str(x)))
    page.goto(f'{base}/campaigns/{cid}/home')
    assert page.locator('.home-pc').count() == 3
    assert page.locator('.home-banner img').count() == 1 and 'Ember King' in page.locator('.home-bio').inner_text()
    assert page.locator('.home-member-list li').count() == 1
    assert page.locator('.sidebar-nav a.nav-tab.active').inner_text().strip() == 'Home'
    _shot(page, 'home-desktop')
    page.set_viewport_size({'width': 390, 'height': 780})
    _shot(page, 'home-phone')
    ctx.close()
    assert not errors, errors


def test_roll_initiative_menu_opens_on_hover_and_rolls_only_the_chosen_side(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Initiative menu')
    pc = dm.character(cid, 'Hero', dex_score=10)
    npc = dm.character(cid, 'Ogre', dex_score=10, is_npc='on')
    for c in (pc, npc):
        dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': c})
    for p in dm.get(f'/campaigns/{cid}/api/battle').json():
        dm.post(f'/campaigns/{cid}/api/battle/{p["id"]}/initiative', json={'value': 99})
    ctx = dm.context(pw, base)
    page = ctx.new_page(); page.set_viewport_size({'width': 1280, 'height': 800})
    errors = []
    page.on('pageerror', lambda x: errors.append(str(x)))
    page.goto(f'{base}/campaigns/{cid}/battle')
    playwright.expect(page.locator('.roll-menu-list')).to_be_hidden()
    page.hover('#roll-initiative-btn')
    playwright.expect(page.locator('.roll-menu-list')).to_be_visible()
    assert page.locator('[data-roll-scope=pc] small').inner_text() == '1' and page.locator('[data-roll-scope=npc] small').inner_text() == '1'
    _shot(page, 'battle-roll-menu')
    page.click('[data-roll-scope=npc]')
    assert 'NPCs and monsters' in page.locator('#confirm-modal-message').inner_text()   # numbers already entered: it asks first
    page.click('#confirm-modal-yes')
    page.wait_for_function("() => [...document.querySelectorAll('input[data-action=initiative]')].some(e => e.value !== '99')")
    vals = sorted(int(v) for v in page.locator('input[data-action="initiative"]').evaluate_all('els => els.map(e => e.value)'))
    assert vals.count(99) == 1 and len(vals) == 2                                   # one side rolled, the other kept its number
    ctx.close()
    assert not errors, errors


def test_sidebar_menu_scrolls_on_a_short_window_and_keeps_the_footer(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Short window')
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    page.set_viewport_size({'width': 1280, 'height': 560})
    page.goto(f'{base}/campaigns/{cid}/home')
    nav = page.locator('.sidebar-nav')
    assert nav.evaluate('e => e.scrollHeight > e.clientHeight')
    assert nav.evaluate('e => getComputedStyle(e).overflowY') == 'auto'
    nav.evaluate('e => { e.scrollTop = e.scrollHeight }')
    if os.environ.get('SHOT_DIR'):
        page.screenshot(path=os.path.join(os.environ['SHOT_DIR'], 'sidebar-scrolled.png'))
    assert nav.evaluate('e => e.scrollTop') > 0
    box = lambda sel: page.locator(sel).evaluate('e => { const r = e.getBoundingClientRect(); return [r.left, r.right]; }')
    sb, nv = box('.sidebar'), box('.sidebar-nav')
    assert nv[0] >= sb[0] - 0.5 and nv[1] <= sb[1] + 0.5, (sb, nv)           # the scrollbar sits inside the sidebar, not past its edge
    foot = page.locator('.sidebar-foot').bounding_box()
    assert foot and foot['y'] + foot['height'] <= 560 + 1           # footer still on screen
    assert page.locator('.sidebar').evaluate('e => e.getBoundingClientRect().height') <= 560 + 1       # the sidebar itself never outgrows the window
    page.click('#sidebar-toggle-btn')                                          # collapsed sidebar: same rule
    sb, nv = box('.sidebar'), box('.sidebar-nav')
    assert nv[0] >= sb[0] - 0.5 and nv[1] <= sb[1] + 0.5, (sb, nv)
    page.click('#sidebar-toggle-btn')
    page.set_viewport_size({'width': 1280, 'height': 900})
    assert not nav.evaluate('e => e.scrollHeight > e.clientHeight')
    ctx.close()
