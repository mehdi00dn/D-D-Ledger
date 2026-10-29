"""DL-92: per-user and per-campaign image storage quotas, enforced on the server."""
import io
import pytest
from conftest import q, png_bytes

HOST = 'http://localhost'


def _char(u, cid, name, w=120, h=90, **extra):
    data = {'name': name, 'avatar': (io.BytesIO(png_bytes(w, h, (w % 255, h % 255, 7))), 'a.png')}
    data.update(extra)
    return u.post(f'/campaigns/{cid}/characters/new', data=data, content_type='multipart/form-data',
                  headers={'Referer': f'{HOST}/campaigns/{cid}/characters/new'})


def _used(**where):
    col, val = next(iter(where.items()))
    return int(q(f'SELECT COALESCE(SUM(bytes),0) AS n FROM stored_files WHERE {col} = ?', val)[0]['n'])


def test_upload_is_recorded_with_its_stored_size(camp, appmod):
    u, cid = camp
    assert _char(u, cid, 'Ayla').status_code == 302
    path = q('SELECT avatar_path FROM characters WHERE campaign_id = ? AND name = ?', cid, 'Ayla')[0]['avatar_path']
    row = q('SELECT bytes, campaign_id, user_id, kind FROM stored_files WHERE key = ?', path)[0]
    from storage import get_storage, UPLOADS
    with appmod.app.app_context():
        assert row['bytes'] == len(get_storage().get(UPLOADS, path)) > 0
    assert row['campaign_id'] == cid and row['kind'] == 'avatars'


def test_deleting_and_replacing_images_frees_quota(camp):
    u, cid = camp
    r = u.post(f'/campaigns/{cid}/characters/new',
               data={'name': 'Bram', 'avatar': (io.BytesIO(png_bytes(90, 90)), 'a.png'),
                     'sheets': [(io.BytesIO(png_bytes(300, 400)), 's.png')]}, content_type='multipart/form-data')
    assert r.status_code == 302
    ch = q('SELECT id FROM characters WHERE campaign_id = ? AND name = ?', cid, 'Bram')[0]['id']
    assert len(q('SELECT key FROM stored_files WHERE campaign_id = ?', cid)) == 2
    sid = q('SELECT id FROM character_sheets WHERE character_id = ?', ch)[0]['id']
    u.post(f'/campaigns/{cid}/characters/{ch}/sheets/{sid}/delete')
    assert len(q('SELECT key FROM stored_files WHERE campaign_id = ?', cid)) == 1
    u.post(f'/campaigns/{cid}/characters/{ch}/delete')
    assert _used(campaign_id=cid) == 0


def test_campaign_limit_refuses_with_a_banner_and_saves_nothing(camp, monkeypatch):
    u, cid = camp
    assert _char(u, cid, 'First').status_code == 302
    used = _used(campaign_id=cid)
    monkeypatch.setenv('LEDGER_QUOTA_CAMPAIGN_MB', str((used + 1) / 1048576))
    r = _char(u, cid, 'Second', w=200, h=150)
    assert r.status_code == 302 and r.headers['Location'].endswith('quota_error=campaign')
    assert not q('SELECT id FROM characters WHERE campaign_id = ? AND name = ?', cid, 'Second')
    assert _used(campaign_id=cid) == used                                   # nothing recorded for the refused upload
    page = u.get(r.headers['Location'])
    assert page.status_code == 200 and b'image storage is full' in page.data


def test_user_limit_spans_campaigns(user, monkeypatch):
    c1, c2 = user.new_campaign(), user.new_campaign()
    assert _char(user, c1, 'One').status_code == 302
    monkeypatch.setenv('LEDGER_QUOTA_USER_MB', str((_used(user_id=q('SELECT id FROM users WHERE username = ?', user.name)[0]['id']) + 1) / 1048576))
    r = _char(user, c2, 'Two', w=210, h=130)
    assert r.headers['Location'].endswith('quota_error=user')
    assert not q('SELECT id FROM characters WHERE campaign_id = ? AND name = ?', c2, 'Two')


def test_other_users_are_not_affected_by_someone_elses_usage(camp, make_user, monkeypatch):
    u, cid = camp
    _char(u, cid, 'Mine')
    used = _used(campaign_id=cid)
    monkeypatch.setenv('LEDGER_QUOTA_USER_MB', str((used + 0.4) / 1048576))   # u is exactly full
    again = _char(u, cid, 'Mine2')
    assert again.headers['Location'].endswith('quota_error=user')
    other = make_user()
    c2 = other.new_campaign()
    assert _char(other, c2, 'Theirs').status_code == 302                       # same image, own empty quota
    assert q('SELECT avatar_path FROM characters WHERE campaign_id = ? AND name = ?', c2, 'Theirs')[0]['avatar_path']


def test_zero_turns_a_limit_off(camp, monkeypatch):
    u, cid = camp
    monkeypatch.setenv('LEDGER_QUOTA_USER_MB', '0'); monkeypatch.setenv('LEDGER_QUOTA_CAMPAIGN_MB', '0')
    for i in range(3):
        assert _char(u, cid, f'N{i}', w=100 + i, h=80).status_code == 302
    assert len(q('SELECT key FROM stored_files WHERE campaign_id = ?', cid)) == 3


def test_rejected_non_image_records_nothing(camp):
    u, cid = camp
    u.post(f'/campaigns/{cid}/characters/new', data={'name': 'Evil', 'avatar': (io.BytesIO(b'not an image'), 'x.png')},
           content_type='multipart/form-data')
    assert _used(campaign_id=cid) == 0


def test_sign_endpoint_says_so_when_storage_is_full(camp, monkeypatch):
    u, cid = camp
    _char(u, cid, 'Fill')
    monkeypatch.setenv('LEDGER_QUOTA_USER_MB', str(1 / 1048576))
    r = u.json('/uploads/sign', {'purpose': 'avatar', 'size': 1000, 'content_type': 'image/png'})
    assert r.status_code == 413 and 'storage is full' in r.get_json()['error']
    # imports are not blocked at signing time: the ZIP is checked against the quota when it is read
    ok = u.json('/uploads/sign', {'purpose': 'import', 'size': 1000, 'content_type': 'application/zip'})
    assert ok.status_code == 200


def test_import_over_the_limit_imports_nothing(make_user, monkeypatch):
    src = make_user(); sc = src.new_campaign()
    src.post(f'/campaigns/{sc}/characters/new', data={'name': 'Ayla', 'avatar': (io.BytesIO(png_bytes(200, 200)), 'a.png'),
                                                       'sheets': [(io.BytesIO(png_bytes(300, 400)), 's.png')]},
             content_type='multipart/form-data')
    export = src.get(f'/campaigns/{sc}/export/data')
    assert export.status_code == 200
    dst = make_user(); dc = dst.new_campaign()
    monkeypatch.setenv('LEDGER_QUOTA_CAMPAIGN_MB', str(1 / 1048576))
    r = dst.post(f'/campaigns/{dc}/import/data', data={'import_file': (io.BytesIO(export.data), 'x.zip')},
                 content_type='multipart/form-data', headers={'Referer': f'{HOST}/campaigns/{dc}/characters'})
    assert r.status_code == 302 and 'import_error=quota' in r.headers['Location']
    assert not q('SELECT id FROM characters WHERE campaign_id = ?', dc)
    monkeypatch.setenv('LEDGER_QUOTA_CAMPAIGN_MB', '300')
    r = dst.post(f'/campaigns/{dc}/import/data', data={'import_file': (io.BytesIO(export.data), 'x.zip')},
                 content_type='multipart/form-data', headers={'Referer': f'{HOST}/campaigns/{dc}/characters'})
    assert 'imported=1' in r.headers['Location']
    assert _used(campaign_id=dc) > 0                                        # imported images are accounted for


def test_deleting_a_campaign_clears_its_ledger(camp):
    u, cid = camp
    _char(u, cid, 'Gone')
    assert _used(campaign_id=cid) > 0
    r = u.post(f'/campaigns/{cid}/delete')
    assert not q('SELECT key FROM stored_files WHERE campaign_id = ?', cid)


def test_campaign_page_shows_usage(camp):
    u, cid = camp
    _char(u, cid, 'Seen')
    page = u.get(f'/campaigns/{cid}/edit')
    assert b'id="storage-usage"' in page.data and b'MB' in page.data


def test_backfill_script_fills_the_ledger_for_old_images(camp, appmod):
    u, cid = camp
    _char(u, cid, 'Old')
    before = _used(campaign_id=cid)
    from database import get_db
    with appmod.app.app_context():
        db = get_db(); db.execute('DELETE FROM stored_files WHERE campaign_id = ?', (cid,)); db.commit()
    assert _used(campaign_id=cid) == 0
    import sys, importlib
    sys.path.insert(0, 'scripts')
    backfill = importlib.import_module('backfill_storage_usage')
    with appmod.app.app_context():
        added, missing = backfill.main()
    assert added >= 1 and _used(campaign_id=cid) == before
    with appmod.app.app_context():
        assert backfill.main() == (0, missing)                              # idempotent
