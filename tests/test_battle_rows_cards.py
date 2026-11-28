"""DL-180 part 14: every battle row is its own rounded card, and the rows behave on a touch screen.

The first group reads the style sheet; the second opens the real battle page with a phone-sized touch screen.
"""
import os
import re
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
from conftest import BACKEND
from test_e2e_browser import Api, SESSION_COOKIE, pw          # noqa: F401  (pw is a fixture)

ROOT = os.path.join(os.path.dirname(__file__), '..')


def _css():
    with open(os.path.join(ROOT, 'static', 'css', 'style.css'), encoding='utf-8') as f:
        return f.read()


def _rule(css, selector):
    m = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', css)
    assert m, selector
    return m.group(1)


# ---- no browser needed ------------------------------------------------------------------------------------------

def test_a_paper_battle_row_is_a_rounded_card_not_a_flat_strip():
    body = _rule(_css(), '.battle-list.o-paper .battle-row')
    assert 'border-radius: var(--radius-m)' in body
    assert re.search(r'border:\s*1px solid', body)
    assert 'border-block-end' not in body and 'border-radius: 0' not in body
    assert '.battle-row:last-child' not in _css()                   # the separator rule is gone with the separators


def test_the_rows_are_separated_by_a_gap_and_the_turn_marker_stays_inside_the_corners():
    css = _css()
    assert re.search(r'\.battle-list\.o-paper\s*\{[^}]*gap:\s*8px', css)
    marker = _rule(css, '.battle-list.o-paper .battle-row::before')
    assert re.search(r'top:\s*[1-9]\d*px', marker) and re.search(r'bottom:\s*[1-9]\d*px', marker)     # inset from the corners


def test_fields_in_a_row_are_16px_on_a_touch_screen():
    css = _css()
    block = css[css.index('DL-180 part 14: battle rows on a touch screen'):]
    assert re.search(r'@media \(pointer: coarse\), \(max-width: 760px\)\s*\{[^}]*\.hp-amount-input[^}]*font-size:\s*var\(--fs-lg\)', block)
    assert 'touch-action: manipulation' in block


# ---- real browser, phone-sized touch screen ---------------------------------------------------------------------

pytestmark_browser = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


@pytestmark_browser
@pytest.mark.parametrize('size', [(390, 844), (360, 640), (844, 390)])
def test_rows_on_a_phone(shared_server, pw, size):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Phone cards %dx%d' % size)
    for n, hp, extra in (('Frodo', 70, {}), ('Mira', 30, {}), ('Ogre', 59, {'is_npc': 'on'})):
        c = dm.character(cid, n, max_hp=hp, **extra)
        dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': c})
    ctx = pw.new_context(viewport={'width': size[0], 'height': size[1]}, has_touch=True, is_mobile=True)
    ctx.add_cookies([{'name': SESSION_COOKIE, 'value': dm.s.cookies.get(SESSION_COOKIE), 'url': base}])
    page = ctx.new_page()
    page.goto(f'{base}/campaigns/{cid}/battle')
    page.wait_for_selector('.battle-row')
    page.wait_for_timeout(400)
    info = page.evaluate('''() => {
      const rows = [...document.querySelectorAll('.battle-row')];
      const rects = rows.map(r => r.getBoundingClientRect());
      const gaps = rects.slice(1).map((r, i) => r.top - rects[i].bottom);
      const radius = rows.map(r => parseFloat(getComputedStyle(r).borderTopLeftRadius));
      const fonts = [...document.querySelectorAll('.battle-row input.stat-chip-input, .battle-row input.hp-amount-input')].map(e => parseFloat(getComputedStyle(e).fontSize));
      const ta = [...document.querySelectorAll('.battle-row button')].map(e => getComputedStyle(e).touchAction);
      const ends = [...document.querySelectorAll('.battle-row .battle-end .btn-icon')].map(e => { const r = e.getBoundingClientRect(); return Math.min(r.width, r.height); });
      return {gaps, radius, fonts, ta, ends, sw: document.documentElement.scrollWidth, iw: innerWidth};
    }''')
    ctx.close()
    assert len(info['radius']) == 3 and min(info['radius']) >= 8, info['radius']
    assert min(info['gaps']) >= 4, info['gaps']                       # the cards do not touch
    assert info['fonts'] and min(info['fonts']) >= 16, info['fonts']  # iOS zooms into anything smaller
    assert set(info['ta']) == {'manipulation'}, info['ta']
    assert min(info['ends']) >= 40, info['ends']
    assert info['sw'] <= info['iw'] + 1, info
