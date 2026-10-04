"""Real-browser upload flows.  With the fake Supabase running, storage lives on a DIFFERENT
origin than the app, so direct uploads exercise real cross-origin fetch + CORS preflight."""
import io, os, re, time, uuid
import numpy as np
import pytest
from PIL import Image
from conftest import BACKEND, FAKE, q, png_bytes
from test_e2e_browser import Api, _collect, pw          # noqa: F401  (pw is a fixture)

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def photo_like_png(w, h):
    """Smooth gradient + noise: a huge PNG that JPEG can shrink a lot (like a real photo)."""
    y = np.linspace(0, 255, h, dtype=np.float32)[:, None, None]
    x = np.linspace(0, 255, w, dtype=np.float32)[None, :, None]
    base = np.concatenate([np.broadcast_to(y, (h, w, 1)), np.broadcast_to(x, (h, w, 1)), np.broadcast_to((x + y) / 2, (h, w, 1))], axis=2)
    noise = np.random.default_rng(1).normal(0, 22, (h, w, 1))
    arr = np.clip(base + noise, 0, 255).astype(np.uint8)
    b = io.BytesIO(); Image.fromarray(arr).save(b, 'PNG'); return b.getvalue()


def noise_png(w, h):
    b = io.BytesIO(); Image.frombytes('RGB', (w, h), os.urandom(w * h * 3)).save(b, 'PNG'); return b.getvalue()


def payload(name, data, mime='image/png'):
    return {'name': name, 'mimeType': mime, 'buffer': data}


def new_page(pw, api, base, mode=None):
    ctx = api.context(pw, base)
    if mode:
        ctx.add_init_script(f"window.LEDGER_UPLOAD_MODE = '{mode}';")
    page = ctx.new_page(); seen = []
    page.on('request', lambda r: seen.append((r.method, r.url)))
    bad, errs = _collect(page)
    return ctx, page, seen, bad, errs


def open_new_map(page, base, cid, name):
    page.goto(f'{base}/campaigns/{cid}/maps?browse=1'); page.wait_for_load_state('networkidle')
    page.locator('#open-new-map-modal, #open-new-map-modal-2').first.click()
    page.fill('#map-name', name)


def test_big_photo_is_shrunk_in_the_browser_and_never_leaves_our_domain(pw, shared_server):
    base = shared_server.url; api = Api(base); cid = api.campaign('Shrink')
    big = photo_like_png(3200, 2400)
    assert len(big) > 3_500_000                                   # too big to post through a serverless function
    ctx, page, seen, bad, errs = new_page(pw, api, base)
    open_new_map(page, base, cid, 'Photo Map')
    page.set_input_files('#map-image-input', payload('battlemap.png', big)); page.wait_for_timeout(600)
    with page.expect_navigation():
        page.click('#new-map-modal button[type=submit]')
    assert not any('/uploads/sign' in u for _, u in seen), 'client-side shrink should have been enough'
    post = [u for m, u in seen if m == 'POST' and '/maps/new' in u]
    assert post
    row = q('SELECT image_path, image_width, image_height FROM maps WHERE campaign_id = ?', cid)[0]
    assert max(row['image_width'], row['image_height']) <= 2000
    assert api.get('/uploads/' + row['image_path']).status_code == 200
    assert not [b for b in bad if b[0] >= 400], bad
    ctx.close()


def test_forced_direct_upload_of_avatar_and_sheets_from_the_browser(pw, shared_server):
    base = shared_server.url; api = Api(base); cid = api.campaign('Direct')
    ctx, page, seen, bad, errs = new_page(pw, api, base, mode='direct')
    page.goto(f'{base}/campaigns/{cid}/characters/new'); page.wait_for_load_state('networkidle')
    page.fill('input[name=name]', 'Cross Origin Hero')
    page.set_input_files('input[name=avatar]', payload('face.png', png_bytes(300, 300, (10, 120, 200))))
    page.wait_for_selector('#crop-modal:not([hidden])'); page.click('#crop-apply-btn'); page.wait_for_timeout(500)
    page.set_input_files('input[name=sheets]', [payload('s1.png', png_bytes(400, 500)), payload('s2.png', png_bytes(420, 520, (5, 5, 90)))])
    with page.expect_navigation():
        page.click('main.content button[type=submit]')
    signs = [u for m, u in seen if m == 'POST' and u.endswith('/uploads/sign')]
    assert len(signs) == 3
    if FAKE is not None:                                          # the bytes went straight to the storage origin
        assert len([1 for m, u in seen if m == 'PUT' and u.startswith(FAKE.url)]) == 3
        assert all(not u.startswith(base) for m, u in seen if m == 'PUT')
    form_post = [r for r in seen if r[0] == 'POST' and '/characters/new' in r[1]]
    assert form_post
    ch = q('SELECT id, avatar_path FROM characters WHERE campaign_id = ?', cid)[0]
    assert ch['avatar_path'] and len(q('SELECT id FROM character_sheets WHERE character_id = ?', ch['id'])) == 2
    page.goto(f'{base}/campaigns/{cid}/characters'); page.wait_for_load_state('networkidle')
    imgs = page.eval_on_selector_all('img[src*="/uploads/"]', 'els => els.map(e => e.naturalWidth)')
    assert imgs and all(w > 0 for w in imgs)
    assert not [b for b in bad if b[0] >= 400], bad
    assert not errs, errs
    ctx.close()


def test_zip_import_too_big_for_the_form_goes_direct_then_imports(pw, shared_server):
    import json, zipfile
    base = shared_server.url; api = Api(base); cid = api.campaign('ImportBig')
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w', zipfile.ZIP_STORED) as z:
        z.writestr('manifest.json', json.dumps({'groups': [], 'characters': [{'name': 'Bulky', 'avatar_file': 'avatars/a.png'}]}))
        z.writestr('images/avatars/a.png', noise_png(1500, 1100))        # ~5 MB, incompressible
    assert len(b.getvalue()) > 4_000_000
    ctx, page, seen, bad, errs = new_page(pw, api, base)
    page.goto(f'{base}/campaigns/{cid}/characters'); page.wait_for_load_state('networkidle')
    with page.expect_navigation():
        page.set_input_files('input[name=import_file]', payload('export.zip', b.getvalue(), 'application/zip'))
    assert [1 for m, u in seen if m == 'POST' and u.endswith('/uploads/sign')]
    assert 'imported=1' in page.url
    ch = q('SELECT avatar_path FROM characters WHERE campaign_id = ? AND name = ?', cid, 'Bulky')[0]
    assert ch['avatar_path'] and api.get('/uploads/' + ch['avatar_path']).status_code == 200
    ctx.close()


def _slow(ctx, page, latency_ms=0, up_bytes_per_s=5_000_000):
    """Throttle the page's network.  latency_ms delays every response (a slow server);
    up_bytes_per_s slows the bytes leaving the browser (a slow connection)."""
    cdp = ctx.new_cdp_session(page)
    cdp.send('Network.enable')
    cdp.send('Network.emulateNetworkConditions', {'offline': False, 'latency': latency_ms,
             'downloadThroughput': 5_000_000, 'uploadThroughput': up_bytes_per_s})


def _sample(page, selector, seconds, js):
    """Poll `js` (evaluated on `selector`) until the page navigates away or time runs out."""
    out, end = [], time.time() + seconds
    while time.time() < end:
        try:
            out.append(page.eval_on_selector(selector, js))
        except Exception:
            break                                          # navigated; the old DOM is gone
        page.wait_for_timeout(60)
    return out


# The ring is the looping "Insider loading circle" animation (static/js/loading-ring.js): no percentage, no track,
# no backing disc -- just two chasing arcs.  The real figure lives on the Save button.
RING = "el => ({shown: !el.hidden && getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().width > 20, " \
       "arcs: el.querySelectorAll('svg circle').length, text: el.textContent.trim(), " \
       "before: getComputedStyle(el, '::before').content, after: getComputedStyle(el, '::after').content, " \
       "bg: getComputedStyle(el).backgroundColor, " \
       "frame: [...el.querySelectorAll('svg circle')].map(c => c.getAttribute('stroke-dasharray') + '|' + c.getAttribute('transform')).join(';')})"


def _assert_plain_animated_ring(ring):
    assert all(r['shown'] for r in ring), 'the ring disappeared mid-upload'
    assert all(r['arcs'] == 2 for r in ring), ring[0]
    assert all(r['text'] == '' and r['after'] in ('none', 'normal') and r['before'] in ('none', 'normal') for r in ring), 'extra stuff on the ring'
    assert all(r['bg'] in ('rgba(0, 0, 0, 0)', 'transparent') for r in ring), 'a backing disc is still drawn'
    assert len({r['frame'] for r in ring}) >= 5, 'the ring is not animating'


def test_progress_fills_during_a_slow_upload(pw, shared_server):
    """Slow CONNECTION: the ring fills with real bytes -- visible the whole time, an increasing
    fill, never the indeterminate spinner the first version showed -- and the Save button
    mirrors it as 'Saving… N%'."""
    base = shared_server.url; api = Api(base); cid = api.campaign('SlowUp')
    ctx, page, seen, bad, errs = new_page(pw, api, base)
    page.goto(f'{base}/campaigns/{cid}/characters/new'); page.wait_for_load_state('networkidle')
    page.fill('input[name=name]', 'Slow Up')
    page.set_input_files('input[data-avatar-input]', payload('a.png', photo_like_png(1600, 1600)))
    page.wait_for_selector('#crop-apply-btn', state='visible'); page.click('#crop-apply-btn'); page.wait_for_timeout(400)
    _slow(ctx, page, latency_ms=20, up_bytes_per_s=250_000)
    page.click('main.content button[type=submit]')
    both = _sample(page, 'main.content', 8, """main => {
        const ring = main.querySelector('.upload-progress-circle'), btn = main.querySelector('button[type=submit]');
        const r = (%s)(ring);
        r.pct = parseFloat(btn.style.getPropertyValue('--btn-pct') || '0'); r.btn = btn.textContent.trim();
        return r
    }""" % RING)
    ctx.close()
    assert len(both) >= 5, f'never saw the ring during the upload: {both}'
    first = next((i for i, r in enumerate(both) if r['shown']), None)     # a big image is shrunk first ('Preparing…')
    assert first is not None, 'the ring never appeared'
    both = both[first:]
    _assert_plain_animated_ring(both)
    pcts = [r['pct'] for r in both]
    assert pcts == sorted(pcts), f'the button fill went backwards: {pcts}'
    assert any(5 < p < 80 for p in pcts), f'no genuine mid-upload fill on the button: {pcts}'


def test_progress_keeps_filling_while_a_slow_server_works(pw, shared_server):
    """Slow SERVER, tiny file -- what a cropped avatar on Vercel actually looks like: the bytes
    are out instantly and all that's left is waiting.  That wait must still read as a fill
    heading toward done (the Save button counting up, the ring still turning), not as a spinner or a frozen page."""
    base = shared_server.url; api = Api(base); cid = api.campaign('SlowServer')
    ctx, page, seen, bad, errs = new_page(pw, api, base)
    page.goto(f'{base}/campaigns/{cid}/characters/new'); page.wait_for_load_state('networkidle')
    page.fill('input[name=name]', 'Slow Server')
    page.set_input_files('input[data-avatar-input]', payload('a.png', photo_like_png(600, 600)))
    page.wait_for_selector('#crop-apply-btn', state='visible'); page.click('#crop-apply-btn'); page.wait_for_timeout(400)
    _slow(ctx, page, latency_ms=2500)
    page.click('main.content button[type=submit]')
    both = _sample(page, 'main.content',  4, """main => {
        const ring = main.querySelector('.upload-progress-circle'), btn = main.querySelector('button[type=submit]');
        const r = (%s)(ring);
        r.pct = parseFloat(btn.style.getPropertyValue('--btn-pct') || '0'); r.btn = btn.textContent.trim(); r.disabled = btn.disabled;
        return r
    }""" % RING)
    ctx.close()
    pcts = [r['pct'] for r in both]
    assert len(both) >= 8, f'too few samples: {len(both)}'
    _assert_plain_animated_ring(both)
    assert pcts == sorted(pcts), f'the button fill went backwards: {pcts}'
    assert pcts[-1] > pcts[0] and 80 <= pcts[-1] < 100, f'should creep toward (never reach) 100 while waiting: {pcts}'
    assert all(r['disabled'] for r in both), 'the Save button must stay disabled while the request is in flight'
    assert re.fullmatch(r'Saving\W+\d+%', both[-1]['btn']), both[-1]['btn']


def test_import_button_shows_progress(pw, shared_server):
    """The import bar was a 4px sliver nobody noticed; the Import button itself now fills and
    reads 'Importing… N%' -- without destroying the hidden file input inside it."""
    import json, zipfile
    base = shared_server.url; api = Api(base); cid = api.campaign('ImportShows')
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w', zipfile.ZIP_STORED) as z:
        z.writestr('manifest.json', json.dumps({'groups': [], 'characters': [{'name': 'Tiny', 'avatar_file': 'avatars/a.png'}]}))
        z.writestr('images/avatars/a.png', photo_like_png(120, 120))
    ctx, page, seen, bad, errs = new_page(pw, api, base)
    page.goto(f'{base}/campaigns/{cid}/characters'); page.wait_for_load_state('networkidle')
    _slow(ctx, page, latency_ms=2500)
    page.set_input_files('input[name=import_file]', payload('export.zip', b.getvalue(), 'application/zip'))
    seen_states = _sample(page, '.import-label', 4, """el => ({text: el.textContent.trim().replace(/\\s+/g, ' '),
        cls: el.className, input: !!el.querySelector('input[type=file]'),
        pct: parseFloat(el.style.getPropertyValue('--btn-pct') || '0')})""")
    ctx.close()
    assert len(seen_states) >= 8
    assert all(s['input'] for s in seen_states), 'the file input inside the Import label was destroyed'
    assert all('is-progress' in s['cls'] for s in seen_states[1:])
    assert re.search(r'Importing\W+\d+%', seen_states[-1]['text']), seen_states[-1]['text']
    pcts = [s['pct'] for s in seen_states]
    assert pcts == sorted(pcts) and pcts[-1] > 80, pcts


def test_double_click_during_a_slow_save_creates_only_one_character(pw, shared_server):
    """Now that the save is an XHR that can take seconds, a second click / Enter must not
    start a second request (the old native re-submit navigated away too fast to matter)."""
    base = shared_server.url; api = Api(base); cid = api.campaign('Dupes')
    ctx, page, seen, bad, errs = new_page(pw, api, base)
    page.goto(f'{base}/campaigns/{cid}/characters/new'); page.wait_for_load_state('networkidle')
    page.fill('input[name=name]', 'Only Once')
    page.set_input_files('input[name=sheets]', payload('s.png', photo_like_png(200, 200)))
    _slow(ctx, page, latency_ms=1500)
    page.click('main.content button[type=submit]')
    page.wait_for_timeout(300)
    page.evaluate("document.querySelector('main.content form').requestSubmit()")     # what Enter would do
    page.evaluate("document.querySelector('main.content form').requestSubmit()")
    with page.expect_navigation():
        pass
    page.wait_for_load_state('networkidle'); ctx.close()
    assert q('SELECT count(*) AS n FROM characters WHERE campaign_id = ? AND name = ?', cid, 'Only Once')[0]['n'] == 1


def test_sheet_only_upload_shows_progress_on_the_save_button(pw, shared_server):
    """No avatar in this save, so there's no ring to put a fill on -- the Save button is the
    only progress a person sees, and it must actually move (this path has no other coverage)."""
    base = shared_server.url; api = Api(base); cid = api.campaign('SheetOnly')
    ctx, page, seen, bad, errs = new_page(pw, api, base)
    _slow(ctx, page, latency_ms=20, up_bytes_per_s=250_000)
    page.goto(f'{base}/campaigns/{cid}/characters/new'); page.wait_for_load_state('networkidle')
    page.fill('input[name=name]', 'Sheet Only')
    page.set_input_files('input[name=sheets]', payload('p.png', photo_like_png(1600, 1600)))
    btn = page.locator('main.content button[type=submit]')
    btn.click()
    samples = _sample(page, 'main.content button[type=submit]',
                       8, "b => ({pct: parseFloat(b.style.getPropertyValue('--btn-pct') || '0'), text: b.textContent.trim()})")
    ctx.close()
    hit100 = next((i for i, s in enumerate(samples) if s['pct'] >= 100), len(samples) - 1)
    samples = samples[:hit100 + 1]                        # navigation follows shortly after 100%; a same-selector
    pcts = [s['pct'] for s in samples]                     # match on the NEW page would otherwise read as 0 again
    assert len(samples) >= 5, f'too few samples: {samples}'
    assert pcts == sorted(pcts), f'the fill went backwards: {pcts}'
    assert any(5 < p < 95 for p in pcts), f'no genuine mid-upload fill on the button: {pcts}'
    assert any(re.fullmatch(r'Saving\W+\d+%', s['text']) for s in samples), samples[-1]['text']


def test_small_uploads_still_use_the_ordinary_form_post(pw, shared_server):
    base = shared_server.url; api = Api(base); cid = api.campaign('Small')
    ctx, page, seen, bad, errs = new_page(pw, api, base)
    page.goto(f'{base}/campaigns/{cid}/characters/new'); page.wait_for_load_state('networkidle')
    page.fill('input[name=name]', 'Tiny')
    page.set_input_files('input[name=sheets]', payload('t.png', png_bytes(80, 80)))
    with page.expect_navigation():
        page.click('main.content button[type=submit]')
    assert not any('/uploads/sign' in u for _, u in seen)
    assert q('SELECT COUNT(*) AS n FROM character_sheets')[0]['n'] >= 1
    ctx.close()


def test_upload_failure_is_shown_to_the_user_and_nothing_is_created(pw, shared_server):
    base = shared_server.url; api = Api(base); cid = api.campaign('Fail')
    ctx, page, seen, bad, errs = new_page(pw, api, base, mode='direct')
    page.route('**/uploads/sign', lambda route: route.fulfill(status=503, content_type='application/json', body='{"error":"file storage is temporarily unavailable"}'))
    messages = []
    page.on('dialog', lambda d: (messages.append(d.message), d.accept()))
    page.goto(f'{base}/campaigns/{cid}/characters/new'); page.wait_for_load_state('networkidle')
    page.fill('input[name=name]', 'Never Created')
    page.set_input_files('input[name=sheets]', payload('s.png', png_bytes(90, 90)))
    page.click('main.content button[type=submit]'); page.wait_for_timeout(1200)
    assert messages and 'unavailable' in messages[0]
    assert q('SELECT COUNT(*) AS n FROM characters WHERE campaign_id = ?', cid)[0]['n'] == 0
    assert page.locator('main.content button[type=submit]').is_enabled()          # not stuck in "Uploading…"
    ctx.close()
