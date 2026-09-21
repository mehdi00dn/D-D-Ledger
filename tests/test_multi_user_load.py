"""Concurrent users spread over two independent app processes (= two serverless
instances) sharing one database.  Nothing may be lost, duplicated or 404."""
import re, uuid, threading, itertools
import concurrent.futures as cf
import pytest, requests
from conftest import BACKEND, CsrfSession

pytestmark = pytest.mark.skipif(BACKEND != 'postgres', reason='needs a shared database')


def _user(inst_a):
    s = CsrfSession(inst_a.url); name = f'l{uuid.uuid4().hex[:9]}'
    assert s.post(inst_a.url + '/register', data={'username': name, 'password': 'secret12', 'confirm': 'secret12'}, allow_redirects=False).status_code == 302
    return s, name


def test_many_users_many_writes_consistent_on_both_instances(two_instances):
    a, b = two_instances
    insts = [a, b]
    N_USERS, N_CHARS = 6, 8

    def worker(i):
        s, name = _user(insts[i % 2])
        r = s.post(insts[i % 2].url + '/campaigns/new', data={'name': f'Camp {i}'}, allow_redirects=False)
        cid = int(re.search(r'/campaigns/(\d+)/', r.headers['Location']).group(1))
        for k in range(N_CHARS):
            inst = insts[(i + k) % 2]                       # every request may land on a different instance
            r = s.post(f'{inst.url}/campaigns/{cid}/characters/new', data={'name': f'c{i}-{k}', 'max_hp': '9'}, allow_redirects=False)
            assert r.status_code == 302, r.status_code
            seen = s.get(f'{insts[(i + k + 1) % 2].url}/campaigns/{cid}/api/characters')
            assert seen.status_code == 200, seen.status_code
            assert f'c{i}-{k}' in [c['name'] for c in seen.json()]      # read-your-write on the OTHER instance
        return cid, s

    with cf.ThreadPoolExecutor(N_USERS) as ex:
        results = list(ex.map(worker, range(N_USERS)))
    for cid, s in results:
        for inst in insts:
            names = sorted(c['name'] for c in s.get(f'{inst.url}/campaigns/{cid}/api/characters').json())
            assert len(names) == N_CHARS == len(set(names))                # none lost, none duplicated, same on both


def test_damage_from_both_instances_never_loses_a_hit(two_instances):
    a, b = two_instances; insts = [a, b]
    dm, _ = _user(a)
    cid = int(re.search(r'/campaigns/(\d+)/', dm.post(a.url + '/campaigns/new', data={'name': 'Arena'}, allow_redirects=False).headers['Location']).group(1))
    dm.post(f'{a.url}/campaigns/{cid}/characters/new', data={'name': 'Dummy', 'max_hp': '400'}, allow_redirects=False)
    chid = dm.get(f'{a.url}/campaigns/{cid}/api/characters').json()[0]['id']
    pid = dm.post(f'{a.url}/campaigns/{cid}/api/battle/add', json={'character_id': chid}).json()[0]['id']
    T, HITS = 8, 10
    def hitter(t):
        s = CsrfSession(a.url); s.cookies.update(dm.cookies)
        for k in range(HITS):
            r = s.post(f'{insts[(t + k) % 2].url}/campaigns/{cid}/api/battle/{pid}/damage', json={'amount': 1})
            assert r.status_code == 200
    with cf.ThreadPoolExecutor(T) as ex:
        list(ex.map(hitter, range(T)))
    hp = next(r for r in dm.get(f'{a.url}/campaigns/{cid}/api/battle').json() if r['id'] == pid)['current_hp']
    assert hp == 400 - T * HITS
