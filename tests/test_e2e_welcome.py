"""DL-161 in a real browser: sign up, land on the profile page, fill it in, carry on to the campaigns list."""
import os

import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from playwright.sync_api import expect
from test_e2e_browser import pw  # noqa: F401
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def test_sign_up_then_fill_in_the_profile(pw, shared_server):
    base = shared_server.url
    ctx = pw.new_context(viewport={'width': 1100, 'height': 1000}); page = ctx.new_page()
    errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(f'{base}/register')
    page.fill('#username', 'welcome_walker'); page.fill('#email', 'walker@example.com')
    page.fill('#password', 'correct horse 1'); page.fill('#confirm', 'correct horse 1')
    page.click('.auth-submit')                                                # DL-167: stopped until both boxes are ticked
    assert page.url.endswith('/register')
    with ctx.expect_page() as other:                                          # the links open a new tab: the typed form stays
        page.click('.auth-accept a >> nth=0')
    other.value.close()
    assert page.input_value('#username') == 'welcome_walker'
    page.check('[name=accept_terms]'); page.check('[name=accept_cookies]')
    page.click('.auth-submit')
    page.wait_for_url('**/welcome**')                                         # test servers run with EMAIL_CONFIRM_REQUIRED=0 (DL-167), so no confirm step;
                                                                              # the "Check your inbox" page itself is covered in tests/test_email_gate.py
    expect(page.locator('h1')).to_have_text('Set up your profile')
    shot = os.environ.get('WELCOME_SHOT')
    if shot:
        page.screenshot(path=shot, full_page=True)
    page.fill('#username', 'Walker_the_wise'); page.fill('#display_name', 'Walker'); page.fill('#bio', 'Rogue main.')
    page.click('.auth-submit')
    page.wait_for_url('**/campaigns')
    page.goto(f'{base}/account'); page.wait_for_load_state('networkidle')
    assert page.input_value('#display_name') == 'Walker'
    assert not errors, errors
    ctx.close()
