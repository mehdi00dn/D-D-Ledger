"""Real-browser checks (Chromium via Playwright) against a live server process."""
import json, os, re, uuid, time
import pytest
import requests
if os.path.isdir('/opt/pw-browsers'):                       # pre-installed browsers (some sandboxes); CI installs its own
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from conftest import png_bytes, CsrfSession, SESSION_COOKIE, q


@pytest.fixture(scope='module')
def pw():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch(); yield b; b.close()


class Api:
    """requests-based helper hitting the live server, for fast setup."""
    def __init__(self, base):
        self.base = base; self.s = CsrfSession(base)
        self.name = f'e{uuid.uuid4().hex[:9]}'
        r = self.s.post(base + '/register', data={'username': self.name, 'password': 'secret12', 'email': 'player@example.com', 'confirm': 'secret12', 'accept_terms': '1', 'accept_cookies': '1'}, allow_redirects=False)
        assert r.status_code == 302
        import database                                                    # DL-165: these helper accounts start unlocked, like accounts that predate the rule
        db = database.get_db()
        try:
            db.execute('UPDATE users SET email_confirm_required = FALSE WHERE username = ?', (self.name,)); db.commit()
        finally:
            db.close()
    def post(self, path, **kw): return self.s.post(self.base + path, allow_redirects=False, **kw)
    def get(self, path, **kw): return self.s.get(self.base + path, **kw)
    def campaign(self, name='E2E'):
        r = self.post('/campaigns/new', data={'name': name}); return int(re.search(r'/campaigns/(\d+)/', r.headers['Location']).group(1))
    def character(self, cid, name, **f):
        self.post(f'/campaigns/{cid}/characters/new', data={'name': name, 'max_hp': '30', **{k: str(v) for k, v in f.items()}}, **({'files': f.pop('files')} if 'files' in f else {}))
        return next(c['id'] for c in self.get(f'/campaigns/{cid}/api/characters').json() if c['name'] == name)
    def add_member(self, cid, other, status='player'):
        """Invite `other` (another Api) and have them accept -- invitations are no longer instant joins."""
        r = self.post(f'/campaigns/{cid}/members/add', data={'username': other.name, 'status': status}); assert r.status_code == 302
        inv = q("SELECT i.id FROM campaign_invitations i JOIN users u ON u.id = i.invited_user_id "
                "WHERE i.campaign_id = ? AND u.username = ? AND i.state = 'pending'", cid, other.name)[0]['id']
        r = other.post(f'/invitations/{inv}/accept'); assert r.status_code == 302
        return r
    def context(self, browser, base):
        ctx = browser.new_context(viewport={'width': 1400, 'height': 900})
        ctx.add_cookies([{'name': SESSION_COOKIE, 'value': self.s.cookies.get(SESSION_COOKIE), 'url': base}])
        return ctx


def wait_text(page, needles, timeout=12.0):
    """Poll the page text from Python (page-side eval is blocked by the app's CSP, as it should be)."""
    import time
    end = time.time() + timeout
    while time.time() < end:
        text = page.inner_text('body')
        if all(n in text for n in needles):
            return
        page.wait_for_timeout(300)
    raise AssertionError(f'{needles} never appeared; page said: {text[:300]!r}')


def _collect(page):
    bad, errs = [], []
    page.on('response', lambda r: bad.append((r.status, r.url)) if r.status >= 400 else None)
    page.on('pageerror', lambda e: errs.append(str(e)))
    return bad, errs


def _confirm_delete(page, form_selector):
    with page.expect_navigation() as nav:
        page.locator(form_selector + ' button').first.evaluate('e => e.click()')
        page.wait_for_selector('#confirm-modal:not([hidden])')
        page.click('#confirm-modal-yes')
    return nav.value


def test_ui_delete_flows_never_land_on_a_404(pw, shared_server):
    base = shared_server.url; api = Api(base); cid = api.campaign('Del')
    api.post(f'/campaigns/{cid}/factions/new', data={'name': 'G1', 'color': '#123456'})
    api.character(cid, 'Doomed')
    api.post(f'/campaigns/{cid}/maps/new', data={'name': 'M1', 'blank_width': '300', 'blank_height': '300'})
    ctx = api.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    for path, sel in ((f'/campaigns/{cid}/characters', 'form[action*="/characters/"][action$="/delete"]'),
                      (f'/campaigns/{cid}/factions', 'form[action*="/factions/"][action$="/delete"]'),
                      (f'/campaigns/{cid}/maps?browse=1', 'form[action*="/maps/"][action$="/delete"]')):
        page.goto(base + path)
        resp = _confirm_delete(page, sel)
        landed = page.request.get(page.url)
        assert landed.status == 200, f'{path}: after delete landed on {page.url} -> {landed.status}'
    page.goto(base + '/campaigns')
    resp = _confirm_delete(page, f'form[action$="/campaigns/{cid}/delete"]')
    assert page.request.get(page.url).status == 200 and page.url.rstrip('/').endswith('/campaigns')
    assert not [b for b in bad if b[0] >= 400 and '/delete' not in b[1]], bad
    ctx.close()


def test_images_render_after_reload(pw, shared_server):
    base = shared_server.url; api = Api(base); cid = api.campaign('Img')
    api.post(f'/campaigns/{cid}/characters/new', data={'name': 'Pic', 'max_hp': '5'}, files={'avatar': ('a.png', png_bytes(200, 200), 'image/png')})
    api.post(f'/campaigns/{cid}/characters/new', data={'name': 'Pic2', 'max_hp': '5'}, files={'avatar': ('b.png', png_bytes(120, 90, (9, 9, 200)), 'image/png')})
    ctx = api.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    for _ in range(3):                                   # several reloads: results must not vary
        page.goto(base + f'/campaigns/{cid}/characters'); page.wait_for_load_state('networkidle')
        imgs = page.eval_on_selector_all('img[src*="/uploads/"]', 'els => els.map(e => [e.src, e.naturalWidth])')
        assert len(imgs) >= 2 and all(w > 0 for _, w in imgs), imgs
        assert 'Pic' in page.content() and 'Pic2' in page.content()
    ctx.close()


def test_two_users_see_each_others_changes_without_reload(pw, shared_server):
    base = shared_server.url
    dm, pl = Api(base), Api(base); cid = dm.campaign('Table')
    dm.add_member(cid, pl)
    hero = dm.character(cid, 'Hero'); ogre = dm.character(cid, 'Ogre', is_npc='on', max_hp=59)
    dctx, pctx = dm.context(pw, base), pl.context(pw, base)
    dpage, ppage = dctx.new_page(), pctx.new_page()
    dbad, _ = _collect(dpage); pbad, _ = _collect(ppage)
    dpage.goto(base + f'/campaigns/{cid}/battle'); ppage.goto(base + f'/campaigns/{cid}/battle')
    dm.s.post(f'{base}/campaigns/{cid}/api/battle/add', json={'character_id': hero})
    dm.s.post(f'{base}/campaigns/{cid}/api/battle/add', json={'character_id': ogre})
    wait_text(ppage, ['Hero', 'Ogre']); wait_text(dpage, ['Hero'])
    body = ppage.inner_text('#battle-root')
    assert '59' not in body                     # the player never sees the ogre's HP
    assert not [b for b in dbad + pbad if b[0] >= 400], (dbad, pbad)
    dctx.close(); pctx.close()


def test_invite_autocomplete_and_inline_feedback(pw, shared_server):
    base = shared_server.url; dm = Api(base); player = Api(base)
    cid = dm.campaign('Invite')
    ctx = dm.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/edit'); page.wait_for_load_state('networkidle')

    # Typing a prefix of a real username surfaces it in the dropdown; clicking it fills the box.
    page.fill('#member-username-input', player.name[:len(player.name) - 2])
    page.wait_for_selector('#member-search-results li[role="option"]')
    assert player.name in page.inner_text('#member-search-results')
    page.click(f'#member-search-results >> text="{player.name}"')
    assert page.input_value('#member-username-input') == player.name
    assert page.is_hidden('#member-search-results')

    # Submitting sends an invitation inline: green feedback, a "pending" row, no navigation away from /edit.
    page.click('#member-add-form button[type=submit]')
    page.wait_for_selector('#member-add-feedback.is-success')
    assert player.name in page.inner_text('#member-add-feedback')
    page.wait_for_selector(f'[data-pending-row]:has-text("{player.name}")')
    assert page.locator('#member-list [data-member-row]').count() == 1           # not a member until they accept
    assert '/edit' in page.url                                  # never a full-page reload/redirect

    # The dynamically-inserted row's own form (cancel invitation) must carry a real CSRF token too --
    # it's built in JS, so the template-source scan can't check this one.
    real_token = page.get_attribute('#member-add-form input[name=csrf_token]', 'value')
    new_row = page.locator('[data-pending-row]', has_text=player.name)
    tokens = new_row.locator('input[name=csrf_token]').all()
    assert len(tokens) == 1, 'expected a token on the cancel-invitation form'
    assert all(t.get_attribute('value') == real_token for t in tokens)

    # Inviting an unknown username shows an inline error instead -- still no navigation.
    page.fill('#member-username-input', 'no-such-user-ghost')
    page.click('#member-add-form button[type=submit]')
    page.wait_for_selector('#member-add-feedback.is-error')
    assert 'No user found' in page.inner_text('#member-add-feedback')
    assert '/edit' in page.url

    assert not errs, errs
    assert not [b for b in bad if b[0] >= 400 and b[0] != 404], bad   # the 404 above is the point of the test
    ctx.close()


def test_drawing_persists_after_reload(pw, shared_server):
    base = shared_server.url; api = Api(base); cid = api.campaign('Map')
    r = api.post(f'/campaigns/{cid}/maps/new', data={'name': 'Draw', 'blank_width': '800', 'blank_height': '600'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    ctx = api.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle'); page.wait_for_timeout(800)
    if page.locator('#setup-confirm-btn').is_visible():
        page.click('#setup-confirm-btn'); page.wait_for_timeout(600)
    page.click('#shapes-flyout [data-flyout-trigger]'); page.click('[data-tool=rect]')
    box = page.locator('#map-canvas').bounding_box()
    page.mouse.move(box['x'] + 150, box['y'] + 150); page.mouse.down(); page.mouse.move(box['x'] + 320, box['y'] + 280, steps=8); page.mouse.up()
    page.wait_for_timeout(800)
    assert 'active' in page.locator('[data-tool=select]').get_attribute('class')                    # drawing is over: back on the Select tool ...
    assert 'active' not in (page.locator('#shapes-flyout [data-flyout-trigger]').get_attribute('class') or '')
    playwright.expect(page.locator('#shape-action-bubble')).to_be_visible()                          # ... with the new shape selected
    n1 = len(api.get(f'/campaigns/{cid}/api/maps/{mid}/drawings').json())
    page.reload(); page.wait_for_load_state('networkidle'); page.wait_for_timeout(600)
    n2 = len(api.get(f'/campaigns/{cid}/api/maps/{mid}/drawings').json())
    assert n1 == n2 == 1
    assert not errs, errs
    assert not [b for b in bad if b[0] >= 400], bad
    ctx.close()


def test_fog_flyout_paints_fog_over_tokens_and_animates(pw, shared_server):
    base = shared_server.url; api = Api(base); cid = api.campaign('Fog')
    r = api.post(f'/campaigns/{cid}/maps/new', data={'name': 'Mist', 'blank_width': '800', 'blank_height': '600'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    ctx = api.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle'); page.wait_for_timeout(800)
    if page.locator('#setup-confirm-btn').is_visible():
        page.click('#setup-confirm-btn'); page.wait_for_timeout(600)
    # The fog tool is one flyout button holding the add and remove icons (no extra toolbar buttons).
    assert page.locator('#fog-flyout [data-tool]').count() == 2
    page.click('#fog-flyout [data-flyout-trigger]')
    page.click('#fog-flyout [data-tool=fog-add]')
    box = page.locator('#map-canvas').bounding_box()
    page.mouse.move(box['x'] + 100, box['y'] + 100); page.mouse.down()
    page.mouse.move(box['x'] + 500, box['y'] + 300, steps=12); page.mouse.up()
    page.wait_for_selector('#map-fog-layer:not([hidden])')
    z = page.locator('#map-fog-layer').evaluate('e => getComputedStyle(e).zIndex')
    assert z == '5', z                                       # above the tokens
    page.wait_for_timeout(2500)                              # lazy-loaded smoke gets drawn
    drawn = page.locator('canvas.map-fog-smoke').evaluate(
        'c => { const d = c.getContext("2d").getImageData(0,0,c.width,c.height).data; let n=0; for (let i=3;i<d.length;i+=4) if (d[i]) n++; return n; }')
    fallback = page.locator('#map-fog-layer').evaluate('e => e.classList.contains("smoke-fallback")')
    assert drawn > 0 and not fallback, (drawn, fallback)
    page.click('#fog-flyout [data-flyout-trigger]')
    page.click('#fog-flyout [data-tool=fog-erase]')
    assert not errs, errs
    assert not [b for b in bad if b[0] >= 400], bad
    page.screenshot(path='/tmp/fog-dm.png')
    ctx.close()


def test_lightbox_arrows_sit_beside_the_image(pw, shared_server):
    base = shared_server.url; api = Api(base); cid = api.campaign('Light')
    files = [('sheets', (f's{i}.png', png_bytes(600, 300, (40 * i, 80, 120)), 'image/png')) for i in range(3)]
    api.post(f'/campaigns/{cid}/characters/new', data={'name': 'Gallery', 'max_hp': '10'}, files=files)
    char_id = next(c['id'] for c in api.get(f'/campaigns/{cid}/api/characters').json() if c['name'] == 'Gallery')
    ctx = api.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/characters/{char_id}'); page.wait_for_load_state('networkidle')
    page.locator('.sheet-thumbs img[data-sheet-src]').nth(1).click()           # the thumbnail picks the sheet in the viewer ...
    page.click('#sheet-viewer-img')                                            # ... and the viewer opens the lightbox on it
    page.wait_for_selector('#image-lightbox:not([hidden])'); page.wait_for_timeout(400)
    img, prev, nxt = (page.locator(s).bounding_box() for s in ('#lightbox-img', '#lightbox-prev-btn', '#lightbox-next-btn'))
    # Each arrow hugs its side of the picture (a small gap), not the edge of the screen.
    assert 0 < img['x'] - (prev['x'] + prev['width']) < 40, (img, prev)
    assert 0 < nxt['x'] - (img['x'] + img['width']) < 40, (img, nxt)
    mid = img['y'] + img['height'] / 2
    assert abs(prev['y'] + prev['height'] / 2 - mid) < 4 and abs(nxt['y'] + nxt['height'] / 2 - mid) < 4
    page.screenshot(path='/tmp/lightbox.png')
    # The mouse wheel zooms the picture in and out, centred on the cursor, and returns to normal size.
    def zoom_level():
        return page.locator('#lightbox-img').evaluate('e => { const m = getComputedStyle(e).transform; return m === "none" ? 1 : parseFloat(m.slice(7)); }')
    assert zoom_level() == 1
    page.mouse.move(img['x'] + img['width'] / 2, img['y'] + img['height'] / 2)
    page.mouse.wheel(0, -400); page.wait_for_timeout(500)
    zin = zoom_level(); assert zin > 1.5, zin
    page.mouse.wheel(0, -400); page.wait_for_timeout(500)
    assert zoom_level() > zin
    page.mouse.wheel(0, 3000); page.wait_for_timeout(500)
    assert zoom_level() == 1
    # First image: previous arrow is invisible but still takes its space, so the picture does not jump.
    page.click('#lightbox-prev-btn'); page.wait_for_timeout(300)
    img0 = page.locator('#lightbox-img').bounding_box()
    assert abs(img0['x'] - img['x']) < 2, (img0, img, page.locator('#lightbox-prev-btn').bounding_box())
    assert not page.locator('#lightbox-prev-btn').is_visible() and page.locator('#lightbox-next-btn').is_visible()
    page.screenshot(path='/tmp/lightbox-first.png')
    assert not errs, errs
    ctx.close()


def test_next_turn_button_stays_put_when_the_current_name_changes(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Steady')
    for name, init in (('Al', 18), ('Bartholomew the Very Long-Named', 9)):
        chid = dm.character(cid, name)
        pid = dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': chid}).json()[-1]['id']
        dm.post(f'/campaigns/{cid}/api/battle/{pid}/initiative', json={'value': init})
    ctx = dm.context(pw, base); page = ctx.new_page(); page.set_viewport_size({'width': 1280, 'height': 800})
    page.goto(base + f'/campaigns/{cid}/battle'); page.wait_for_selector('#turn-bar:not([hidden])')
    box = lambda: page.locator('#next-turn-btn').bounding_box()
    page.click('#next-turn-btn'); page.wait_for_selector('#turn-now:not([hidden])')
    first = box()
    page.click('#next-turn-btn'); page.wait_for_timeout(600)
    assert 'Bartholomew' in page.locator('#turn-now').inner_text()
    second = box()
    assert abs(first['x'] - second['x']) < 0.5 and abs(first['y'] - second['y']) < 0.5     # the name sits after the buttons
    assert page.locator('#turn-now').bounding_box()['x'] > second['x'] + second['width']


def test_turn_tracker_highlights_the_active_turn_for_everyone(pw, shared_server):
    base = shared_server.url; dm = Api(base); player = Api(base); cid = dm.campaign('Turns')
    dm.add_member(cid, player)
    for name, init in (('Aria', 18), ('Borin', 9)):
        chid = dm.character(cid, name)
        pid = dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': chid}).json()[-1]['id']
        dm.post(f'/campaigns/{cid}/api/battle/{pid}/initiative', json={'value': init})
    dctx = dm.context(pw, base); dpage = dctx.new_page(); dbad, derrs = _collect(dpage)
    pctx = player.context(pw, base); ppage = pctx.new_page(); pbad, perrs = _collect(ppage)
    dpage.goto(base + f'/campaigns/{cid}/battle'); ppage.goto(base + f'/campaigns/{cid}/battle')
    dpage.wait_for_selector('#turn-bar:not([hidden])'); ppage.wait_for_selector('#turn-bar:not([hidden])')
    assert dpage.locator('#next-turn-label').inner_text() == 'Start combat'
    assert ppage.locator('#next-turn-btn').count() == 0                   # Players get no button
    dpage.click('#next-turn-btn')
    dpage.wait_for_selector('.battle-row.is-active')
    assert 'Aria' in dpage.locator('.battle-row.is-active .battle-name').inner_text()
    assert dpage.locator('#next-turn-label').inner_text() == 'Next turn'
    ppage.wait_for_selector('.battle-row.is-active', timeout=15000)       # arrives by the normal live refresh
    assert 'Aria' in ppage.locator('.battle-row.is-active .battle-name').inner_text()
    dpage.click('#next-turn-btn'); dpage.click('#next-turn-btn')          # Borin, then a new round with Aria
    dpage.wait_for_timeout(600)
    assert dpage.locator('#round-num').inner_text() == '2'
    assert 'Aria' in dpage.locator('.battle-row.is-active .battle-name').inner_text()
    dpage.click('#prev-turn-btn'); dpage.wait_for_timeout(600)                # Back: Borin, round 1
    assert dpage.locator('#round-num').inner_text() == '1'
    assert 'Borin' in dpage.locator('.battle-row.is-active .battle-name').inner_text()
    dpage.screenshot(path='/tmp/turns.png')
    dpage.click('#restart-turns-btn'); dpage.wait_for_selector('#confirm-modal:not([hidden])'); dpage.click('#confirm-modal-yes')
    dpage.wait_for_timeout(600)
    assert 'Aria' in dpage.locator('.battle-row.is-active .battle-name').inner_text()
    assert dpage.locator('#end-turns-btn').count() == 0                      # there is no separate "end" button
    assert not derrs and not perrs, (derrs, perrs)
    dctx.close(); pctx.close()


def test_battle_conditions_picker_and_chips(pw, shared_server):
    base = shared_server.url; dm = Api(base); player = Api(base); cid = dm.campaign('Conds')
    dm.add_member(cid, player)
    chid = dm.character(cid, 'Aria')
    dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': chid})
    dctx = dm.context(pw, base); dpage = dctx.new_page(); dbad, derrs = _collect(dpage)
    pctx = player.context(pw, base); ppage = pctx.new_page(); pbad, perrs = _collect(ppage)
    dpage.goto(base + f'/campaigns/{cid}/battle'); ppage.goto(base + f'/campaigns/{cid}/battle')
    dpage.wait_for_selector('.battle-row'); ppage.wait_for_selector('.battle-row')
    assert ppage.locator('.cond-add').count() == 0                         # Players get no picker
    dpage.click('.cond-add'); dpage.wait_for_selector('#conditions-modal:not([hidden])')
    dpage.click('[data-cond="prone"]'); dpage.click('[data-cond="poisoned"]')
    dpage.wait_for_timeout(500)
    assert dpage.locator('#conditions-picker .cond-tile.active').count() == 2
    assert dpage.locator('#conditions-picker [data-cond] svg.cond-icon').count() == 15     # every condition has its icon tile
    dpage.screenshot(path='/tmp/conds-modal.png')
    dpage.evaluate("() => document.documentElement.setAttribute('data-theme', 'light')")
    dpage.wait_for_timeout(300); dpage.screenshot(path='/tmp/conds-modal-light.png')
    dpage.evaluate("() => document.documentElement.setAttribute('data-theme', 'dark')")
    dpage.click('#close-conditions-modal')
    assert dpage.locator('.battle-row .cond-chip').all_text_contents() == ['Poisoned', 'Prone']
    ppage.wait_for_selector('.battle-row .cond-chip', timeout=15000)       # arrives by the normal live refresh
    assert ppage.locator('.battle-row .cond-chip').all_text_contents() == ['Poisoned', 'Prone']
    dpage.screenshot(path='/tmp/conds-row.png')
    dpage.goto(base + f'/campaigns/{cid}/factions'); dpage.wait_for_timeout(500)
    dpage.screenshot(path='/tmp/factions.png')
    assert not derrs and not perrs, (derrs, perrs)
    dctx.close(); pctx.close()


def test_view_as_player_banner_and_hidden_dm_tools(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Preview')
    ogre = dm.character(cid, 'Ogre')
    dm.post(f'/campaigns/{cid}/characters/{ogre}/edit', data={'name': 'Ogre', 'is_npc': 'on', 'level': '1', 'max_hp': '59', 'armor_class': '11'})
    dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': ogre})
    dctx = dm.context(pw, base); dpage = dctx.new_page(); dbad, derrs = _collect(dpage)
    dpage.goto(base + f'/campaigns/{cid}/battle'); dpage.wait_for_selector('.battle-row')
    assert dpage.locator('#next-turn-btn').count() == 1 and dpage.locator('.preview-banner').count() == 0
    dpage.click('.nav-tab-button')                                          # sidebar: View as player
    dpage.wait_for_selector('.preview-banner'); dpage.wait_for_selector('.battle-row')
    assert dpage.url.endswith('/battle')                                    # stays on the page it was on
    assert dpage.locator('#next-turn-btn').count() == 0 and dpage.locator('#clear-battle-btn').count() == 0
    dpage.screenshot(path='/tmp/preview.png')
    dpage.click('.preview-banner button')                                   # Exit player view
    dpage.wait_for_selector('#next-turn-btn')
    assert dpage.locator('.preview-banner').count() == 0
    assert not derrs, derrs
    dctx.close()


def test_new_faction_form_can_add_members(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('NewFaction')
    dm.character(cid, 'Aria'); dm.character(cid, 'Borin')
    dctx = dm.context(pw, base); page = dctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/factions/new'); page.wait_for_selector('#member-search')
    page.fill('#name', 'Ashfall Company')
    page.fill('#member-search', 'ar'); page.wait_for_selector('.member-result')
    page.click('.member-result button')                                     # Add Aria
    page.wait_for_selector('.member-chip')
    page.screenshot(path='/tmp/newfaction.png', full_page=True)
    page.click('.form-actions button[type=submit]'); page.wait_for_timeout(1500)
    assert page.url.rstrip('/').endswith('/factions'), page.url
    assert 'Ashfall Company' in page.inner_text('body')
    aria = next(c for c in dm.get(f'/campaigns/{cid}/api/characters').json() if c['name'] == 'Aria')
    assert aria['group_name'] == 'Ashfall Company'                          # Aria joined the faction while it was created
    assert not errs, errs
    dctx.close()


def test_class_dropdown_hover_guide_select_clear_and_icons(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Classes')
    dctx = dm.context(pw, base); page = dctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/characters/new'); page.wait_for_selector('#class-trigger')
    assert page.locator('#class-menu').is_hidden() and page.inner_text('#class-trigger-text') == 'No class'
    page.click('#class-trigger'); page.wait_for_selector('#class-menu:not([hidden])')
    assert page.locator('.class-option').count() == 14                       # "No class" + the 13 classes
    page.hover('.class-option[data-value=wizard]'); page.wait_for_selector('.class-tip:not([hidden])')
    assert 'scholar who masters arcane magic' in page.inner_text('.class-tip') and 'Wizard' in page.inner_text('.class-tip')
    page.screenshot(path='/tmp/class-hover.png')
    page.fill('#name', 'Merlin')
    page.click('.class-option[data-value=wizard]')
    assert page.locator('#class-menu').is_hidden() and page.input_value('#class_key') == 'wizard'
    assert page.inner_text('#class-trigger-text') == 'Wizard' and page.locator('#class-trigger-icon svg').count() == 1
    page.click('#class-trigger'); page.click('.class-option-none')           # back to no class
    assert page.input_value('#class_key') == '' and page.inner_text('#class-trigger-text') == 'No class'
    page.mouse.move(5, 5)                                                      # a resting pointer over the menu would hover-select over the keys
    page.focus('#class-trigger'); page.keyboard.press('ArrowDown')            # keyboard: open, move, choose
    page.keyboard.press('ArrowDown'); page.keyboard.press('ArrowDown'); page.keyboard.press('ArrowDown'); page.keyboard.press('Enter')
    assert page.input_value('#class_key') == 'bard'
    page.click('#class-trigger')
    page.evaluate("() => document.documentElement.setAttribute('data-theme', 'light')")
    page.screenshot(path='/tmp/class-picker-light.png')
    page.evaluate("() => document.documentElement.setAttribute('data-theme', 'dark')")
    page.keyboard.press('Escape')
    page.screenshot(path='/tmp/class-picker.png')
    page.click('.cf-bar button[type=submit]'); page.wait_for_timeout(1200)
    page.goto(base + f'/campaigns/{cid}/characters'); page.wait_for_selector('.dossier-card [title=Bard]')
    assert page.locator('.dossier-card [title=Bard]').first.get_attribute('title') == 'Bard'
    chid = next(c for c in dm.get(f'/campaigns/{cid}/api/characters').json() if c['name'] == 'Merlin')['id']
    dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': chid})
    page.goto(base + f'/campaigns/{cid}/battle'); page.wait_for_selector('.battle-name .class-icon')
    page.screenshot(path='/tmp/class-battle.png')
    assert not errs, errs
    dctx.close()


def test_note_and_bio_previews_wrap_like_the_text(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Wrap')
    long_word = 'W' * 90
    dm.character(cid, 'Scribe', notes=json.dumps([f'<div>Alpha beta</div><div>Gamma {long_word}</div><div>Delta</div>']))
    dm.post(f'/campaigns/{cid}/factions/new', data={'name': 'Order', 'color': '#336699', 'bio': f'First line\nSecond line\nThird line {long_word}\nFourth line'})
    dctx = dm.context(pw, base); page = dctx.new_page(); bad, errs = _collect(page)
    for width in (1400, 480):
        page.set_viewport_size({'width': width, 'height': 900})
        page.goto(base + f'/campaigns/{cid}/characters'); page.wait_for_selector('.dossier-notes')
        box = page.eval_on_selector('.dossier-notes', "e => ({ws: getComputedStyle(e).whiteSpace, sw: e.scrollWidth, cw: e.clientWidth, h: e.clientHeight, lh: parseFloat(getComputedStyle(e).lineHeight)})")
        assert box['ws'] == 'pre-line' and box['sw'] <= box['cw'] + 1, box          # nothing pushed past the card
        assert box['h'] <= box['lh'] * 2 + 2, box                                       # still clamped to two lines
        page.goto(base + f'/campaigns/{cid}/factions'); page.wait_for_selector('.group-bio')
        box = page.eval_on_selector('.group-bio', "e => ({ws: getComputedStyle(e).whiteSpace, sw: e.scrollWidth, cw: e.clientWidth, h: e.clientHeight, lh: parseFloat(getComputedStyle(e).lineHeight)})")
        assert box['ws'] == 'pre-line' and box['sw'] <= box['cw'] + 1, box
        assert box['lh'] * 2.5 < box['h'] <= box['lh'] * 3 + 2, box                     # separate lines, clamped to three
    page.screenshot(path='/tmp/wrap-factions.png')
    assert not errs, errs
    dctx.close()


def test_invitation_flow_in_the_browser(pw, shared_server):
    base = shared_server.url; dm, pl = Api(base), Api(base); cid = dm.campaign('Invites')
    dctx = dm.context(pw, base); dpage = dctx.new_page(); dbad, derrs = _collect(dpage)
    dpage.goto(base + f'/campaigns/{cid}/edit'); dpage.wait_for_selector('#member-username-input')
    dpage.fill('#member-username-input', pl.name); dpage.select_option('#member-add-form select[name=status]', 'dm')
    dpage.wait_for_selector('#member-search-results li[role=option]'); dpage.press('#member-username-input', 'Escape')
    dpage.click('#member-add-form button[type=submit]')
    dpage.wait_for_selector('#pending-invites:not([hidden]) [data-pending-row]')
    assert pl.name in dpage.inner_text('#pending-invite-list') and 'Waiting for a reply' in dpage.inner_text('#pending-invite-list')
    assert dpage.locator('#member-list [data-member-row]').count() == 1                      # still only the owner
    assert 'Invitation sent' in dpage.inner_text('#member-add-feedback')
    dpage.screenshot(path='/tmp/invite-owner.png')

    pctx = pl.context(pw, base); ppage = pctx.new_page(); pbad, perrs = _collect(ppage)
    ppage.goto(base + '/campaigns'); ppage.wait_for_selector('.nav-badge')
    assert ppage.inner_text('.nav-badge') == '1'
    assert 'Campaigns' in ppage.inner_text('.sidebar-nav') and ppage.locator('.foot-bell:not(.foot-privacy)').count() == 0     # outside a campaign: Campaigns + Notifications in the menu
    ppage.click('.nav-notifications'); ppage.wait_for_selector('.notification-card.is-unread'); ppage.wait_for_timeout(700)
    assert 'invited you to Invites' in ppage.inner_text('.notification-card')
    ppage.screenshot(path='/tmp/invite-player.png')
    ppage.evaluate("() => document.documentElement.setAttribute('data-theme', 'light')")
    ppage.wait_for_timeout(400); ppage.screenshot(path='/tmp/invite-player-light.png')
    ppage.click('.notification-card button:has-text("Accept")'); ppage.wait_for_url(f'**/campaigns/{cid}/**')
    assert ppage.locator('.nav-badge').count() == 0
    # inside a campaign the bell is an icon-only button beside the theme switch, not a menu entry
    assert ppage.locator('.sidebar-nav .nav-notifications').count() == 0
    assert ppage.locator('.foot-actions #theme-toggle').count() == 1 and ppage.locator('.foot-actions .foot-bell').count() == 1
    assert ppage.inner_text('.foot-bell').strip() == ''

    dpage.goto(base + f'/campaigns/{cid}/edit'); dpage.wait_for_selector('#member-list')
    assert dpage.inner_text('.foot-bell .nav-badge') == '1'                                      # the owner learns the invitation was accepted
    dpage.wait_for_timeout(600); dpage.screenshot(path='/tmp/foot-bell.png', clip={'x': 0, 'y': 650, 'width': 240, 'height': 250})
    dpage.evaluate("() => document.documentElement.setAttribute('data-sidebar', 'collapsed')"); dpage.wait_for_timeout(500)
    dpage.screenshot(path='/tmp/foot-bell-collapsed.png', clip={'x': 0, 'y': 600, 'width': 140, 'height': 300})
    dpage.evaluate("() => document.documentElement.removeAttribute('data-sidebar')")
    assert dpage.locator('#member-list [data-member-row]').count() == 2 and dpage.locator('#pending-invites').is_hidden()
    assert not derrs and not perrs, (derrs, perrs)
    dctx.close(); pctx.close()


def test_invite_link_options_appear_live_when_the_access_mode_changes(pw, shared_server):
    base = shared_server.url; dm = Api(base)
    cid = dm.campaign('Links')
    ctx = dm.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/edit'); page.wait_for_load_state('networkidle')
    assert page.is_hidden('#invite-links')                                      # private: nothing to show

    page.click('label.access-option:has(input[value=invite_link])')             # no save, no reload
    assert page.is_visible('#invite-links') and page.is_visible('#invite-links-unsaved')
    page.click('label.access-option:has(input[value=private])')
    assert page.is_hidden('#invite-links')
    page.click('label.access-option:has(input[value=invite_link])')
    page.locator('#invite-links').screenshot(path='/tmp/invite_links_unsaved.png')

    # Create a link straight away: no page navigation, the link appears in place, and the mode is now saved.
    marker = page.evaluate("window.__marker = 'same-page'")
    page.fill('#invite-link-form input[name=max_uses]', '3')
    page.click('#invite-link-form button[type=submit]'); page.wait_for_selector('#invite-link-new:not([hidden])')
    assert '/join/' in page.input_value('#invite-link-url')
    assert page.evaluate('window.__marker') == 'same-page'                      # the page was never reloaded
    assert page.is_hidden('#invite-links-unsaved')
    page.wait_for_selector('[data-invite-link-row]:has-text("0 / 3 used")')
    assert q_mode(cid) == 'invite_link'
    page.wait_for_timeout(600)
    page.locator('#invite-links').screenshot(path='/tmp/invite_links_saved.png')

    # a second link stacks on top, and the revoke button on the freshly-added row works
    page.click('#invite-link-form button[type=submit]'); page.locator('[data-invite-link-row]').nth(1).wait_for()
    page.click('[data-invite-link-row] >> nth=0 >> button[title="Revoke link"]'); page.wait_for_load_state('networkidle')
    assert not errs and not bad
    ctx.close()


def q_mode(cid):
    return q('SELECT access_mode FROM campaigns WHERE id = ?', cid)[0]['access_mode']


def test_hostile_names_never_execute_in_battle_or_map_screens(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Xss')
    gname = '<img src=x onerror=alert("group")>'
    r = dm.post(f'/campaigns/{cid}/factions/new', data={'name': gname, 'color': '#336699'}, files={'x': ('', b'')}); assert r.status_code == 302
    gid = q('SELECT id FROM groups WHERE campaign_id = ?', cid)[0]['id']
    cname = 'a" onmouseover="alert(\'attr\')" x="'
    chid = dm.character(cid, cname, group_id=gid)
    pid = dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': chid}).json()[-1]['id']
    ctx = dm.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    dialogs = []
    page.on('dialog', lambda d: (dialogs.append(d.message), d.dismiss()))
    page.goto(base + f'/campaigns/{cid}/battle'); page.wait_for_selector('.battle-row')
    page.hover('.cond-add'); page.wait_for_timeout(300)                      # would fire the injected onmouseover
    page.click('.battle-name[data-action="view"]'); page.wait_for_selector('.peek-chips')
    page.wait_for_timeout(500)
    assert 'onerror' in page.inner_text('.peek-chips')                        # the faction name is shown as literal text
    assert page.locator('.peek-chips img').count() == 0 and page.locator('#detail-modal-title img').count() == 0
    assert cname in page.inner_text('#detail-modal-title')
    assert dialogs == [], dialogs
    ctx.close()


def test_custom_conditions_are_typed_in_place_and_capped_at_three(pw, shared_server):
    base = shared_server.url; dm = Api(base); player = Api(base); cid = dm.campaign('Custom')
    dm.add_member(cid, player)
    dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': dm.character(cid, 'Aria')})
    dctx = dm.context(pw, base); dpage = dctx.new_page(); dbad, derrs = _collect(dpage)
    pctx = player.context(pw, base); ppage = pctx.new_page()
    dialogs = []; dpage.on('dialog', lambda d: (dialogs.append(d.message), d.dismiss()))
    dpage.goto(base + f'/campaigns/{cid}/battle'); ppage.goto(base + f'/campaigns/{cid}/battle')
    dpage.wait_for_selector('.battle-row'); ppage.wait_for_selector('.battle-row')
    dpage.click('.cond-add'); dpage.wait_for_selector('#conditions-modal:not([hidden])')
    assert dpage.locator('[data-custom-new]').count() == 1

    for name in ('Hexed', '<b onclick=alert(1)>x', 'On fire'):
        dpage.click('[data-custom-new]')
        dpage.fill('.cond-custom-input', name); dpage.keyboard.press('Enter')
        dpage.wait_for_selector(f'[data-custom-index]:has-text("{name.replace(chr(34), "")}")')
    assert dpage.locator('[data-custom-index]').count() == 3
    assert dpage.locator('[data-custom-new]').count() == 0                                  # the limit is 3: no more "+" tile
    assert 'limit reached' in dpage.inner_text('#custom-cond-hint')
    dpage.screenshot(path='/tmp/conds-custom-full.png')

    dpage.click('[data-custom-index="0"]')                                                  # click a custom one to remove it
    dpage.locator('[data-custom-new]').wait_for()
    assert dpage.locator('[data-custom-index]').count() == 2

    dpage.click('[data-custom-new]'); dpage.fill('.cond-custom-input', 'Marked')              # the Add button commits too
    dpage.click('[data-custom-add]')
    dpage.wait_for_selector('[data-custom-index]:has-text("Marked")')
    assert dpage.locator('[data-custom-index]').count() == 3
    assert dpage.locator('[data-custom-index] svg').count() == 0 and dpage.locator('[data-custom-new]').count() == 0   # no icon on customs
    # the custom tiles sit in the same grid as the standard ones (no separate section)
    assert dpage.locator('#conditions-picker .cond-section-label').count() == 0
    dpage.click('[data-custom-index="2"]'); dpage.locator('[data-custom-new]').wait_for()
    assert dpage.locator('[data-custom-new] svg').count() == 1                                   # the "+" stays on the add tile
    dpage.click('[data-custom-new]'); dpage.fill('.cond-custom-input', 'x'); dpage.keyboard.press('Escape')   # Escape cancels
    dpage.locator('[data-custom-new]').wait_for()
    assert dpage.locator('[data-custom-index]').count() == 2

    dpage.click('#close-conditions-modal')
    assert sorted(dpage.locator('.battle-row .cond-chip.cond-custom').all_text_contents()) == ['<b onclick=alert(1)>x', 'On fire']
    ppage.wait_for_selector('.battle-row .cond-chip.cond-custom', timeout=15000)           # the party sees it on a PC row
    ppage.wait_for_function("() => document.querySelectorAll('.battle-row .cond-chip.cond-custom').length === 2", timeout=15000)   # the live update can lag one beat behind the first chip
    assert ppage.locator('.battle-row .cond-chip.cond-custom').count() == 2
    assert dpage.locator('.battle-row .cond-chip.cond-custom svg').count() == 0 and dpage.locator('.battle-row .cond-chip.cond-custom b').count() == 0 and dialogs == []   # markup stays text
    assert not derrs


def test_condition_chip_x_removes_it_on_hover(pw, shared_server):
    base = shared_server.url; dm = Api(base); player = Api(base); cid = dm.campaign('ChipX')
    dm.add_member(cid, player)
    pid = dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': dm.character(cid, 'Aria')}).json()[0]['id']
    dm.post(f'/campaigns/{cid}/api/battle/{pid}/conditions', json={'conditions': ['prone', 'poisoned'], 'custom_conditions': ['Hexed']})
    dctx = dm.context(pw, base); dpage = dctx.new_page(); dbad, derrs = _collect(dpage)
    pctx = player.context(pw, base); ppage = pctx.new_page()
    dpage.goto(base + f'/campaigns/{cid}/battle'); ppage.goto(base + f'/campaigns/{cid}/battle')
    dpage.wait_for_selector('.cond-chip'); ppage.wait_for_selector('.cond-chip')
    assert ppage.locator('.cond-x').count() == 0                                     # Players never get the x
    chip = dpage.locator('.cond-chip.cond-prone')
    x = chip.locator('.cond-x')
    opacity = lambda: float(x.evaluate("(el) => getComputedStyle(el).opacity"))
    before = chip.bounding_box(); row_before = dpage.locator('.cond-row').bounding_box()
    assert opacity() == 0                                                            # invisible until hover
    chip.hover(); dpage.wait_for_timeout(400)
    assert opacity() == 1
    same = lambda p, q: all(abs(p[k] - q[k]) < 0.5 for k in ('x', 'y', 'width', 'height'))             # (sub-pixel jitter is not movement)
    assert same(chip.bounding_box(), before) and same(dpage.locator('.cond-row').bounding_box(), row_before)   # hovering moves/resizes nothing
    dpage.screenshot(path='/tmp/cond-chip-hover.png', clip={**row_before, 'x': row_before['x'] - 10, 'y': row_before['y'] - 30, 'width': 420, 'height': 80})
    x.click()
    dpage.locator('.cond-chip.cond-prone').wait_for(state='detached')
    assert sorted(dpage.locator('.cond-chip').all_text_contents()) == ['Hexed', 'Poisoned']
    cc = dpage.locator('.cond-chip.cond-custom'); cbox = cc.bounding_box()
    cc.hover(); dpage.wait_for_timeout(400)
    hbox = cc.bounding_box(); xbox = cc.locator('.cond-x').bounding_box()
    assert hbox['width'] > cbox['width'] and hbox['height'] == cbox['height']        # snug at rest, widens on hover for the x
    assert xbox['x'] + xbox['width'] <= hbox['x'] + hbox['width'] and xbox['x'] >= hbox['x'] + hbox['width'] - 30
    cc.locator('.cond-x').click()
    dpage.locator('.cond-chip.cond-custom').wait_for(state='detached')
    assert dpage.locator('.cond-chip').all_text_contents() == ['Poisoned']
    assert not derrs, derrs
    dctx.close(); pctx.close()


def test_dm_places_characters_on_a_map_without_a_battle(pw, shared_server):
    base = shared_server.url; dm = Api(base); player = Api(base); cid = dm.campaign('Roster')
    dm.add_member(cid, player)
    dm.character(cid, 'Aria'); dm.character(cid, 'Goblin')
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Cave', 'blank_width': '800', 'blank_height': '600'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    dctx = dm.context(pw, base); page = dctx.new_page(); bad, errs = _collect(page)
    pctx = player.context(pw, base); ppage = pctx.new_page()
    page.goto(base + f'/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle'); page.wait_for_timeout(800)
    if page.locator('#setup-confirm-btn').is_visible():
        page.click('#setup-confirm-btn'); page.wait_for_timeout(600)
    ppage.goto(base + f'/campaigns/{cid}/maps/{mid}'); ppage.wait_for_load_state('networkidle'); ppage.wait_for_timeout(600)
    assert ppage.locator('#characters-flyout').count() == 0                      # Players don't get the tool

    page.set_viewport_size({'width': 1100, 'height': 520})                       # a short window: the panel must still fit
    page.click('#characters-trigger'); page.wait_for_timeout(300)
    menu = page.locator('#characters-flyout .tool-flyout-menu-inner').bounding_box()
    page.screenshot(path='/tmp/roster-short.png')
    assert menu['y'] >= 0 and menu['y'] + menu['height'] <= 520, menu
    page.set_viewport_size({'width': 1280, 'height': 720})
    page.click('#characters-trigger')
    page.click('#characters-trigger'); page.wait_for_timeout(200)
    page.fill('#roster-search', 'gob')
    assert page.locator('.roster-item').count() == 1
    page.click('.roster-item')                                                   # pick the goblin...
    page.wait_for_selector('#place-hint:not([hidden])')
    box = page.locator('#map-canvas').bounding_box()
    for dx in (150, 300):                                                        # ...and drop two copies
        page.mouse.click(box['x'] + dx, box['y'] + 200); page.wait_for_timeout(500)
    page.wait_for_selector('.map-pin')
    assert page.locator('.map-pin-label').all_text_contents() == ['Goblin #1', 'Goblin #2']
    page.screenshot(path='/tmp/map-characters.png')
    assert dm.get(f'/campaigns/{cid}/api/battle').json() == []                   # no battle involved
    page.keyboard.press('Escape')
    assert page.locator('#place-hint').is_hidden()
    ppage.wait_for_selector('.map-pin', timeout=15000)                           # the party sees them on the next refresh
    assert ppage.locator('.map-pin').count() == 2

    page.click('#characters-trigger'); page.fill('#roster-search', '')           # the roster counts what is already placed
    assert 'x2' in page.locator('.roster-item:has-text("Goblin") .roster-count').inner_text().replace('×', 'x')

    dm.post(f'/campaigns/{cid}/api/maps/{mid}/settings', json={'linked_to_battle': 1})   # linking merges them into the battle
    assert len(dm.get(f'/campaigns/{cid}/api/battle').json()) == 2
    assert not errs, errs
    dctx.close(); pctx.close()


def _open_map(pw, base, api, cid, name='Arena'):
    r = api.post(f'/campaigns/{cid}/maps/new', data={'name': name, 'blank_width': '1000', 'blank_height': '700'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    ctx = api.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle'); page.wait_for_timeout(800)
    if page.locator('#setup-confirm-btn').is_visible():
        page.click('#setup-confirm-btn'); page.wait_for_timeout(600)
    return mid, ctx, page, bad, errs


def test_roster_drag_and_drop_zoomed_and_snapped(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('DnD')
    aria = dm.character(cid, 'Aria')
    mid, ctx, page, bad, errs = _open_map(pw, base, dm, cid)
    box = page.locator('#map-canvas').bounding_box()
    page.click('#characters-trigger'); page.wait_for_selector('.roster-item')
    # drag straight from the list (no picking first)
    page.drag_and_drop('.roster-item', '#map-canvas', target_position={'x': 220, 'y': 180})
    page.wait_for_selector('.map-pin')
    assert page.locator('.map-pin-label').all_text_contents() == ['Aria']
    pins = dm.get(f'/campaigns/{cid}/api/maps/{mid}/pins').json()
    assert len(pins) == 1 and pins[0]['character_id'] == aria and pins[0]['participant_id'] is None
    assert 150 < pins[0]['x'] < 300 and 120 < pins[0]['y'] < 250                  # it landed where it was dropped (snapped to a cell)
    assert page.locator('#characters-flyout.roster-dragging').count() == 0       # the panel is back to normal afterwards
    # picking + clicking after zooming in still lands under the cursor
    page.keyboard.down('Control'); page.mouse.move(box['x'] + 400, box['y'] + 300); page.mouse.wheel(0, -300); page.keyboard.up('Control')
    page.wait_for_timeout(300)
    page.click('#characters-trigger'); page.click('.roster-item')
    page.mouse.click(box['x'] + 500, box['y'] + 350); page.wait_for_timeout(600)
    assert len(dm.get(f'/campaigns/{cid}/api/maps/{mid}/pins').json()) == 2
    assert not errs, errs
    ctx.close()


def test_placing_failure_is_shown_not_swallowed(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Fail')
    dm.character(cid, 'Aria')
    mid, ctx, page, bad, errs = _open_map(pw, base, dm, cid)
    page.route('**/api/maps/*/pins', lambda route: route.fulfill(status=500, body='{}') if route.request.method == 'POST' else route.continue_())
    page.click('#characters-trigger'); page.click('.roster-item')
    box = page.locator('#map-canvas').bounding_box()
    page.mouse.click(box['x'] + 200, box['y'] + 200)
    assert "Couldn't place" in page.locator('#place-hint').inner_text() or page.wait_for_selector('#place-hint:has-text("Couldn\'t place")')
    ctx.close()


def test_linked_map_shares_the_roster_through_the_ui(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Shared')
    aria = dm.character(cid, 'Aria'); bram = dm.character(cid, 'Bram')
    dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': bram})
    mid, ctx, page, bad, errs = _open_map(pw, base, dm, cid)
    page.locator('label.toggle-switch:has(#link-battle-checkbox)').click()        # link to the battle
    page.wait_for_selector('.map-pin')                                             # Bram, who was only in the battle, is on the map
    assert page.locator('.map-pin-label').all_text_contents() == ['Bram']
    page.click('#characters-trigger'); page.locator('.roster-item:has-text("Aria")').click()
    box = page.locator('#map-canvas').bounding_box()
    page.mouse.click(box['x'] + 300, box['y'] + 250); page.wait_for_timeout(600)
    assert sorted(r['character_id'] for r in dm.get(f'/campaigns/{cid}/api/battle').json()) == sorted([aria, bram])   # Aria joined the battle
    # remove Aria from the map through the pin bubble: asks first, then takes her out of the battle too
    page.keyboard.press('Escape')
    page.locator('.map-pin:has-text("Aria")').click(); page.wait_for_selector('#pin-action-bubble:not([hidden])')
    page.click('#pin-delete-btn'); page.wait_for_selector('#confirm-modal:not([hidden])')
    assert 'battle' in page.inner_text('#confirm-modal-message')
    page.click('#confirm-modal-yes'); page.wait_for_timeout(600)
    assert [r['character_id'] for r in dm.get(f'/campaigns/{cid}/api/battle').json()] == [bram]
    assert page.locator('.map-pin').count() == 1
    # and removing someone from the battle clears them from the map on the next refresh
    row = dm.get(f'/campaigns/{cid}/api/battle').json()[0]['id']
    dm.post(f'/campaigns/{cid}/api/battle/{row}/remove', json={})
    page.wait_for_selector('.map-pin', state='detached', timeout=15000)
    assert not errs, errs
    ctx.close()


def test_roster_opens_downward_and_wheel_scrolls_only_the_list(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Wheel')
    for i in range(25):
        dm.character(cid, f'Hero {i:02d}')
    mid, ctx, page, bad, errs = _open_map(pw, base, dm, cid)
    page.set_viewport_size({'width': 1280, 'height': 640})
    page.click('#characters-trigger'); page.wait_for_selector('.roster-item'); page.wait_for_timeout(300)
    trigger = page.locator('#characters-trigger').bounding_box()
    menu = page.locator('#characters-flyout .tool-flyout-menu-inner').bounding_box()
    assert menu['y'] > trigger['y'], (menu, trigger)                              # opens below the toolbar by default
    assert menu['y'] + menu['height'] <= 640, menu                                # and fits the window
    items = page.locator('.roster-item').first.bounding_box()
    page.mouse.move(items['x'] + 60, items['y'] + 10)
    scroll_before = page.evaluate('() => window.scrollY')
    for _ in range(12):                                                           # far past the end of the list, where the page used to take over
        page.mouse.wheel(0, 400)
    page.wait_for_timeout(300)
    assert page.evaluate('() => window.scrollY') == scroll_before                 # the page/map did not move
    assert page.locator('#roster-list').evaluate('e => e.scrollTop') > 0         # the list did
    assert not errs, errs
    ctx.close()


def test_a_player_drags_only_the_token_assigned_to_them(pw, shared_server):
    """Token ownership in a real browser: the Player's own token follows the mouse, the others stay put, and the DM
    sees an owner picker while the Player does not."""
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Owners')
    pl = Api(base); dm.add_member(cid, pl)
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Arena', 'blank_width': '1000', 'blank_height': '700'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    api = f'/campaigns/{cid}/api/maps/{mid}'
    ids = {}
    for key, x in (('mine', 200), ('theirs', 600)):
        rr = dm.post(f'{api}/pins', json={'pin_type': 'prop', 'icon_key': 'skull', 'custom_name': key, 'x': x, 'y': 300})
        ids[key] = rr.json()['id']
    pl_id = q('SELECT id FROM users WHERE username = ?', pl.name)[0]['id']
    assert dm.post(f'{api}/pins/{ids["mine"]}/update', json={'owner_user_id': pl_id}).status_code == 200
    ctx = pl.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle'); page.wait_for_timeout(800)
    if page.locator('#setup-confirm-btn').is_visible():
        page.click('#setup-confirm-btn'); page.wait_for_timeout(600)
    assert page.locator('#pin-owner-btn').count() == 0                         # Players never get the assignment picker
    assert page.locator('.map-pin.pin-not-yours').count() == 1                    # exactly the DM-only token is greyed out
    def drag(pin_id):
        box = page.locator(f'.map-pin[data-pin-id="{pin_id}"]').bounding_box()
        page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
        page.mouse.down(); page.mouse.move(box['x'] + 160, box['y'] + 120, steps=8); page.mouse.up(); page.wait_for_timeout(700)
    def pos(pin_id):
        return next((p['x'], p['y']) for p in dm.get(f'{api}/pins').json() if p['id'] == pin_id)
    drag(ids['theirs'])
    assert pos(ids['theirs']) == (600, 300)                                       # not theirs: it did not move
    drag(ids['mine'])
    assert pos(ids['mine'])[0] > 250                                              # theirs: it followed the mouse
    assert not [b for b in bad if b[0] >= 500], bad
    assert not errs, errs
    ctx.close()
    dctx = dm.context(pw, base); dpage = dctx.new_page()
    dpage.goto(base + f'/campaigns/{cid}/maps/{mid}'); dpage.wait_for_load_state('networkidle'); dpage.wait_for_timeout(800)
    if dpage.locator('#setup-confirm-btn').is_visible():
        dpage.click('#setup-confirm-btn'); dpage.wait_for_timeout(600)
    assert dpage.locator('#pin-owner-btn').count() == 1                        # the DM can assign from the pin bubble
    dctx.close()


def test_character_form_previews_bonuses_live_and_the_sheet_shows_them(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Sheets')
    dm.post(f'/campaigns/{cid}/characters/new', data={'name': 'Vex', 'max_hp': '30', 'level': '1'})
    ch = next(c['id'] for c in dm.get(f'/campaigns/{cid}/api/characters').json() if c['name'] == 'Vex')
    ctx = dm.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/characters/{ch}/edit'); page.wait_for_load_state('networkidle')
    assert page.input_value('#speed') == '30'
    page.fill('#level', '5'); page.fill('#dex_score', '14')
    assert page.inner_text('[data-skill-bonus="stealth"]') == '+2'                  # DEX +2, untrained
    page.check('input[name="skill_stealth"][value="2"]')
    assert page.inner_text('[data-skill-bonus="stealth"]') == '+8'                  # + 2 x 3 expertise at level 5
    page.check('[data-save="dex"]')
    assert page.inner_text('[data-save-bonus="dex"]') == '+5'
    assert page.inner_text('[data-mod="dex"]') == '+2'                              # the tile's own modifier follows the score
    assert page.inner_text('[data-skill-count]') == '1 trained'
    page.fill('#speed', '40')
    with page.expect_navigation():
        page.click('.cf-bar button[type=submit]')
    page.wait_for_load_state('networkidle')
    page.goto(base + f'/campaigns/{cid}/characters/{ch}'); page.wait_for_load_state('networkidle')
    text = page.inner_text('body')
    for needle in ('40 ft', 'saving throws', 'passive perc.'):
        assert needle in text.lower(), needle
    assert page.locator('.sheet-skill.is-expert .val').first.inner_text() == '+8'
    assert page.locator('.stat-pill .mod').nth(1).inner_text() == '+2'               # DEX 14 -> +2 under the score
    assert not [b for b in bad if b[0] >= 400], bad
    assert not errs, errs
    ctx.close()


def test_the_token_menu_stays_inside_the_map_and_the_owner_list_flips(pw, shared_server):
    """The radial menu opens towards the side with room (never off the map), and the owner list opens downwards
    when there is no room above the button."""
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Edges')
    for _ in range(3): dm.add_member(cid, Api(base))
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Arena', 'blank_width': '1000', 'blank_height': '700'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    api = f'/campaigns/{cid}/api/maps/{mid}'
    ids = {k: dm.post(f'{api}/pins', json={'pin_type': 'prop', 'icon_key': 'skull', 'custom_name': k, 'x': x, 'y': y}).json()['id']
           for k, (x, y) in {'left': (25, 300), 'right': (975, 300), 'corner': (25, 25), 'top': (500, 40)}.items()}
    ctx = dm.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.set_viewport_size({'width': 1400, 'height': 900})
    page.goto(base + f'/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle'); page.wait_for_timeout(800)
    if page.locator('#setup-confirm-btn').is_visible():
        page.click('#setup-confirm-btn'); page.wait_for_timeout(600)
    stage = page.locator('#map-stage-wrap').bounding_box()
    def inside(sel):
        for b in page.locator(sel).all():
            bb = b.bounding_box()
            assert bb['x'] >= stage['x'] - 1 and bb['y'] >= stage['y'] - 1, (sel, bb, stage)
            assert bb['x'] + bb['width'] <= stage['x'] + stage['width'] + 1 and bb['y'] + bb['height'] <= stage['y'] + stage['height'] + 1, (sel, bb, stage)
    for key in ('left', 'right', 'corner'):
        page.locator(f'.map-pin[data-pin-id="{ids[key]}"]').click(); page.wait_for_timeout(700)
        inside('#pin-action-bubble > .pin-radial-btn:visible, #pin-owner-btn')
    page.locator(f'.map-pin[data-pin-id="{ids["top"]}"]').click(); page.wait_for_timeout(700)
    page.click('#pin-owner-btn'); page.wait_for_timeout(300)
    assert page.locator('#pin-owner-menu').get_attribute('data-dir') == 'down'      # no room above a token at the top edge
    menu = page.locator('#pin-owner-menu').bounding_box()
    assert menu['y'] >= stage['y'] - 1
    assert not errs, errs
    ctx.close()


def test_new_character_can_start_from_a_monster_in_the_manual(pw):
    """Search the monster manual in "New character", pick one, and the form (scores, saves, skills, notes) is filled in."""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import test_reference as ref

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
        base = srv.url; dm = Api(base); cid = dm.campaign('Manual')
        ctx = dm.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
        page.goto(base + f'/campaigns/{cid}/characters/new'); page.wait_for_load_state('networkidle')
        page.click('[data-monster-picker] [data-ref-query]')
        page.fill('[data-monster-picker] [data-ref-query]', 'gob')
        page.click('[data-monster-picker] .ref-item >> text=Goblin Warrior')
        from playwright.sync_api import expect
        expect(page.locator('#name')).to_have_value('Goblin Warrior')
        assert page.input_value('#max_hp') == '10' and page.input_value('#armor_class') == '15' and page.input_value('#dex_score') == '15'
        assert page.is_checked('[data-save="dex"]') and page.is_checked('input[name="skill_stealth"][value="2"]')
        assert page.is_checked('input[name="is_npc"]')
        assert page.locator('#note-blocks [data-note-hidden]:checked').count() == page.locator('#note-blocks [data-note-hidden]').count() >= 1   # the stat block starts hidden from Players
        assert 'Small humanoid' in page.inner_text('#note-blocks') and 'Nimble Escape' in page.inner_text('#note-blocks')
        rows = page.locator('[data-attacks] .atk-edit-row')                       # the monster's attack rolls land in the Attacks card
        assert rows.count() == 3
        assert rows.nth(0).locator('[name=attack_name]').input_value() == 'Scimitar' and rows.nth(0).locator('[name=attack_bonus]').input_value() == '4'
        assert rows.nth(0).locator('[name=attack_damage]').input_value() == '1d6+2' and rows.nth(0).locator('[name=attack_type]').input_value() == 'slashing'
        with page.expect_navigation():
            page.click('.cf-bar button[type=submit]')
        ch = next(c['id'] for c in dm.get(f'/campaigns/{cid}/api/characters').json() if c['name'] == 'Goblin Warrior')
        assert [a['name'] for a in dm.get(f'/campaigns/{cid}/api/characters/{ch}/detail').json()['attacks']] == ['Scimitar', 'Shortbow', 'Weird Gaze']
        page.goto(base + f'/campaigns/{cid}/characters/{ch}'); page.wait_for_load_state('networkidle')
        assert 'Stealth' in page.inner_text('body') and page.locator('.sheet-skill.is-expert .val').first.inner_text() == '+6'
        # the Reference page lists the same data
        page.goto(base + f'/campaigns/{cid}/reference'); page.wait_for_load_state('networkidle')
        page.click('.ref-tab[data-kind="items"]'); page.wait_for_selector('.ref-item')
        page.click('.ref-item >> text=Bag of Holding'); page.wait_for_selector('.ref-title')
        assert 'Uncommon' in page.inner_text('[data-ref-detail]')
        assert not [b for b in bad if b[0] >= 400 and '/api/' not in b[1]], bad
        assert not errs, errs
        ctx.close()
    finally:
        srv.stop(); api.shutdown()


def test_shared_rolls_show_as_a_toast_on_every_page_and_click_to_roll_works(pw, shared_server):
    base = shared_server.url
    dm, pl = Api(base), Api(base); cid = dm.campaign('Dice table')
    dm.add_member(cid, pl)
    hero = dm.character(cid, 'Hero', dex_score=16)
    dctx, pctx = dm.context(pw, base), pl.context(pw, base)
    dpage, ppage = dctx.new_page(), pctx.new_page()
    dbad, _ = _collect(dpage); pbad, _ = _collect(ppage)
    dpage.goto(base + f'/campaigns/{cid}/characters')                 # the DM is on an ordinary page, not the dice page
    ppage.goto(base + f'/campaigns/{cid}/characters/{hero}')
    dpage.wait_for_load_state('networkidle'); ppage.wait_for_load_state('networkidle')
    time.sleep(0.8)                                                  # let both pages learn the newest roll id first

    # click a skill: the roller and the other person both get a toast
    ppage.locator('.sheet-skill', has_text='Stealth').click()
    playwright.expect(ppage.locator('.dice-toast').first).to_contain_text('Stealth')
    playwright.expect(ppage.locator('.dice-toast').first).to_contain_text('Hero')
    playwright.expect(dpage.locator('.dice-toast').first).to_contain_text('Stealth', timeout=15000)
    playwright.expect(dpage.locator('.dice-toast').first).to_contain_text(pl.name)

    # shift-click rolls with advantage: two d20 chips, one dropped
    ppage.locator('.cd-vital', has_text='Init.').click(modifiers=['Shift'])
    playwright.expect(ppage.locator('.dice-toast').last).to_contain_text('advantage')
    playwright.expect(ppage.locator('.dice-toast').last.locator('.roll-chip.is-dropped')).to_have_count(1)
    # the touch-friendly switch does the same without a modifier key
    ppage.locator('label', has_text='Dis.').click()
    ppage.locator('.cd-ab-save').first.click()
    playwright.expect(ppage.locator('.dice-toast').last).to_contain_text('disadvantage')
    assert not [b for b in dbad + pbad if b[0] >= 400], (dbad, pbad)
    dctx.close(); pctx.close()


def test_dice_page_rolls_on_the_server_and_keeps_a_shared_log(pw, shared_server):
    base = shared_server.url
    dm, pl = Api(base), Api(base); cid = dm.campaign('Dice page')
    dm.add_member(cid, pl)
    dctx, pctx = dm.context(pw, base), pl.context(pw, base)
    dpage, ppage = dctx.new_page(), pctx.new_page()
    dbad, _ = _collect(dpage); pbad, _ = _collect(ppage)
    dpage.goto(base + f'/campaigns/{cid}/dice'); ppage.goto(base + f'/campaigns/{cid}/dice')
    dpage.wait_for_load_state('networkidle'); ppage.wait_for_load_state('networkidle')
    time.sleep(0.8)
    dpage.fill('#roll-label', 'Sneak attack')
    dpage.locator('.die-selector[data-die="6"]').click()
    dpage.locator('.die-selector[data-die="6"]').click()
    dpage.fill('#modifier', '4')
    dpage.click('#roll-btn')
    playwright.expect(dpage.locator('.roll-summary')).to_be_visible()
    playwright.expect(dpage.locator('.die-tile.rolling')).to_have_count(0, timeout=5000); playwright.expect(dpage.locator('.die-tile .dw-pips')).to_have_count(2)
    playwright.expect(dpage.locator('#roll-log-list .roll-log-item').first).to_contain_text('Sneak attack')
    assert dpage.locator('.dice-toast').count() == 0                 # your own roll is on screen already: no toast on this page
    playwright.expect(ppage.locator('.dice-toast').first).to_contain_text('Sneak attack', timeout=15000)
    playwright.expect(ppage.locator('#roll-log-list .roll-log-item').first).to_contain_text('Sneak attack')
    assert dpage.locator('#roll-private').count() == 1 and ppage.locator('#roll-private').count() == 0
    # the redesigned page: real SVG dice in the tray and in the result, a live formula, Roll only once something is queued
    assert dpage.locator('.die-selector[data-die] svg').count() == 7 and dpage.locator('.die-tile svg').count() == 2
    assert 'Sneak' not in dpage.locator('#dice-formula').inner_text() and '2d6' in dpage.locator('#dice-formula').inner_text() and '+ 4' in dpage.locator('#dice-formula').inner_text()
    # advantage is only offered for one lone d20
    assert dpage.locator('input[value="advantage"]').is_disabled()
    dpage.click('#clear-tray-btn'); assert dpage.locator('#roll-btn').is_disabled()
    dpage.locator('.die-selector[data-die="20"]').click(); assert not dpage.locator('#roll-btn').is_disabled()
    assert not dpage.locator('input[value="advantage"]').is_disabled()
    assert not [b for b in dbad + pbad if b[0] >= 400], (dbad, pbad)
    dctx.close(); pctx.close()


# ---------------------------------------------------------------- phones and tablets (DL-122)

def _touch_context(browser, api, base, width=390, height=844):
    ctx = browser.new_context(viewport={'width': width, 'height': height}, has_touch=True, is_mobile=True)
    ctx.add_cookies([{'name': SESSION_COOKIE, 'value': api.s.cookies.get(SESSION_COOKIE), 'url': base}])
    return ctx


@pytest.mark.parametrize('size', [(390, 844), (820, 1180)])
def test_phone_and_tablet_pages_fit_the_screen_with_a_bottom_tab_bar(pw, shared_server, size):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Pocket')
    hero = dm.character(cid, 'Hero', dex_score=14); dm.character(cid, 'Ogre', is_npc='on')
    dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': hero})
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Cave', 'blank_width': '800', 'blank_height': '600'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    ctx = _touch_context(pw, dm, base, *size); page = ctx.new_page(); bad, errs = _collect(page)
    w, h = size
    paths = ['/campaigns', f'/campaigns/{cid}/characters', f'/campaigns/{cid}/characters/{hero}', f'/campaigns/{cid}/characters/new',
             f'/campaigns/{cid}/factions', f'/campaigns/{cid}/battle', f'/campaigns/{cid}/maps', f'/campaigns/{cid}/maps/{mid}',
             f'/campaigns/{cid}/dice', f'/campaigns/{cid}/reference']
    for path in paths:
        page.goto(base + path); page.wait_for_load_state('load'); page.wait_for_timeout(400)
        sideways = page.evaluate('() => document.documentElement.scrollWidth - document.documentElement.clientWidth')
        assert sideways <= 1, f'{path} scrolls sideways by {sideways}px at {w}px'
        bar = page.locator('.sidebar-nav').bounding_box()
        assert bar and abs(bar['y'] + bar['height'] - h) <= 1 and bar['width'] >= w - 2, (path, bar)   # the tab bar sits on the bottom edge
        assert page.locator('.sidebar').bounding_box()['height'] < 90, path                          # a slim top bar, not a tall block
    # every tab of the campaign is on screen at once and one tap goes there
    page.goto(base + f'/campaigns/{cid}/characters')
    tabs = page.locator('.sidebar-nav .nav-tab:visible')                                       # four tabs and a More button; the rest live in the More sheet
    assert tabs.count() == 5
    for i in range(tabs.count()):
        box = tabs.nth(i).bounding_box()
        assert box and 0 <= box['x'] and box['x'] + box['width'] <= w + 1 and box['height'] >= 38
    page.locator('.sidebar-nav .nav-tab', has_text='Dice').tap()
    page.wait_for_url(re.compile(r'/dice$'))
    page.locator('.die-selector[data-die="20"]').tap(); page.locator('#roll-btn').tap()
    playwright.expect(page.locator('#roll-log-list .roll-log-item').first).to_be_visible()
    assert not [b for b in bad if b[0] >= 400 and '/api/reference/' not in b[1]], bad      # (the reference page asks the real 5e API, which a sandbox may block)
    assert not errs, errs
    ctx.close()


def test_phone_map_touch_drag_moves_a_token_and_pinch_zooms(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Touch')
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Arena', 'blank_width': '1000', 'blank_height': '700'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    api = f'/campaigns/{cid}/api/maps/{mid}'
    pin = dm.post(f'{api}/pins', json={'pin_type': 'prop', 'icon_key': 'skull', 'custom_name': 'Trap', 'x': 200, 'y': 200}).json()['id']
    ctx = _touch_context(pw, dm, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('load'); page.wait_for_timeout(800)
    if page.locator('#setup-confirm-btn').is_visible():
        page.locator('#setup-confirm-btn').tap(); page.wait_for_timeout(600)
    playwright.expect(page.locator('#nav-hint')).to_contain_text('pinch', ignore_case=True)                  # touch wording, not Ctrl/Space
    assert page.locator('#hold-space-hint').is_hidden()
    cdp = ctx.new_cdp_session(page)
    def touch(kind, *pts):
        cdp.send('Input.dispatchTouchEvent', {'type': kind, 'touchPoints': [{'x': x, 'y': y, 'id': i} for i, (x, y) in enumerate(pts)]})

    page.locator(f'.map-pin[data-pin-id="{pin}"]').scroll_into_view_if_needed(); page.wait_for_timeout(300)
    box = page.locator(f'.map-pin[data-pin-id="{pin}"]').bounding_box()
    sx, sy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
    touch('touchStart', (sx, sy))
    for step in range(1, 9):
        touch('touchMove', (sx + step * 8, sy + step * 5)); page.wait_for_timeout(30)
    touch('touchEnd'); page.wait_for_timeout(900)
    x, y = next((p['x'], p['y']) for p in dm.get(f'{api}/pins').json() if p['id'] == pin)
    assert x > 215 and y > 205, (x, y)                                                     # one finger dragged the token

    before = page.inner_text('#zoom-level-label')
    stage = page.locator('#map-stage-wrap, .map-stage-wrap').first.bounding_box()
    cx, cy = stage['x'] + stage['width'] / 2, max(stage['y'], 0) + min(stage['height'], 300) / 2
    touch('touchStart', (cx - 20, cy), (cx + 20, cy))
    for step in range(1, 9):
        touch('touchMove', (cx - 20 - step * 6, cy), (cx + 20 + step * 6, cy)); page.wait_for_timeout(30)
    touch('touchEnd'); page.wait_for_timeout(300)
    after = page.inner_text('#zoom-level-label')
    assert before == '100%' and int(after.rstrip('%')) > 150, (before, after)               # two fingers spread: zoomed in
    assert len(dm.get(f'{api}/pins').json()) == 1 and not dm.get(f'{api}/drawings').json()  # the pinch drew nothing and moved nothing else
    assert not [b for b in bad if b[0] >= 500], bad
    assert not errs, errs
    ctx.close()


# ---------------------------------------------------------------- character popup, phone layouts, sideways phone

def _map_with_pin(dm, cid, character_id):
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Cave', 'blank_width': '1000', 'blank_height': '700'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    dm.post(f'/campaigns/{cid}/api/maps/{mid}/pins', json={'pin_type': 'character', 'character_id': character_id, 'x': 300, 'y': 300})
    return mid


def _confirm_grid(page):
    if page.locator('#setup-confirm-btn').is_visible():
        page.locator('#setup-confirm-btn').click(); page.wait_for_timeout(500)


def test_character_popup_is_small_and_every_bonus_rolls_from_battle_and_map(pw, shared_server):
    base = shared_server.url; dm, pl = Api(base), Api(base); cid = dm.campaign('Popup'); dm.add_member(cid, pl)
    hero = dm.character(cid, 'Hero', dex_score=16, skill_stealth=2, save_dex='on')
    dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': hero})
    mid = _map_with_pin(dm, cid, hero)
    ctx = dm.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/battle'); page.wait_for_load_state('load'); page.wait_for_timeout(600)
    page.locator('.battle-row [data-action="view"]').first.click()
    panel = page.locator('.peek-panel'); playwright.expect(panel).to_be_visible()
    box = panel.bounding_box(); assert box['width'] <= 600 and box['height'] <= 900 - 30, box          # a light box, not a page
    assert page.locator('.peek-ab').count() == 6 and page.locator('.peek-ab-save').count() == 6
    page.locator('.peek-trained .sheet-skill', has_text='Stealth').click()                              # trained skills are one tap away
    playwright.expect(page.locator('.dice-toast').first).to_contain_text('Stealth')
    playwright.expect(page.locator('.dice-toast').first).to_contain_text('Hero')
    page.locator('.peek-vital', has_text='Init.').click(modifiers=['Shift'])                            # shift = advantage, as on the full page
    playwright.expect(page.locator('.dice-toast').last).to_contain_text('advantage')
    page.locator('.peek-panel label', has_text='Dis.').click()
    page.locator('.peek-ab-save', has_text='Save').nth(1).click()
    playwright.expect(page.locator('.dice-toast').last).to_contain_text('disadvantage')
    page.locator('.peek-panel summary', has_text='All skills').click()
    assert page.locator('.peek-skills .sheet-skill').count() == 18
    page.keyboard.press('Escape'); playwright.expect(panel).to_be_hidden()
    # the same card from a map pin
    page.goto(base + f'/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('load'); page.wait_for_timeout(700); _confirm_grid(page)
    page.locator('.map-pin').first.click(); page.locator('#pin-view-btn').click()
    playwright.expect(page.locator('.peek-panel')).to_be_visible()
    page.locator('.peek-ab-mod').nth(1).click()
    playwright.expect(page.locator('.dice-toast').last).to_contain_text('Dexterity check')
    assert not [b for b in bad if b[0] >= 400], bad
    assert not errs, errs
    ctx.close()


def test_phone_characters_battle_and_map_pages_are_compact(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Compact')
    ids = [dm.character(cid, n, is_npc=npc) for n, npc in (('Hero', ''), ('Ogre', 'on'), ('Goblin', 'on'))]
    for i in ids: dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': i})
    mid = _map_with_pin(dm, cid, ids[0])
    ctx = _touch_context(pw, dm, base, 390, 844); page = ctx.new_page(); bad, errs = _collect(page)
    # characters: one row of small buttons, a search that is a button until tapped
    page.goto(base + f'/campaigns/{cid}/characters'); page.wait_for_load_state('load')
    ys = {round(page.locator(s).first.bounding_box()['y']) for s in ('.page-actions details.menu summary', '.page-actions .btn-primary')}
    assert max(ys) - min(ys) <= 3, ys                                                                  # the primary button and the more menu share one row
    assert page.locator('#character-search').is_hidden() and page.locator('.search-bar').bounding_box()['width'] <= 44
    assert page.locator('.dossier-card').first.bounding_box()['y'] < 330, page.locator('.dossier-card').first.bounding_box()
    page.locator('[data-search-open]').tap(); playwright.expect(page.locator('#character-search')).to_be_visible()
    assert page.locator('#group-filter').is_hidden() or page.locator('.segmented-switch').is_hidden()
    page.fill('#character-search', 'gob'); page.locator('h1').tap()                                    # typed text keeps it open
    assert page.locator('#character-search').is_visible()
    # battle: a row is short and nothing runs off the right edge
    page.goto(base + f'/campaigns/{cid}/battle'); page.wait_for_load_state('load'); page.wait_for_timeout(500)
    rows = page.locator('.battle-row')
    for i in range(rows.count()):
        b = rows.nth(i).bounding_box(); assert b['height'] < 235 and b['x'] + b['width'] <= 391, b
    assert page.evaluate("() => [...document.querySelectorAll('.battle-row *')].filter(e => e.getBoundingClientRect().right > 391).length") == 0
    tops = [page.locator('.battle-actions .btn').nth(i).bounding_box()['y'] for i in range(3)]; assert max(tops) - min(tops) <= 3
    # map: the toolbar is a Tools menu that folds away after picking a tool
    page.goto(base + f'/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('load'); page.wait_for_timeout(600)
    if page.locator('#setup-confirm-btn').is_visible(): page.locator('#setup-confirm-btn').tap(); page.wait_for_timeout(400)
    assert page.locator('#map-toolbar').bounding_box()['height'] < 70
    assert page.locator('[data-tool="rect"]').is_hidden() and page.locator('#clear-drawings-btn').is_hidden()
    page.locator('#map-toolbar-toggle').tap(); playwright.expect(page.locator('[data-tool="eraser"]')).to_be_visible()
    assert page.locator('#clear-drawings-btn').is_visible() and page.locator('#grid-settings-btn').is_visible()
    page.locator('[data-tool="eraser"]').tap(); page.wait_for_timeout(400)
    assert page.locator('[data-tool="eraser"]').is_hidden() and page.locator('#map-toolbar').bounding_box()['height'] < 70
    assert page.locator('#map-toolbar-toggle-icon svg, #map-toolbar-toggle-icon img').count() == 1     # the button shows the tool in use
    assert not [b for b in bad if b[0] >= 400], bad
    assert not errs, errs
    ctx.close()


def test_phone_reference_results_close_when_the_search_is_not_in_use(pw, shared_server):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import test_reference as ref
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split('?')[0].replace('/api/2024', '', 1)
            body = ref.LISTS.get(path) or ref.DETAILS.get(path)
            self.send_response(200 if body else 404); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(body or {}).encode())
        def log_message(self, *a): pass
    api = HTTPServer(('127.0.0.1', 0), H); threading.Thread(target=api.serve_forever, daemon=True).start()
    from conftest import Server
    srv = Server({'SRD_API_BASE': f'http://127.0.0.1:{api.server_port}'})
    try:
        dm = Api(srv.url); cid = dm.campaign('Ref')
        ctx = _touch_context(pw, dm, srv.url, 390, 844); page = ctx.new_page()
        page.goto(srv.url + f'/campaigns/{cid}/reference'); page.wait_for_load_state('load'); page.wait_for_timeout(500)
        assert page.locator('[data-ref-list]').is_hidden()                                              # closed until the search is used
        page.locator('[data-ref-query]').tap(); page.wait_for_selector('.ref-item'); assert page.locator('[data-ref-list]').is_visible()
        page.locator('h1').tap(); page.wait_for_timeout(500)                                           # tap away: closed again
        assert page.locator('[data-ref-list]').is_hidden()
        page.locator('[data-ref-query]').tap(); page.locator('.ref-item', has_text='Fireball').tap()                  # picking an entry closes it and shows the entry
        playwright.expect(page.locator('.ref-title')).to_be_visible(); assert page.locator('[data-ref-list]').is_hidden()
        ctx.close()
    finally:
        srv.stop(); api.shutdown()


def test_sideways_phone_bars_hide_on_scroll_and_the_dock_brings_them_back(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Sideways')
    for i in range(8): dm.character(cid, f'Person {i}')
    ctx = _touch_context(pw, dm, base, 844, 390); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/characters'); page.wait_for_load_state('load'); page.wait_for_timeout(500)
    def nav_on_screen(): return page.locator('.sidebar-nav').bounding_box()['y'] < 390 - 5
    assert nav_on_screen() and page.locator('#mobile-dock').is_visible()
    assert page.locator('.sidebar-nav').bounding_box()['height'] <= 60 and page.locator('.sidebar').bounding_box()['height'] <= 50   # slim bars, not a third of the screen
    page.evaluate('() => window.scrollTo(0, 500)'); page.wait_for_timeout(900)                          # (the bars ignore the scroll their own hiding causes for half a second)
    assert not nav_on_screen()                                                                          # scrolling down: out of the way
    top = page.evaluate('() => window.scrollY')
    page.evaluate(f'() => window.scrollTo(0, {max(top - 120, 30)})'); page.wait_for_timeout(700)
    assert nav_on_screen()                                                                              # scrolling up: back
    page.locator('.js-nav-toggle').tap(); page.wait_for_timeout(450); assert not nav_on_screen()        # by hand
    page.locator('.js-nav-toggle').tap(); page.wait_for_timeout(450); assert nav_on_screen()
    page.goto(base + f'/campaigns/{cid}/characters'); page.wait_for_load_state('load')                 # content is never hidden behind the bar
    page.evaluate('() => window.scrollTo(0, document.body.scrollHeight)'); page.wait_for_timeout(400)
    page.locator('.js-nav-toggle').tap(); page.wait_for_timeout(450)
    page.evaluate('() => window.scrollTo(0, document.body.scrollHeight)'); page.wait_for_timeout(300)
    last = page.locator('.dossier-card').last.bounding_box(); assert last['y'] + last['height'] <= 390 + 1
    assert page.evaluate('() => document.documentElement.scrollWidth - document.documentElement.clientWidth') <= 1
    assert not [b for b in bad if b[0] >= 400], bad
    assert not errs, errs
    ctx.close()


def test_battle_name_and_picture_open_details_and_copies_roll_with_their_number(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Copies')
    gob = dm.character(cid, 'Goblin Minion', dex_score=14, is_npc='on')
    for _ in range(2):
        dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': gob})
    ctx = dm.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/battle'); page.wait_for_load_state('load'); page.wait_for_timeout(600)
    assert page.locator('.battle-row .btn-icon[data-action="view"]').count() == 0      # no separate details button any more
    names = page.locator('.battle-row .battle-name')
    assert names.count() == 2 and 'Goblin Minion #2' in names.nth(1).inner_text()
    page.locator('.battle-row .battle-avatar').nth(1).click()                          # the picture opens it...
    panel = page.locator('.peek-panel'); playwright.expect(panel).to_be_visible()
    page.locator('.peek-vitals [data-roll-bonus]').first.click()
    playwright.expect(page.locator('.dice-toast').first).to_contain_text('Goblin Minion #2')
    page.keyboard.press('Escape'); playwright.expect(panel).to_be_hidden()
    names.nth(0).click()                                                               # ...and so does the name
    playwright.expect(panel).to_be_visible()
    page.locator('.peek-vitals [data-roll-bonus]').first.click()
    playwright.expect(page.locator('.dice-toast').last).to_contain_text('Goblin Minion #1')
    assert not bad and not errs, (bad, errs)


def _swipe(page, x, y, dx, dy, steps=8):
    cdp = page.context.new_cdp_session(page)
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y}]})
    for i in range(1, steps + 1):
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': x + dx * i / steps, 'y': y + dy * i / steps}]})
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})


def test_phone_map_scrolls_the_page_from_empty_map_but_not_from_a_token(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Scrolly')
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Arena', 'blank_width': '1000', 'blank_height': '1400'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    pin = dm.post(f'/campaigns/{cid}/api/maps/{mid}/pins', json={'pin_type': 'prop', 'icon_key': 'skull', 'custom_name': 'Trap', 'x': 300, 'y': 300}).json()['id']
    ctx = _touch_context(pw, dm, base, 390, 700); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('load'); page.wait_for_timeout(800)
    if page.locator('#setup-confirm-btn').is_visible():
        page.locator('#setup-confirm-btn').tap(); page.wait_for_timeout(600)
    page.evaluate('() => window.scrollTo(0, 0)')
    box = page.locator('#map-stage-wrap').bounding_box()
    assert box['height'] > 500
    sx, sy = box['x'] + box['width'] - 40, min(box['y'] + 460, 640)              # empty map, away from the token
    _swipe(page, sx, sy, 0, -300); page.wait_for_timeout(300)
    assert page.evaluate('() => window.scrollY') > 80                             # the page followed the finger
    pinbox = page.locator('.map-pin').first
    before = page.evaluate('() => window.scrollY')
    page.evaluate('() => window.scrollTo(0, 0)')
    pb = pinbox.bounding_box()
    _swipe(page, pb['x'] + pb['width'] / 2, pb['y'] + pb['height'] / 2, 40, 0); page.wait_for_timeout(300)
    assert page.evaluate('() => window.scrollY') < 20                             # a token drag never scrolls the page
    assert not bad and not errs, (bad, errs)


def test_monster_manual_in_the_battle_add_menu_brings_copies_into_the_fight(pw):
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import test_reference as ref

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
        base = srv.url; dm = Api(base); cid = dm.campaign('Manual')
        ctx = dm.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
        page.goto(base + f'/campaigns/{cid}/battle'); page.wait_for_load_state('load'); page.wait_for_timeout(500)
        page.locator('#open-add-modal').click()
        page.locator('.add-monster > summary').click()
        page.locator('[data-monster-count]').fill('2')
        page.locator('.add-monster [data-ref-list] .ref-item', has_text='Goblin Warrior').click()
        playwright.expect(page.locator('.battle-row')).to_have_count(2)
        playwright.expect(page.locator('.battle-name').first).to_contain_text('Goblin Warrior #1')
        playwright.expect(page.locator('[data-monster-status]')).to_contain_text('Added 2 x Goblin Warrior')
        # ...and the Monsters tab of the Reference has the same button
        page.goto(base + f'/campaigns/{cid}/reference'); page.wait_for_load_state('load')
        page.locator('.ref-tab[data-kind="monsters"]').click()
        page.locator('.ref-item', has_text='Goblin Warrior').click()
        page.locator('.ref-battle-bar select').select_option('rolled')
        page.locator('.ref-battle-bar button').click()
        playwright.expect(page.locator('.ref-battle-bar .hint-text')).to_contain_text('Added 1 to the battle')
        assert len(dm.get(f'/campaigns/{cid}/api/battle').json()) == 3
        assert not bad and not errs, (bad, errs)
    finally:
        srv.stop(); api.shutdown()


def test_roll_initiative_button_fills_in_every_row_and_fits_the_phone_header(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Initiative')
    for n in ('Ayla', 'Brom'):
        dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': dm.character(cid, n, dex_score=20)})
    ctx = _touch_context(pw, dm, base, 390, 800); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/battle'); page.wait_for_load_state('load'); page.wait_for_timeout(600)
    ys = {round(page.locator(f'.battle-actions > :nth-child({i})').bounding_box()['y']) for i in (1, 2, 3, 4)}
    assert max(ys) - min(ys) <= 2, ys                                                  # four buttons on one row
    page.locator('#roll-initiative-btn').tap()                                         # no hover on a phone: the first tap opens the choices
    page.locator('[data-roll-scope=all]').tap(); page.wait_for_timeout(700)
    vals = [int(v) for v in page.locator('input[data-action="initiative"]').evaluate_all('els => els.map(e => e.value)')]
    assert len(vals) == 2 and all(6 <= v <= 25 for v in vals), vals
    assert not bad and not errs, (bad, errs)


def test_phone_shape_action_bubble_is_docked_bottom_centre_and_floats_on_desktop(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Bubble')
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Arena', 'blank_width': '1000', 'blank_height': '700'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    dm.post(f'/campaigns/{cid}/api/maps/{mid}/drawings', json={'kind': 'rect', 'cx': 900, 'cy': 120, 'w': 160, 'h': 120, 'fill': True, 'data': {}})   # hard against the right edge
    ctx = _touch_context(pw, dm, base, 390, 800); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('load'); page.wait_for_timeout(800)
    if page.locator('#setup-confirm-btn').is_visible():
        page.locator('#setup-confirm-btn').tap(); page.wait_for_timeout(600)
    box = page.locator('#map-stage-wrap').bounding_box()
    page.touchscreen.tap(box['x'] + box['width'] * 0.9, box['y'] + box['height'] * (120 / 700)); page.wait_for_timeout(400)
    bubble = page.locator('#shape-action-bubble'); playwright.expect(bubble).to_be_visible()
    b = bubble.bounding_box()
    assert abs((b['x'] + b['width'] / 2) - 195) <= 3, b                      # centred on the screen
    assert 0 <= b['x'] and b['x'] + b['width'] <= 390 and b['y'] + b['height'] <= 800 - 60, b      # fully on screen, above the tab bar
    page.locator('#shape-delete-btn').tap(); page.wait_for_timeout(500)
    ctx.close()
    # on a wide screen it still floats beside the shape
    ctx = dm.context(pw, base); page = ctx.new_page()
    page.goto(base + f'/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('load'); page.wait_for_timeout(800)
    assert page.evaluate("() => document.getElementById('shape-action-bubble').classList.contains('is-docked')") is False


def test_character_list_sorts_pages_twelve_at_a_time_and_remembers_its_state(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Roster')
    for i in range(30):
        dm.character(cid, f'Hero {i:02d}', level=1 + i % 7, is_npc='on' if i % 3 == 0 else '')
    ctx = dm.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/characters'); page.wait_for_load_state('load'); page.wait_for_timeout(400)
    visible = lambda: page.locator('.dossier-card:visible .dossier-name').all_inner_texts()
    assert len(visible()) == 12 and visible()[0] == 'Hero 00' and visible()[-1] == 'Hero 11'
    assert page.locator('.pager-pages .pager-btn.is-current').inner_text() == '1' and 'of 30' in page.locator('.pager-info').inner_text()
    page.locator('.pager-btn[aria-label="Next page"]').click(); page.wait_for_timeout(200)
    assert visible()[0] == 'Hero 12' and 'page=2' in page.url
    page.reload(); page.wait_for_load_state('load'); page.wait_for_timeout(300)           # a reload comes back to page 2
    assert visible()[0] == 'Hero 12' and page.locator('.pager-btn.is-current').inner_text() == '2'
    page.locator('.pager-btn[aria-label="Page 3"]').click(); page.wait_for_timeout(200)
    assert len(visible()) == 6 and page.locator('.pager-btn[aria-label="Next page"]').is_disabled()
    page.select_option('#character-sort', 'level-desc'); page.wait_for_timeout(200)       # changing the sort goes back to page 1
    assert 'page=' not in page.url and 'sort=level-desc' in page.url
    levels = [int(x.split()[1]) for x in page.locator('.dossier-card:visible .dossier-meta').all_inner_texts()]
    assert levels == sorted(levels, reverse=True) and levels[0] == 7
    page.select_option('#character-sort', 'newest'); page.wait_for_timeout(200)
    assert visible()[0] == 'Hero 29'
    page.select_option('#character-sort', 'name-desc'); page.wait_for_timeout(200)
    assert visible()[0] == 'Hero 29'
    page.select_option('#character-sort', 'name-asc')
    page.locator('[data-type-switch="npc"]').click(); page.wait_for_timeout(200)           # 10 NPCs: one page, no pager
    assert len(visible()) == 10 and page.locator('#character-pager').is_hidden() and 'type=npc' in page.url
    page.locator('[data-type-switch="all"]').click()
    page.fill('#character-search', 'hero 2'); page.wait_for_timeout(200)
    assert len(visible()) == 10 and 'q=hero+2' in page.url
    page.fill('#character-search', 'zzz'); page.wait_for_timeout(200)
    assert len(visible()) == 0 and page.locator('#character-no-matches').is_visible()
    assert not bad and not errs, (bad, errs)


def test_dm_hides_a_token_and_a_shape_from_players_in_the_browser(pw, shared_server):
    """DL-43: the DM's hide button flips the object to a faint ghost for the DM and removes it for a Player entirely."""
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Hidden')
    pl = Api(base); dm.add_member(cid, pl)
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Vault', 'blank_width': '1000', 'blank_height': '700'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    api = f'/campaigns/{cid}/api/maps/{mid}'
    pin = dm.post(f'{api}/pins', json={'pin_type': 'prop', 'icon_key': 'skull', 'custom_name': 'Trap', 'x': 250, 'y': 250}).json()['id']
    shape = dm.post(f'{api}/drawings', json={'kind': 'rect', 'cx': 650, 'cy': 400, 'w': 200, 'h': 150, 'color': '#ff0000', 'fill': True, 'fill_opacity': 0.5, 'data': {}}).json()['id']

    def open_map(user):
        ctx = user.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
        page.goto(base + f'/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle'); page.wait_for_timeout(800)
        if page.locator('#setup-confirm-btn').is_visible():
            page.click('#setup-confirm-btn'); page.wait_for_timeout(600)
        return ctx, page, bad, errs

    ctx, page, bad, errs = open_map(dm)
    assert page.locator('#new-hidden-toggle').count() == 1
    page.locator(f'.map-pin[data-pin-id="{pin}"]').click(); page.wait_for_timeout(500)
    assert page.locator('#pin-hide-btn').is_visible()
    page.click('#pin-hide-btn'); page.wait_for_timeout(600)
    assert page.locator(f'.map-pin[data-pin-id="{pin}"].is-dm-only').count() == 1       # ghosted for the DM, not removed
    assert q('SELECT dm_only FROM map_pins WHERE id = ?', pin)[0]['dm_only'] == 1
    # the shape: select it by clicking inside it, then hide it
    box = page.locator('#map-canvas').bounding_box()
    page.mouse.click(box['x'] + box['width'] * 0.65, box['y'] + box['height'] * (400 / 700)); page.wait_for_timeout(500)
    assert page.locator('#shape-hide-btn').is_visible()
    page.click('#shape-hide-btn'); page.wait_for_timeout(600)
    assert q('SELECT dm_only FROM map_drawings WHERE id = ?', shape)[0]['dm_only'] == 1
    # the "new objects start hidden" toggle flips its pressed state
    page.click('#new-hidden-toggle')
    assert page.get_attribute('#new-hidden-toggle', 'aria-pressed') == 'true'
    assert not errs, errs
    ctx.close()

    pctx, ppage, pbad, perrs = open_map(pl)
    assert ppage.locator('.map-pin').count() == 0                                        # the Player is never sent it
    assert ppage.locator('#pin-hide-btn, #shape-hide-btn, #new-hidden-toggle').count() == 0
    assert pl.get(f'{api}/drawings').json() == []
    assert not perrs, perrs
    pctx.close()


def test_phone_more_sheet_holds_the_extras_and_closes_every_way(pw, shared_server):
    """The phone tab bar keeps four places plus More; Factions, Reference, view-as-player, theme, full screen and
    log out live in the sheet, which closes with Escape, a tap outside, or a drag down."""
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Sheet')
    ctx = pw.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
    ctx.add_cookies([{'name': SESSION_COOKIE, 'value': dm.s.cookies.get(SESSION_COOKIE), 'url': base}])
    page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/characters'); page.wait_for_load_state('networkidle')
    tabs = page.locator('.sidebar-nav > .nav-tab:visible')
    assert tabs.count() == 5                                                       # Characters, Battle, Maps, Dice, More
    assert page.locator('.sidebar-foot .theme-toggle-btn').is_hidden() and page.locator('.sidebar-foot .user-row').is_hidden()
    assert page.locator('#more-sheet').is_hidden()
    page.click('#nav-more-btn'); page.wait_for_timeout(500)
    assert page.locator('#more-sheet').is_visible()
    for text in ('Factions', 'Reference', 'View as player', 'Theme', 'Log out'):
        assert page.locator('#more-sheet').get_by_text(text).first.is_visible(), text
    box = page.locator('#more-sheet').bounding_box()
    assert box['y'] + box['height'] <= 844 + 1 and box['y'] > 150                  # a sheet, not a full page
    page.keyboard.press('Escape'); page.wait_for_timeout(600)
    assert page.locator('#more-sheet').is_hidden()
    page.click('#nav-more-btn'); page.wait_for_timeout(500)
    page.mouse.click(195, 60); page.wait_for_timeout(600)                           # tap on the dimmed page
    assert page.locator('#more-sheet').is_hidden()
    page.click('#nav-more-btn'); page.wait_for_timeout(500)
    g = page.locator('#more-grip').bounding_box()
    page.mouse.move(g['x'] + g['width'] / 2, g['y'] + 8); page.mouse.down()
    page.mouse.move(g['x'] + g['width'] / 2, g['y'] + 220, steps=6); page.mouse.up(); page.wait_for_timeout(700)
    assert page.locator('#more-sheet').is_hidden()                                  # dragged down to dismiss
    assert not [b for b in bad if b[0] >= 400], bad
    assert not errs, errs
    page.click('#nav-more-btn'); page.wait_for_timeout(500)
    page.locator('#more-sheet').get_by_text('Reference').click(); page.wait_for_load_state('domcontentloaded')
    assert '/reference' in page.url
    ctx.close()


def test_map_bubble_scales_with_the_screen_and_rides_with_the_bars(pw, shared_server):
    """The token menu is smaller on a narrow phone than sideways, and the shape bubble docked sideways moves down when
    the bars slide away (like the full-screen button)."""
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Scale')
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Hall', 'blank_width': '1000', 'blank_height': '700'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1)); api = f'/campaigns/{cid}/api/maps/{mid}'
    pin = dm.post(f'{api}/pins', json={'pin_type': 'prop', 'icon_key': 'skull', 'x': 300, 'y': 300}).json()['id']
    shape = dm.post(f'{api}/drawings', json={'kind': 'rect', 'cx': 600, 'cy': 350, 'w': 200, 'h': 120, 'color': '#ff4040', 'fill': True, 'fill_opacity': 0.4, 'data': {}}).json()['id']
    k = {}
    for name, (w, h) in {'portrait': (390, 844), 'landscape': (844, 390)}.items():
        ctx = pw.new_context(viewport={'width': w, 'height': h}, has_touch=True, is_mobile=True)
        ctx.add_cookies([{'name': SESSION_COOKIE, 'value': dm.s.cookies.get(SESSION_COOKIE), 'url': base}])
        page = ctx.new_page(); bad, errs = _collect(page)
        page.goto(base + f'/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('networkidle'); page.wait_for_timeout(700)
        if page.locator('#setup-confirm-btn').is_visible():
            page.evaluate("document.getElementById('setup-confirm-btn').scrollIntoView({block:'center'})")
            page.click('#setup-confirm-btn'); page.wait_for_timeout(900)
        page.evaluate("window.scrollTo(0, 100)"); page.wait_for_timeout(250)
        page.locator(f'.map-pin[data-pin-id="{pin}"]').click(); page.wait_for_timeout(500)
        k[name] = float(page.eval_on_selector('#pin-action-bubble', 'e => e.style.getPropertyValue("--k")'))
        btn = page.locator('#pin-action-bubble .pin-radial-btn:visible').first.bounding_box()
        assert 24 <= btn['width'] <= 50, btn                                          # smaller than the old 38 on a phone, never tiny
        # every visible button of the token menu is inside the part of the map that is on screen
        for b in page.locator('#pin-action-bubble .pin-radial-btn:visible').all():
            bb = b.bounding_box()
            assert bb['x'] >= -1 and bb['x'] + bb['width'] <= w + 1, (name, bb)
        if name == 'landscape':
            page.evaluate("window.scrollTo(0, 0)"); page.wait_for_timeout(200)
            cb = page.locator('#map-canvas').bounding_box()
            page.evaluate(f"window.scrollBy(0, {max(0, int(cb['y'] + cb['height'] * 0.5 - 200))})"); page.wait_for_timeout(300)
            cb = page.locator('#map-canvas').bounding_box()
            page.mouse.click(cb['x'] + cb['width'] * 0.6, cb['y'] + cb['height'] * 0.5); page.wait_for_timeout(500)
            if page.locator('#shape-action-bubble').is_visible():
                shown = page.locator('#shape-action-bubble').bounding_box()['y']
                page.click('.js-nav-toggle'); page.wait_for_timeout(600)               # the bars slide away
                lower = page.locator('#shape-action-bubble').bounding_box()['y']
                assert lower > shown + 20, (shown, lower)                              # the bubble rode down with them
        assert not errs, errs
        ctx.close()
    assert k['landscape'] > k['portrait'], k                                          # bigger sideways than on the narrow phone


def test_reference_item_tables_render_as_real_tables_and_ability_modifiers_sit_centred(pw, shared_server):
    import copy, threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import test_reference as ref
    lists = copy.deepcopy(ref.LISTS); details = copy.deepcopy(ref.DETAILS)
    lists['/magic-items']['results'].append({'index': 'belt-of-giant-strength', 'name': 'Belt of Giant Strength'})
    details['/magic-items/belt-of-giant-strength'] = {
        'name': 'Belt of Giant Strength', 'rarity': {'name': 'Rarity Varies'}, 'equipment_category': {'name': 'Wondrous Items'},
        'desc': ['Wondrous Item, Rarity Varies (Requires Attunement)', 'Your Strength changes to the belt\'s score.',
                 '| Belt | Str. | Rarity |', '|---|---|---|', '| Belt of Giant Strength (hill) | 21 | Rare |', '| Belt of Giant Strength (storm) | 29 | Legendary |']}
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split('?')[0].replace('/api/2024', '', 1)
            body = lists.get(path) or details.get(path)
            self.send_response(200 if body else 404); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(body or {}).encode())
        def log_message(self, *a): pass
    api = HTTPServer(('127.0.0.1', 0), H); threading.Thread(target=api.serve_forever, daemon=True).start()
    from conftest import Server
    srv = Server({'SRD_API_BASE': f'http://127.0.0.1:{api.server_port}'})
    try:
        dm = Api(srv.url); cid = dm.campaign('Tables')
        hero = dm.character(cid, 'Hero', str_score=27, dex_score=10)
        ctx = _touch_context(pw, dm, srv.url, 390, 844); page = ctx.new_page(); bad, errs = _collect(page)
        page.goto(srv.url + f'/campaigns/{cid}/reference'); page.wait_for_load_state('load')
        page.locator('.ref-tab', has_text='Items').tap(); page.locator('[data-ref-query]').fill('belt')
        page.locator('.ref-item', has_text='Belt of Giant Strength').tap()
        playwright.expect(page.locator('.ref-table')).to_be_visible()
        assert [t.lower() for t in page.locator('.ref-table th').all_inner_texts()] == ['belt', 'str.', 'rarity']
        assert page.locator('.ref-table tbody tr').count() == 2 and page.locator('.ref-table tbody tr').nth(1).inner_text().split('\t')[1:] == ['29', 'Legendary']
        assert '|' not in page.locator('.ref-detail').inner_text()                                   # no raw table text left over
        page.goto(srv.url + f'/campaigns/{cid}/characters/{hero}'); page.wait_for_load_state('load')
        for mod in page.locator('.cd-ab-mod').all():
            box = mod.bounding_box()
            assert box['height'] <= 36, box
            top, bottom = mod.evaluate("e => { const r = document.createRange(); r.selectNodeContents(e); const t = r.getBoundingClientRect(), b = e.getBoundingClientRect(); return [t.top - b.top, b.bottom - t.bottom]; }")
            assert abs(top - bottom) <= 3, (top, bottom)                                             # text centred in the pill
        assert not [b for b in bad if b[0] >= 400], bad
        ctx.close()
    finally:
        srv.stop(); api.shutdown()


def test_the_three_typefaces_load_from_this_site(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Type')
    dm.character(cid, 'Hero')
    ctx = dm.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    hosts = set(); page.on('request', lambda r: hosts.add(r.url.split('/')[2]))
    page.goto(base + f'/campaigns/{cid}/characters'); page.wait_for_load_state('load'); page.wait_for_timeout(800)
    loaded = page.evaluate("async () => { await document.fonts.ready; return [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family.replace(/\"/g, '')); }")
    assert {'Inter', 'Fraunces', 'IBM Plex Mono'} <= set(loaded), loaded
    assert hosts == {base.split('/')[2]}, hosts                                                         # nothing but our own server
    assert not bad and not errs, (bad, errs)
    ctx.close()


def test_lightbox_turns_the_page_quickly_and_calms_down_for_reduced_motion(pw, shared_server):
    base = shared_server.url; api = Api(base); cid = api.campaign('Flip')
    files = [('sheets', (f's{i}.png', png_bytes(600, 300, (40 * i, 80, 120)), 'image/png')) for i in range(3)]
    api.post(f'/campaigns/{cid}/characters/new', data={'name': 'Gallery', 'max_hp': '10'}, files=files)
    char_id = next(c['id'] for c in api.get(f'/campaigns/{cid}/api/characters').json() if c['name'] == 'Gallery')
    for reduced in ('no-preference', 'reduce'):
        ctx = api.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
        page.emulate_media(reduced_motion=reduced)
        page.goto(base + f'/campaigns/{cid}/characters/{char_id}'); page.wait_for_load_state('networkidle')
        page.locator('.sheet-thumbs img[data-sheet-src]').nth(0).click(); page.click('#sheet-viewer-img')
        page.wait_for_selector('#image-lightbox:not([hidden])'); page.wait_for_timeout(500)
        assert page.evaluate("() => document.getElementById('lightbox-img').getAnimations().length") == 0     # opening is not a page turn
        before = page.locator('#lightbox-img').get_attribute('src')
        page.click('#lightbox-next-btn')
        info = page.evaluate("() => { const a = document.getElementById('lightbox-img').getAnimations()[0]; return a && { ms: a.effect.getTiming().duration, rot: JSON.stringify(a.effect.getKeyframes()[0].transform || '') }; }")
        assert info and info['ms'] <= 220, info                                                              # fast: never more than a fifth of a second
        assert ('rotateY' in info['rot']) == (reduced == 'no-preference'), info                              # reduced motion: a plain fade, no swing
        assert page.locator('#lightbox-img').get_attribute('src') != before                                  # the new picture is there at once
        page.wait_for_timeout(350)
        assert page.evaluate("() => document.getElementById('lightbox-img').getAnimations().length") == 0     # and the turn is over
        page.click('#lightbox-next-btn'); page.click('#lightbox-prev-btn')                                   # fast presses never queue up
        page.wait_for_timeout(350)
        assert page.evaluate("() => document.getElementById('lightbox-img').getAnimations().length") == 0
        assert not errs, errs
        ctx.close()


def test_phone_map_toolbar_is_a_bottom_dock_that_tucks_away_sideways(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Dock')
    mid = _map_with_pin(dm, cid, dm.character(cid, 'Hero'))
    def open_map(w, h):
        ctx = _touch_context(pw, dm, base, w, h); page = ctx.new_page(); bad, errs = _collect(page)
        page.goto(base + f'/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('load'); page.wait_for_timeout(600)
        if page.locator('#setup-confirm-btn').is_visible(): page.locator('#setup-confirm-btn').tap(); page.wait_for_timeout(400)
        return ctx, page, errs
    # portrait: a bar fixed above the tab bar; the tool tray rises out of it
    ctx, page, errs = open_map(390, 844)
    tb = page.locator('#map-toolbar')
    box = tb.bounding_box(); nav = page.locator('.sidebar-nav').bounding_box()
    assert box['y'] > 844 / 2 and box['y'] + box['height'] <= nav['y'] + 1, (box, nav)
    page.mouse.wheel(0, 400); page.wait_for_timeout(300)
    assert abs(tb.bounding_box()['y'] - box['y']) < 2                                                # fixed: does not scroll away
    fab0 = page.locator('.dice-fab').bounding_box()                                                  # closed: the dock and the dice button share one row, edge to edge
    assert abs(box['x'] - 12) < 2 and box['x'] + box['width'] < fab0['x'] and abs(fab0['x'] + fab0['width'] - (390 - 12)) < 2, (box, fab0)
    assert abs((box['y'] + box['height'] / 2) - (fab0['y'] + fab0['height'] / 2)) < 3, (box, fab0)
    redo = page.locator('#redo-btn').bounding_box(); zr = page.locator('#zoom-reset-btn').bounding_box()
    rl = page.locator('[data-tool="ruler"]').bounding_box()                                            # the Ruler sits between Redo and Reset Zoom
    assert rl['x'] - (redo['x'] + redo['width']) < 40 and zr['x'] - (rl['x'] + rl['width']) < 40, (redo, rl, zr)      # no empty gap before Reset Zoom
    page.locator('#map-toolbar-toggle').tap(); page.wait_for_timeout(500)
    tray = page.locator('[data-tool="eraser"]').bounding_box()
    assert tray['y'] < tb.bounding_box()['y'] + tb.bounding_box()['height'] and tb.bounding_box()['y'] + tb.bounding_box()['height'] <= nav['y'] + 1
    xs = [page.locator(sel).bounding_box() for sel in ('[data-tool="select"]', '#shapes-flyout [data-flyout-trigger]', '#fog-flyout [data-flyout-trigger]')]
    assert len({round(b['y']) for b in xs}) == 1 and abs(xs[0]['width'] - xs[1]['width']) < 1, xs        # one even row of tools
    assert page.locator('#clear-drawings-btn').bounding_box()['y'] > xs[0]['y'] + xs[0]['height']        # map actions on their own row below
    page.locator('#shapes-flyout [data-flyout-trigger]').tap(); page.wait_for_timeout(300)
    menu = page.locator('#shapes-flyout .tool-flyout-menu').bounding_box()
    assert menu and menu['y'] + menu['height'] <= page.locator('#shapes-flyout').bounding_box()['y'] + 4   # menus open upward
    assert not errs, errs; ctx.close()
    # sideways: one row above the bottom bar, tucks to a pill, rides with the bars
    ctx, page, errs = open_map(844, 390)
    tb = page.locator('#map-toolbar'); nav = page.locator('.sidebar-nav').bounding_box()
    box = tb.bounding_box()
    assert box['height'] < 60 and box['y'] + box['height'] <= nav['y'] + 1 and box['x'] >= 90, (box, nav)
    fab = page.locator('.dice-fab').bounding_box()                                                   # right side: the Tools pill, then the dice button, on one baseline
    assert abs((box['x'] + box['width']) - (844 - 72)) < 6 and box['x'] + box['width'] < fab['x'], (box, fab)
    assert abs((box['y'] + box['height'] / 2) - (fab['y'] + fab['height'] / 2)) < 6, (box, fab)
    assert page.locator('#map-toolbar-toggle-icon').is_hidden()                                        # no tool icon on the Tools button
    dock = [page.locator('.mobile-dock .dock-btn').nth(i).bounding_box() for i in range(2)]          # left side: the two round buttons
    assert dock[1]['x'] + dock[1]['width'] < box['x'] and fab['x'] > 844 - 80, (dock, fab)
    page.locator('.js-nav-toggle').first.tap(); page.wait_for_timeout(500)                           # hiding the bars: dice, toolbar and buttons drop together
    fab2, box2, dock2 = page.locator('.dice-fab').bounding_box(), tb.bounding_box(), page.locator('.mobile-dock .dock-btn').first.bounding_box()
    drop = fab2['y'] - fab['y']
    assert drop > 20 and abs((box2['y'] - box['y']) - drop) < 4 and abs((dock2['y'] - dock[0]['y']) - drop) < 8, (drop, box2, dock2)
    page.locator('.js-nav-toggle').first.tap(); page.wait_for_timeout(500)
    assert page.locator('[data-tool="eraser"]').is_visible()
    page.locator('#map-toolbar-toggle').tap(); page.wait_for_timeout(400)
    assert page.locator('[data-tool="eraser"]').is_hidden() and tb.bounding_box()['width'] < 120
    page.reload(); page.wait_for_load_state('load'); page.wait_for_timeout(500)
    assert page.locator('[data-tool="eraser"]').is_hidden()                                          # remembered
    page.locator('#map-toolbar-toggle').tap(); page.wait_for_timeout(400)
    assert page.locator('[data-tool="eraser"]').is_visible()
    tabs = page.locator('.sidebar-nav .nav-tab:visible')
    for i in range(tabs.count()):
        t = tabs.nth(i).bounding_box(); inner = tabs.nth(i).evaluate("e => { const r = document.createRange(); r.selectNodeContents(e); const b = r.getBoundingClientRect(); return [b.left, b.right]; }")
        assert abs((inner[0] + inner[1]) / 2 - (t['x'] + t['width'] / 2)) < 6, (t, inner)           # content centred in each tab
    assert not errs, errs; ctx.close()


def test_corner_dice_widget_rolls_shared_dice_from_any_page(pw, shared_server):
    base = shared_server.url
    dm, pl = Api(base), Api(base); cid = dm.campaign('Corner dice')
    dm.add_member(cid, pl)
    dctx, pctx = dm.context(pw, base), pl.context(pw, base)
    dpage, ppage = dctx.new_page(), pctx.new_page()
    dbad, derr = _collect(dpage); pbad, perr = _collect(ppage)
    dpage.goto(base + f'/campaigns/{cid}/characters'); ppage.goto(base + f'/campaigns/{cid}/characters')
    dpage.wait_for_load_state('networkidle'); ppage.wait_for_load_state('networkidle'); time.sleep(0.8)
    fab = ppage.locator('.dice-fab'); panel = ppage.locator('#dice-widget-panel')
    playwright.expect(fab).to_be_visible(); playwright.expect(panel).to_be_hidden()
    box = fab.bounding_box(); vp = ppage.viewport_size
    assert box['width'] <= 52 and box['x'] + box['width'] > vp['width'] - 40 and box['y'] + box['height'] > vp['height'] - 40, box   # small, bottom-right
    fab.click(); playwright.expect(panel).to_be_visible(); assert fab.get_attribute('aria-expanded') == 'true'
    ppage.wait_for_timeout(350); assert panel.bounding_box()['y'] + panel.bounding_box()['height'] <= box['y']                                                      # opens above the button
    # tap a d20 with a +3 modifier: the total shows in the tray, the roller gets no toast, the other person does
    ppage.locator('.dw-step-btn[aria-label="More modifier"]').click(); ppage.locator('.dw-step-btn[aria-label="More modifier"]').click(); ppage.locator('.dw-step-btn[aria-label="More modifier"]').click()
    ppage.locator('.dw-die[data-sides="20"]').click()
    playwright.expect(ppage.locator('.dw-detail')).to_contain_text('+3', timeout=5000); playwright.expect(panel).not_to_have_class(re.compile('is-busy'), timeout=5000)
    total = int(ppage.locator('.dw-total').inner_text()); assert 4 <= total <= 23, total
    assert ppage.locator('.dice-toast').count() == 0
    playwright.expect(dpage.locator('.dice-toast').first).to_contain_text(str(total), timeout=15000)
    # several dice of one kind, and advantage
    ppage.locator('.dw-step-btn[aria-label="More dice"]').click()
    ppage.locator('.dw-die[data-sides="6"]').click(); playwright.expect(ppage.locator('.dw-detail')).to_contain_text('2d6', timeout=5000); playwright.expect(panel).not_to_have_class(re.compile('is-busy'), timeout=5000)
    ppage.locator('.dw-step-btn[aria-label="Fewer dice"]').click()
    ppage.get_by_role('button', name='Advantage', exact=True).click(); ppage.locator('.dw-die[data-sides="20"]').click()
    playwright.expect(ppage.locator('.dw-detail')).to_contain_text('advantage', timeout=5000)
    # Escape closes it and gives focus back; the Dice page has no corner widget (it has the full tray)
    ppage.keyboard.press('Escape'); playwright.expect(panel).to_be_hidden(); assert ppage.evaluate("document.activeElement.classList.contains('dice-fab')")
    ppage.goto(base + f'/campaigns/{cid}/dice'); assert ppage.locator('.dice-fab').count() == 0
    assert not [b for b in dbad + pbad if b[0] >= 400] and not derr and not perr, (dbad, pbad, derr, perr)
    dctx.close(); pctx.close()


def test_character_sheet_viewer_turns_the_page_between_sheets(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Sheets')
    files = [('sheets', (f's{i}.png', png_bytes(600, 300, (40 * i, 80, 120)), 'image/png')) for i in range(3)]
    dm.post(f'/campaigns/{cid}/characters/new', data={'name': 'Gallery', 'max_hp': '10'}, files=files)
    hero = next(c['id'] for c in dm.get(f'/campaigns/{cid}/api/characters').json() if c['name'] == 'Gallery')
    ctx = dm.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/characters/{hero}'); page.wait_for_load_state('load')
    img = page.locator('#sheet-viewer-img')
    page.evaluate("() => { window.__turns = []; const a = HTMLElement.prototype.animate; HTMLElement.prototype.animate = function (k, o) { if (this.id === 'sheet-viewer-img') window.__turns.push([k[0].transform || '', o.duration]); return a.call(this, k, o); }; }")
    page.locator('[data-sheet-next]').click()
    turns = page.evaluate('window.__turns'); assert len(turns) == 1 and 'rotateY(-24deg)' in turns[0][0] and turns[0][1] == 220, turns
    page.locator('[data-sheet-prev]').click()
    turns = page.evaluate('window.__turns'); assert len(turns) == 2 and 'rotateY(24deg)' in turns[1][0], turns
    assert not errs, errs; ctx.close()


def test_full_screen_choice_is_remembered_between_pages(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Fullscreen')
    ctx = dm.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/characters'); page.wait_for_load_state('load')
    btn = page.locator('.js-fullscreen:visible').first
    if btn.count() == 0:
        page.evaluate("document.querySelector('.js-fullscreen').hidden = false; document.querySelector('.js-fullscreen').style.display = 'inline-flex'")
        btn = page.locator('.js-fullscreen').first
    btn.click(); page.wait_for_timeout(400)
    assert page.evaluate("sessionStorage.getItem('ledger-fullscreen')") == '1'
    # a new page does not start in full screen (the browser does not allow that): a note appears and the first tap goes back in
    page.goto(base + f'/campaigns/{cid}/battle'); page.wait_for_load_state('load')
    playwright.expect(page.locator('.fs-resume')).to_be_visible()
    page.mouse.click(300, 300); page.wait_for_timeout(500)
    assert page.evaluate("!!document.fullscreenElement"); assert page.locator('.fs-resume').count() == 0
    # leaving full screen yourself (Escape / the button) forgets it
    page.evaluate("document.exitFullscreen()"); page.wait_for_timeout(800)
    assert page.evaluate("sessionStorage.getItem('ledger-fullscreen')") is None
    page.goto(base + f'/campaigns/{cid}/maps'); page.wait_for_load_state('load')
    assert page.locator('.fs-resume').count() == 0
    assert not errs, errs; ctx.close()


_UNNAMED_JS = """() => {
  const txt = e => (e.textContent || '').replace(/\\s+/g, ' ').trim();
  const bad = [];
  document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, img').forEach(e => {
    const tag = e.tagName.toLowerCase();
    let name = e.getAttribute('aria-label') || '';
    if (!name && e.getAttribute('aria-labelledby')) name = 'labelledby';
    if (tag === 'img') { if (!e.hasAttribute('alt')) bad.push(e.outerHTML.slice(0, 100)); return; }
    if (!name && ['input', 'select', 'textarea'].includes(tag)) name = (e.labels && e.labels[0] ? txt(e.labels[0]) : '') || e.getAttribute('placeholder') || e.getAttribute('title') || '';
    if (!name) name = txt(e) || (e.querySelector('img[alt]:not([alt=""])') ? 'img' : '');
    if (!name) bad.push(e.outerHTML.slice(0, 100));
  });
  return bad;
}"""


def test_every_button_input_and_image_has_a_name_a_screen_reader_can_read(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Names')
    hero = dm.character(cid, 'Hero'); dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': hero})
    mid = _map_with_pin(dm, cid, hero)
    ctx = dm.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    problems = {}
    for path in ('/characters', f'/characters/{hero}', f'/characters/{hero}/edit', '/battle', '/maps', f'/maps/{mid}', '/dice', '/reference', '/factions', '/factions/new', '/characters/new'):
        page.goto(base + f'/campaigns/{cid}' + path); page.wait_for_load_state('load'); page.wait_for_timeout(400)
        found = page.evaluate(_UNNAMED_JS)
        if found: problems[path] = found
    assert not problems, problems
    # icon-only buttons the scripts build later are covered too, and a changing title keeps its name in step
    page.goto(base + f'/campaigns/{cid}/characters'); page.wait_for_load_state('load')
    page.evaluate("() => { const b = document.createElement('button'); b.id = 'late'; b.title = 'Late one'; b.innerHTML = '<svg></svg>'; document.body.appendChild(b); }")
    page.wait_for_timeout(200); assert page.locator('#late').get_attribute('aria-label') == 'Late one'
    page.evaluate("document.getElementById('late').title = 'Renamed'"); page.wait_for_timeout(200)
    assert page.locator('#late').get_attribute('aria-label') == 'Renamed'
    assert not errs, errs; ctx.close()


def test_forms_work_from_the_keyboard_alone(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Keys')
    ctx = dm.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/characters/new'); page.wait_for_load_state('load')
    # the first Tab stop is a skip link that jumps past the sidebar to the page content
    page.keyboard.press('Tab')
    assert page.evaluate("document.activeElement.className") == 'skip-link'
    assert page.evaluate("document.activeElement.getBoundingClientRect().top") >= 0
    page.keyboard.press('Enter')
    assert page.evaluate("document.activeElement.id") == 'main-content'
    # a field reached by keyboard shows a ring, not just a border colour
    page.keyboard.press('Tab')
    ring = page.evaluate("(() => { const e = document.activeElement; return [e.tagName, getComputedStyle(e).boxShadow, getComputedStyle(e).outlineStyle]; })()")
    assert ring[1] != 'none' or ring[2] != 'none', ring
    # typing a name and pressing Enter saves the character without touching the mouse
    page.fill('input[name=name]', 'Keyboard Hero'); page.press('input[name=name]', 'Enter')
    page.wait_for_url(lambda u: '/characters/new' not in u)
    assert 'Keyboard Hero' in page.content()
    ctx.close()
    # sign-in / sign-up fields tell password managers what they are, and errors are announced
    anon = pw.new_context().new_page()
    anon.goto(base + '/login'); anon.wait_for_load_state('load')
    assert anon.get_attribute('#username', 'autocomplete') == 'username'
    assert anon.get_attribute('#password', 'autocomplete') == 'current-password'
    anon.fill('#username', 'nobody'); anon.fill('#password', 'wrongpass'); anon.press('#password', 'Enter')
    anon.wait_for_selector('.auth-error[role=alert]')
    assert anon.get_attribute('#username', 'aria-describedby') == 'auth-error'
    anon.goto(base + '/register'); anon.wait_for_load_state('load')
    assert anon.get_attribute('#password', 'autocomplete') == 'new-password'
    assert anon.get_attribute('#confirm', 'autocomplete') == 'new-password'
    assert not errs, errs


def test_dice_card_does_not_grow_when_a_die_is_picked(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Steady')
    for size in ({'width': 1280, 'height': 800}, {'width': 390, 'height': 800}):
        ctx = dm.context(pw, base); page = ctx.new_page(); page.set_viewport_size(size)
        page.goto(base + f'/campaigns/{cid}/dice'); page.wait_for_load_state('load'); page.wait_for_timeout(300)
        probe = "() => ['.dice-card', '#dice-tray', '#dice-formula', '#roll-btn'].map(s => Math.round(document.querySelector(s).getBoundingClientRect().top * 10) / 10)"
        before = page.evaluate(probe)
        assert page.evaluate("getComputedStyle(document.getElementById('clear-tray-btn')).visibility") == 'hidden'
        page.click('.die-selector[data-die="20"] .die-btn'); page.wait_for_timeout(300)
        assert page.evaluate("getComputedStyle(document.getElementById('clear-tray-btn')).visibility") == 'visible'
        assert page.evaluate(probe) == before, (size, before, page.evaluate(probe))
        ctx.close()


def test_battle_page_shows_turn_rank_and_gives_hit_and_heal_feedback(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Arena')
    ids = [dm.character(cid, n, max_hp=40) for n in ('Aria', 'Borin', 'Cael')]
    for i in ids: dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': i})
    ctx = dm.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/battle'); page.wait_for_selector('.battle-row')
    # initiative order is numbered 1..n for the DM, and "now" names whoever has the turn once combat starts
    assert page.locator('.battle-rank').all_inner_texts() == ['1', '2', '3']
    assert page.locator('#turn-now').is_hidden()
    page.click('#next-turn-btn'); page.wait_for_selector('.battle-row.is-active')
    first = page.locator('.battle-row.is-active .battle-name').inner_text().upper().replace('TURN', '').strip().title()
    assert first in page.locator('#turn-now').inner_text()
    page.click('#next-turn-btn'); page.wait_for_function("(f) => document.querySelector('.battle-row.is-active') && document.querySelector('#turn-now').textContent.trim() !== f", arg=first)
    # damage floats a number up, drops the bar by scale (not width) and the readout follows
    row = page.locator('.battle-row').nth(1)
    row.locator('.hp-amount-input').fill('15'); row.locator('.damage-btn').click()
    page.wait_for_selector('.hp-delta-hit')
    assert '15' in page.locator('.hp-delta-hit').first.inner_text()
    page.wait_for_function("() => document.querySelectorAll('.hp-delta').length === 0")
    assert page.locator('.battle-row').nth(1).locator('.hp-readout').inner_text().startswith('25 / 40')
    assert float(page.locator('.battle-row').nth(1).locator('.hp-bar-fill').evaluate("e => e.style.getPropertyValue('--p')")) == 0.63
    page.locator('.battle-row').nth(1).locator('.heal-btn').click(); page.wait_for_selector('.hp-delta-heal')
    # sorting by faction drops the numbers
    page.click('#sort-toggle [data-sort="group"]'); page.wait_for_timeout(300)
    assert page.locator('.battle-rank').count() == 0
    assert not errs, errs; ctx.close()


@pytest.mark.parametrize('size', [(390, 844), (360, 640), (844, 390), (667, 375)])
def test_phone_grid_settings_lightbox_fits_and_the_grid_drags_by_touch(pw, shared_server, size):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('Lightbox')
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Hall', 'blank_width': '1000', 'blank_height': '700'})
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    w, h = size
    ctx = _touch_context(pw, dm, base, w, h); page = ctx.new_page(); bad, errs = _collect(page)
    page.goto(base + f'/campaigns/{cid}/maps/{mid}'); page.wait_for_load_state('load'); page.wait_for_timeout(800)

    def check_fits(label):
        for sel in ('.map-setup-stack', '.map-setup-preview-box', '#setup-preview-canvas', '.map-setup-bar', '#setup-confirm-btn'):
            box = page.locator(sel).bounding_box()
            assert box and box['x'] >= -1 and box['y'] >= -1 and box['x'] + box['width'] <= w + 1 and box['y'] + box['height'] <= h + 1, (label, sel, box)
        conf = page.locator('#setup-confirm-btn').bounding_box()
        assert conf['height'] >= 40, (label, conf)                                    # a finger-sized Confirm
        canvas = page.locator('#setup-preview-canvas').bounding_box()
        assert canvas['width'] > 120 and canvas['height'] > 90, (label, canvas)       # the preview is still usable

    check_fits('first open')
    # dragging the preview with a finger moves the grid
    box = page.locator('#setup-preview-canvas').bounding_box()
    x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
    before = page.evaluate("() => document.getElementById('setup-preview-canvas').toDataURL()")
    cdp = ctx.new_cdp_session(page)
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y}]})
    for i in range(1, 8):
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': x + i * 5, 'y': y + i * 3}]})
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    page.wait_for_timeout(200)
    assert page.evaluate("() => document.getElementById('setup-preview-canvas').toDataURL()") != before
    page.locator('#setup-confirm-btn').tap(); page.wait_for_timeout(500)
    assert not page.locator('#map-setup-overlay').is_visible()
    # reopened from the toolbar: same fit, now with the close button inside the screen
    page.evaluate("() => document.getElementById('grid-settings-btn').click()"); page.wait_for_timeout(500)
    check_fits('reopened')
    close = page.locator('#setup-close-btn').bounding_box()
    assert close and close['width'] >= 40 and 0 <= close['x'] and close['x'] + close['width'] <= w and 0 <= close['y'], close
    page.locator('#setup-close-btn').tap(); page.wait_for_timeout(300)
    assert not page.locator('#map-setup-overlay').is_visible()
    assert not [b for b in bad if b[0] >= 400], bad
    assert not errs, errs
    ctx.close()


def test_condition_chip_hover_shows_its_rule(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('RuleTip')
    pid = dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': dm.character(cid, 'Aria')}).json()[0]['id']
    dm.post(f'/campaigns/{cid}/api/battle/{pid}/conditions', json={'conditions': ['prone'], 'custom_conditions': ['Hexed']})
    dctx = dm.context(pw, base); dpage = dctx.new_page()
    asked = []
    def fake(route):
        asked.append(route.request.url)
        route.fulfill(status=200, content_type='application/json', body='{"title":"Prone","subtitle":"Condition","facts":[],"body":"Crawl only.\\nAttacks against you have <b>x</b>."}')
    dpage.route('**/api/reference/entry**', fake)
    dpage.goto(base + f'/campaigns/{cid}/battle'); dpage.wait_for_selector('.cond-chip')
    dpage.hover('.cond-chip.cond-prone .cond-name'); dpage.wait_for_selector('#cond-rule-tip:not([hidden])')
    tip = dpage.inner_text('#cond-rule-tip')
    assert 'Prone' in tip and 'Crawl only.' in tip and '<b>x</b>' in tip            # shown as text, never parsed as html
    assert dpage.locator('#cond-rule-tip b').count() == 0
    assert 'kind=conditions' in asked[0] and 'conditions%2Fprone' in asked[0]
    dpage.mouse.move(5, 5); dpage.wait_for_selector('#cond-rule-tip', state='hidden')
    dpage.hover('.cond-chip.cond-custom .cond-name'); dpage.wait_for_timeout(600)       # custom conditions have no rule
    assert dpage.locator('#cond-rule-tip').is_hidden() and len(asked) == 1


def test_condition_picker_tiles_show_the_rule_too(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('PickerTip')
    dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': dm.character(cid, 'Aria')})
    dctx = dm.context(pw, base); dpage = dctx.new_page()
    dpage.route('**/api/reference/entry**', lambda route: route.fulfill(status=200, content_type='application/json', body='{"title":"Poisoned","subtitle":"Condition","facts":[],"body":"Disadvantage on attacks."}'))
    dpage.goto(base + f'/campaigns/{cid}/battle'); dpage.click('.cond-add'); dpage.wait_for_selector('#conditions-modal:not([hidden])')
    dpage.hover('#conditions-picker [data-cond="poisoned"]'); dpage.wait_for_selector('#cond-rule-tip:not([hidden])')
    assert 'Disadvantage on attacks.' in dpage.inner_text('#cond-rule-tip')
    tip = dpage.locator('#cond-rule-tip').bounding_box(); tile = dpage.locator('#conditions-picker [data-cond="poisoned"]').bounding_box()
    assert tip['y'] + tip['height'] <= tile['y'] + 1 or tip['y'] >= tile['y'] + tile['height'] - 1      # beside the tile, not covering it
    dpage.click('#conditions-picker [data-cond="poisoned"]')                                           # toggling still works and drops the tip
    dpage.wait_for_selector('#conditions-picker [data-cond="poisoned"].active'); dpage.wait_for_selector('#cond-rule-tip', state='hidden')


def test_dice_toasts_leave_after_a_few_seconds_even_if_the_tab_was_in_the_background(pw, shared_server):
    base = shared_server.url
    dm, pl = Api(base), Api(base); cid = dm.campaign('Toast timing')
    dm.add_member(cid, pl)
    pctx = pl.context(pw, base); ppage = pctx.new_page()
    ppage.goto(base + f'/campaigns/{cid}/characters'); ppage.wait_for_load_state('networkidle'); time.sleep(0.8)
    dm.post(f'/campaigns/{cid}/api/dice/roll', json={'dice': {'20': 1}, 'label': 'First'})
    playwright.expect(ppage.locator('.dice-toast').first).to_contain_text('First', timeout=15000)
    playwright.expect(ppage.locator('.dice-toast')).to_have_count(0, timeout=9000)       # gone by itself (5 s + a moment)
    # a background tab has its timers throttled: pretend 20 s passed without a single timer firing, then come back
    ppage.evaluate("window.__realNow = Date.now.bind(Date); window.__skew = 0; Date.now = () => window.__realNow() + window.__skew")
    dm.post(f'/campaigns/{cid}/api/dice/roll', json={'dice': {'20': 1}, 'label': 'Second'})
    playwright.expect(ppage.locator('.dice-toast').first).to_contain_text('Second', timeout=15000)
    ppage.evaluate("window.__skew = 20000; document.dispatchEvent(new Event('visibilitychange'))")
    playwright.expect(ppage.locator('.dice-toast')).to_have_count(0, timeout=1500)


def test_battle_log_is_collapsible_small_and_live(pw, shared_server):
    base = shared_server.url; dm = Api(base); pl = Api(base); cid = dm.campaign('LogUI')
    dm.add_member(cid, pl)
    pid = dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': dm.character(cid, 'Aria')}).json()[0]['id']
    dctx = dm.context(pw, base); dpage = dctx.new_page(); dbad, derrs = _collect(dpage)
    pctx = pl.context(pw, base); ppage = pctx.new_page()
    dpage.goto(base + f'/campaigns/{cid}/battle'); ppage.goto(base + f'/campaigns/{cid}/battle')
    dpage.wait_for_selector('#battle-log:not([hidden])'); ppage.wait_for_selector('#battle-log:not([hidden])')
    bar = dpage.locator('#battle-log-toggle')
    assert bar.get_attribute('aria-expanded') == 'false'                             # starts closed: one slim bar
    box = bar.bounding_box(); vh = dpage.viewport_size['height']
    assert box['height'] < 48 and box['y'] + box['height'] > vh - 70                  # pinned to the bottom of the page
    main_left = dpage.locator('#main-content').bounding_box()['x']
    assert box['width'] < 460 and abs(box['x'] - (main_left + 32)) < 80               # short, and on the left (not full width)
    fab = dpage.locator('.dice-fab').bounding_box()
    assert abs((box['y'] + box['height'] / 2) - (fab['y'] + fab['height'] / 2)) <= 1.5   # the strip and the dice button share one centre line
    dpage.set_viewport_size({'width': 390, 'height': 800}); dpage.wait_for_timeout(400)         # on a phone: same centre line, and the strip stops short of the dice button
    box = bar.bounding_box(); fab = dpage.locator('.dice-fab').bounding_box()
    assert abs((box['y'] + box['height'] / 2) - (fab['y'] + fab['height'] / 2)) <= 1.5 and box['x'] + box['width'] <= fab['x'] - 4
    dpage.set_viewport_size({'width': 1280, 'height': 900}); dpage.wait_for_timeout(400)
    toggle = dpage.locator('#sidebar-toggle-btn')
    for _ in range(2):                                                                # closing and reopening the sidebar keeps it with the content
        toggle.click(); dpage.wait_for_timeout(600)
        ml = dpage.locator('#main-content').bounding_box()['x']
        assert abs(bar.bounding_box()['x'] - (ml + 32)) < 80 and bar.bounding_box()['y'] + bar.bounding_box()['height'] > vh - 70
    assert 'Aria joined the battle' in dpage.inner_text('#battle-log-latest')
    assert dpage.locator('#battle-log-sheet').is_hidden()
    bar.click()
    playwright.expect(dpage.locator('#battle-log-sheet')).to_be_visible()
    assert dpage.locator('#battle-log-list .bl-item').count() == 1
    blur = lambda sel: dpage.locator(sel).evaluate('(el) => getComputedStyle(el).backdropFilter || getComputedStyle(el).webkitBackdropFilter')
    assert blur('#battle-log-sheet') != 'none'                                       # the card itself is frosted glass...
    assert blur('#battle-log-backdrop') in ('none', '')                              # ...and the page behind is NOT blurred
    assert dpage.locator('#battle-log-sheet').bounding_box()['y'] + dpage.locator('#battle-log-sheet').bounding_box()['height'] < dpage.locator('#battle-log-toggle').bounding_box()['y']   # floats above the bar
    dm.post(f'/campaigns/{cid}/api/battle/{pid}/damage', json={'amount': 4})
    playwright.expect(dpage.locator('#battle-log-list .bl-item').last).to_contain_text('Aria took', timeout=15000)       # newest at the bottom
    playwright.expect(ppage.locator('#battle-log-list .bl-item').last).to_contain_text('Aria took', timeout=15000)   # live for the Player too
    assert float(dpage.locator('#battle-log-list').evaluate('(el) => parseFloat(getComputedStyle(el).maxHeight)')) <= 140   # about six lines, then it scrolls
    assert float(dpage.locator('#battle-log-list .bl-item').last.evaluate('(el) => parseFloat(getComputedStyle(el).fontSize)')) <= 12
    dpage.keyboard.press('Escape')
    assert bar.get_attribute('aria-expanded') == 'false'
    bar.click(); dpage.mouse.click(20, 20)                                           # a click on the blurred page closes it too
    assert bar.get_attribute('aria-expanded') == 'false'
    dpage.reload(); dpage.wait_for_selector('#battle-log:not([hidden])')
    assert dpage.locator('#battle-log-toggle').get_attribute('aria-expanded') == 'false'   # nothing is remembered: always starts closed
    assert not dbad and not derrs


def test_character_sheet_prints_without_the_app_chrome(pw, shared_server):
    base = shared_server.url; dm = Api(base); cid = dm.campaign('PrintUI'); chid = dm.character(cid, 'Printable')
    ctx = dm.context(pw, base); page = ctx.new_page()
    page.goto(base + f'/campaigns/{cid}/characters/{chid}')
    page.wait_for_selector('[data-print]', state='attached')
    page.emulate_media(media='print')
    assert not page.locator('.sidebar').is_visible() and not page.locator('[data-print]').is_visible()      # menus and buttons stay off the paper
    assert page.locator('.cd-hero').is_visible()
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == 'rgb(255, 255, 255)'
