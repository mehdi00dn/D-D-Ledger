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
        r = self.s.post(base + '/register', data={'username': self.name, 'password': 'secret12', 'confirm': 'secret12'}, allow_redirects=False)
        assert r.status_code == 302
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
    page.on('response', lambda r: bad.append((r.status, r.url)) if r.status >= 400 and 'fonts.g' not in r.url else None)
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
    body = ppage.inner_text('body')
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
    page.locator('.sheet-thumbs img[data-lightbox-src]').nth(1).click()
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
    assert dpage.locator('#conditions-picker .cond-tile svg.cond-icon').count() == 15      # every condition has its icon tile
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
    page.focus('#class-trigger'); page.keyboard.press('ArrowDown')            # keyboard: open, move, choose
    page.keyboard.press('ArrowDown'); page.keyboard.press('ArrowDown'); page.keyboard.press('ArrowDown'); page.keyboard.press('Enter')
    assert page.input_value('#class_key') == 'bard'
    page.click('#class-trigger')
    page.evaluate("() => document.documentElement.setAttribute('data-theme', 'light')")
    page.screenshot(path='/tmp/class-picker-light.png')
    page.evaluate("() => document.documentElement.setAttribute('data-theme', 'dark')")
    page.keyboard.press('Escape')
    page.screenshot(path='/tmp/class-picker.png')
    page.click('.form-actions button[type=submit]'); page.wait_for_timeout(1200)
    page.goto(base + f'/campaigns/{cid}/characters'); page.wait_for_selector('.dossier-name .class-icon')
    assert page.locator('.dossier-name .class-icon').get_attribute('title') == 'Bard'
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
    ppage.click('.nav-notifications'); ppage.wait_for_selector('.notification-card.is-unread'); ppage.wait_for_timeout(700)
    assert 'invited you to Invites' in ppage.inner_text('.notification-card')
    ppage.screenshot(path='/tmp/invite-player.png')
    ppage.evaluate("() => document.documentElement.setAttribute('data-theme', 'light')")
    ppage.wait_for_timeout(400); ppage.screenshot(path='/tmp/invite-player-light.png')
    ppage.click('.notification-card button:has-text("Accept")'); ppage.wait_for_url(f'**/campaigns/{cid}/**')
    assert ppage.locator('.nav-badge').count() == 0

    dpage.goto(base + f'/campaigns/{cid}/edit'); dpage.wait_for_selector('#member-list')
    assert dpage.locator('#member-list [data-member-row]').count() == 2 and dpage.locator('#pending-invites').is_hidden()
    assert not derrs and not perrs, (derrs, perrs)
    dctx.close(); pctx.close()
