"""Campaign isolation.  Tests marked xfail(strict) document the known holes that
Phase 3 must close -- strict, so the marker has to be removed the moment the fix lands."""
import json, io
import pytest
from conftest import q

IDOR = lambda f: f          # fixed: campaign scoping is now enforced centrally


@pytest.fixture
def two_campaigns(make_user):
    alice, bob = make_user(), make_user()
    ca, cb = alice.new_campaign('Alice'), bob.new_campaign('Bob')
    mid = alice.new_map(ca)
    did = alice.json(f'/campaigns/{ca}/api/maps/{mid}/drawings', {'kind': 'rect', 'data': {}, 'cx': 1, 'cy': 1, 'w': 5, 'h': 5}).get_json()['id']
    pin = alice.json(f'/campaigns/{ca}/api/maps/{mid}/pins', {'pin_type': 'prop', 'icon_key': 'skull', 'x': 1, 'y': 1}).get_json()['id']
    ch = alice.new_character(ca, name='Boss', max_hp=100); pid = alice.add_to_battle(ca, ch)
    return dict(alice=alice, bob=bob, ca=ca, cb=cb, mid=mid, did=did, pin=pin, ch=ch, pid=pid)


@IDOR
def test_bob_cannot_read_alices_map_drawings(two_campaigns):
    t = two_campaigns
    assert t['bob'].get(f"/campaigns/{t['cb']}/api/maps/{t['mid']}/drawings").status_code in (403, 404)

@IDOR
def test_bob_cannot_read_alices_map_pins_or_state(two_campaigns):
    t = two_campaigns
    assert t['bob'].get(f"/campaigns/{t['cb']}/api/maps/{t['mid']}/pins").status_code in (403, 404)
    assert t['bob'].get(f"/campaigns/{t['cb']}/api/maps/{t['mid']}/state").status_code in (403, 404)

@IDOR
def test_bob_cannot_modify_alices_drawings(two_campaigns):
    t = two_campaigns; base = f"/campaigns/{t['cb']}/api/maps/{t['mid']}/drawings"
    assert t['bob'].json(base, {'kind': 'rect', 'data': {}}).status_code in (403, 404)
    assert t['bob'].json(f"{base}/{t['did']}/delete").status_code in (403, 404)
    assert q('SELECT COUNT(*) AS n FROM map_drawings WHERE id = ?', t['did'])[0]['n'] == 1

@IDOR
def test_bob_cannot_modify_alices_pins_or_map_settings(two_campaigns):
    t = two_campaigns; base = f"/campaigns/{t['cb']}/api/maps/{t['mid']}"
    assert t['bob'].json(f"{base}/pins/{t['pin']}/delete").status_code in (403, 404)
    assert t['bob'].json(f"{base}/settings", {'grid_size': 1}).status_code in (403, 404)
    assert q('SELECT COUNT(*) AS n FROM map_pins WHERE id = ?', t['pin'])[0]['n'] == 1

@IDOR
def test_bob_cannot_touch_alices_battle_participant(two_campaigns):
    t = two_campaigns; B = f"/campaigns/{t['cb']}/api/battle/{t['pid']}"
    for action, payload in (('damage', {'amount': 60}), ('heal', {'amount': 1}), ('die', {}), ('remove', {}), ('set_hp', {'value': 1})):
        assert t['bob'].json(f'{B}/{action}', payload).status_code in (403, 404), action
    assert q('SELECT current_hp FROM battle_participants WHERE id = ?', t['pid'])[0]['current_hp'] == 100

@IDOR
def test_character_cannot_join_a_foreign_group(two_campaigns):
    t = two_campaigns
    gid = t['alice'].new_group(t['ca'], name='Secret Guild')
    r = t['bob'].post(f"/campaigns/{t['cb']}/characters/new", data={'name': 'Spy', 'group_id': str(gid)}, content_type='multipart/form-data')
    assert q('SELECT group_id FROM characters WHERE campaign_id = ? AND name = ?', t['cb'], 'Spy')[0]['group_id'] is None

def test_scoped_routes_already_hold(two_campaigns):
    """Regression guard for routes that ARE campaign-scoped today."""
    t = two_campaigns
    assert t['bob'].get(f"/campaigns/{t['cb']}/characters/{t['ch']}").status_code in (302, 404)
    assert t['bob'].post(f"/campaigns/{t['cb']}/characters/{t['ch']}/delete").status_code in (302, 404)
    assert q('SELECT COUNT(*) AS n FROM characters WHERE id = ?', t['ch'])[0]['n'] == 1


UNTERMINATED = '<img src=x onerror=alert(1)//'

def test_notes_sanitizer_blocks_unterminated_tag(camp):
    u, cid = camp
    ch = u.new_character(cid, name='X', notes=json.dumps([UNTERMINATED]))
    assert 'onerror' not in (q('SELECT notes FROM characters WHERE id = ?', ch)[0]['notes'] or '')

def test_import_sanitizes_notes(camp):
    import zipfile
    u, cid = camp
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('manifest.json', json.dumps({'version': 1, 'groups': [], 'characters': [{'name': 'Imp', 'notes': json.dumps([UNTERMINATED + '<script>alert(2)</script>'])}]}))
    u.post(f'/campaigns/{cid}/import/data', data={'import_file': (io.BytesIO(buf.getvalue()), 'x.zip')}, content_type='multipart/form-data')
    notes = q('SELECT notes FROM characters WHERE campaign_id = ? AND name = ?', cid, 'Imp')[0]['notes']
    assert 'onerror' not in notes and '<script' not in notes
