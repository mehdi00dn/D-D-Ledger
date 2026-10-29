"""Treasury purses in a real browser: spend from a character's purse."""
import os

import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from playwright.sync_api import expect
from test_e2e_browser import Api, pw                      # noqa: F401  (pw is a fixture)
from conftest import BACKEND, Server, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def shot(page, name):
    if os.environ.get('SHOT_DIR'):
        page.wait_for_timeout(300)
        page.screenshot(path=os.path.join(os.environ['SHOT_DIR'], name), full_page=True)


def test_treasury_purses_page(shared_server, pw):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Purses')
    a = dm.character(cid, 'Aria'); dm.character(cid, 'Bo')
    ctx = dm.context(pw, base); page = ctx.new_page(); page.set_viewport_size({'width': 1280, 'height': 900})
    errors = []
    page.on('pageerror', lambda x: errors.append(str(x)))
    page.goto(f'{base}/campaigns/{cid}/treasury')
    shot(page, 'treasury-empty.png')
    page.locator('.page-actions button:has-text("Add or spend")').click()
    page.select_option('#t-char', label=page.locator('#t-char option', has_text='Aria').inner_text())
    page.fill('#t-gp', '25'); page.fill('#t-note', 'Sold a ruby'); page.click('#t-add button:has-text("Coins in")')
    expect(page.locator('.people-purse', has_text='Aria')).to_contain_text('25')
    assert q('SELECT COALESCE(SUM(gp),0) gp FROM treasury_entries WHERE character_id IS NULL AND campaign_id = ?', cid)[0]['gp'] == 0
    shot(page, 'treasury-filled.png')
    page.locator('.page-actions button:has-text("Share")').click()
    page.select_option('#s-source', label=page.locator('#s-source option', has_text='Aria').inner_text()); page.fill('#s-gp', '10')   # everyone is ticked: Aria is skipped, Bo gets it
    page.click('#t-share button[type=submit]')
    expect(page.locator('.people-purse', has_text='Bo')).to_contain_text('10')
    expect(page.locator('.people-purse', has_text='Aria')).to_contain_text('15')
    page.goto(f'{base}/campaigns/{cid}/characters/{a}')
    expect(page.locator('.cd-purse')).to_contain_text('15 gp')
    shot(page, 'character-purse.png')
    page.click('.cd-purse a:has-text("Add or spend")')
    expect(page.locator('#t-char')).to_have_value(str(a))
    assert not errors, errors
    ctx.close()
