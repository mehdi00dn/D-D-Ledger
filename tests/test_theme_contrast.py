"""DL-173: small tinted-button text keeps 4.5:1 in both themes."""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / 'static/css/style.css').read_text()


def _lum(h):
    h = h.lstrip('#')
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(ch[0]) + 0.7152 * f(ch[1]) + 0.0722 * f(ch[2])


def _ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _tokens(name):
    return re.findall(r'--%s:\s*(#[0-9a-fA-F]{6})' % name, CSS)


def test_small_label_inks_pass_on_both_themes():
    # (token, dark background, light background) as measured by axe on the battle row
    for token, dark_bg, light_bg in (
        ('blood-ink-sm', '#3c2933', '#f0d6ce'),
        ('blue-ink', '#2a3a45', '#dee7e6'),
    ):
        dark, light = _tokens(token)[:2]
        assert _ratio(dark, dark_bg) >= 4.5, token
        assert _ratio(light, light_bg) >= 4.5, token


def test_temp_hp_colours_are_not_hardcoded():
    assert '#7ec8ff;' not in CSS.split('--blue-ink: #7ec8ff;', 1)[1]
