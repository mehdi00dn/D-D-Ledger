"""Campaign Home in a real browser: Read more, the bio editor in Settings, and attaching a character to a player.
Set SHOT_DIR to save screenshots."""
import json
import os
import re
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, pw                      # noqa: F401  (pw is a fixture)
from conftest import BACKEND, q, png_bytes

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')

LONG = [f'Paragraph {i}: the party hunts the Ember King across the Cinder Coast.' for i in range(1, 9)]


def _shot(page, name):
    d = os.environ.get('SHOT_DIR')
    if d:
        os.makedirs(d, exist_ok=True)
        page.screenshot(path=os.path.join(d, name + '.png'), full_page=True)


def _campaign(dm, bio, notes=None):
    r = dm.post('/campaigns/new', data={'name': 'The Ashfall Chronicles', 'description': json.dumps(bio)},
                files={'avatar': ('c.png', png_bytes(640, 384, (120, 60, 30)), 'image/png')})
    cid = int(re.search(r'/campaigns/(\d+)/', r.headers['Location']).group(1))
    if notes:
        dm.post(f'/campaigns/{cid}/edit', data={'name': 'The Ashfall Chronicles', 'description': json.dumps(bio), 'notes': json.dumps(notes)})
    return cid


def test_long_bio_folds_behind_read_more(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = _campaign(dm, [f'<div>{t}</div>' for t in LONG], ['<div>Plot: the duke is the traitor</div>', {'t': 'Only the DM sees this', 'h': 1}])
    for n in ('Kael Thorne', 'Nyx'):
        dm.character(cid, n, level=3)
    ctx = dm.context(pw, base)
    page = ctx.new_page(); page.set_viewport_size({'width': 1280, 'height': 800})
    errors = []
    page.on('pageerror', lambda x: errors.append(str(x)))
    page.goto(f'{base}/campaigns/{cid}/home')
    toggle = page.locator('.home-bio [data-clamp-toggle]')
    playwright.expect(toggle).to_be_visible()
    box = page.locator('.home-bio .clamp')
    folded = box.bounding_box()['height']
    _shot(page, 'home-readmore-folded')
    toggle.click()
    assert toggle.inner_text() == 'Read less' and toggle.get_attribute('aria-expanded') == 'true'
    assert box.bounding_box()['height'] > folded + 40
    toggle.click()
    assert toggle.inner_text() == 'Read more'
    # side column sits beside the hero, not under everything
    side_top = page.locator('.home-side').bounding_box()['y']
    hero_top = page.locator('.home-hero').bounding_box()['y']
    assert abs(side_top - hero_top) < 4
    assert 'Only the DM sees this' in page.locator('.home-notes').inner_text()
    _shot(page, 'home-desktop')
    page.set_viewport_size({'width': 390, 'height': 780})
    _shot(page, 'home-phone')
    ctx.close()
    assert not errors, errors


def test_short_bio_has_no_read_more(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = _campaign(dm, ['<div>A short tale.</div>'])
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    page.goto(f'{base}/campaigns/{cid}/home')
    playwright.expect(page.locator('.home-bio [data-clamp-toggle]')).to_be_hidden()
    ctx.close()


def test_bio_and_notes_are_edited_with_bold_and_italic_and_characters_attached(shared_server, pw):
    base = shared_server.url
    dm, player = Api(base), Api(base)
    cid = dm.campaign('Editor')
    dm.add_member(cid, player)
    hero = dm.character(cid, 'Kael Thorne', level=3)
    ctx = dm.context(pw, base)
    page = ctx.new_page(); page.set_viewport_size({'width': 1280, 'height': 900})
    errors = []
    page.on('pageerror', lambda x: errors.append(str(x)))
    page.goto(f'{base}/campaigns/{cid}/edit')
    ed = page.locator('#bio-blocks .note-editor')
    ed.click(); ed.type('Ash falls. ')
    page.click('#bio-blocks [data-cmd=bold]'); ed.type('Bold words'); page.click('#bio-blocks [data-cmd=bold]')
    page.click('#bio-blocks [data-cmd=italic]'); ed.type(' slanted'); page.click('#bio-blocks [data-cmd=italic]')
    assert page.locator('#bio-blocks [data-note-remove]').count() == 0         # one box, nothing to remove
    note = page.locator('#campnotes-blocks .note-editor').first
    note.click(); note.type('Remember the toll bridge')
    page.click('#campnotes-add')
    assert page.locator('#campnotes-blocks [data-note-block]').count() == 2
    page.locator('#campnotes-blocks [data-note-hidden]').nth(1).check()
    page.locator('#campnotes-blocks .note-editor').nth(1).type('Secret for the DM')
    page.click('#campaign-form button[type=submit]')
    page.wait_for_load_state()
    page.goto(f'{base}/campaigns/{cid}/home')
    assert page.locator('.home-bio b, .home-bio strong').inner_text() == 'Bold words'
    assert page.locator('.home-bio i, .home-bio em').inner_text().strip() == 'slanted'
    notes_text = page.locator('.home-notes').inner_text()
    assert 'toll bridge' in notes_text and 'Secret for the DM' in notes_text

    # attach the character to the player from the campaign settings
    page.goto(f'{base}/campaigns/{cid}/edit')
    row = page.locator('[data-member-row]', has_text=player.name)
    row.locator('.member-chars summary').click()
    row.locator('input[name=character_ids]').first.check()
    _shot(page, 'settings-attach')
    row.locator('.member-chars button[type=submit]').click()
    page.wait_for_load_state()
    assert q('SELECT player_id FROM characters WHERE id = ?', hero)[0]['player_id'] is not None
    assert player.get(f'/campaigns/{cid}/characters/{hero}/edit').status_code == 200
    page.goto(f'{base}/campaigns/{cid}/home')
    assert 'Kael Thorne' in page.locator('.home-member-chars').inner_text()
    ctx.close()
    assert not errors, errors
