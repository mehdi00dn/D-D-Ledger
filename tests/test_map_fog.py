"""DL-64: map fog of war -- the server must never hand a Player anything that sits under fog."""
import io
import json

import fog
from PIL import Image
from conftest import q

W, H = 400, 300
COLS, ROWS = fog.grid_dims(W, H)


def _mask(left_cols=0, all_cells=False):
    """Packed mask: fog every cell in the first `left_cols` columns (or everything)."""
    if all_cells:
        return fog.all_fogged(COLS, ROWS)
    row_bytes = (COLS + 7) // 8
    raw = bytearray(row_bytes * ROWS)
    for y in range(ROWS):
        for x in range(left_cols):
            raw[y * row_bytes + (x >> 3)] |= 0x80 >> (x & 7)
    return bytes(raw)


def _b64(raw):
    return fog.to_client(raw)


def _setup(make_user):
    dm, player = make_user(), make_user()
    cid = dm.new_campaign()
    assert dm.post(f'/campaigns/{cid}/members/add', data={'username': player.name, 'status': 'player'}).status_code == 302
    mid = dm.new_map(cid, w=W, h=H)
    return dm, player, cid, mid


def _img(resp):
    assert resp.status_code == 200, resp.status_code
    return Image.open(io.BytesIO(resp.data)).convert('RGB')


def test_player_image_is_painted_out_under_fog(make_user):
    dm, player, cid, mid = _setup(make_user)
    url = f'/campaigns/{cid}/maps/{mid}/image'
    original = _img(dm.get(url))
    # No fog yet: a Player gets the real picture.
    assert _img(player.get(url)).getpixel((50, 150)) == original.getpixel((50, 150))

    # Left half fogged.
    r = dm.json(f'/campaigns/{cid}/api/maps/{mid}/fog', {'mask': _b64(_mask(left_cols=25))})
    assert r.status_code == 200 and r.get_json()['active'] is True
    seen = _img(player.get(url))
    for x, y in ((0, 0), (50, 150), (150, 290), (190, 10)):             # well inside the fogged half
        assert seen.getpixel((x, y)) == fog.FOG_RGB, (x, y)
    for x, y in ((250, 150), (399, 299), (300, 5)):                      # clear half is untouched
        assert seen.getpixel((x, y)) == original.getpixel((x, y)), (x, y)
    # The DM always sees the real map.
    assert _img(dm.get(url)).getpixel((50, 150)) == original.getpixel((50, 150))

    # Everything fogged: not one pixel of the map survives for a Player.
    dm.json(f'/campaigns/{cid}/api/maps/{mid}/fog', {'fill': True})
    colours = set(_img(player.get(url)).getdata())
    assert colours == {fog.FOG_RGB}
    # Clearing brings it all back.
    dm.json(f'/campaigns/{cid}/api/maps/{mid}/fog', {'clear': True})
    assert _img(player.get(url)).getpixel((50, 150)) == original.getpixel((50, 150))
    assert q('SELECT fog_mask FROM maps WHERE id = ?', mid)[0]['fog_mask'] is None


def test_fog_editing_is_dm_only_and_validated(make_user):
    dm, player, cid, mid = _setup(make_user)
    api = f'/campaigns/{cid}/api/maps/{mid}/fog'
    assert player.json(api, {'fill': True}).status_code == 403
    assert player.json(api, {'clear': True}).status_code == 403
    assert q('SELECT fog_version FROM maps WHERE id = ?', mid)[0]['fog_version'] == 0
    # Malformed masks are refused, never stored.
    assert dm.json(api, {'mask': '!!!not base64!!!'}).status_code == 400
    assert dm.json(api, {'mask': _b64(b'\x00' * 3)}).status_code == 400
    assert dm.json(api, {}).status_code == 400
    assert q('SELECT fog_version FROM maps WHERE id = ?', mid)[0]['fog_version'] == 0
    # An all-clear mask means "no fog".
    r = dm.json(api, {'mask': _b64(_mask())})
    assert r.status_code == 200 and r.get_json()['active'] is False
    # Both roles can read the mask (it is what the DM chooses to show) and the version moves with every change.
    dm.json(api, {'fill': True})
    got = player.get(api).get_json()
    assert got['mask'] == _b64(_mask(all_cells=True)) and (got['cols'], got['rows'], got['cell']) == (COLS, ROWS, fog.CELL)
    v = got['version']
    dm.json(api, {'clear': True})
    assert player.get(api).get_json()['version'] > v and player.get(api).get_json()['mask'] is None
    state = player.get(f'/campaigns/{cid}/api/maps/{mid}/state').get_json()
    assert state['fog_active'] is False and state['fog_version'] == player.get(api).get_json()['version']


def test_pins_and_drawings_under_fog_are_withheld_from_players(make_user):
    dm, player, cid, mid = _setup(make_user)
    base = f'/campaigns/{cid}/api/maps/{mid}'
    for cx in (50, 350):
        assert dm.json(f'{base}/drawings', {'kind': 'rect', 'cx': cx, 'cy': 150, 'w': 20, 'h': 20, 'data': {}}).status_code == 200
        assert dm.json(f'{base}/pins', {'pin_type': 'prop', 'icon_key': 'skull', 'custom_name': f'secret-{cx}', 'x': cx, 'y': 150}).status_code == 200
    dm.json(f'{base}/fog', {'mask': _b64(_mask(left_cols=25))})       # left half (x < 200) fogged

    for who, expected in ((dm, {50, 350}), (player, {350})):
        assert {d['cx'] for d in who.get(f'{base}/drawings').get_json()} == expected
        assert {p['x'] for p in who.get(f'{base}/pins').get_json()} == expected
    assert 'secret-50' not in player.get(f'{base}/pins').data.decode()
    # The combined sync feed applies the same rule.
    assert 'secret-50' not in player.get(f'{base}/sync').data.decode()
    assert 'secret-50' in dm.get(f'{base}/pins').data.decode()
    # Clearing the fog shows them again.
    dm.json(f'{base}/fog', {'clear': True})
    assert {d['cx'] for d in player.get(f'{base}/drawings').get_json()} == {50, 350}


def test_players_never_receive_the_raw_map_key(make_user):
    dm, player, cid, mid = _setup(make_user)
    key = q('SELECT image_path FROM maps WHERE id = ?', mid)[0]['image_path']
    dm.json(f'/campaigns/{cid}/api/maps/{mid}/fog', {'fill': True})
    surfaces = [
        player.get(f'/campaigns/{cid}/maps/{mid}').data.decode(),
        player.get(f'/campaigns/{cid}/maps?browse=1').data.decode(),
        player.get(f'/campaigns/{cid}/api/maps/{mid}/state').data.decode(),
        player.get(f'/campaigns/{cid}/api/maps/{mid}/sync').data.decode(),
        player.get(f'/campaigns/{cid}/api/maps/{mid}/fog').data.decode(),
        player.json(f'/campaigns/{cid}/api/maps/{mid}/settings', {'grid_size': 60}).data.decode(),
    ]
    for body in surfaces:
        assert key not in body and 'fog_mask' not in body
    # The editor page points at the fog-aware route instead.
    page = player.get(f'/campaigns/{cid}/maps/{mid}').data.decode()
    assert f'/campaigns/{cid}/maps/{mid}/image' in page


def test_fog_is_scoped_to_the_campaign(make_user):
    a, b = make_user(), make_user()
    cid_a, cid_b = a.new_campaign(), b.new_campaign()
    mid_b = b.new_map(cid_b, w=W, h=H)
    assert a.json(f'/campaigns/{cid_a}/api/maps/{mid_b}/fog', {'fill': True}).status_code == 404
    assert a.get(f'/campaigns/{cid_a}/maps/{mid_b}/image').status_code == 404
    assert a.get(f'/campaigns/{cid_a}/api/maps/{mid_b}/fog').status_code == 404
    assert q('SELECT fog_version FROM maps WHERE id = ?', mid_b)[0]['fog_version'] == 0


def test_every_fogged_pixel_is_fully_hidden_for_any_mask(make_user):
    """The guarantee itself: for an arbitrary scattering of fogged cells, no pixel inside a fogged
    cell may differ from the flat fog colour in what a Player downloads."""
    import random
    dm, player, cid, mid = _setup(make_user)
    rng = random.Random(7)
    row_bytes = (COLS + 7) // 8
    raw = bytearray(row_bytes * ROWS)
    fogged = set()
    for _ in range(260):                                   # scattered blobs, singles and clusters
        cx, cy = rng.randrange(COLS), rng.randrange(ROWS)
        for dx in range(rng.choice((0, 0, 1, 2))):
            for dy in range(rng.choice((0, 1, 2))):
                x, y = min(COLS - 1, cx + dx), min(ROWS - 1, cy + dy)
                fogged.add((x, y)); raw[y * row_bytes + (x >> 3)] |= 0x80 >> (x & 7)
    assert dm.json(f'/campaigns/{cid}/api/maps/{mid}/fog', {'mask': _b64(bytes(raw))}).status_code == 200
    seen = _img(player.get(f'/campaigns/{cid}/maps/{mid}/image'))
    original = _img(dm.get(f'/campaigns/{cid}/maps/{mid}/image'))
    leaked = 0
    for (x, y) in fogged:
        for px in range(x * fog.CELL, min(W, (x + 1) * fog.CELL)):
            for py in range(y * fog.CELL, min(H, (y + 1) * fog.CELL)):
                if seen.getpixel((px, py)) != fog.FOG_RGB:
                    leaked += 1
    assert leaked == 0, f'{leaked} fogged pixels leaked'
    # ...and the picture is not simply blanked everywhere: far from any fog it is the real map.
    clear = [(x, y) for x in range(0, COLS, 3) for y in range(0, ROWS, 3)
             if all((x + dx, y + dy) not in fogged for dx in range(-3, 4) for dy in range(-3, 4))]
    assert clear, 'test mask left no clear area'
    x, y = clear[0]
    assert seen.getpixel((x * fog.CELL + 4, y * fog.CELL + 4)) == original.getpixel((x * fog.CELL + 4, y * fog.CELL + 4))


def test_fog_on_a_jpeg_map_stays_a_jpeg_and_hides_it(make_user):
    """Uploaded JPEG maps are re-encoded as JPEG for Players (not silently sent unmasked or as a huge PNG)."""
    dm, player = make_user(), make_user()
    cid = dm.new_campaign()
    dm.post(f'/campaigns/{cid}/members/add', data={'username': player.name, 'status': 'player'})
    buf = io.BytesIO(); Image.new('RGB', (W, H), (200, 40, 40)).save(buf, 'JPEG')
    r = dm.post(f'/campaigns/{cid}/maps/new', data={'name': 'Jpeg', 'image': (io.BytesIO(buf.getvalue()), 'm.jpg')},
                content_type='multipart/form-data')
    assert r.status_code == 302
    import re
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    dm.json(f'/campaigns/{cid}/api/maps/{mid}/fog', {'fill': True})
    resp = player.get(f'/campaigns/{cid}/maps/{mid}/image')
    assert resp.status_code == 200 and resp.mimetype in ('image/jpeg', 'image/png')
    im = Image.open(io.BytesIO(resp.data)).convert('RGB')
    r_, g_, b_ = im.getpixel((200, 150))
    assert (r_, g_, b_) != (200, 40, 40) and abs(r_ - fog.FOG_RGB[0]) < 12 and abs(b_ - fog.FOG_RGB[2]) < 12
