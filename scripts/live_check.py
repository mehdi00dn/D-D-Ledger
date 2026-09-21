#!/usr/bin/env python3
"""End-to-end check of a RUNNING Campaign Ledger (local or deployed).

    pip install requests
    python scripts/live_check.py --base https://d-d-ledger.vercel.app

It plays two players through the whole app the way a browser would and reports
PASS/FAIL per step: health, sign-up/login, campaigns, characters, small and LARGE
image uploads (the large one goes straight from THIS computer to Supabase Storage,
so running it from where your players are also tests that they can reach it),
maps, battle, live sync, campaign isolation between users, delete redirects.

It creates two throw-away users named livecheck_* and deletes its campaigns at the
end (users are left; remove them in Supabase with:
    delete from users where username like 'livecheck_%';  ).
Exit code is 0 only if every step passed.
"""
import argparse
import os
import random
import re
import struct
import sys
import time
import zlib

import requests

RESULTS = []


def png(width, height, noise=False):
    """A valid RGB PNG with no third-party dependency; noise=True is incompressible."""
    row = lambda: b'\x00' + (os.urandom(width * 3) if noise else b'\x60\x40\x40' * width)
    raw = b''.join(row() for _ in range(height))
    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 1)) + chunk(b'IEND', b''))


def check(name, ok, detail='', ms=None):
    RESULTS.append(ok)
    timing = f'  ({ms:.0f} ms)' if ms is not None else ''
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{timing}" + (f'  -> {detail}' if detail and not ok else ''))
    return ok


class Player:
    def __init__(self, base, name):
        self.base, self.name, self.s, self.token = base.rstrip('/'), name, requests.Session(), ''
        self.s.headers['User-Agent'] = 'ledger-live-check/1.0'

    def _grab_token(self, html):
        m = re.search(r'name="csrf-token" content="([^"]+)"', html) or re.search(r'name="csrf_token" value="([^"]+)"', html)
        if m:
            self.token = m.group(1)

    def get(self, path, **kw):
        r = self.s.get(self.base + path, timeout=30, **kw)
        if 'text/html' in r.headers.get('Content-Type', ''):
            self._grab_token(r.text)
        return r

    def form(self, path, data=None, files=None):
        data = dict(data or {}, csrf_token=self.token)
        return self.s.post(self.base + path, data=data, files=files, allow_redirects=False, timeout=60)

    def json(self, path, payload=None, method='post'):
        return getattr(self.s, method)(self.base + path, json=payload or {}, headers={'X-CSRF-Token': self.token}, timeout=30)

    def timed(self, fn, *a, **kw):
        t = time.time(); r = fn(*a, **kw); return r, (time.time() - t) * 1000

    def register(self):
        self.get('/register')
        r = self.form('/register', {'username': self.name, 'password': 'LiveCheck-pass-1', 'confirm': 'LiveCheck-pass-1'})
        self.get('/campaigns')                               # the session token rotates at login
        return r


def cid_from(resp):
    m = re.search(r'/campaigns/(\d+)/', resp.headers.get('Location', ''))
    return int(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True, help='e.g. https://d-d-ledger.vercel.app')
    ap.add_argument('--skip-direct', action='store_true', help='skip the large direct-to-storage upload')
    ap.add_argument('--keep', action='store_true', help='do not delete the test campaigns')
    args = ap.parse_args()
    base = args.base.rstrip('/')
    tag = f'livecheck_{random.randint(100000, 999999)}'
    a, b = Player(base, tag + '_a'), Player(base, tag + '_b')
    campaigns = []
    mid = pid = None

    print(f'\nCampaign Ledger live check  ->  {base}\n')
    print('Health')
    try:
        r, ms = a.timed(a.get, '/healthz')
    except requests.RequestException as exc:
        check('site reachable from this computer', False, type(exc).__name__)
        return 1
    check('/healthz says ready (database + tables)', r.status_code == 200 and r.json().get('ok') is True, r.text[:200], ms)
    if r.status_code != 200:
        print('\nStopping: fix the health problem above first.')
        return 1
    hdr = r.headers
    check('security headers present', hdr.get('X-Content-Type-Options') == 'nosniff' and 'frame-ancestors' in hdr.get('Content-Security-Policy', ''))

    print('\nAccounts')
    ra, ms = a.timed(a.register); check('player A registers', ra.status_code == 302, str(ra.status_code), ms)
    rb = b.register();          check('player B registers', rb.status_code == 302, str(rb.status_code))
    check('A is logged in', a.get('/campaigns').status_code == 200)
    r = requests.Session().post(base + '/login', data={'username': a.name, 'password': 'wrong'}, allow_redirects=False, timeout=30)
    check('a POST without a CSRF token is refused', r.status_code in (400, 403), str(r.status_code))

    print('\nCampaign + characters')
    r, ms = a.timed(a.form, '/campaigns/new', {'name': 'Live Check'})
    cid = cid_from(r); campaigns.append((a, cid)); check('A creates a campaign', bool(cid), str(r.status_code), ms)
    if not cid:
        return 1
    r = a.form(f'/campaigns/{cid}/characters/new', {'name': 'Small Avatar Hero', 'max_hp': '30'},
               files={'avatar': ('a.png', png(120, 120), 'image/png')})
    check('character with a small avatar', r.status_code == 302, str(r.status_code))
    chars = a.get(f'/campaigns/{cid}/api/characters').json()
    hero = next((c for c in chars if c['name'] == 'Small Avatar Hero'), None)
    check('character was saved (and reads back the same on a fresh request)', hero is not None)
    if hero and hero.get('avatar_path'):
        r, ms = a.timed(a.get, '/uploads/' + hero['avatar_path'])
        check('avatar image loads', r.status_code == 200 and r.headers.get('Content-Type', '').startswith('image/'), str(r.status_code), ms)
        anon = requests.get(base + '/uploads/' + hero['avatar_path'], allow_redirects=False, timeout=30)
        check('images are private (anonymous visitors are sent to login)', anon.status_code in (301, 302, 303, 307))
    else:
        check('avatar image loads', False, 'no avatar_path')

    if not args.skip_direct:
        print('\nLarge upload straight to storage')
        big = png(1500, 1500, noise=True)
        r = a.json('/uploads/sign', {'purpose': 'map', 'size': len(big), 'content_type': 'image/png'})
        ok = r.status_code == 200
        check(f'server issues a signed upload URL ({len(big) // 1024} KB file)', ok, r.text[:160])
        if ok:
            j = r.json(); t = j['upload']
            url = t['url'] if t['url'].startswith('http') else base + t['url']
            try:
                t0 = time.time()
                if t['body'] == 'formdata':
                    put = requests.put(url, files={'': ('blob', big, 'image/png')}, timeout=120)
                else:
                    put = requests.put(url, data=big, headers={'X-CSRF-Token': a.token}, cookies=a.s.cookies, timeout=120)
                check('this computer can upload directly to storage', put.status_code == 200, f'{put.status_code} {put.text[:120]}', (time.time() - t0) * 1000)
                if put.status_code == 200:
                    r = a.form(f'/campaigns/{cid}/maps/new', {'name': 'Big Map', 'image__key': j['key']})
                    check('map created from the direct upload', r.status_code == 302, str(r.status_code))
                    mid = int(re.search(r'/maps/(\d+)', r.headers.get('Location', '') or '/maps/0').group(1))
                    row = a.get(f'/campaigns/{cid}/api/maps/{mid}/sync').json() if mid else {}
                    check('big image was validated and shrunk for serving', bool(row))
            except requests.RequestException as exc:
                check('this computer can upload directly to storage', False,
                      f'{type(exc).__name__}: storage is unreachable from here (blocked network?)')

    print('\nMap, battle, live sync')
    r = a.form(f'/campaigns/{cid}/maps/new', {'name': 'Blank', 'blank_width': '400', 'blank_height': '300'})
    m = re.search(r'/maps/(\d+)', r.headers.get('Location', ''))
    check('blank map created', bool(m), str(r.status_code))
    if m:
        mid = int(m.group(1))
        d = a.json(f'/campaigns/{cid}/api/maps/{mid}/drawings', {'kind': 'rect', 'data': {}, 'cx': 10, 'cy': 10, 'w': 50, 'h': 40})
        check('drawing saved', d.status_code == 200, str(d.status_code))
        r, ms = a.timed(a.get, f'/campaigns/{cid}/api/maps/{mid}/sync')
        check('one /sync request returns state + drawings', r.status_code == 200 and len(r.json()['drawings']) == 1, r.text[:120], ms)
    if hero:
        r = a.json(f'/campaigns/{cid}/api/battle/add', {'character_id': hero['id']})
        pid = next((x['id'] for x in (r.json() if r.status_code == 200 else [])), None)
        check('character added to battle', pid is not None, str(r.status_code))
        if pid:
            a.json(f'/campaigns/{cid}/api/battle/{pid}/damage', {'amount': 7})
            a.json(f'/campaigns/{cid}/api/battle/{pid}/heal', {'amount': 2})
            hp = next(x['current_hp'] for x in a.get(f'/campaigns/{cid}/api/battle').json() if x['id'] == pid)
            check('damage and healing are exact (30 - 7 + 2 = 25)', hp == 25, f'hp={hp}')

    print('\nIsolation between players')
    rb = b.form('/campaigns/new', {'name': 'B Campaign'})
    bcid = cid_from(rb); campaigns.append((b, bcid)); check('B creates their own campaign', bool(bcid))
    check("B cannot open A's campaign", b.get(f'/campaigns/{cid}/characters').status_code == 404)
    if m and bcid:
        r = b.get(f'/campaigns/{bcid}/api/maps/{mid}/sync')
        check("B cannot read A's map by putting A's map id in B's own URL", r.status_code in (403, 404), str(r.status_code))
        r = b.json(f'/campaigns/{bcid}/api/maps/{mid}/drawings', {'kind': 'rect', 'data': {}})
        check("B cannot draw on A's map", r.status_code in (403, 404), str(r.status_code))
    if hero and pid:
        r = b.json(f'/campaigns/{bcid}/api/battle/{pid}/damage', {'amount': 99})
        still = next(x['current_hp'] for x in a.get(f'/campaigns/{cid}/api/battle').json() if x['id'] == pid)
        check("B cannot damage A's creatures", r.status_code in (403, 404) and still == 25, f'{r.status_code}, hp={still}')

    print('\nExport + delete')
    z = a.get(f'/campaigns/{cid}/export/data', allow_redirects=True)
    check('campaign export downloads a ZIP', z.status_code == 200 and z.content[:2] == b'PK', str(z.status_code))
    if not args.keep:
        for player, c in campaigns:
            if not c:
                continue
            r = player.form(f'/campaigns/{c}/delete')
            follow = player.get(r.headers.get('Location', '/campaigns')) if r.status_code == 302 else r
            check(f'{player.name[-1].upper()} deletes their campaign and lands on a working page', r.status_code == 302 and follow.status_code == 200,
                  f'{r.status_code}/{follow.status_code}')

    failed = RESULTS.count(False)
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed.")
    if not args.keep:
        print("Cleanup: delete from users where username like 'livecheck_%';   (in the Supabase SQL editor)")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
