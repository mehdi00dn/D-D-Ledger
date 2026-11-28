"""DL-180 part 12: text sizes come from one scale (--fs-* in oaken.css) instead of 30 loose pixel values.

The first group reads the style sheets and templates, so it needs no browser. The second group measures the rendered text of real pages.
"""
import glob
import os
import re
import pytest

ROOT = os.path.join(os.path.dirname(__file__), '..')
SCALE = {'2xs': 10, 'xs': 11, 'sm': 12, 'md': 13, 'base': 14, 'lg': 16, 'xl': 18, '2xl': 20, '3xl': 24, '4xl': 28, '5xl': 34}
SHEETS = ['style.css', 'auth.css', 'public.css']


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


# ---- no browser needed ------------------------------------------------------------------------------------------

def test_the_scale_is_defined_once_in_oaken_css_and_ascends():
    css = _read('static', 'css', 'oaken.css')
    found = {name: int(px) for name, px in re.findall(r'--fs-([a-z0-9]+): (\d+)px;', css)}
    assert found == SCALE
    values = list(found.values())
    assert values == sorted(values) and len(set(values)) == len(values)


@pytest.mark.parametrize('sheet', SHEETS)
def test_no_style_sheet_sets_a_text_size_in_loose_pixels(sheet):
    """Pixel sizes are allowed only for display type (40px and up) and for the numbers drawn inside the dice SVGs (.dw-num)."""
    loose = []
    for n, line in enumerate(_read('static', 'css', sheet).split('\n'), 1):
        if '.dw-num' in line:
            continue
        for m in re.finditer(r'(?<![-\w])font-size:\s*([\d.]+)px|(?<![-\w])font:\s*(?:(?:italic|normal|bold|oblique|small-caps|\d{3})\s+)*([\d.]+)px', line):
            px = float(m.group(1) or m.group(2))
            if 0 < px < 40:
                loose.append((n, line.strip()[:90]))
    assert not loose, loose


@pytest.mark.parametrize('sheet', SHEETS)
def test_every_size_token_a_style_sheet_uses_exists(sheet):
    used = set(re.findall(r'var\(--fs-([a-z0-9]+)\)', _read('static', 'css', sheet)))
    assert used <= set(SCALE), used - set(SCALE)


def test_every_page_that_loads_a_sheet_with_size_tokens_also_loads_oaken_css():
    """The tokens live in oaken.css; a page without it would lose every text size."""
    for path in glob.glob(os.path.join(ROOT, 'templates', '*.html')):
        html = _read('templates', os.path.basename(path))
        links = re.findall(r"css/([a-z]+)\.css", html)
        if any(f'{s}.css' in [f'{l}.css' for l in links] for s in ('style', 'auth', 'public')) and 'oaken' not in links:
            # partials such as _public_header only carry a class name; a page that links the sheet must link oaken.css too
            assert re.search(r"<link[^>]+css/(style|auth|public)\.css", html) is None, os.path.basename(path)


# ---- real browser ------------------------------------------------------------------------------------------------

if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, pw                      # noqa: F401,E402  (pw is a fixture)
from conftest import BACKEND                              # noqa: E402

browser_only = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')

OFF_SCALE = '''(scale) => {
  const bad = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) {
    const n = w.currentNode;
    if (!n.textContent.trim()) continue;
    const e = n.parentElement;
    if (!e || e.closest('small, svg')) continue;                                // <small> is relative to its parent; SVG numbers are in drawing units
    const r = e.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) continue;                               // visually hidden text (a 1px clipped label) is not seen
    const cs = getComputedStyle(e);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const px = parseFloat(cs.fontSize);
    if (px === 0 && e.classList.contains('pm')) continue;                      // proficiency marks draw their shape in CSS; the glyph text is kept at size 0 on purpose (DL-180 part 23)
    if (px >= 29) continue;                                                     // display type is fluid on purpose
    if (!scale.some(s => Math.abs(s - px) < 0.05)) bad.push([e.tagName.toLowerCase() + '.' + String(e.className).slice(0, 30), n.textContent.trim().slice(0, 20), px]);
  }
  return bad;
}'''


@browser_only
@pytest.mark.parametrize('width', [390, 1440])
def test_text_on_the_app_pages_uses_only_the_scale(shared_server, pw, width):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Type scale')
    char = dm.character(cid, 'Kael Thorne')
    dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': char})
    dm.post(f'/campaigns/{cid}/quests/add', data={'title': 'Find the key', 'details': 'Under the old oak'})
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    page.set_viewport_size({'width': width, 'height': 900})
    offenders = {}
    for path in (f'/campaigns/{cid}/home', f'/campaigns/{cid}/characters', f'/campaigns/{cid}/characters/{char}', f'/campaigns/{cid}/characters/{char}/edit',
                 f'/campaigns/{cid}/battle', f'/campaigns/{cid}/dice', f'/campaigns/{cid}/treasury', f'/campaigns/{cid}/factions', f'/campaigns/{cid}/journal',
                 f'/campaigns/{cid}/maps', '/campaigns'):
        page.goto(base + path)
        page.wait_for_timeout(300)
        bad = page.evaluate(OFF_SCALE, sorted(SCALE.values()))
        if bad:
            offenders[path.replace(str(cid), '<id>').replace(str(char), '<char>')] = bad[:4]
    ctx.close()
    assert not offenders, offenders


@browser_only
def test_text_on_the_public_pages_uses_only_the_scale(shared_server, pw):
    base = shared_server.url
    ctx = pw.new_context(viewport={'width': 390, 'height': 900})
    page = ctx.new_page()
    offenders = {}
    for path in ('/login', '/register', '/about', '/terms', '/privacy', '/contact'):
        page.goto(base + path)
        page.wait_for_timeout(200)
        bad = page.evaluate(OFF_SCALE, sorted(SCALE.values()))
        if bad:
            offenders[path] = bad[:4]
    ctx.close()
    assert not offenders, offenders
