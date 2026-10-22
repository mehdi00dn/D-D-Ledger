"""DL-150 in a real browser: add attacks on the edit form, then roll to-hit and damage from the sheet.
Set SHOT_DIR to save screenshots."""
import os, time
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, pw                      # noqa: F401  (pw is a fixture)
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _shot(page, name):
    d = os.environ.get('SHOT_DIR')
    if d:
        os.makedirs(d, exist_ok=True)
        page.screenshot(path=os.path.join(d, name + '.png'), full_page=True)


def test_add_attacks_and_roll_them(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Attacks')
    ch = dm.character(cid, 'Mira', level=3, max_hp=24, armor_class=14)
    errors = []
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
    page.on('pageerror', lambda x: errors.append(str(x)))
    page.goto(f'{base}/campaigns/{cid}/characters/{ch}/edit')
    page.wait_for_selector('[data-attack-add]')
    for name, bonus, dmg, typ in (('Longsword', '5', '1d8+3', 'slashing'), ('Dagger', '3', '1d4+1', 'piercing')):
        page.click('[data-attack-add]')
        row = page.locator('.atk-edit-row').last
        row.locator('[name=attack_name]').fill(name); row.locator('[name=attack_bonus]').fill(bonus)
        row.locator('[name=attack_damage]').fill(dmg); row.locator('[name=attack_type]').fill(typ)
    page.locator('.atk-edit-row').first.locator('[data-attack-remove]').click()     # remove Longsword, then add it back below
    page.click('[data-attack-add]')
    row = page.locator('.atk-edit-row').last
    row.locator('[name=attack_name]').fill('Longsword'); row.locator('[name=attack_bonus]').fill('5')
    row.locator('[name=attack_damage]').fill('1d8+3'); row.locator('[name=attack_type]').fill('slashing')
    _shot(page, 'attacks-form')
    page.get_by_role('button', name='Save Changes').click()
    end = time.time() + 15
    while time.time() < end and page.url.rstrip('/').endswith('/edit'):
        time.sleep(0.2)
    assert not page.url.rstrip('/').endswith('/edit')
    page.goto(f'{base}/campaigns/{cid}/characters/{ch}')
    page.wait_for_selector('.atk-row')
    assert page.locator('.atk-row').count() == 2
    assert page.locator('.atk-row .atk-name').all_inner_texts() == ['Dagger', 'Longsword']
    _shot(page, 'attacks-details')
    page.locator('.atk-row').nth(1).get_by_title('Roll Longsword damage').click()
    toast = page.locator('.dice-toast').first
    playwright.expect(toast).to_contain_text('Longsword damage', timeout=15000)
    page.locator('.atk-row').nth(1).get_by_title('Roll Longsword to hit').click()
    playwright.expect(page.locator('.dice-toast').filter(has_text='Longsword attack').first).to_be_visible(timeout=15000)
    ctx.close()
    assert not [e for e in errors if 'favicon' not in e], errors
