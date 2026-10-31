"""DL-172 (backlog DL-140): the page's policy no longer allows style="..." attributes, so none may be written in a template
or a script.  A style that depends on data goes in a data-* attribute (static/js/dyn-style.js applies it, checked);
a fixed one goes in static/css/style.css."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STYLE_ATTR = re.compile(r"""[\s'"`]style\s*=\s*["'\\]""")


def test_no_template_or_script_writes_a_style_attribute():
    bad = []
    files = list((ROOT / 'templates').glob('*.html')) + [p for p in (ROOT / 'static' / 'js').glob('*.js') if p.name != 'dyn-style.js']
    for f in files:
        for n, line in enumerate(f.read_text(encoding='utf-8').splitlines(), 1):
            code = line.split('//')[0] if f.suffix == '.js' else line
            if STYLE_ATTR.search(code) and 'dyn-style' not in code:
                bad.append(f'{f.relative_to(ROOT)}:{n}: {line.strip()[:90]}')
    assert not bad, 'style="..." is blocked by the Content-Security-Policy; use a class or a data-* attribute:\n' + '\n'.join(bad)


def test_the_policy_has_no_style_attribute_exception(appmod):
    csp = appmod._csp('abc')
    assert 'style-src-attr' not in csp and "'unsafe-inline'" not in csp
