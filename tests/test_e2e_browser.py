"""Real-browser checks (Chromium via Playwright) against a live server process."""
import os, re, uuid, time
import pytest
import requests
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from conftest import png_bytes


@pytest.fixture(scope='module')
def pw():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch(); yield b; b.close()


class Api:
    """requests-based helper hitting the live server, for fast setup."""
    def __init__(self, base):
        self.base = base; self.s = requests.Session()
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
    def context(self, browser, base):
        ctx = browser.new_context(viewport={'width': 1400, 'height': 900})
        ctx.add_cookies([{'name': 'session', 'value': self.s.cookies.get('session'), 'url': base}])
        return ctx


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
    api.post(f'/campaigns/{cid}/groups/new', data={'name': 'G1', 'color': '#123456'})
    api.character(cid, 'Doomed')
    api.post(f'/campaigns/{cid}/maps/new', data={'name': 'M1', 'blank_width': '300', 'blank_height': '300'})
    ctx = api.context(pw, base); page = ctx.new_page(); bad, errs = _collect(page)
    for path, sel in ((f'/campaigns/{cid}/characters', 'form[action*="/characters/"][action$="/delete"]'),
                      (f'/campaigns/{cid}/groups', 'form[action*="/groups/"][action$="/delete"]'),
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
    dm.post(f'/campaigns/{cid}/members/add', data={'username': pl.name, 'status': 'player'})
    hero = dm.character(cid, 'Hero'); ogre = dm.character(cid, 'Ogre', is_npc='on', max_hp=59)
    dctx, pctx = dm.context(pw, base), pl.context(pw, base)
    dpage, ppage = dctx.new_page(), pctx.new_page()
    dbad, _ = _collect(dpage); pbad, _ = _collect(ppage)
    dpage.goto(base + f'/campaigns/{cid}/battle'); ppage.goto(base + f'/campaigns/{cid}/battle')
    dm.s.post(f'{base}/campaigns/{cid}/api/battle/add', json={'character_id': hero})
    dm.s.post(f'{base}/campaigns/{cid}/api/battle/add', json={'character_id': ogre})
    ppage.wait_for_function("document.body.innerText.includes('Hero') && document.body.innerText.includes('Ogre')", timeout=12000)
    dpage.wait_for_function("document.body.innerText.includes('Hero')", timeout=12000)
    body = ppage.inner_text('body')
    assert '59' not in body                     # the player never sees the ogre's HP
    assert not [b for b in dbad + pbad if b[0] >= 400], (dbad, pbad)
    dctx.close(); pctx.close()


def test_map_draw_and_pin_survive_reload(pw, shared_server):
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
