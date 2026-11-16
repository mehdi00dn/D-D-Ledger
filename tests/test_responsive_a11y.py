"""DL-182 part 10: pages fit a phone, controls are reachable by keyboard and touch, and headings and buttons have names.

The first group needs no browser. The second group opens the pages in a real browser, so it runs with the other end-to-end tests.
"""
import os
import re
import pytest

BROWSER = {'Accept': 'text/html'}
PUBLIC_PAGES = ['/', '/about', '/contact', '/terms', '/privacy', '/cookies', '/login', '/register', '/forgot-password']


# ---- no browser needed ------------------------------------------------------------------------------------------

@pytest.mark.parametrize('path', PUBLIC_PAGES)
def test_public_pages_do_not_skip_heading_levels(appmod, path):
    html = appmod.app.test_client().get(path, headers=BROWSER).get_data(as_text=True)
    levels = [int(n) for n in re.findall(r'<h([1-6])[\s>]', html)]
    assert levels and levels[0] == 1, (path, levels)
    assert all(b <= a + 1 for a, b in zip(levels, levels[1:])), (path, levels)


def test_footer_headings_are_second_level(appmod):
    html = appmod.app.test_client().get('/about', headers=BROWSER).get_data(as_text=True)
    for name in ('Explore', 'Resources', 'Legal'):
        assert f'<h2>{name}</h2>' in html and f'<h3>{name}</h3>' not in html


def test_the_icon_only_back_button_on_the_map_page_has_a_name(user):
    cid = user.new_campaign()
    mid = user.new_map(cid)
    html = user.get(f'/campaigns/{cid}/maps/{mid}', headers=BROWSER, follow_redirects=True).get_data(as_text=True)
    assert 'map-back-btn' in html and 'aria-label="Back to maps"' in html


# ---- real browser ------------------------------------------------------------------------------------------------

if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, pw                      # noqa: F401,E402  (pw is a fixture)
from conftest import BACKEND, SESSION_COOKIE              # noqa: E402

browser_only = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _phone(pw, dm, base, width, touch=True):
    ctx = pw.new_context(viewport={'width': width, 'height': 800}, is_mobile=touch, has_touch=touch)
    if dm:
        ctx.add_cookies([{'name': SESSION_COOKIE, 'value': dm.s.cookies.get(SESSION_COOKIE), 'url': base}])
    return ctx


def _sideways(page):
    return page.evaluate('() => document.documentElement.scrollWidth - document.documentElement.clientWidth')


@browser_only
@pytest.mark.parametrize('width', [360, 390])
def test_overview_and_about_do_not_scroll_sideways_on_a_phone(shared_server, pw, width):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Phone fit')
    dm.post(f'/campaigns/{cid}/quests/add', data={'title': 'Complete the encounter and explore the tools available in Oaken',
                                                  'details': 'Visit https://example.com/a/very/long/address/that/has/no/spaces/in/it/at/all/so/it/cannot/wrap'})
    dm.character(cid, 'Kael Thorne')
    ctx = _phone(pw, dm, base, width)
    page = ctx.new_page()
    for path in (f'/campaigns/{cid}/home', '/about'):
        page.goto(base + path)
        assert _sideways(page) <= 0, (path, width, _sideways(page))
    ctx.close()


@browser_only
@pytest.mark.parametrize('width', [360, 390])
@pytest.mark.parametrize('is_npc', [0, 1])
def test_the_character_menu_opens_inside_the_phone_screen(shared_server, pw, width, is_npc):
    """A player character has one more button, which pushes the "..." button to the right; an NPC's sits next to Edit at the left.
    Both menus open on the screen."""
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Menus')
    char = dm.character(cid, 'Goblin Minion', **({'is_npc': 'on'} if is_npc else {}))
    ctx = _phone(pw, dm, base, width)
    page = ctx.new_page()
    page.goto(f'{base}/campaigns/{cid}/characters/{char}')
    page.click('details.menu > summary')
    page.wait_for_timeout(150)
    box = page.locator('details.menu .menu-list').bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= width, box
    ctx.close()


@browser_only
def test_the_search_box_shows_that_it_has_focus(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Search focus')
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    page.goto(f'{base}/campaigns/{cid}/factions')
    bar = page.locator('.search-bar')
    before = bar.evaluate('e => getComputedStyle(e).boxShadow')
    page.focus('#group-search')
    after = bar.evaluate('e => getComputedStyle(e).boxShadow')
    assert before == 'none' and after != 'none'
    ctx.close()


@browser_only
def test_the_dice_stepper_ring_is_drawn_inside_its_box(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Stepper')
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    page.goto(f'{base}/campaigns/{cid}/dice')
    page.keyboard.press('Tab')
    page.focus('.dice-stepper-btn')
    ring = page.locator('.dice-stepper-btn').first.evaluate('e => { const s = getComputedStyle(e); return [s.outlineStyle, parseFloat(s.outlineOffset)]; }')
    assert ring[0] != 'none' and ring[1] < 0                  # inside: the stepper clips anything outside its box
    ctx.close()


@browser_only
def test_small_controls_are_at_least_24px(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Targets')
    char = dm.character(cid, 'Kael Thorne')
    ctx = _phone(pw, dm, base, 390)
    page = ctx.new_page()

    def smallest(selector):
        return page.evaluate('''sel => { const out = [];
            for (const e of document.querySelectorAll(sel)) { const r = e.getBoundingClientRect(); if (r.width && r.height) out.push([Math.round(r.width), Math.round(r.height)]); }
            return out; }''', selector)

    page.goto(f'{base}/campaigns/{cid}/characters/{char}/edit')
    skills = smallest('.tri span')
    assert skills and all(w >= 24 and h >= 24 for w, h in skills), skills
    boxes = smallest('input[type=checkbox]:not(.toggle-switch input)')
    assert all(w >= 24 and h >= 24 for w, h in boxes), boxes

    dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': char})
    page.goto(f'{base}/campaigns/{cid}/battle')
    page.wait_for_selector('.stat-chip-input')
    fields = smallest('.stat-chip-input')
    assert fields and all(w >= 24 and h >= 24 for w, h in fields), fields
    ctx.close()


@browser_only
def test_the_confirm_question_is_a_dialog_that_keeps_focus_and_gives_it_back(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Confirm by keyboard')
    dm.post(f'/campaigns/{cid}/quests/add', data={'title': 'Find the key', 'details': ''})
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    page.goto(f'{base}/campaigns/{cid}/home')
    trigger = page.locator('form[data-confirm] button[type=submit]').first
    assert trigger.count() == 1
    trigger.focus()
    page.keyboard.press('Enter')
    page.wait_for_selector('#confirm-modal:not([hidden])')
    panel = page.locator('#confirm-modal .modal-panel')
    assert panel.get_attribute('role') == 'alertdialog' and panel.get_attribute('aria-modal') == 'true'
    assert panel.get_attribute('aria-labelledby') == 'confirm-modal-message'
    assert page.evaluate('document.activeElement.id') == 'confirm-modal-no'          # the safe answer has focus
    seen = set()
    for _ in range(4):
        page.keyboard.press('Tab')
        seen.add(page.evaluate('document.activeElement.id'))
    assert seen == {'confirm-modal-yes', 'confirm-modal-no'}                           # Tab never leaves the window
    page.keyboard.press('Escape')
    assert page.evaluate("document.getElementById('confirm-modal').hidden")
    assert page.evaluate('document.activeElement.closest("form[data-confirm]") !== null')   # back on the button that asked
    ctx.close()


@browser_only
def test_other_pop_up_windows_are_dialogs_and_close_with_escape(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Add to battle by keyboard')
    dm.character(cid, 'Kael Thorne')
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    page.goto(f'{base}/campaigns/{cid}/battle')
    page.focus('#open-add-modal')
    page.keyboard.press('Enter')
    page.wait_for_selector('#add-modal:not([hidden])')
    panel = page.locator('#add-modal .modal-panel')
    assert panel.get_attribute('role') == 'dialog' and panel.get_attribute('aria-modal') == 'true' and panel.get_attribute('aria-labelledby')
    assert page.evaluate("document.getElementById('add-modal').contains(document.activeElement)")
    for _ in range(14):
        page.keyboard.press('Tab')
        assert page.evaluate("document.getElementById('add-modal').contains(document.activeElement)")
    page.keyboard.press('Escape')
    assert page.evaluate("document.getElementById('add-modal').hidden")
    assert page.evaluate('document.activeElement.id') == 'open-add-modal'
    ctx.close()
