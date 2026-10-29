"""Compact battle rows in a real browser: a normal row stays one tidy line on desktop, the phone layout does not scroll
sideways, and the Died? / remove icon buttons still work.  Set SHOT_DIR to save screenshots (desktop dark and light, phone)."""
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


def test_rows_are_compact_and_still_work(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Compact rows')
    ids = {n: dm.character(cid, n, max_hp=hp, **extra) for n, hp, extra in
           (('Frodo', 70, {}), ('Mira', 30, {}), ('Ogre', 59, {'is_npc': 'on'}), ('Sam', 22, {}))}
    pids = {}
    for n, cid_char in ids.items():
        rows = dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': cid_char}).json()
        pids[n] = rows[-1]['id']
    assert dm.post(f'/campaigns/{cid}/api/battle/{pids["Frodo"]}/damage', json={'amount': 500}).status_code == 200      # down
    assert dm.post(f'/campaigns/{cid}/api/battle/{pids["Sam"]}/damage', json={'amount': 5}).status_code == 200
    assert dm.post(f'/campaigns/{cid}/api/battle/{pids["Mira"]}/conditions', json={'conditions': ['poisoned', 'prone']}).status_code == 200

    errors = []
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
    page.on('pageerror', lambda x: errors.append(str(x)))
    page.goto(f'{base}/campaigns/{cid}/battle')
    page.wait_for_selector('.battle-row')

    mira = page.locator('.battle-row', has_text='Mira')
    assert mira.bounding_box()['height'] <= 62, mira.bounding_box()            # was ~90 px with the conditions on their own line
    ogre = page.locator('.battle-row', has_text='Ogre')
    assert ogre.bounding_box()['height'] <= 62, ogre.bounding_box()
    frodo = page.locator('.battle-row', has_text='Frodo')
    assert frodo.locator('.death-saves').count() == 1
    end, name = frodo.locator('.battle-end').bounding_box(), frodo.locator('.battle-name').bounding_box()
    assert abs(end['y'] - name['y']) < 30, 'the end buttons belong on the first line, not under the death saves'
    _shot(page, 'rows-desktop-dark')
    page.evaluate("document.documentElement.setAttribute('data-theme','light')")
    page.wait_for_timeout(900)
    _shot(page, 'rows-desktop-light')
    page.evaluate("document.documentElement.setAttribute('data-theme','dark')")
    page.wait_for_timeout(900)

    page.set_viewport_size({'width': 390, 'height': 844})
    page.wait_for_timeout(400)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
    _shot(page, 'rows-phone')
    page.set_viewport_size({'width': 1400, 'height': 900})
    page.wait_for_timeout(300)

    page.locator('.battle-row', has_text='Frodo').get_by_role('button', name=re.compile('Mark Frodo as died')).click()
    end = time.time() + 8
    while time.time() < end and not page.locator('.battle-row.is-dead', has_text='Frodo').count():
        time.sleep(0.2)
    assert page.locator('.battle-row.is-dead', has_text='Frodo').count() == 1
    page.locator('.battle-row', has_text='Sam').get_by_role('button', name=re.compile('Remove Sam from battle')).click()
    page.get_by_role('button', name=re.compile('^(Remove|Confirm|Yes)', re.I)).last.click()
    end = time.time() + 8
    while time.time() < end and page.locator('.battle-row', has_text='Sam').count():
        time.sleep(0.2)
    assert page.locator('.battle-row', has_text='Sam').count() == 0
    ctx.close()
    assert not [e for e in errors if 'favicon' not in e], errors
