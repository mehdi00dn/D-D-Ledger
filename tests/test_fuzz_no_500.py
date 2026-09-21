"""Hostile input must never crash the server.  Every state-changing route of the app, called as a
legitimate DM with valid ids, is hit with garbage; the only acceptable answers are < 500."""
import io, json
import pytest
from conftest import q, png_bytes
from test_authorization_matrix import build_world, fill

NASTY_JSON = [
    {}, [], 'text', 12345, None, True, [1, 2, 3], [[]], {'a': {'b': {'c': [None]}}},
    {'amount': 'abc', 'value': 'x', 'x': 'NaN', 'y': 'Infinity', 'w': '1e999', 'h': None, 'cx': [], 'cy': {}, 'kind': 123, 'data': 'not-json',
     'name': {'a': 1}, 'status': 7, 'character_id': 'abc', 'group_id': [1], 'pin_type': 5, 'icon_key': {}, 'custom_name': [1],
     'linked_to_battle': 'yes', 'grid_size': 'big', 'locked': 'maybe', 'rotation': 'spin', 'scale': '', 'fill_opacity': 'x', 'sort_order': 'first'},
    {'amount': 10 ** 30, 'value': -10 ** 30, 'x': 1e308, 'y': -1e308, 'w': 1e308, 'h': 1e-320, 'grid_size': 10 ** 12, 'cx': float('inf') if False else 1e999},
    {'amount': 1.5, 'value': 2.7, 'grid_offset_x': 3.5, 'grid_size': 0, 'grid_size ': 1},
    {'name': '\x00' * 5 + 'a' * 200000, 'username': '\x00', 'status': 'dm\x00', 'custom_name': 'z' * 100000, 'icon_key': '../../etc/passwd'},
    {'kind': 'line', 'data': {'pts': [[1, 2]] * 5000}, 'cx': 1, 'cy': 2, 'w': 3, 'h': 4, 'color': 'x' * 5000},
    {'purpose': 'avatar', 'size': 10 ** 25, 'content_type': ['a']},
    {'purpose': ['avatar'], 'size': {}, 'content_type': None},
    {'character_id': 10 ** 30}, {'character_id': -1}, {'group_id': 'DROP TABLE users;--'},
    {'username': "' OR 1=1 --", 'status': "player'; DROP TABLE users;--"},
    {'v\u200bal': '\u202e\ufeff\U0001f4a9'},
]
NASTY_FORMS = [
    {}, {'name': ''}, {'name': 'a\x00b', 'level': 'abc', 'max_hp': '99999999999999999999', 'is_npc': 'on', 'str_score': '-1e9', 'group_id': 'abc', 'notes': '[[[',
                        'color': 'javascript:alert(1)', 'description': 'x' * 300000, 'armor_class': '3.5', 'dex_score': '', 'int_score': '\x00', 'wis_score': ' 12 ', 'cha_score': '١٢'},
    {'name': 'z' * 500000}, {'status': 'god', 'username': '\x00', 'setting': 'x' * 10000, 'access_mode': 'public', 'blank_width': 'wide', 'blank_height': '-5'},
    {'blank_width': '99999999999999', 'blank_height': '0', 'name': 'm'}, {'notes': json.dumps([1, None, {'a': 1}, '<b>ok</b>', ['nested']])},
    {'notes': json.dumps({'not': 'a list'}), 'name': 'n'}, {'username': 'nobody-here', 'status': 'player'}, {'remove_avatar': '1', 'name': '\U0001f4a9'},
    {'setting_preset': 'Homebrew\x00', 'setting_custom': 'q' * 9999, 'status': 'archived', 'name': 'Zed'},
]
SKIP_ENDPOINTS = {'local_direct_upload', 'local_download', 'logout', 'register', 'login', 'campaign_delete'}   # need special tokens / would end the session


def post_rules(appmod):
    seen = set()
    for rule in appmod.app.url_map.iter_rules():
        if 'POST' in rule.methods and rule.endpoint not in SKIP_ENDPOINTS and rule.endpoint not in seen and not rule.arguments & {'token', 'filename'}:
            seen.add(rule.endpoint)
            yield rule


def hit(u, url, payload_kind, payload):
    if payload_kind == 'json':
        return u.c.post(url, json=payload)
    if payload_kind == 'form':
        return u.c.post(url, data=payload, content_type='multipart/form-data')
    if payload_kind == 'badjson':
        return u.c.post(url, data=payload, content_type='application/json')
    if payload_kind == 'file':
        return u.c.post(url, data={'avatar': (io.BytesIO(payload), 'x.png'), 'image': (io.BytesIO(payload), 'x.png'), 'sheets': (io.BytesIO(payload), 'x.png'),
                                   'import_file': (io.BytesIO(payload), 'x.zip'), 'name': 'f'}, content_type='multipart/form-data')
    return u.c.post(url)


def test_no_post_route_ever_returns_a_500(appmod, make_user):
    failures, calls, routes = [], 0, 0
    for rule in post_rules(appmod):
        routes += 1
        u = make_user(); w = build_world(u)
        if 'campaign_id' in rule.arguments:
            url = fill(rule, w['cid'], w)
        else:
            url = rule.rule
        attempts = [('json', p) for p in NASTY_JSON] + [('form', p) for p in NASTY_FORMS] + [('badjson', b'{'), ('badjson', b'\xff\xfe\x00'), ('badjson', b''), ('none', None),
                    ('file', b'not an image at all'), ('file', b'PK\x03\x04' + b'\x00' * 40), ('file', b'\x89PNG\r\n\x1a\n' + b'\x00' * 30)]
        for kind, payload in attempts:
            calls += 1
            r = hit(u, url, kind, payload)
            if r.status_code >= 500:
                failures.append((rule.endpoint, kind, str(payload)[:60], r.status_code))
    assert routes >= 35, routes
    assert not failures, f'{len(failures)} responses were 5xx, e.g. {failures[:8]}'


def test_bad_json_and_bad_numbers_are_400_not_500(camp):
    u, cid = camp
    ch = u.new_character(cid, name='Fuzz', max_hp=10); pid = u.add_to_battle(cid, ch)
    B = f'/campaigns/{cid}/api/battle/{pid}'
    assert u.c.post(f'{B}/damage', data='{', content_type='application/json').status_code == 400
    assert u.json(f'{B}/damage', {'amount': 'lots'}).status_code == 400
    assert u.json(f'{B}/initiative', {'value': 10 ** 30}).status_code == 400            # too big for the column
    assert u.json(f'{B}/damage', {'amount': 10 ** 30}).status_code == 200                # huge damage just kills; HP floors at 0
    assert u.json(f'{B}/damage', []).status_code == 400
    assert u.json(f'{B}/revive').status_code == 200 and u.json(f'{B}/heal', {'amount': 3}).status_code == 200      # valid input still works
    assert u.post(f'/campaigns/{cid}/characters/new', data={'name': 'x', 'level': 'abc'}, content_type='multipart/form-data').status_code == 400
    assert q('SELECT COUNT(*) AS n FROM characters WHERE name = ?', 'x')[0]['n'] == 0    # and nothing half-saved


def test_json_error_shape_for_api_and_text_for_pages(camp):
    u, cid = camp
    r = u.json(f'/campaigns/{cid}/api/battle/add', {'character_id': 'abc'})
    assert r.status_code in (400, 404) and r.get_json() is not None
    r = u.post(f'/campaigns/{cid}/characters/new', data={'name': 'x', 'level': 'abc'}, content_type='multipart/form-data')
    assert r.status_code == 400 and 'not valid' in r.get_data(as_text=True)
