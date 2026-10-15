"""DL-37: layout CSS uses logical properties (inline-start/end) so a right-to-left language can be added later without a rewrite.
Physical left/right is allowed only on a line that says why with a `/* phys */` marker (centering with translate, map canvas
coordinates, knobs moved by transforms)."""
import re
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / 'static' / 'css'
PHYSICAL = re.compile(
    r'(margin|padding)-(left|right)\b|border-(left|right)\b|border-(top|bottom)-(left|right)-radius|'
    r'text-align:\s*(left|right)\b|(?<![\w-])(left|right):|float:\s*(left|right)|scroll-(margin|padding)-(left|right)')


def test_no_unmarked_physical_direction_in_css():
    bad = []
    for f in sorted(CSS.glob('*.css')):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if PHYSICAL.search(line) and '/* phys */' not in line:
                bad.append(f'{f.name}:{n}: {line.strip()[:90]}')
    assert not bad, ('Use logical properties (margin-inline-start, inset-inline-end, text-align: start, ...) '
                     'or mark a deliberate exception with /* phys */:\n' + '\n'.join(bad))
