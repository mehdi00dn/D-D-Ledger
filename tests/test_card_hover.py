"""DL-180 part 11: the hover burn is gone; clickable paper cards take one quiet lift instead.

The first group reads the shipped templates and style sheets, so it needs no browser. The second group hovers a real card.
"""
import glob
import os
import re
import pytest

ROOT = os.path.join(os.path.dirname(__file__), '..')
LIFT_CARDS = {
    'characters_list.html': 'dossier-card', 'library.html': 'lib-card', 'groups_list.html': 'group-card',
    'campaigns_list.html': 'group-card', 'maps_list.html': 'map-card', 'journal.html': 'journal-row',
}


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


# ---- no browser needed ------------------------------------------------------------------------------------------

def test_no_template_or_script_still_uses_the_burn():
    for path in glob.glob(os.path.join(ROOT, 'templates', '*.html')) + glob.glob(os.path.join(ROOT, 'static', 'js', '*.js')):
        assert 'o-burn' not in _read(os.path.relpath(path, ROOT)), path


@pytest.mark.parametrize('sheet', ['oaken.css', 'style.css', 'public.css', 'auth.css'])
def test_no_style_sheet_still_draws_the_burn(sheet):
    css = _read('static', 'css', sheet)
    assert not re.search(r'o-burn|--burn-|scorch', css), sheet


@pytest.mark.parametrize('template,card', sorted(LIFT_CARDS.items()))
def test_clickable_paper_cards_opt_in_to_the_lift(template, card):
    html = _read('templates', template)
    assert re.search(r'class="[^"]*\b' + card + r'\b[^"]*\bo-paper o-lift\b', html), (template, card)


def test_the_lift_only_moves_for_a_mouse_and_stays_put_with_reduced_motion():
    css = _read('static', 'css', 'oaken.css')
    hover = re.search(r'@media \(hover: hover\) and \(pointer: fine\) \{\s*\.o-paper\.o-lift:hover \{[^}]*translateY\(-2px\)', css)
    assert hover, 'the movement must sit inside the hover-capable media query'
    assert not re.search(r'\.o-lift[^{]*\{[^}]*translateY', css.replace(hover.group(0), '')), 'no other rule may move the card'
    reduced = re.search(r'@media \(prefers-reduced-motion: reduce\) \{[^@]*\.o-paper\.o-lift:hover \{ transform: none; \}', css)
    assert reduced
    assert 'transition: transform' not in reduced.group(0)           # the movement is not animated either


def test_the_lift_uses_the_shared_ease_out_and_stays_under_300ms():
    css = _read('static', 'css', 'oaken.css')
    assert '--ease-out: cubic-bezier(0.23, 1, 0.32, 1)' in css
    block = re.search(r'\.o-paper\.o-lift \{[^}]*transition: ([^;]+);', css).group(1)
    assert 'var(--ease-out)' in block
    assert all(int(ms) < 300 for ms in re.findall(r'(\d+)ms', block))


# ---- real browser ------------------------------------------------------------------------------------------------

if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, pw                      # noqa: F401,E402  (pw is a fixture)
from conftest import BACKEND, SESSION_COOKIE              # noqa: E402

browser_only = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _card_state(page, base, cid):
    page.goto(f'{base}/campaigns/{cid}/characters')
    card = page.locator('.dossier-card.o-lift').first
    box = card.bounding_box()
    page.mouse.move(2, 2)
    page.wait_for_timeout(300)
    rest = card.evaluate('e => getComputedStyle(e).transform')
    page.mouse.move(box['x'] + box['width'] / 2, box['y'] + 60)
    page.wait_for_timeout(400)
    return rest, card.evaluate('e => [getComputedStyle(e).transform, getComputedStyle(e).boxShadow]')


@browser_only
def test_a_hovered_card_rises_two_pixels_and_gets_a_ring(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Lift')
    dm.character(cid, 'Kael Thorne')
    ctx = dm.context(pw, base)
    rest, (moved, shadow) = _card_state(ctx.new_page(), base, cid)
    assert rest == 'none' and moved == 'matrix(1, 0, 0, 1, 0, -2)'
    assert '0px 0px 0px 1px' in shadow                         # the hairline ring
    ctx.close()


@browser_only
def test_with_reduced_motion_the_card_does_not_move_but_still_shows_the_ring(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Lift reduced')
    dm.character(cid, 'Kael Thorne')
    ctx = pw.new_context(reduced_motion='reduce')
    ctx.add_cookies([{'name': SESSION_COOKIE, 'value': dm.s.cookies.get(SESSION_COOKIE), 'url': base}])
    rest, (moved, shadow) = _card_state(ctx.new_page(), base, cid)
    assert rest == 'none' and moved == 'none'
    assert '0px 0px 0px 1px' in shadow
    ctx.close()


@browser_only
def test_a_touch_screen_never_gets_the_hover_movement(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Lift touch')
    dm.character(cid, 'Kael Thorne')
    ctx = pw.new_context(viewport={'width': 390, 'height': 800}, is_mobile=True, has_touch=True)
    ctx.add_cookies([{'name': SESSION_COOKIE, 'value': dm.s.cookies.get(SESSION_COOKIE), 'url': base}])
    page = ctx.new_page()
    page.goto(f'{base}/campaigns/{cid}/characters')
    assert page.evaluate("matchMedia('(hover: hover) and (pointer: fine)').matches") is False
    page.locator('.dossier-card.o-lift').first.hover(force=True)
    page.wait_for_timeout(300)
    assert page.locator('.dossier-card.o-lift').first.evaluate('e => getComputedStyle(e).transform') == 'none'
    ctx.close()


@browser_only
def test_clickable_cards_only_ring_and_do_not_move_under_keyboard_focus_and_the_burn_pseudo_elements_are_gone(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Lift keyboard')
    dm.character(cid, 'Kael Thorne')
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    page.goto(f'{base}/campaigns/{cid}/characters')
    page.mouse.move(2, 2)
    page.focus('.dossier-card.o-lift a.btn-ghost, .dossier-card.o-lift button')
    page.keyboard.press('Shift+Tab')
    page.keyboard.press('Tab')
    page.wait_for_timeout(300)
    card = page.locator('.dossier-card.o-lift:has(:focus-visible)').first
    assert card.evaluate('e => getComputedStyle(e).transform') == 'none'
    assert '0px 0px 0px 1px' in card.evaluate('e => getComputedStyle(e).boxShadow')
    assert page.evaluate("getComputedStyle(document.querySelector('.dossier-card'), '::before').content") in ('none', 'normal')
    assert page.evaluate("getComputedStyle(document.querySelector('.dossier-card'), '::after').content") in ('none', 'normal')
    ctx.close()
