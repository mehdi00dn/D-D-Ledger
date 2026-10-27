"""Treasury purses and the encounters list in a real browser: rename and run from the list, spend from a character's purse."""
import os

import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from playwright.sync_api import expect
from test_e2e_browser import Api, pw                      # noqa: F401  (pw is a fixture)
from test_e2e_encounters import rules_api                 # noqa: F401  (fixture)
from conftest import BACKEND, Server, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def shot(page, name):
    if os.environ.get('SHOT_DIR'):
        page.wait_for_timeout(300)
        page.screenshot(path=os.path.join(os.environ['SHOT_DIR'], name), full_page=True)


def test_encounters_list_rename_and_run(rules_api, pw):
    server = Server({'SRD_API_BASE': rules_api})
    try:
        base = server.url; dm = Api(base); cid = dm.campaign('Enc')
        for n in ('Aria', 'Bo', 'Cy', 'Di'):
            dm.character(cid, n, level=3)
        ctx = dm.context(pw, base); page = ctx.new_page(); page.set_viewport_size({'width': 1280, 'height': 900})
        errors = []
        page.on('pageerror', lambda x: errors.append(str(x)))
        page.goto(f'{base}/campaigns/{cid}/encounters')
        page.fill('.enc-new-box input[name=name]', 'Cave mouth'); page.press('.enc-new-box input[name=name]', 'Enter'); page.wait_for_load_state()
        page.fill('[data-enc-query]', 'gob')
        page.locator('.enc-result', has_text='Goblin Warrior').get_by_role('button', name='Add').click()
        page.wait_for_selector('.enc-line')
        page.get_by_role('button', name='One more Goblin Warrior').click()
        shot(page, 'enc-builder.png')
        page.click('[data-enc-save]'); page.wait_for_url(f'**/campaigns/{cid}/encounters')
        page.goto(f'{base}/campaigns/{cid}/encounters')
        shot(page, 'enc-list.png')
        box = page.locator('[data-enc-rename]')
        box.fill('Goblin ambush'); box.press('Enter')
        expect(page.locator('[data-enc-list-status]')).to_contain_text('Renamed to Goblin ambush')
        assert q('SELECT name FROM encounters WHERE campaign_id = ?', cid)[0]['name'] == 'Goblin ambush'
        page.click('[data-enc-run]'); page.wait_for_url(f'**/campaigns/{cid}/battle')
        assert [r['name'] for r in q('SELECT name FROM characters WHERE campaign_id = ? AND is_npc = 1 ORDER BY id', cid)] == ['Goblin Warrior']                      # two goblins, one character
        # deleting asks whether the creature should leave the character list too
        page.goto(f'{base}/campaigns/{cid}/encounters')
        page.get_by_role('button', name='Delete the encounter Goblin ambush').click()
        expect(page.locator('[data-enc-dialog]')).to_be_visible()
        shot(page, 'enc-delete-dialog.png')
        page.click('[data-enc-dialog-cancel]')
        expect(page.locator('[data-enc-dialog]')).to_be_hidden()
        assert q('SELECT id FROM encounters WHERE campaign_id = ?', cid)
        page.get_by_role('button', name='Delete the encounter Goblin ambush').click()
        page.click('[data-enc-dialog-keep]'); page.wait_for_load_state()
        assert not q('SELECT id FROM encounters WHERE campaign_id = ?', cid) and len(q('SELECT id FROM characters WHERE is_npc = 1 AND campaign_id = ?', cid)) == 1
        assert not errors, errors
    finally:
        server.stop()


def test_treasury_purses_page(shared_server, pw):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Purses')
    a = dm.character(cid, 'Aria'); dm.character(cid, 'Bo')
    ctx = dm.context(pw, base); page = ctx.new_page(); page.set_viewport_size({'width': 1280, 'height': 900})
    errors = []
    page.on('pageerror', lambda x: errors.append(str(x)))
    page.goto(f'{base}/campaigns/{cid}/treasury')
    shot(page, 'treasury-empty.png')
    page.select_option('#t-char', label='Aria'); page.fill('#t-gp', '25'); page.fill('#t-note', 'Sold a ruby')
    page.click('#add-form button[type=submit]'); page.wait_for_load_state()
    expect(page.locator('.people-purse', has_text='Aria')).to_contain_text('25')
    assert q('SELECT COALESCE(SUM(gp),0) gp FROM treasury_entries WHERE character_id IS NULL AND campaign_id = ?', cid)[0]['gp'] == 0
    shot(page, 'treasury-filled.png')
    page.select_option('#s-source', label='Aria'); page.fill('#s-gp', '10')                  # everyone is ticked: Aria is skipped, Bo gets it
    page.click('button:has-text("Share")'); page.wait_for_load_state()
    assert not page.locator('.banner-error').count()
    expect(page.locator('.people-purse', has_text='Bo')).to_contain_text('10')
    expect(page.locator('.people-purse', has_text='Aria')).to_contain_text('15')
    page.goto(f'{base}/campaigns/{cid}/characters/{a}')
    expect(page.locator('.cd-purse')).to_contain_text('15 gp')
    shot(page, 'character-purse.png')
    page.click('.cd-purse a:has-text("Add or spend")')
    expect(page.locator('#t-char')).to_have_value(str(a))
    assert not errors, errors
    ctx.close()
