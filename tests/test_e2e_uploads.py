"""Real-browser upload flows.  With the fake Supabase running, storage lives on a DIFFERENT
origin than the app, so direct uploads exercise real cross-origin fetch + CORS preflight."""
import io, os, re, uuid
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
