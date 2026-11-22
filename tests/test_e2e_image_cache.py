"""DL-180 part 29 in a real browser: avatars and campaign pictures come from the browser's own cache after the first visit."""
import os
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, pw                                      # noqa: F401
from conftest import BACKEND, png_bytes

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def test_avatars_are_not_downloaded_again_when_a_page_is_reloaded_or_revisited(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Cache')
    dm.post(f'/campaigns/{cid}/characters/new', data={'name': 'Pic', 'max_hp': '5'}, files={'avatar': ('a.png', png_bytes(300, 300), 'image/png')})
    ctx = dm.context(pw, base); page = ctx.new_page()
    served = {}
    cdp = ctx.new_cdp_session(page); cdp.send('Network.enable')
    urls, cached = {}, set()
    cdp.on('Network.requestWillBeSent', lambda e: urls.__setitem__(e['requestId'], e['request']['url']))
    cdp.on('Network.requestServedFromCache', lambda e: cached.add(e['requestId']))
    hits = []
    page.on('request', lambda r: hits.append(r.url) if '/uploads/' in r.url else None)

    def image_loads():
        page.wait_for_load_state('networkidle'); page.wait_for_timeout(400)
        mine = [rid for rid, u in urls.items() if '/uploads/' in u]
        return mine, [rid for rid in mine if rid in cached]

    page.goto(f'{base}/campaigns/{cid}/characters'); first, _ = image_loads()
    assert first, 'the page shows an avatar'
    urls.clear(); cached.clear()
    page.wait_for_timeout(1500)                                                    # the session cookie is signed with the time, so it differs from the first visit
    page.reload(); again, from_cache = image_loads()
    assert again and set(again) == set(from_cache), (again, from_cache)           # a reload does not fetch them again
    urls.clear(); cached.clear()
    page.wait_for_timeout(1500)
    page.goto(f'{base}/campaigns/{cid}'); other, from_cache = image_loads()          # nor does another page that shows the same picture
    assert set(other) == set(from_cache), (other, from_cache)
    ctx.close()
