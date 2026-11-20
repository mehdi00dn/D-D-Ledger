"""DL-75 / DL-78 / DL-76: the browser loads nothing from other sites and the page cannot be made to.
If you add an analytics script, a CDN font or an embed, this test fails on purpose: update THIRD_PARTY.md and the
privacy/cookie pages first (DL-69, DL-77, DL-82)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_HOSTS = ('www.w3.org',)       # XML namespace URIs in SVG, not network requests
# DL-171: these two pages may link OUT to a licence or a source (a plain <a>, only followed when the visitor clicks it);
# nothing on them may load from another site.
LINK_OUT_PAGES = ('about.html', 'reference.html')
ANCHOR = re.compile(r'<a\b[^>]*>.*?</a>', re.S)
URL = re.compile(r'https?://[^\s"\'<>)\\]+')


def test_no_external_url_in_templates_styles_scripts_or_svg():
    bad = []
    files = list((ROOT / 'templates').glob('*.html')) + [p for p in (ROOT / 'static').rglob('*')
                                                          if p.suffix in ('.html', '.js', '.css', '.svg') and p.is_file()]
    for f in files:
        text = f.read_text(errors='ignore')
        if f.name in LINK_OUT_PAGES and f.parent.name == 'templates':
            text = ANCHOR.sub('', text)
        for m in URL.finditer(text):
            if not any(h in m.group(0) for h in ALLOWED_HOSTS):
                bad.append(f'{f.relative_to(ROOT)}: {m.group(0)[:80]}')
    assert not bad, 'External URL found (update THIRD_PARTY.md and the privacy pages first):\n' + '\n'.join(bad)


def test_csp_allows_only_the_app_itself(appmod, monkeypatch):
    monkeypatch.delenv('SUPABASE_URL', raising=False)
    csp = appmod._csp('abc')
    parts = {p.split(' ')[0]: p for p in csp.split('; ')}
    assert parts['default-src'] == "default-src 'self'"
    for directive in ('script-src', 'img-src', 'font-src', 'media-src', 'style-src'):
        assert 'http' not in parts[directive] and '*' not in parts[directive], parts[directive]
    assert parts['frame-ancestors'] == "frame-ancestors 'none'" and parts['object-src'] == "object-src 'none'"


def test_known_tracker_names_are_absent():
    names = re.compile(r'google-analytics|googletagmanager|gtag\(|plausible\.io|posthog|mixpanel|hotjar|fbq\(|matomo|umami', re.I)
    hits = [str(p.relative_to(ROOT)) for p in list((ROOT / 'templates').glob('*.html')) + list((ROOT / 'static' / 'js').glob('*.js'))
            if names.search(p.read_text(errors='ignore'))]
    assert not hits, hits


def test_skull_pin_is_the_credited_game_icons_skull_not_a_gpl_svg_repo_file():
    """DL-180 part 21: the previous skull pin was a GPL icon from SVG Repo; the replacement is CC BY 3.0 and credited on the About page."""
    import pathlib
    svg = pathlib.Path('static/icons/pins/skull.svg').read_text()
    assert 'svgrepo' not in svg.lower() and 'Lorc' in svg and 'CC BY 3.0' in svg
    about = pathlib.Path('templates/about.html').read_text()
    assert 'Fanged Skull' in about and 'Lorc' in about
