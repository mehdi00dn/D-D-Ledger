"""Character cards, campaign covers and empty states in a real browser.  Set SHOT_DIR to save screenshots."""
import os
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, pw                      # noqa: F401  (pw is a fixture)
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _shot(page, name):
    d = os.environ.get('SHOT_DIR')
    if d:
        os.makedirs(d, exist_ok=True)
        page.screenshot(path=os.path.join(d, name + '.png'), full_page=True)


def test_cards_covers_and_empty_states_render(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    empty = dm.campaign('Empty Hall')
    cid = dm.campaign('The Ashfall Chronicles')
    a = dm.character(cid, 'Kael Thorne', level=3, class_key='fighter', max_hp=40, armor_class=16, str_score=16, dex_score=12)
    dm.character(cid, 'Nyx', level=3, class_key='rogue', max_hp=21, armor_class=14)
    dm.character(cid, 'Plain Jane')
    pid = dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': a}).json().get('id') if False else None
    ctx = dm.context(pw, base)
    page = ctx.new_page(); page.set_viewport_size({'width': 1280, 'height': 800})
    errors = []
    page.on('pageerror', lambda x: errors.append(str(x)))
    page.goto(f'{base}/campaigns')
    assert page.locator('.cover-monogram').count() >= 2
    _shot(page, 'cards-campaigns')
    page.goto(f'{base}/campaigns/{cid}/characters')
    assert page.locator('.dossier-card').count() == 3
    assert page.locator('.avatar-class').count() == 2 and page.locator('.avatar-initial').count() == 1
    _shot(page, 'cards-characters')
    page.goto(f'{base}/campaigns/{empty}/characters')
    _shot(page, 'cards-empty')
    ctx.close()
    assert not errors, errors
