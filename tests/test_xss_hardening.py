"""DL-32: nothing a member types may become markup/script in another member's browser."""
import os
import pytest
from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')

JS_DIR = os.path.join(os.path.dirname(__file__), '..', 'static', 'js')


def _pins(cid, mid):
    return q('SELECT pin_type, icon_key, custom_name FROM map_pins WHERE map_id = ?', mid)


def test_pin_api_only_stores_known_types_and_icons(make_user):
    dm, pl = make_user(), make_user(); cid = dm.new_campaign(); dm.add_member(cid, pl); mid = dm.new_map(cid)
    url = f'/campaigns/{cid}/api/maps/{mid}/pins'
    base = {'pin_type': 'prop', 'icon_key': 'paw', 'x': 5, 'y': 5}
    for bad in ({'pin_type': '" onmouseover="alert(1)'}, {'pin_type': 'character'}, {'icon_key': 'x" onerror="alert(1)'},
                {'icon_key': '../../uploads/evil'}, {'icon_key': 7}, {'custom_name': 'x' * 81}, {'custom_name': ['a']}, {'custom_name': 5}):
        r = pl.json(url, {**base, **bad})
        assert r.status_code == 400, (bad, r.status_code)
    assert _pins(cid, mid) == []                                     # nothing was stored
    ok = pl.json(url, {**base, 'custom_name': '  Rex\n '})
    assert ok.status_code == 200 and _pins(cid, mid)[0]['custom_name'] == 'Rex'
    # a name is plain text: markup characters are kept as TEXT (the pages escape them), not rejected or executed
    assert pl.json(url, {**base, 'custom_name': '<b>hi</b>'}).status_code == 200


def test_pin_rename_is_validated_too(make_user):
    dm = make_user(); cid = dm.new_campaign(); mid = dm.new_map(cid)
    pid = dm.json(f'/campaigns/{cid}/api/maps/{mid}/pins', {'pin_type': 'prop', 'icon_key': 'paw', 'x': 1, 'y': 1}).get_json()['id']
    upd = f'/campaigns/{cid}/api/maps/{mid}/pins/{pid}/update'
    assert dm.json(upd, {'custom_name': 'y' * 200}).status_code == 400
    assert dm.json(upd, {'custom_name': {'a': 1}}).status_code == 400
    assert dm.json(upd, {'custom_name': 'Fido'}).status_code == 200
    assert q('SELECT custom_name FROM map_pins WHERE id = ?', pid)[0]['custom_name'] == 'Fido'


def test_notification_links_must_be_in_app_paths(appmod, make_user):
    import database
    u = make_user(); uid = q('SELECT id FROM users WHERE username = ?', u.name)[0]['id']
    db = database.get_db()
    for link in ('/campaigns/1/edit', 'https://evil.example/x', '//evil.example/x', 'javascript:alert(1)'):
        appmod.notify(db, uid, 'test', 'T', link=link)
    db.commit(); db.close()
    links = [r['link'] for r in q("SELECT link FROM notifications WHERE user_id = ? AND kind = 'test' ORDER BY id", uid)]
    assert links == ['/campaigns/1/edit', None, None, None]


@pytest.mark.parametrize('name', ['battle.js', 'map.js'])
def test_client_escape_helper_covers_quotes(name):
    src = open(os.path.join(JS_DIR, name)).read()
    assert "textContent = str == null" not in src, 'escapeHtml must escape quotes (attribute context), not use textContent->innerHTML'
    assert '&quot;' in src and '&#39;' in src
    # group names / colours / upload paths never go into markup raw
    assert '${c.group_name ||' not in src.replace("${escapeHtml(c.group_name ||", '')
    assert '/uploads/${c.avatar_path}' not in src and '/uploads/${p.avatar_path}' not in src and '/uploads/${s.image_path}' not in src
