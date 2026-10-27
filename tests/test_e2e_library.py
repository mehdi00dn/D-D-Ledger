"""DL-160 in a real browser: build a library character, add it to a campaign, feature it on the profile."""
import os
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from playwright.sync_api import expect
from test_e2e_browser import Api, pw, _collect                      # noqa: F401  (pw is a fixture)
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def test_build_add_and_feature_a_library_character(pw, shared_server):
    base = shared_server.url; me = Api(base); cid = me.campaign('Ember Coast')
    ctx = me.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + '/library')
    expect(page.locator('.empty-state')).to_contain_text('Your library is empty')
    page.click('.page-actions .btn-primary')
    page.fill('#name', 'Sylas Thorne'); page.fill('#level', '5'); page.fill('#max_hp', '41')
    assert page.locator('input[name="is_npc"]').count() == 0 and page.locator('.faction-picks').count() == 0
    page.click('.cf-bar .btn-primary'); page.wait_for_url('**/library/*')
    expect(page.locator('h1.page-title')).to_have_text('Sylas Thorne')
    page.select_option('#lib-camp', label='Ember Coast'); page.click('.lib-add button'); page.wait_for_load_state()
    expect(page.locator('.banner-success')).to_contain_text('Added to the campaign')
    expect(page.locator('.lib-chips')).to_contain_text('Ember Coast')
    assert page.locator('.lib-add').count() == 0                                   # already in the only campaign
    page.click('text=Show on my profile'); page.wait_for_load_state()
    expect(page.locator('.cd-chip', has_text='On your profile')).to_be_visible()
    page.goto(base + f'/campaigns/{cid}/characters')
    expect(page.locator('#character-grid')).to_contain_text('Sylas Thorne')
    expect(page.locator('#character-grid')).to_contain_text('Added from a personal library')
    page.goto(base + f'/u/{me.name}')
    expect(page.locator('body')).to_contain_text('Sylas Thorne')
    assert not bad and not errs, (bad, errs)
    assert q('SELECT COUNT(*) n FROM characters WHERE library_id IS NOT NULL')[0]['n'] >= 1
