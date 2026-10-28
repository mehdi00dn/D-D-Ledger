"""The Treasury changes in place: no page load for adding, spending, sharing, removing, filtering or paging."""
import os
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from playwright.sync_api import expect
from test_e2e_browser import Api, pw, _collect                      # noqa: F401
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only')
SHOTS = os.environ.get('TREASURY_SHOTS')


def test_treasury_updates_without_reloading(pw, shared_server):
    base = shared_server.url; me = Api(base); cid = me.campaign('Coin Hall')
    for n in ('Frodo', 'Mond'):
        me.character(cid, n)
    for i in range(1, 14):
        me.post(f'/campaigns/{cid}/treasury/add', data={'kind': 'income', 'note': f'Seed {i:02d}', 'gp': '1'})
    ctx = me.context(pw, base); ctx.set_default_timeout(8000); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/treasury'); page.wait_for_load_state('load')
    loads = []; page.on('request', lambda r: loads.append(r.url) if r.resource_type == 'document' else None)
    expect(page.locator('.treasury-entry')).to_have_count(10)
    expect(page.locator('.treasury-entry').first).to_contain_text('Seed 13')       # newest on top
    if SHOTS: page.screenshot(path=SHOTS + '/page-%s.png' % page.viewport_size['width'])
    # page two, in place
    page.click('.treasury-pager a:has-text("Older")'); expect(page.locator('.treasury-entry')).to_have_count(3)
    expect(page.locator('.treasury-entry').first).to_contain_text('Seed 03')
    # add to a character's purse from its own button
    page.locator('.people-purse', has_text='Frodo').locator('button:has-text("Add or spend")').click()
    expect(page.locator('#t-add')).to_be_visible()
    expect(page.locator('#t-char')).to_have_value(page.locator('#t-char option', has_text='Frodo').get_attribute('value'))
    if SHOTS: page.screenshot(path=SHOTS + '/dialog-%s.png' % page.viewport_size['width'])
    page.fill('#t-pp', '15'); page.fill('#t-gp', '40'); page.fill('#t-note', 'Dragon hoard'); page.click('#t-add button[type=submit]')
    expect(page.locator('#treasury-status')).to_contain_text("Added 15 pp 40 gp to Frodo's purse")
    expect(page.locator('#t-add')).not_to_be_visible()
    expect(page.locator('.people-purse', has_text='Frodo').locator('.people-purse-total')).to_contain_text('190 gp')
    expect(page.locator('.treasury-entry').first).to_contain_text('Dragon hoard')     # back on page one, newest on top
    # an error stays in the dialog
    page.click('.page-actions button:has-text("Add or spend")')
    page.check('#t-add input[value=expense]'); page.fill('#t-gp', '99999'); page.click('#t-add button[type=submit]')
    expect(page.locator('#t-add .treasury-error')).to_contain_text('enough')
    page.click('#t-add [data-close-dialog] >> nth=1')
    # filter and remove
    page.click('.treasury-filter a:has-text("Frodo")'); expect(page.locator('.treasury-entry')).to_have_count(1)
    page.once('dialog', lambda d: d.accept())
    page.locator('.treasury-entry button.quest-delete').first.click()
    page.click('.modal-confirm, [data-confirm-ok], button:has-text("Confirm"), button:has-text("Remove")') if page.locator('.confirm-modal, [role=alertdialog]').count() else None
    expect(page.locator('.treasury-entry')).to_have_count(0)
    assert loads == [] , loads                      # nothing was a page load
    assert not [b for b in bad if b[0] != 400] and not errs, (bad, errs)       # the one 400 is the refused overspend
