"""Brute-force protection, backed by the database so it works across serverless instances."""
import os
from unittest import mock
import pytest
import security
from conftest import q


@pytest.fixture
def tight(appmod):
    """Real, low limits for the duration of one test, with a controllable client IP."""
    limits = {'login-pair': (3, 900), 'login-user': (8, 3600), 'login-ip': (10, 900), 'register-ip': (4, 3600)}
    with mock.patch.dict(security.WINDOWS, limits), mock.patch.dict(appmod.app.config, {'CLIENT_IP_HEADER': 'X-Test-IP'}):
        yield


def attempt(appmod, user, pw, ip=None, client=None):
    c = client or appmod.app.test_client()
    return c.post('/login', data={'username': user, 'password': pw}, headers={'X-Test-IP': ip or '10.0.0.1'})


def fresh_ip(n=0):
    """A random /24 per call site, so counters from other tests never interfere."""
    return '10.%d.%d.%d' % (os.urandom(1)[0], os.urandom(1)[0], n)


def test_repeated_failures_lock_that_user_and_ip_even_for_the_right_password(appmod, make_user, tight):
    u = make_user(); u.post('/logout'); ip, other_ip = fresh_ip(1), fresh_ip(2)
    for _ in range(3):
        assert attempt(appmod, u.name, 'wrong-pass', ip).status_code == 200
    r = attempt(appmod, u.name, u.password, ip)                                # correct, but locked
    assert r.status_code == 429 and 'Too many attempts' in r.get_data(as_text=True)
    assert attempt(appmod, u.name, u.password, other_ip).status_code == 302    # same user from another address: allowed
    assert attempt(appmod, make_user().name, 'nope', ip).status_code == 200    # other user from the locked IP: not locked


def test_a_correct_password_resets_the_counter(appmod, make_user, tight):
    u = make_user(); u.post('/logout'); ip = fresh_ip(3)
    for _ in range(2):
        attempt(appmod, u.name, 'wrong-pass', ip)
    assert attempt(appmod, u.name, u.password, ip).status_code == 302
    for _ in range(2):
        assert attempt(appmod, u.name, 'wrong-again', ip).status_code == 200    # counter started over
    assert attempt(appmod, u.name, u.password, ip).status_code == 302


def test_distributed_guessing_against_one_account_is_capped(appmod, make_user, tight):
    u = make_user(); u.post('/logout')
    for i in range(8):                                                          # a different address every time
        assert attempt(appmod, u.name, 'guess', ip=fresh_ip(i)).status_code == 200
    assert attempt(appmod, u.name, u.password, ip=fresh_ip(99)).status_code == 429


def test_one_address_cannot_spray_many_accounts(appmod, make_user, tight):
    names = [make_user().name for _ in range(11)]; ip = fresh_ip(5)
    codes = [attempt(appmod, n, 'spray', ip=ip).status_code for n in names]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_registration_is_rate_limited_per_address(appmod, tight):
    codes = []; ip = fresh_ip(7); ip2 = fresh_ip(8)
    for i in range(6):
        c = appmod.app.test_client()
        codes.append(c.post('/register', data={'username': f'flood{i}{os.urandom(2).hex()}', 'password': 'secret12', 'confirm': 'secret12'},
                            headers={'X-Test-IP': ip}).status_code)
    assert codes == [302, 302, 302, 302, 429, 429]
    c = appmod.app.test_client()
    assert c.post('/register', data={'username': 'elsewhere' + os.urandom(2).hex(), 'password': 'secret12', 'confirm': 'secret12'},
                  headers={'X-Test-IP': ip2}).status_code == 302


def test_the_lockout_expires(appmod, make_user, tight):
    u = make_user(); u.post('/logout'); ip = fresh_ip(9)
    for _ in range(3):
        attempt(appmod, u.name, 'bad', ip)
    assert attempt(appmod, u.name, u.password, ip).status_code == 429
    import database
    db = database.get_db()                                                      # age every recorded attempt by ~17 minutes
    db.execute("UPDATE login_attempts SET created_at = created_at - make_interval(secs => 1000)"); db.commit(); db.close()
    assert attempt(appmod, u.name, u.password, ip).status_code == 302


def test_stored_keys_never_contain_the_username_or_the_ip(appmod, make_user, tight):
    u = make_user(); u.post('/logout')
    attempt(appmod, u.name, 'bad', ip='198.51.100.23')
    rows = q('SELECT kind, key FROM login_attempts')
    assert rows and not any(u.name.lower() in r['key'] or '198.51.100.23' in r['key'] for r in rows)
    assert {r['kind'] for r in rows} >= {'login-pair', 'login-user', 'login-ip'}


def test_unknown_users_cost_the_same_as_known_ones(appmod, make_user, tight):
    """A hash check is always performed, so response time doesn't reveal which usernames exist."""
    u = make_user(); u.post('/logout')
    with mock.patch.object(appmod, 'check_password_hash', wraps=appmod.check_password_hash) as spy:
        attempt(appmod, 'no-such-user-' + os.urandom(3).hex(), 'whatever', ip=fresh_ip(10))
        attempt(appmod, u.name, 'whatever', ip=fresh_ip(11))
    assert spy.call_count == 2


def test_client_ip_comes_from_the_trusted_header_only(appmod):
    with appmod.app.test_request_context('/', headers={'X-Forwarded-For': '6.6.6.6', 'X-Test-IP': '7.7.7.7'}, environ_base={'REMOTE_ADDR': '1.2.3.4'}):
        with mock.patch.dict(appmod.app.config, {'CLIENT_IP_HEADER': None}):
            assert appmod.client_ip() == '1.2.3.4'                              # never trust XFF by default
        with mock.patch.dict(appmod.app.config, {'CLIENT_IP_HEADER': 'X-Test-IP'}):
            assert appmod.client_ip() == '7.7.7.7'
