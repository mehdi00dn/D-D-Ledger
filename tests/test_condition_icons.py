"""DL-173: the condition sprite has an icon for every condition, and the About page credits their authors (CC BY 3.0)."""
import re
from pathlib import Path

CONDITIONS = ('blinded', 'charmed', 'deafened', 'exhaustion', 'frightened', 'grappled', 'incapacitated', 'invisible', 'paralyzed',
              'petrified', 'poisoned', 'prone', 'restrained', 'stunned', 'unconscious')
SPRITE = Path(__file__).resolve().parent.parent / 'static' / 'icons' / 'conditions.svg'


def test_sprite_has_every_condition_plus_the_app_symbols_and_nothing_hard_coded_in_colour():
    svg = SPRITE.read_text(encoding='utf-8')
    ids = re.findall(r'<symbol id="([^"]+)"', svg)
    assert set(CONDITIONS) | {'custom', 'plus'} == set(ids) and len(ids) == len(set(ids))
    for sym in re.findall(r'<symbol .*?</symbol>', svg, re.S):
        assert not re.search(r'(?:fill|stroke)(?:=|:)\s*"?#', sym), 'icons take the text colour (currentColor)'
        assert 'd="' in sym


def test_about_credits_the_icon_authors(make_user):
    about = make_user().c.get('/about').get_data(as_text=True)
    assert 'game-icons.net' in about and 'creativecommons.org/licenses/by/3.0' in about
    for author in ('Lorc', 'Delapouite', 'Skoll', 'Cathelineau', 'Sbed'):
        assert author in about, author
