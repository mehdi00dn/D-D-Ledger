"""DL-180 part 16 in a real browser: a slow or failed picture never shows the previous one (details viewer and lightbox), and the class picker."""
import os
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, SESSION_COOKIE, pw                      # noqa: F401
from conftest import BACKEND, png_bytes

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _gallery(pw, shared_server, name):
    base = shared_server.url; api = Api(base); cid = api.campaign(name)
    files = [('sheets', (f's{i}.png', png_bytes(600, 300, (40 + 90 * i, 60, 120)), 'image/png')) for i in range(3)]
    api.post(f'/campaigns/{cid}/characters/new', data={'name': 'Gallery', 'max_hp': '10'}, files=files)
    char_id = next(c['id'] for c in api.get(f'/campaigns/{cid}/api/characters').json() if c['name'] == 'Gallery')
    ctx = api.context(pw, base); page = ctx.new_page()
    page.goto(base + f'/campaigns/{cid}/characters/{char_id}'); page.wait_for_load_state('networkidle')
    return base, cid, api, ctx, page


def _hold_second_picture(page):
    """Stall every upload except the first, like a bad connection; returns a function that lets them through."""
    first = page.get_attribute('#sheet-viewer-img', 'src')
    held, state = [], {'open': False}
    def handler(route):
        if state['open'] or route.request.url.endswith(first.split('/')[-1]):
            route.continue_()
        else:
            held.append(route)
    page.route('**/uploads/**', handler)
    def release():
        state['open'] = True
        for r in held:
            r.continue_()
    return release


def test_viewer_hides_the_old_picture_while_the_next_one_loads(pw, shared_server):
    base, cid, api, ctx, page = _gallery(pw, shared_server, 'Slow')
    release = _hold_second_picture(page)
    first = page.get_attribute('#sheet-viewer-img', 'src')
    page.locator('[data-sheet-next]').click()
    assert page.get_attribute('#sheet-viewer-img', 'src') != first
    page.wait_for_selector('.sheet-viewer-stage.is-loading')                              # a spinner after a moment ...
    assert page.eval_on_selector('#sheet-viewer-img', 'e => getComputedStyle(e).visibility') == 'hidden'   # ... and the old picture is not shown as the new one
    release()
    page.wait_for_selector('.sheet-viewer-stage:not(.is-loading)')
    assert page.eval_on_selector('#sheet-viewer-img', 'e => getComputedStyle(e).visibility') == 'visible'
    assert page.eval_on_selector('#sheet-viewer-img', 'e => e.complete && e.naturalWidth > 0')
    ctx.close()


def test_viewer_says_when_a_picture_fails_and_retries_on_click(pw, shared_server):
    base, cid, api, ctx, page = _gallery(pw, shared_server, 'Failing')
    first = page.get_attribute('#sheet-viewer-img', 'src')
    page.route('**/uploads/**', lambda r: r.continue_() if r.request.url.endswith(first.split('/')[-1]) else r.abort())
    page.locator('[data-sheet-next]').click()
    page.wait_for_selector('.sheet-viewer-stage.is-failed')
    assert page.eval_on_selector('#sheet-viewer-img', 'e => getComputedStyle(e).visibility') == 'hidden'
    assert 'try again' in page.eval_on_selector('.sheet-viewer-stage', "e => getComputedStyle(e, '::after').content").lower()
    page.unroute('**/uploads/**')
    page.click('.sheet-viewer-stage')                                                       # the connection is back: one click retries
    page.wait_for_function("() => { const e = document.getElementById('sheet-viewer-img'); return e.complete && e.naturalWidth > 0; }")
    assert page.eval_on_selector('.sheet-viewer-stage', 'e => !e.classList.contains("is-failed") && !e.classList.contains("is-loading")')
    ctx.close()


def test_lightbox_hides_the_old_picture_while_the_next_one_loads(pw, shared_server):
    base, cid, api, ctx, page = _gallery(pw, shared_server, 'LightSlow')
    release = _hold_second_picture(page)
    page.click('#sheet-viewer-img'); page.wait_for_selector('#image-lightbox:not([hidden])')
    page.wait_for_function("() => document.getElementById('lightbox-img').complete")
    page.click('#lightbox-next-btn')
    page.wait_for_selector('.lightbox-stage.is-loading')
    assert page.eval_on_selector('#lightbox-img', 'e => getComputedStyle(e).visibility') == 'hidden'
    release()
    page.wait_for_selector('.lightbox-stage:not(.is-loading)')
    assert page.eval_on_selector('#lightbox-img', 'e => e.complete && e.naturalWidth > 0 && getComputedStyle(e).visibility === "visible"')
    ctx.close()


def test_fast_loads_never_flash_a_spinner(pw, shared_server):
    base, cid, api, ctx, page = _gallery(pw, shared_server, 'Fast')
    page.evaluate("() => { window.__spin = 0; new MutationObserver(() => { if (document.querySelector('.sheet-viewer-stage.is-loading')) window.__spin++; }).observe(document.querySelector('.sheet-viewer-stage'), { attributes: true, attributeFilter: ['class'] }); }")
    for _ in range(2):
        page.locator('[data-sheet-next]').click(); page.wait_for_timeout(300)
    assert page.evaluate('() => window.__spin') == 0
    ctx.close()


def test_class_picker_is_solid_paper_with_framed_icons(pw, shared_server):
    base = shared_server.url; api = Api(base); cid = api.campaign('Picker')
    ctx = api.context(pw, base); page = ctx.new_page()
    page.goto(base + f'/campaigns/{cid}/characters/new'); page.wait_for_selector('#class-trigger')
    page.click('#class-trigger'); page.wait_for_selector('#class-menu:not([hidden])')
    bg = page.eval_on_selector('#class-menu', 'e => getComputedStyle(e).backgroundColor')
    assert bg.startswith('rgb(') or bg.endswith(', 1)'), bg                                  # opaque: the page behind never shows through
    icons = page.eval_on_selector_all('.class-option[data-class] .class-option-icon', 'els => els.map(e => { const c = getComputedStyle(e); return [c.color, c.borderTopWidth, c.borderTopStyle]; })')
    assert len(icons) == 13 and all(w == '1px' and st == 'solid' for _, w, st in icons), icons   # every icon sits in a visible frame
    assert len({c for c, _, _ in icons}) == 1                                                  # one calm accent, not a rainbow
    text = page.eval_on_selector('.class-option[data-class]', 'e => getComputedStyle(e).color')
    assert icons[0][0] != text                                                                   # and it is not just the text colour
    assert page.eval_on_selector('#class-trigger-icon', 'e => getComputedStyle(e).borderTopWidth') in ('1px', '0px')
    boxes = page.eval_on_selector_all('.class-option', 'els => els.map(e => { const r = e.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width)]; })')
    assert len({b[0] for b in boxes[1:]}) == 2                                              # two columns
    menu = page.locator('#class-menu').bounding_box()
    assert menu['x'] >= 0 and menu['x'] + menu['width'] <= page.viewport_size['width']
    # keyboard: right/left move one, down/up move a row
    page.mouse.move(2, 2); page.focus('#class-trigger')
    page.keyboard.press('Escape'); page.keyboard.press('ArrowDown')                         # open on "No class"
    page.keyboard.press('ArrowDown'); page.keyboard.press('ArrowRight')                     # artificer -> barbarian
    assert page.eval_on_selector('.class-option.active', 'e => e.dataset.value') == 'barbarian'
    page.keyboard.press('ArrowDown')                                                        # next row, same column: cleric? (barbarian is 2nd of row 1)
    assert page.eval_on_selector('.class-option.active', 'e => e.dataset.value') == 'cleric'
    page.keyboard.press('ArrowLeft')
    assert page.eval_on_selector('.class-option.active', 'e => e.dataset.value') == 'bard'
    page.keyboard.press('Enter')
    assert page.input_value('#class_key') == 'bard'
    assert page.eval_on_selector('#class-trigger-icon', 'e => e.dataset.class') == 'bard'
    ctx.close()


def test_class_picker_fits_a_phone(pw, shared_server):
    base = shared_server.url; api = Api(base); cid = api.campaign('PickerPhone')
    ctx = pw.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
    ctx.add_cookies([{'name': SESSION_COOKIE, 'value': api.s.cookies.get(SESSION_COOKIE), 'url': base}])
    page = ctx.new_page()
    page.goto(base + f'/campaigns/{cid}/characters/new'); page.wait_for_selector('#class-trigger')
    page.tap('#class-trigger'); page.wait_for_selector('#class-menu:not([hidden])')
    menu = page.locator('#class-menu').bounding_box()
    assert menu['x'] >= 0 and menu['x'] + menu['width'] <= 390, menu
    page.tap('.class-option[data-value=wizard]')
    assert page.input_value('#class_key') == 'wizard'
    ctx.close()


def test_dialogs_ease_out_before_they_go(pw, shared_server):
    """DL-180 part 20: a dialog stays on screen while it fades and settles (160 ms), instead of vanishing the instant it closes."""
    base = shared_server.url; api = Api(base); cid = api.campaign('Dialogs')
    ctx = api.context(pw, base); page = ctx.new_page()
    page.goto(base + f'/campaigns/{cid}/battle'); page.wait_for_load_state('networkidle')
    dlg = page.locator('dialog.pick-dialog').first
    page.evaluate("() => document.querySelector('dialog.pick-dialog').showModal()")
    page.wait_for_timeout(400)
    assert dlg.evaluate('e => getComputedStyle(e).opacity') == '1'
    page.keyboard.press('Escape')
    assert dlg.evaluate('e => e.open') is False                                              # closed for the logic at once ...
    page.evaluate('() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')     # let the page draw the first frame of the exit
    page.wait_for_timeout(40)
    mid = dlg.evaluate('e => [getComputedStyle(e).display, parseFloat(getComputedStyle(e).opacity)]')
    assert mid[0] != 'none' and mid[1] < 1, mid                                              # ... but still on screen and fading
    page.wait_for_timeout(400)
    assert dlg.evaluate('e => getComputedStyle(e).display') == 'none'                       # and gone once the exit has played
    page.evaluate("() => document.querySelector('dialog.pick-dialog').showModal()")          # opening again works as before
    page.wait_for_timeout(400)
    assert dlg.evaluate('e => e.open && getComputedStyle(e).opacity') == '1'
    ctx.close()
