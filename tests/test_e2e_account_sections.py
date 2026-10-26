"""DL-39: the account page shows one section at a time (profile, privacy, sign-in and security, appearance, your data),
remembers the section, opens the right one after an error or a link, and the theme choice sticks."""
import os
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, pw                      # noqa: F401  (pw is a fixture)
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def visible_sections(page):
    return page.evaluate("() => [...new Set([...document.querySelectorAll('[data-section]')].filter(e => !e.hidden).map(e => e.dataset.section.split(' ')[0]))]")


def test_sections_switch_and_are_remembered(shared_server, pw):
    base = shared_server.url
    ctx = Api(base).context(pw, base)
    page = ctx.new_page(); page.set_viewport_size({'width': 1280, 'height': 900})
    errors = []
    page.on('pageerror', lambda x: errors.append(str(x)))
    page.goto(base + '/account')
    assert visible_sections(page) == ['profile'] and page.locator('.account-nav a.active').inner_text() == 'Profile'
    assert page.locator('#password-form').is_hidden() and page.locator('#display_name').is_visible()
    page.click('[data-section-link=security]')
    assert page.locator('#password-form').is_visible() and page.locator('#display_name').is_hidden()
    assert page.locator('#account-form').is_hidden()                            # no empty panel frame left behind
    assert page.locator('.account-nav a.active').get_attribute('aria-current') == 'page'
    page.click('[data-section-link=privacy]')
    assert page.locator('#account-form .form-actions').is_visible()          # the Save button serves Profile and Privacy
    page.reload()
    assert page.locator('.account-nav a.active').inner_text() == 'Privacy'                              # remembered through the reload
    page.goto(base + '/account#data')
    assert page.locator('#delete-form').is_visible() and page.locator('#export-form').is_visible()
    page.goto(base + '/account#email-panel')                                    # links from other pages land on the right section
    assert page.locator('#email-panel').is_visible() and page.locator('#display_name').is_hidden()
    assert not errors, errors


def test_error_opens_its_section(shared_server, pw):
    base = shared_server.url
    ctx = Api(base).context(pw, base)
    page = ctx.new_page()
    page.goto(base + '/account#profile')
    page.click('[data-section-link=security]')
    page.fill('#current_password', 'wrong-password'); page.fill('#new_password', 'another-pass-1'); page.fill('#confirm_password', 'another-pass-1')
    page.click('#password-form button[type=submit]'); page.wait_for_load_state()
    assert page.locator('#password-error').is_visible() and page.locator('#password-form').is_visible()
    page.goto(base + '/account?mail=sent')
    assert page.locator('#email-panel').is_visible()


def test_theme_choice_sticks_and_follows_the_device(shared_server, pw):
    base = shared_server.url
    ctx = Api(base).context(pw, base)
    page = ctx.new_page()
    page.goto(base + '/account#appearance')
    page.click('label.access-option:has(input[value=light])')
    assert page.evaluate("document.documentElement.dataset.theme") == 'light'
    page.goto(base + '/campaigns')
    assert page.evaluate("document.documentElement.dataset.theme") == 'light'     # the head script on other pages agrees
    page.goto(base + '/account#appearance')
    assert page.locator('input[name=theme_pref][value=light]').is_checked()
    page.click('label.access-option:has(input[value=system])')
    page.emulate_media(color_scheme='dark'); page.goto(base + '/campaigns')
    assert page.evaluate("document.documentElement.dataset.theme") == 'dark'
    page.emulate_media(color_scheme='light'); page.goto(base + '/campaigns')
    assert page.evaluate("document.documentElement.dataset.theme") == 'light'


def test_phone_width_shows_the_sections_as_a_row_of_chips(shared_server, pw):
    base = shared_server.url
    ctx = Api(base).context(pw, base)
    page = ctx.new_page(); page.set_viewport_size({'width': 390, 'height': 800})
    page.goto(base + '/account')
    box = page.locator('.account-nav').bounding_box()
    assert box['height'] < 70 and page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
