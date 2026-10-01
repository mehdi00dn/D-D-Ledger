"""Exhaustive campaign-isolation checks.

For EVERY route that carries a resource id, a member of campaign B calls it -- from inside
campaign B's URL -- with the ids of campaign A's resources.  It must be a 404 and must not
change anything.  Because the routes are enumerated from the app's URL map, a route added
tomorrow is covered automatically; one that introduces a brand-new kind of URL argument
fails test_every_url_argument_is_covered_by_the_policy until the policy learns about it.
"""
import io, re
import pytest
from conftest import q, png_bytes

SCOPED = {'map_id', 'char_id', 'group_id', 'pid', 'drawing_id', 'pin_id', 'sheet_id'}


def build_world(u):
    cid = u.new_campaign()
    gid = u.new_group(cid)
    ch = u.new_character(cid, name='Owner Char', max_hp=50, group_id=gid)
    u.post(f'/campaigns/{cid}/characters/{ch}/edit', data={'name': 'Owner Char', 'sheets': (io.BytesIO(png_bytes(40, 40)), 's.png')}, content_type='multipart/form-data')
    sheet = q('SELECT id FROM character_sheets WHERE character_id = ?', ch)[0]['id']
    mid = u.new_map(cid)
    did = u.json(f'/campaigns/{cid}/api/maps/{mid}/drawings', {'kind': 'rect', 'data': {}, 'cx': 1, 'cy': 1, 'w': 5, 'h': 5}).get_json()['id']
    pin = u.json(f'/campaigns/{cid}/api/maps/{mid}/pins', {'pin_type': 'prop', 'icon_key': 'skull', 'x': 1, 'y': 1}).get_json()['id']
    pid = u.add_to_battle(cid, ch)
    uid = q('SELECT id FROM users WHERE username = ?', u.name)[0]['id']
    return dict(cid=cid, group_id=gid, char_id=ch, sheet_id=sheet, map_id=mid, drawing_id=did, pin_id=pin, pid=pid, user_id=uid)


def snapshot(cid):
    return {
        'members': q('SELECT user_id, status FROM campaign_members WHERE campaign_id = ? ORDER BY user_id', cid),
        'chars': q('SELECT id, name, group_id, avatar_path, notes, max_hp FROM characters WHERE campaign_id = ? ORDER BY id', cid),
        'groups': q('SELECT id, name, color FROM groups WHERE campaign_id = ? ORDER BY id', cid),
        'maps': q('SELECT * FROM maps WHERE campaign_id = ? ORDER BY id', cid),
        'sheets': q('SELECT cs.* FROM character_sheets cs JOIN characters c ON c.id = cs.character_id WHERE c.campaign_id = ? ORDER BY cs.id', cid),
        'draw': q('SELECT d.* FROM map_drawings d JOIN maps m ON m.id = d.map_id WHERE m.campaign_id = ? ORDER BY d.id', cid),
        'pins': q('SELECT p.* FROM map_pins p JOIN maps m ON m.id = p.map_id WHERE m.campaign_id = ? ORDER BY p.id', cid),
        'battle': q('SELECT bp.* FROM battle_participants bp JOIN characters c ON c.id = bp.character_id WHERE c.campaign_id = ? ORDER BY bp.id', cid),
    }


def routes_with_ids(appmod):
    out = []
    for rule in appmod.app.url_map.iter_rules():
        if 'campaign_id' in rule.arguments and (rule.arguments - {'campaign_id'}):
            for method in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
                out.append((rule, method))
    return out


def fill(rule, cid, ids):
    url = rule.rule.replace('<int:campaign_id>', str(cid))
    for arg in rule.arguments - {'campaign_id'}:
        url = url.replace(f'<int:{arg}>', str(ids[arg]))
    return url


def test_every_url_argument_is_covered_by_the_policy(appmod):
    unknown = {a for rule in appmod.app.url_map.iter_rules() for a in rule.arguments} - appmod.SCOPED_URL_ARGS
    assert not unknown, f'new URL argument(s) {unknown}: teach enforce_resource_scope how to verify them'


def test_matrix_covers_a_meaningful_number_of_routes(appmod):
    assert len(routes_with_ids(appmod)) >= 35


def test_cross_campaign_access_is_denied_on_every_route(appmod, make_user):
    alice, bob = make_user(), make_user()
    a = build_world(alice)
    cb = bob.new_campaign()
    before_a, before_b = snapshot(a['cid']), snapshot(cb)
    checked = denied = 0
    for rule, method in routes_with_ids(appmod):
        url = fill(rule, cb, a)                                            # Bob's campaign, Alice's ids
        resp = bob.get(url) if method == 'GET' else bob.post(url, json={'amount': 1, 'value': 1, 'name': 'x', 'status': 'dm'})
        checked += 1
        if rule.arguments & SCOPED:
            assert resp.status_code == 404, f'{method} {url} -> {resp.status_code} (endpoint {rule.endpoint})'
            denied += 1
    assert checked >= 37 and denied >= 34
    assert snapshot(a['cid']) == before_a, 'Alice\'s data changed'
    assert snapshot(cb) == before_b, 'Bob\'s own campaign changed'


def test_non_member_cannot_reach_a_campaign_by_any_route(appmod, make_user):
    alice, mallory = make_user(), make_user()
    a = build_world(alice)
    before = snapshot(a['cid'])
    for rule in appmod.app.url_map.iter_rules():
        if 'campaign_id' not in rule.arguments:
            continue
        for method in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
            url = fill(rule, a['cid'], a)
            resp = mallory.get(url) if method == 'GET' else mallory.post(url, json={'amount': 1})
            assert resp.status_code in (403, 404), f'{method} {url} -> {resp.status_code}'
    assert snapshot(a['cid']) == before


def test_owner_can_still_use_every_id_route_in_their_own_campaign(appmod, make_user):
    """The matrix above must not pass merely because everything is broken."""
    alice = make_user(); a = build_world(alice)
    ok = 0
    for rule, method in routes_with_ids(appmod):
        if method != 'GET' or rule.endpoint in ('character_export', 'group_export'):
            continue
        resp = alice.get(fill(rule, a['cid'], a))
        assert resp.status_code in (200, 302), f'{rule.rule} -> {resp.status_code}'
        ok += 1
    assert ok >= 8


def test_children_must_belong_to_the_parent_in_the_url(make_user):
    u = make_user(); cid = u.new_campaign()
    m1, m2 = u.new_map(cid, 'One'), u.new_map(cid, 'Two')
    d1 = u.json(f'/campaigns/{cid}/api/maps/{m1}/drawings', {'kind': 'rect', 'data': {}, 'w': 1, 'h': 1}).get_json()['id']
    p1 = u.json(f'/campaigns/{cid}/api/maps/{m1}/pins', {'pin_type': 'prop', 'icon_key': 'x', 'x': 1, 'y': 1}).get_json()['id']
    assert u.json(f'/campaigns/{cid}/api/maps/{m2}/drawings/{d1}/delete').status_code == 404      # drawing of map 1 via map 2
    assert u.json(f'/campaigns/{cid}/api/maps/{m2}/drawings/{d1}/update', {'cx': 5}).status_code == 404
    assert u.json(f'/campaigns/{cid}/api/maps/{m2}/pins/{p1}/delete').status_code == 404
    assert q('SELECT COUNT(*) AS n FROM map_drawings WHERE id = ?', d1)[0]['n'] == 1
    c1, c2 = u.new_character(cid, name='C1'), u.new_character(cid, name='C2')
    u.post(f'/campaigns/{cid}/characters/{c1}/edit', data={'name': 'C1', 'sheets': (io.BytesIO(png_bytes(30, 30)), 's.png')}, content_type='multipart/form-data')
    s1 = q('SELECT id FROM character_sheets WHERE character_id = ?', c1)[0]['id']
    assert u.post(f'/campaigns/{cid}/characters/{c2}/sheets/{s1}/delete').status_code == 404       # sheet of C1 via C2
    assert q('SELECT COUNT(*) AS n FROM character_sheets WHERE id = ?', s1)[0]['n'] == 1


def test_ids_inside_request_bodies_are_scoped_too(make_user):
    alice, bob = make_user(), make_user()
    a = build_world(alice); cb = bob.new_campaign()
    r = bob.json(f'/campaigns/{cb}/api/battle/add', {'character_id': a['char_id']})                 # Alice's character
    assert r.status_code == 404 and q('SELECT COUNT(*) AS n FROM battle_participants WHERE character_id = ?', a['char_id'])[0]['n'] == 1
    assert bob.json(f'/campaigns/{cb}/api/battle/add-group', {'group_id': a['group_id']}).get_json() == []
    bob.post(f'/campaigns/{cb}/characters/new', data={'name': 'Spy', 'group_id': str(a['group_id'])}, content_type='multipart/form-data')
    assert q('SELECT group_id FROM characters WHERE campaign_id = ? AND name = ?', cb, 'Spy')[0]['group_id'] is None
    ch = bob.new_character(cb, name='Mine')
    bob.post(f'/campaigns/{cb}/characters/{ch}/edit', data={'name': 'Mine', 'group_id': str(a['group_id'])}, content_type='multipart/form-data')
    assert q('SELECT group_id FROM characters WHERE id = ?', ch)[0]['group_id'] is None


def test_battle_clear_only_touches_the_current_campaign(make_user):
    alice, bob = make_user(), make_user()
    a = build_world(alice); cb = bob.new_campaign()
    bob.add_to_battle(cb, bob.new_character(cb))
    bob.json(f'/campaigns/{cb}/api/battle/clear')
    assert len(alice.battle(a['cid'])) == 1 and bob.battle(cb) == []


def test_a_stale_link_to_a_deleted_thing_still_redirects_gracefully(make_user):
    """Nonexistent (as opposed to foreign) ids keep the friendly behaviour: no 404 wall after a delete."""
    u = make_user(); cid = u.new_campaign(); ch = u.new_character(cid)
    u.post(f'/campaigns/{cid}/characters/{ch}/delete')
    assert u.get(f'/campaigns/{cid}/characters/{ch}').status_code in (200, 302)
    assert u.get(f'/campaigns/{cid}/characters/{ch}/edit').status_code in (200, 302)
