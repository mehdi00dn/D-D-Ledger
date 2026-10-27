"""DL-147 in a real browser: search the manual, add monsters, watch the budget meter, save, and run the list in the battle.
The rules API is stood in for by a tiny local server (the app is pointed at it with SRD_API_BASE)."""
import copy
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from playwright.sync_api import expect
from test_e2e_browser import Api, pw                      # noqa: F401  (pw is a fixture)
from test_reference import DETAILS, GOBLIN, LISTS
from conftest import BACKEND, Server, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')

BUGBEAR = dict(GOBLIN, index='bugbear-warrior', name='Bugbear Warrior', challenge_rating=3, xp=700, hit_points=65, hit_points_roll='10d8+20')


@pytest.fixture
def rules_api():
    lists = copy.deepcopy(LISTS)
    lists['/monsters']['results'].append({'index': 'bugbear-warrior', 'name': 'Bugbear Warrior'})
    details = dict(DETAILS, **{'/monsters/bugbear-warrior': BUGBEAR})

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split('?')[0].replace('/api/2024', '', 1)
            data = lists.get(path) or details.get(path)
            self.send_response(200 if data is not None else 404)
            self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(data if data is not None else {}).encode())
        def log_message(self, *a): pass
    srv = HTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_port}'
    srv.shutdown()


def test_build_save_and_run_an_encounter(appmod, rules_api, pw):
    server = Server({'SRD_API_BASE': rules_api})
    try:
        base = server.url
        dm = Api(base)
        cid = dm.campaign('Enc')
        for n, lv in (('Aria', 3), ('Bo', 3), ('Cy', 3), ('Di', 3)):
            dm.character(cid, n, level=lv)
        ctx = dm.context(pw, base)
        page = ctx.new_page(); page.set_viewport_size({'width': 1280, 'height': 900})
        errors = []
        page.on('pageerror', lambda x: errors.append(str(x)))
        page.goto(f'{base}/campaigns/{cid}/encounters')
        page.fill('input[name=name]', 'Cave mouth'); page.press('input[name=name]', 'Enter'); page.wait_for_load_state()
        assert page.locator('[data-enc-name]').input_value() == 'Cave mouth'
        assert page.locator('[data-enc-run]').is_disabled() and page.locator('[data-enc-tier]').inner_text() == 'No monsters'
        assert page.locator('[data-enc-b=high]').inner_text() == '1,600'                         # four level 3 characters
        page.fill('[data-enc-query]', 'gob')
        page.locator('.enc-result', has_text='Goblin Warrior').get_by_role('button', name='Add').click()
        page.wait_for_selector('.enc-line')
        page.get_by_role('button', name='One more Goblin Warrior').click()
        assert page.locator('.enc-count').inner_text() == '2' and page.locator('[data-enc-total]').inner_text() == '100 XP'
        assert page.locator('[data-enc-tier]').inner_text() == 'Low'
        for _ in range(11):
            page.get_by_role('button', name='One more Goblin Warrior').click()
        assert page.locator('[data-enc-total]').inner_text() == '650 XP' and page.locator('[data-enc-tier]').inner_text() == 'Moderate'
        page.fill('[data-enc-query]', 'bug')
        page.locator('.enc-result', has_text='Bugbear').get_by_role('button', name='Add').click()
        expect(page.locator('.enc-line')).to_have_count(2)
        assert page.locator('[data-enc-total]').inner_text() == '1,350 XP' and page.locator('[data-enc-tier]').inner_text() == 'High'
        page.locator('[data-enc-pc]').first.uncheck()                                            # three characters: budget shrinks
        assert page.locator('[data-enc-b=high]').inner_text() == '1,200' and page.locator('[data-enc-tier]').inner_text() == 'Over High'
        assert page.locator('[data-enc-save]').is_enabled()
        page.click('[data-enc-save]'); page.wait_for_url(f'**/campaigns/{cid}/encounters')                # saving closes the builder
        eid = q('SELECT id FROM encounters WHERE campaign_id = ?', cid)[0]['id']
        page.goto(f'{base}/campaigns/{cid}/encounters/{eid}'); page.wait_for_selector('.enc-line')
        assert page.locator('[data-enc-save]').is_disabled()
        assert sum(r['count'] for r in q('SELECT count FROM encounter_monsters WHERE encounter_id = ?', eid)) == 14
        page.get_by_role('button', name='Remove Goblin Warrior').click()
        page.click('[data-enc-run]'); page.wait_for_url(f'**/campaigns/{cid}/battle')
        assert [r['name'] for r in q("SELECT name FROM characters WHERE campaign_id = ? AND is_npc = 1", cid)] == ['Bugbear Warrior']
        assert not errors, errors
    finally:
        server.stop()
