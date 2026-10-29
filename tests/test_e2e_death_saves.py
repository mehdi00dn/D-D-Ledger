"""DL-145 in a real browser: the death-save sub-row on a downed character - who sees what, and a roll from the page.
(The d20 is rolled by the server process here, so the roll itself is only checked for shape; test_death_saves.py pins the rules.)
Set SHOT_DIR to also save screenshots of the DM's, the owner's and another player's view."""
import os, re, time
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, pw, wait_text           # noqa: F401  (pw is a fixture)
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _shot(page, name):
    d = os.environ.get('SHOT_DIR')
    if d:
        os.makedirs(d, exist_ok=True)
        page.screenshot(path=os.path.join(d, name + '.png'), full_page=False)


def test_downed_character_shows_a_death_save_row_to_the_right_people(shared_server, pw):
    base = shared_server.url
    dm, ann, bob = Api(base), Api(base), Api(base)
    cid = dm.campaign('Death saves')
    dm.add_member(cid, ann); dm.add_member(cid, bob)
    char = ann.character(cid, 'Mira', max_hp=10)
    pid = dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': char}).json()[-1]['id']
    assert dm.post(f'/campaigns/{cid}/api/battle/{pid}/damage', json={'amount': 50}).status_code == 200

    errors = []
    views = {}
    for who, api in (('dm', dm), ('ann', ann), ('bob', bob)):
        ctx = api.context(pw, base)
        page = ctx.new_page()
        page.on('console', lambda m, e=errors: e.append(m.text) if m.type == 'error' else None)
        page.on('pageerror', lambda x, e=errors: e.append(str(x)))
        page.goto(f'{base}/campaigns/{cid}/battle')
        page.wait_for_selector('.battle-row')
        views[who] = (ctx, page)

    dm_page, ann_page, bob_page = views['dm'][1], views['ann'][1], views['bob'][1]
    for page in (dm_page, ann_page):                                   # the DM and the owner get the tally and the button
        page.wait_for_selector('.death-saves')
        assert page.locator('.ds-pips--ok .ds-pip').count() == 3 and page.locator('.ds-pips--bad .ds-pip').count() == 3
        assert page.locator('.ds-pip.is-on').count() == 0
        assert page.get_by_role('button', name=re.compile('Roll a death save for Mira')).count() == 1
    assert bob_page.locator('.death-saves').count() == 0               # another player sees a downed character, not the tally
    _shot(dm_page, 'death-saves-dm'); _shot(bob_page, 'death-saves-other-player')

    ann_page.get_by_role('button', name=re.compile('Roll a death save for Mira')).click()
    wait_text(ann_page, [], timeout=1)
    deadline = time.time() + 10
    while time.time() < deadline and not re.search(r'Rolled \d+|Natural 20', ann_page.inner_text('body')):
        time.sleep(0.2)
    body = ann_page.inner_text('body')
    assert re.search(r'Rolled (\d+): (success|failure|two failures|stable|dead)|Natural 20: back on 1 HP', body), body[-400:]
    _shot(ann_page, 'death-saves-owner-after-roll')

    row = next(r for r in dm.get(f'/campaigns/{cid}/api/battle').json() if r['id'] == pid)
    assert row['current_hp'] == 1 or row['death_successes'] + row['death_failures'] > 0 or row['is_stable'] or row['is_dead']
    for ctx, _ in views.values():
        ctx.close()
    assert not [e for e in errors if 'favicon' not in e], errors
