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
