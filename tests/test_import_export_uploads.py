import io, json, re, zipfile
import pytest
from conftest import q, png_bytes


def _make_source(u, cid):
    gid = u.new_group(cid, name='Rebels')
    r = u.post(f'/campaigns/{cid}/characters/new',
               data={'name': 'Ayla', 'level': '4', 'max_hp': '33', 'group_id': str(gid),
                     'avatar': (io.BytesIO(png_bytes(200, 200)), 'ayla.png'),
                     'sheets': [(io.BytesIO(png_bytes(300, 400)), 's1.png'), (io.BytesIO(png_bytes(300, 400, (1, 2, 3))), 's2.png')]},
               content_type='multipart/form-data')
    assert r.status_code == 302
    return q('SELECT id, avatar_path FROM characters WHERE campaign_id = ? AND name = ?', cid, 'Ayla')[0]


def test_avatar_and_sheet_upload_persist_and_serve(camp):
    u, cid = camp
    ch = _make_source(u, cid)
    assert ch['avatar_path']
    img = u.get('/uploads/' + ch['avatar_path'])
    assert img.status_code == 200 and img.data[:4] == b'\x89PNG'
    sheets = q('SELECT image_path FROM character_sheets WHERE character_id = ? ORDER BY sort_order', ch['id'])
    assert len(sheets) == 2
    for s in sheets:
        assert u.get('/uploads/' + s['image_path']).status_code == 200
    # sheet delete redirects somewhere that loads
    sid = q('SELECT id FROM character_sheets WHERE character_id = ? ORDER BY id', ch['id'])[0]['id']
    r = u.post(f'/campaigns/{cid}/characters/{ch["id"]}/sheets/{sid}/delete')
    assert r.status_code == 302 and u.get(r.headers['Location']).status_code == 200
    assert len(q('SELECT id FROM character_sheets WHERE character_id = ?', ch['id'])) == 1


def test_disallowed_extension_is_ignored(camp):
    u, cid = camp
    r = u.post(f'/campaigns/{cid}/characters/new', data={'name': 'Evil', 'avatar': (io.BytesIO(b'<script>alert(1)</script>'), 'x.html')},
               content_type='multipart/form-data')
    assert r.status_code == 302
    assert q('SELECT avatar_path FROM characters WHERE campaign_id = ? AND name = ?', cid, 'Evil')[0]['avatar_path'] is None


def test_persian_filename_upload(camp):
    u, cid = camp
    r = u.post(f'/campaigns/{cid}/characters/new',
               data={'name': 'Nasim', 'avatar': (io.BytesIO(png_bytes()), 'عکس.png')}, content_type='multipart/form-data')
    assert r.status_code == 302
    assert q('SELECT avatar_path FROM characters WHERE campaign_id = ? AND name = ?', cid, 'Nasim')[0]['avatar_path']


def test_export_import_roundtrip_between_campaigns(make_user):
    u = make_user()
    src, dst = u.new_campaign('Source'), u.new_campaign('Dest')
    _make_source(u, src)
    z = u.get(f'/campaigns/{src}/export/data')
    assert z.status_code == 200 and z.data[:2] == b'PK'
    zf = zipfile.ZipFile(io.BytesIO(z.data))
    manifest = json.loads(zf.read('manifest.json'))
    assert [c['name'] for c in manifest['characters']] == ['Ayla'] and manifest['groups'][0]['name'] == 'Rebels'
    assert any(n.startswith('images/') for n in zf.namelist())
    r = u.post(f'/campaigns/{dst}/import/data', data={'import_file': (io.BytesIO(z.data), 'export.zip')}, content_type='multipart/form-data')
    assert r.status_code == 302 and u.get(r.headers['Location']).status_code == 200
    got = q('SELECT id, avatar_path, group_id FROM characters WHERE campaign_id = ? AND name = ?', dst, 'Ayla')
    assert len(got) == 1 and got[0]['group_id'] is not None
    assert u.get('/uploads/' + got[0]['avatar_path']).status_code == 200
    assert len(q('SELECT id FROM character_sheets WHERE character_id = ?', got[0]['id'])) == 2
    # re-import: characters duplicate, groups dedupe (documented legacy behaviour)
    u.post(f'/campaigns/{dst}/import/data', data={'import_file': (io.BytesIO(z.data), 'export.zip')}, content_type='multipart/form-data')
    assert len(q('SELECT id FROM characters WHERE campaign_id = ? AND name = ?', dst, 'Ayla')) == 2
    assert len(q('SELECT id FROM groups WHERE campaign_id = ?', dst)) == 1


def test_single_character_and_group_export(camp):
    u, cid = camp
    ch = _make_source(u, cid)
    r = u.get(f'/campaigns/{cid}/characters/{ch["id"]}/export'); assert r.status_code == 200 and r.data[:2] == b'PK'
    gid = q('SELECT id FROM groups WHERE campaign_id = ?', cid)[0]['id']
    r = u.get(f'/campaigns/{cid}/groups/{gid}/export'); assert r.status_code == 200 and r.data[:2] == b'PK'


def test_bad_import_files_do_not_500(camp):
    u, cid = camp
    r = u.post(f'/campaigns/{cid}/import/data', data={'import_file': (io.BytesIO(b'not a zip'), 'x.zip')}, content_type='multipart/form-data')
    assert r.status_code in (302, 400)
