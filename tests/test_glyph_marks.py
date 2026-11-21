"""DL-180 part 23: the bundled fonts (Newsreader and Lato) have no diamond or check glyphs and Newsreader has no circles either, so those marks must be drawn by the .pm CSS shapes
instead of depending on whichever fallback font a device picks."""
import pathlib, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
GLYPHS = '●○◆✓✔✕✖▸▾▴◂'
ENTITIES = ('&#9679;', '&#9675;', '&#9670;', '&#10003;', '&#x25cf;', '&#x25cb;', '&#x25c6;')


def test_symbols_missing_from_the_fonts_only_appear_inside_pm_shapes():
    pm_tag = re.compile(r'<(span|i)\b[^>]*\bpm\b[^>]*>[^<]*</\1>')
    for path in list((ROOT / 'templates').glob('*.html')) + list((ROOT / 'static' / 'js').glob('*.js')):
        text = pm_tag.sub('', path.read_text(encoding='utf-8'))
        for g in GLYPHS:
            assert g not in text, f'{path.name} uses {g!r} outside a .pm shape'
        for e in ENTITIES:
            assert e not in text, f'{path.name} uses {e} outside a .pm shape'


def test_pm_shapes_are_drawn_in_css_for_all_three_levels():
    css = (ROOT / 'static' / 'css' / 'style.css').read_text(encoding='utf-8')
    for level in ('pm-0', 'pm-1', 'pm-2'):
        assert re.search(r'\.%s::before\s*\{' % level, css), level
    assert re.search(r'\.pm,[^{]*\{[^}]*font-size:\s*0', css)
