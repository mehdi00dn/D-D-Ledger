"""DL-185 in a real browser: slots and spells on the edit form, slot pips on the character page and in the peek lightbox, and none in
the battle rows.  Set SHOT_DIR to save screenshots."""
import os, time
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, pw                      # noqa: F401  (pw is a fixture)
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def _shot(page, name):
    d = os.environ.get('SHOT_DIR')
    if d:
        os.makedirs(d, exist_ok=True)
        page.screenshot(path=os.path.join(d, name + '.png'), full_page=True)


def _used(ch):
    return [(r['slot_level'], r['max_slots'], r['used_slots']) for r in q('SELECT * FROM character_spell_slots WHERE character_id = ? ORDER BY slot_level', ch)]


def test_slots_on_the_form_the_page_and_the_peek_but_not_the_battle_row(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Slots')
    ch = dm.character(cid, 'Mira', level=5, max_hp=24, armor_class=14)
    dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': ch})
    errors = []
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
    page.on('pageerror', lambda x: errors.append(str(x)))

    page.goto(f'{base}/campaigns/{cid}/characters/{ch}/edit')
    page.wait_for_selector('[name=slot_max_1]')
    page.fill('[name=slot_max_1]', '4'); page.fill('[name=slot_max_3]', '2')
    page.click('[data-spell-add]')
    row = page.locator('.spl-edit-row').last
    row.locator('[name=spell_name]').fill('Fireball'); row.locator('[name=spell_level]').select_option('3')
    row.locator('[name=spell_damage]').fill('8d6'); row.locator('[name=spell_type]').fill('fire')
    page.click('[data-spell-add]')
    bad = page.locator('.spl-edit-row').last
    bad.locator('[name=spell_name]').fill('Oops'); bad.locator('[name=spell_damage]').fill('9d7')
    playwright.expect(bad.locator('.atk-error')).to_be_visible()
    bad.locator('[data-spell-remove]').click()
    _shot(page, 'slots-form')
    page.get_by_role('button', name='Save changes').click()
    end = time.time() + 15
    while time.time() < end and page.url.rstrip('/').endswith('/edit'):
        time.sleep(0.2)
    assert _used(ch) == [(1, 4, 0), (3, 2, 0)]

    page.goto(f'{base}/campaigns/{cid}/characters/{ch}')
    page.wait_for_selector('[data-slots] .slot-pip')
    assert page.locator('[data-slots] .slot-pip').count() == 6
    page.locator('[data-slots] [data-slot-level="1"].slot-pip').first.click()
    end = time.time() + 10
    while time.time() < end and _used(ch)[0][2] != 1:
        time.sleep(0.2)
    assert _used(ch)[0] == (1, 4, 1)
    playwright.expect(page.locator('[data-slots] .slot-pip.is-full')).to_have_count(5, timeout=10000)
    page.get_by_title('Cast Fireball').click() if page.get_by_title('Cast Fireball').count() else page.locator('[data-cast-level="3"]').first.click()
    end = time.time() + 10
    while time.time() < end and _used(ch)[1][2] != 1:
        time.sleep(0.2)
    assert _used(ch)[1] == (3, 2, 1)
    _shot(page, 'slots-details')

    page.goto(f'{base}/campaigns/{cid}/battle')
    page.wait_for_selector('.battle-row')
    assert page.locator('.battle-row .slot-pip, .battle-slots').count() == 0                 # the battle list stays plain
    page.evaluate('(id) => window.CharPeek.open(id)', ch)
    page.wait_for_selector('#detail-modal [data-peek-slots] .slot-pip')
    assert page.locator('#detail-modal [data-peek-slots] .slot-pip').count() == 6
    _shot(page, 'slots-peek')
    page.locator('#detail-modal [data-peek-slots] [data-slot-level="1"].slot-pip:not(.is-full)').first.click()      # give the spent one back
    end = time.time() + 10
    while time.time() < end and _used(ch)[0][2] != 0:
        time.sleep(0.2)
    assert _used(ch)[0] == (1, 4, 0)
    playwright.expect(page.locator('#detail-modal [data-peek-slots] [data-slot-level="1"].slot-pip.is-full')).to_have_count(4, timeout=10000)
    ctx.close()
    assert not [e for e in errors if 'favicon' not in e], errors


def test_steppers_and_the_spell_book_on_a_phone(pw):
    """The Spell slots card is a stepper per level and the spell book adds a spell that was found by search."""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import test_reference as ref
    from test_e2e_browser import SESSION_COOKIE

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split('?')[0].replace('/api/2024', '', 1)
            body = ref.LISTS.get(path) or ref.DETAILS.get(path)
            self.send_response(200 if body else 404); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(_json.dumps(body or {}).encode())
        def log_message(self, *a): pass
    api = HTTPServer(('127.0.0.1', 0), H); threading.Thread(target=api.serve_forever, daemon=True).start()
    from conftest import Server
    srv = Server({'SRD_API_BASE': f'http://127.0.0.1:{api.server_port}'})
    try:
        base = srv.url
        dm = Api(base)
        cid = dm.campaign('Phone')
        ch = dm.character(cid, 'Mira', level=5, max_hp=24, armor_class=14)
        for scheme in ('dark', 'light'):
            ctx = pw.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True, has_touch=True, color_scheme=scheme)
            ctx.add_cookies([{'name': SESSION_COOKIE, 'value': dm.s.cookies.get(SESSION_COOKIE), 'url': base}])
            page = ctx.new_page()
            errors = []
            page.on('pageerror', lambda x: errors.append(str(x)))
            page.goto(f'{base}/campaigns/{cid}/characters/{ch}/edit')
            page.wait_for_selector('[data-slot-steps]')
            if scheme == 'light':
                page.evaluate("document.documentElement.dataset.theme = 'light'")
            first = page.locator('[data-slot-step]').first
            minus, plus = first.locator('[data-step="-1"]'), first.locator('[data-step="1"]')
            assert minus.is_disabled()
            for _ in range(4):
                plus.tap()
            assert first.locator('.slot-step-val').input_value() == '4' and first.locator('[data-step-pips] i').count() == 4
            minus.tap()
            assert first.locator('.slot-step-val').input_value() == '3' and 'has-slots' in first.get_attribute('class')
            third = page.locator('[data-slot-step]').nth(2)
            third.locator('[data-step="1"]').tap(); third.locator('[data-step="1"]').tap()
            nine = page.locator('[data-slot-step]').nth(8)
            for _ in range(9):
                nine.locator('[data-step="1"]').tap()
            assert nine.locator('.slot-step-val').input_value() == '9' and nine.locator('[data-step="1"]').is_disabled()
            page.locator('#cf-slots').scroll_into_view_if_needed()
            if scheme == 'dark':
                page.locator('section[aria-labelledby="cf-slots"]').screenshot(path=os.path.join(os.environ.get('SHOT_DIR', '/tmp'), 'form-slots-phone-dark.png'))
            page.locator('[data-spellbook-open]').scroll_into_view_if_needed()
            assert page.locator('.spl-empty').is_visible()
            page.locator('[data-spellbook-open]').tap()
            page.wait_for_selector('[data-spellbook-list] .ref-item')
            page.locator('[data-spellbook-list] .ref-item', has_text='Fireball').tap()
            page.wait_for_selector('.spl-edit-row')
            row = page.locator('.spl-edit-row').first
            assert row.locator('[name=spell_name]').input_value() == 'Fireball' and row.locator('[name=spell_level]').input_value() == '3'
            assert not page.locator('.spl-empty').is_visible()
            playwright.expect(page.locator('[data-spellbook-list] .ref-item.is-added')).to_have_count(1)
            page.locator('[data-spell-add]').tap()
            page.locator('section[aria-labelledby="cf-spells"]').screenshot(path=os.path.join(os.environ.get('SHOT_DIR', '/tmp'), f'form-spells-phone-{scheme}.png'))
            _shot(page, f'form-phone-{scheme}')
            assert not errors, errors
            ctx.close()
        dctx = pw.new_context(viewport={'width': 1280, 'height': 900}, device_scale_factor=1)
        dctx.add_cookies([{'name': SESSION_COOKIE, 'value': dm.s.cookies.get(SESSION_COOKIE), 'url': base}])
        page = dctx.new_page()
        page.goto(f'{base}/campaigns/{cid}/characters/{ch}/edit')
        page.wait_for_selector('[data-slot-steps]')
        page.locator('[data-slot-step]').first.locator('[data-step="1"]').click()
        page.locator('section[aria-labelledby="cf-slots"]').screenshot(path=os.path.join(os.environ.get('SHOT_DIR', '/tmp'), 'form-slots-desktop.png'))
        dctx.close()
    finally:
        srv.stop()
