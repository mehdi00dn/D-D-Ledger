"""DL-68: the one-time converter for images stored before everything became WebP."""
import io
import os

import pytest
from PIL import Image, ImageFilter

import database
from conftest import BACKEND, _TMP
from storage import LocalStorage, UPLOADS

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')

import importlib.util
_spec = importlib.util.spec_from_file_location('optimize_stored_images',
        os.path.join(os.path.dirname(__file__), '..', 'scripts', 'optimize_stored_images.py'))
opt = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(opt)


def _photo(w, h, fmt):
    im = Image.frombytes('RGB', (w // 8, h // 8), os.urandom((w // 8) * (h // 8) * 3)).resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(2))
    b = io.BytesIO(); im.save(b, fmt); return b.getvalue()


def _run(storage, apply):
    db = database.get_db()
    try:
        lines = []
        result = opt.convert_all(db, storage, apply=apply, out=lines.append)
        return result, lines
    finally:
        db.close()


def _exec(sql, *params):
    db = database.get_db()
    try:
        db.execute(sql, params); db.commit()
    finally:
        db.close()


def _row(sql, *params):
    db = database.get_db()
    try:
        return dict(db.execute(sql, params).fetchone())
    finally:
        db.close()


def test_legacy_images_are_converted_in_place_and_maps_keep_their_size(user):
    store = LocalStorage(os.path.join(_TMP, 'opt-' + os.urandom(3).hex()), 'secret')
    cid = user.new_campaign()
    mid = user.new_map(cid, w=400, h=300)
    old_map = f'maps/{os.urandom(8).hex()}.png'
    data = _photo(1600, 1200, 'PNG')
    store.put(UPLOADS, old_map, data, 'image/png')
    _exec('UPDATE maps SET image_path = ?, image_width = 1600, image_height = 1200 WHERE id = ?', old_map, mid)
    old_av = f'avatars/{os.urandom(8).hex()}.jpg'
    store.put(UPLOADS, old_av, _photo(1500, 1500, 'JPEG'), 'image/jpeg')
    _exec('UPDATE campaigns SET avatar_path = ? WHERE id = ?', old_av, cid)

    # Dry run: reports, changes nothing.
    (n, skipped, before, after), lines = _run(store, apply=False)
    assert n >= 2 and after < before
    assert _row('SELECT image_path AS p FROM maps WHERE id = ?', mid)['p'] == old_map and store.exists(UPLOADS, old_map)

    # Real run.
    _run(store, apply=True)
    m = _row('SELECT image_path AS p, image_width AS w, image_height AS h FROM maps WHERE id = ?', mid)
    assert m['p'].endswith('.webp') and (m['w'], m['h']) == (1600, 1200)
    new = Image.open(io.BytesIO(store.get(UPLOADS, m['p'])))
    assert new.format == 'WEBP' and new.size == (1600, 1200)                    # same pixels grid, never shrunk
    assert not store.exists(UPLOADS, old_map)                                    # old file removed after the switch
    a = _row('SELECT avatar_path AS p FROM campaigns WHERE id = ?', cid)['p']
    assert a.endswith('.webp') and max(Image.open(io.BytesIO(store.get(UPLOADS, a))).size) == 512

    # Running again finds nothing left to do.
    (n2, _s, _b, _a), _l = _run(store, apply=True)
    assert n2 == 0


def test_missing_file_is_skipped_not_fatal(user):
    store = LocalStorage(os.path.join(_TMP, 'opt-' + os.urandom(3).hex()), 'secret')
    cid = user.new_campaign()
    _exec('UPDATE campaigns SET avatar_path = ? WHERE id = ?', 'avatars/doesnotexist.png', cid)
    (n, skipped, _b, _a), lines = _run(store, apply=True)
    assert skipped >= 1 and any('missing' in l for l in lines)
    assert _row('SELECT avatar_path AS p FROM campaigns WHERE id = ?', cid)['p'] == 'avatars/doesnotexist.png'
