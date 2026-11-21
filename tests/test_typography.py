"""DL-180 part 26: Newsreader for titles and headings, Lato for everything else, regular and medium weights, no monospace, nothing from outside this site."""
import pathlib, re
import pytest
from conftest import BACKEND
from test_e2e_browser import Api, pw      # noqa: F401

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSS = ROOT / 'static' / 'css'
OLD = ('Fraunces', 'Inter', 'IBM Plex', 'monospace', 'ui-monospace', 'Courier', 'Consolas', 'Menlo')


def test_the_stylesheets_name_only_newsreader_and_lato_and_no_monospace():
    for css in CSS.glob('*.css'):
        text = css.read_text(encoding='utf-8')
        for name in OLD:
            assert name not in text, f'{css.name} still mentions {name}'
    for js in (ROOT / 'static' / 'js').glob('*.js'):
        text = js.read_text(encoding='utf-8')
        assert not re.search(r'\b(Inter|Fraunces|IBM Plex Mono)\b', text), js.name
    for sheet, token in (('style.css', '--font-display'), ('landing.css', '--font-display'), ('public.css', '--pub-font-display')):
        assert re.search(token + r":\s*'Newsreader'", (CSS / sheet).read_text(encoding='utf-8')), (sheet, token)
    for sheet, token in (('style.css', '--font-body'), ('landing.css', '--font-body'), ('public.css', '--pub-font-body'),
                         ('style.css', '--font-mono'), ('public.css', '--pub-font-mono')):
        assert re.search(token + r":\s*'Lato'", (CSS / sheet).read_text(encoding='utf-8')), (sheet, token)


def test_no_text_is_heavier_than_semibold():
    for css in CSS.glob('*.css'):
        for w in re.findall(r'font-weight:\s*(\d{3})', css.read_text(encoding='utf-8')):
            if 'font-face' in w: continue
            assert int(w) <= 600 or css.name == 'zz', (css.name, w)
        for w in re.findall(r'font:\s*(\d{3})\s', css.read_text(encoding='utf-8')):
            assert int(w) <= 600, (css.name, w)
    base = (CSS / 'oaken.css').read_text(encoding='utf-8')
    assert re.search(r'b, strong \{\s*font-weight: 600', base) and re.search(r'h1, h2, h3, h4, h5, h6 \{[^}]*font-weight: 500', base)


def test_every_font_file_is_present_and_the_licence_covers_both_families():
    files = set()
    for css in ('style.css', 'landing.css'):
        files |= set(re.findall(r"url\('\.\./fonts/([^']+)'\)", (CSS / css).read_text(encoding='utf-8')))
    assert files == {'newsreader-latin-opsz-normal.woff2', 'newsreader-latin-opsz-italic.woff2', 'lato2-400-normal.woff2', 'lato2-400-italic.woff2', 'lato2-500-normal.woff2', 'lato2-600-normal.woff2'}, files
    for f in files:
        assert (ROOT / 'static' / 'fonts' / f).stat().st_size > 20000, f
    assert sorted(p.name for p in (ROOT / 'static' / 'fonts').iterdir() if p.suffix == '.woff2') == sorted(files), 'a font file nothing uses'
    lic = (ROOT / 'static' / 'fonts' / 'LICENSES.txt').read_text(encoding='utf-8')
    assert 'Newsreader Project Authors' in lic and 'Lukasz Dziedzic' in lic and 'SIL OPEN FONT LICENSE Version 1.1' in lic
    about = (ROOT / 'templates' / 'about.html').read_text(encoding='utf-8')
    assert 'Newsreader' in about and 'Lato' in about and 'Fraunces' not in about


AUDIT = """() => {
  const out = {other: [], heavy: [], heads: [], names: [], n: 0, lato: 0, news: 0};
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT), seen = new Set();
  while (w.nextNode()) {
    const e = w.currentNode.parentElement; if (!e || !w.currentNode.textContent.trim() || seen.has(e)) continue; seen.add(e);
    const r = e.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const c = getComputedStyle(e); if (c.visibility === 'hidden') continue;
    const fam = c.fontFamily.split(',')[0].replace(/['"]/g, '').trim(), sel = e.tagName.toLowerCase() + '.' + String(e.className).trim().split(/\\s+/).join('.');
    out.n++; if (fam === 'Lato') out.lato++; if (fam === 'Newsreader') out.news++;
    if (fam !== 'Lato' && fam !== 'Newsreader') out.other.push(sel + ' ' + fam);
    if (parseInt(c.fontWeight) > 600) out.heavy.push(sel + ' ' + c.fontWeight);
    if (/^H[1-3]$/.test(e.tagName) && fam !== 'Newsreader' && parseFloat(c.fontSize) > 12 && !e.closest('footer, .pub-footer'))   // small uppercase labels that are headings in the markup stay Lato
      out.heads.push(sel + ' ' + fam);
  }
  return out;
}"""


@pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')
def test_every_visible_text_on_public_and_campaign_pages_is_newsreader_or_lato(pw, shared_server):
    base = shared_server.url; api = Api(base); cid = api.campaign('The Lost Mines of Phandelver')
    api.character(cid, 'Mira Stormwhisper')
    ctx = api.context(pw, base); page = ctx.new_page(); page.set_viewport_size({'width': 1280, 'height': 860}); errs = []
    hosts = set(); page.on('request', lambda r: hosts.add(r.url.split('/')[2])); page.on('pageerror', lambda e: errs.append(str(e)))
    urls = ['/', '/about', '/login'] + [f'/campaigns/{cid}/{p}' for p in ('home', 'characters', 'factions', 'treasury', 'battle', 'maps', 'dice', 'reference', 'journal', 'activity', 'edit')]
    seen_news = 0
    for u in urls:
        page.goto(base + u); page.wait_for_load_state('networkidle'); page.wait_for_timeout(200)
        r = page.evaluate(AUDIT)
        assert not r['other'], (u, r['other'][:6]); assert not r['heavy'], (u, r['heavy'][:6]); assert not r['heads'], (u, r['heads'][:6])
        assert r['lato'] > 0, u; seen_news += r['news']
    assert seen_news > 30
    # a few named things
    page.goto(base + f'/campaigns/{cid}/home'); page.wait_for_load_state('networkidle')
    fam = lambda sel: page.eval_on_selector(sel, 'e => getComputedStyle(e).fontFamily.split(",")[0].replace(/[\'"]/g, "").trim()')
    assert fam('h1') == 'Newsreader' and fam('.campaign-switcher-name') == 'Newsreader' and fam('.nav-tab') == 'Lato' and fam('.btn') == 'Lato'
    assert fam('body') == 'Lato'
    page.goto(base + f'/campaigns/{cid}/characters/new'); page.wait_for_load_state('networkidle')
    assert fam('input[name="name"]') == 'Lato'
    for sel in ('select', 'textarea', 'button'):
        for f in page.eval_on_selector_all(sel, 'els => els.map(e => getComputedStyle(e).fontFamily.split(",")[0].replace(/[\'"]/g, "").trim())'):
            assert f == 'Lato', (sel, f)
    assert hosts == {base.split('/')[2]}, hosts
    assert not errs, errs
    ctx.close()
