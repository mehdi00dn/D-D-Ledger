"""DL-146 in a real browser: the "Turn" line under each battle row - a Player ticks their own, the DM sees it, enemies stay
private.  Set SHOT_DIR to also save screenshots (desktop dark, desktop light, phone)."""
import os, re, time
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
        page.screenshot(path=os.path.join(d, name + '.png'), full_page=False)


def _wait(fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.2)
    return False


def test_turn_line_ticks_and_privacy(shared_server, pw):
    base = shared_server.url
    dm, ann = Api(base), Api(base)
    cid = dm.campaign('Economy')
    dm.add_member(cid, ann)
    mira = ann.character(cid, 'Mira', max_hp=20)
    ogre = dm.character(cid, 'Ogre', max_hp=59, is_npc='on')
    for c in (mira, ogre):
        assert dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': c}).status_code == 200
    errors = []
    pages = {}
    for who, api in (('dm', dm), ('ann', ann)):
        ctx = api.context(pw, base)
        page = ctx.new_page()
        page.on('console', lambda m, e=errors: e.append(m.text) if m.type == 'error' else None)
        page.on('pageerror', lambda x, e=errors: e.append(str(x)))
        page.goto(f'{base}/campaigns/{cid}/battle')
        page.wait_for_selector('.battle-row')
        pages[who] = (ctx, page)
    dm_page, ann_page = pages['dm'][1], pages['ann'][1]
    assert dm_page.locator('.eco').count() == 0                         # no combat yet, no Turn line
    assert dm.post(f'/campaigns/{cid}/api/battle/next-turn').status_code == 200
    dm_page.wait_for_selector('.eco', timeout=8000)                     # the 3 s poll picks up that combat started
    ann_page.wait_for_selector('.eco', timeout=8000)

    mira_row = ann_page.locator('.battle-row', has_text='Mira')
    mira_row.get_by_role('button', name=re.compile('Reaction used for Mira')).click()
    mira_row.get_by_role('button', name='Mira is concentrating').click()
    assert _wait(lambda: mira_row.locator('.eco-chip.is-on').count() == 2)
    assert mira_row.get_by_role('button', name=re.compile('Reaction used for Mira')).get_attribute('aria-pressed') == 'true'
    ogre_row_for_player = ann_page.locator('.battle-row', has_text='Ogre')
    assert ogre_row_for_player.locator('.eco').count() == 0             # a monster's turn line is the DM's only
    assert dm_page.locator('.battle-row', has_text='Ogre').locator('.eco-chip').count() == 5

    assert _wait(lambda: dm_page.locator('.battle-row', has_text='Mira').locator('.eco-chip.is-on').count() == 2, 8)
    dm_page.locator('.battle-row', has_text='Ogre').get_by_role('button', name=re.compile('Action used for Ogre')).click()
    _shot(dm_page, 'economy-dm-dark'); _shot(ann_page, 'economy-player-dark')
    dm_page.evaluate("document.documentElement.setAttribute('data-theme','light')")
    dm_page.wait_for_timeout(900)                                       # let the theme cross-fade finish
    _shot(dm_page, 'economy-dm-light')

    ctx = dm.context(pw, base)
    phone = ctx.new_page()
    phone.set_viewport_size({'width': 390, 'height': 844})
    phone.goto(f'{base}/campaigns/{cid}/battle')
    phone.wait_for_selector('.eco')
    _shot(phone, 'economy-phone')
    row = next(r for r in dm.get(f'/campaigns/{cid}/api/battle').json() if r['character_id'] == mira)
    assert row['spent'] == ['reaction'] and row['concentrating'] is True
    ctx.close()
    for c, _ in pages.values():
        c.close()
    assert not [e for e in errors if 'favicon' not in e], errors
