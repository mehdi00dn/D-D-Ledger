"""DL-180 part 13: motion rules that the style sheets keep.

Static checks only: they read the shipped style sheets.
"""
import glob
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), '..')
SHEETS = sorted(glob.glob(os.path.join(ROOT, 'static', 'css', '*.css')))


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def test_no_style_sheet_transitions_all_properties():
    # `transition: all` animates every property that ever changes (layout included) and hides which ones were meant to.
    for path in SHEETS:
        assert not re.search(r'transition\s*:\s*all\b', _read(path)), os.path.basename(path)


def test_dialogs_and_panels_open_without_overshoot():
    # panel-pop-in is the opening motion of every dialog and overlay panel; a bouncing curve (a y2 above 1) made them wobble.
    css = _read(os.path.join(ROOT, 'static', 'css', 'style.css'))
    uses = re.findall(r'animation\s*:\s*panel-pop-in\s+([^;]+);', css)
    assert len(uses) >= 4
    for timing in uses:
        assert 'var(--ease-out)' in timing, timing
        m = re.search(r'(\d*\.?\d+)s', timing)
        assert float(m.group(1)) <= 0.3, timing


def test_reduced_motion_still_flattens_every_animation_and_transition():
    css = _read(os.path.join(ROOT, 'static', 'css', 'style.css'))
    assert re.search(r'prefers-reduced-motion:\s*reduce\)\s*\{\s*\*\s*\{\s*animation-duration:\s*0\.001ms\s*!important;\s*transition-duration:\s*0\.001ms\s*!important', css)
