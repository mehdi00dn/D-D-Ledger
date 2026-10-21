"""DL-105 in a real browser: changing Level on the edit form brings back that level's saved stats (and keeps unsaved edits
while the page is open); the details page lists the history.  Set SHOT_DIR to save screenshots."""
import os, time
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


def test_level_change_loads_saved_stats(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Levels')
    ch = dm.character(cid, 'Mira', level=1, max_hp=10, armor_class=12, str_score=10)
    for lv, hp, ac, st in ((2, 18, 13, 12), (3, 27, 15, 14)):
        r = dm.post(f'/campaigns/{cid}/characters/{ch}/edit', data={'name': 'Mira', 'level': lv, 'max_hp': hp, 'armor_class': ac, 'str_score': st,
                                                                   'speed': 30 + 5 * lv, 'save_str': 'on' if lv >= 2 else '', 'skill_stealth': 1 if lv == 3 else ''})
        assert r.status_code == 302
    errors = []
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
    page.on('pageerror', lambda x: errors.append(str(x)))
    page.goto(f'{base}/campaigns/{cid}/characters/{ch}/edit')
    page.wait_for_selector('#level')
    assert page.input_value('#max_hp') == '27'
    assert page.input_value('#speed') == '45' and page.is_checked('input[name=save_str]') and page.is_checked('input[name=skill_stealth][value="1"]')
    page.fill('#level', '1'); page.press('#level', 'Tab')
    assert page.input_value('#speed') == '30' and not page.is_checked('input[name=save_str]') and page.is_checked('input[name=skill_stealth][value=""]')
    assert page.input_value('#max_hp') == '10' and page.input_value('#armor_class') == '12' and page.input_value('#str_score') == '10'
    assert 'level 1' in page.inner_text('#level-hint')
    page.fill('#max_hp', '11')                                     # an unsaved edit at level 1 ...
    page.fill('#level', '2'); page.press('#level', 'Tab')
    assert page.input_value('#max_hp') == '18'
    page.fill('#level', '1'); page.press('#level', 'Tab')
    assert page.input_value('#max_hp') == '11'                     # ... is still there when we come back
    page.fill('#level', '9'); page.press('#level', 'Tab')
    assert page.input_value('#max_hp') == '11' and 'No stats saved for level 9' in page.inner_text('#level-hint')
    _shot(page, 'levels-form')
    page.get_by_role('button', name='Save Changes').click()
    end = time.time() + 15
    while time.time() < end and page.url.rstrip('/').endswith('/edit'):
        time.sleep(0.2)
    assert not page.url.rstrip('/').endswith('/edit'), page.inner_text('body')[-600:]
    page.goto(f'{base}/campaigns/{cid}/characters/{ch}')
    page.wait_for_selector('.lv-table')
    assert page.locator('.lv-table tbody tr').count() == 4 and page.locator('.lv-table tr.is-current').count() == 1
    _shot(page, 'levels-details')
    ctx.close()
    assert not [e for e in errors if 'favicon' not in e], errors
