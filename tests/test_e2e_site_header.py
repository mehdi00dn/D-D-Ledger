"""DL-180 part 31 in a real browser: the header's links are centred on the page, and Log out waits under the profile until it is hovered."""
import os
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, pw                                      # noqa: F401
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _page(pw, api, base, w, h=700):
    ctx = api.context(pw, base); page = ctx.new_page(); page.set_viewport_size({'width': w, 'height': h})
    return ctx, page


@pytest.mark.parametrize('width', [1440, 1280, 1100, 980])
def test_the_links_are_centred_on_the_page_and_log_out_shows_on_hovering_the_profile(pw, shared_server, width):
    base = shared_server.url; api = Api(base)
    ctx, page = _page(pw, api, base, width)
    page.goto(base + '/campaigns'); page.wait_for_load_state('load')
    box = page.locator('.pub-links').bounding_box()
    assert abs((box['x'] + box['width'] / 2) - width / 2) <= 1, 'the links sit in the middle of the bar'
    brand, actions = page.locator('.pub-header .pub-brand').bounding_box(), page.locator('.pub-actions').bounding_box()
    assert brand['x'] + brand['width'] <= box['x'] and box['x'] + box['width'] <= actions['x'], 'nothing overlaps'
    assert page.evaluate('() => document.documentElement.scrollWidth - document.documentElement.clientWidth') <= 1
    logout = page.locator('.pub-account-menu .pub-login')
    assert not logout.is_visible(), 'Log out is hidden until the profile is hovered'
    page.hover('.pub-profile'); page.wait_for_timeout(400)
    assert logout.is_visible()
    page.mouse.move(box['x'] + 10, 400); page.wait_for_timeout(400)
    assert not logout.is_visible()
    page.focus('.pub-profile'); page.wait_for_timeout(300)                     # the keyboard gets it too
    assert logout.is_visible()
    page.hover('.pub-profile'); page.hover('.pub-account-menu .pub-login')       # and the pointer can travel from the profile to the button
    page.wait_for_timeout(300)
    assert logout.is_visible()
    ctx.close()


def test_on_a_phone_log_out_stays_in_the_menu(pw, shared_server):
    base = shared_server.url; api = Api(base)
    ctx, page = _page(pw, api, base, 390, 844)
    page.goto(base + '/campaigns'); page.wait_for_load_state('load')
    page.click('[data-nav-toggle]'); page.wait_for_timeout(500)
    assert page.locator('.pub-account-menu .pub-login').is_visible()
    ctx.close()
