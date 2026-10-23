"""The sidebar in a real browser: grouped links, a Settings link inside a campaign, and the Hide NPCs switch on that page.
Set SHOT_DIR to save screenshots."""
import os
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, pw                      # noqa: F401  (pw is a fixture)
from conftest import BACKEND, q

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
    cid = dm.campaign('The Ashfall Chronicles')
    ids = [dm.character(cid, n, level=lv, max_hp=hp, armor_class=ac) for n, lv, hp, ac in (('Kael Thorne', 3, 28, 16), ('Nyx', 3, 21, 14), ('Sera Veyl', 3, 24, 14))]
    r = dm.post(f'/campaigns/{cid}/api/dice/roll', json={'dice': {'20': 1}, 'modifier': 4, 'label': 'Perception', 'character_id': ids[1]})
    ctx = dm.context(pw, base)
    page = ctx.new_page(); page.set_viewport_size({'width': 1280, 'height': 800})
    errors = []
    page.on('pageerror', lambda x: errors.append(str(x)))
    page.goto(f'{base}/campaigns/{cid}/home')
    assert page.locator('.home-pc').count() == 3
    assert page.locator('.sidebar-nav a.nav-tab.active').inner_text().strip() == 'Home'
    _shot(page, 'home-desktop')
    page.set_viewport_size({'width': 390, 'height': 780})
    _shot(page, 'home-phone')
    ctx.close()
    assert not errors, errors
