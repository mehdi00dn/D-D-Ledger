"""Signed-in write limits, the campaign cap, robots.txt, security.txt and noindex."""
from unittest import mock
import pytest
import security
from conftest import q


@pytest.fixture
def tight():
    limits = {'write-user': (6, 60), 'dice-user': (3, 60), 'campaign-create': (2, 3600)}
    with mock.patch.dict(security.WINDOWS, limits):
        yield


def test_a_signed_in_account_cannot_write_faster_than_the_limit(make_user, tight):
    u, other = make_user(), make_user()
    cid = u.new_campaign()                                                        # one write already counted
    codes = [u.json(f'/campaigns/{cid}/api/battle/add', {'character_id': 0}).status_code for _ in range(8)]
    assert 429 in codes and codes[0] != 429                                       # allowed at first, then limited
    r = u.json(f'/campaigns/{cid}/api/battle/add', {'character_id': 0})
    assert r.status_code == 429 and r.get_json()['code'] == 'rate_limited' and int(r.headers['Retry-After']) >= 1
    assert u.get(f'/campaigns/{cid}/characters').status_code == 200               # reading is never limited
    assert other.json(f'/campaigns/{other.new_campaign()}/api/battle/clear', {}).status_code != 429   # another account is unaffected
    assert u.post('/logout').status_code in (200, 302)                            # signing out always works


def test_dice_rolls_have_their_own_cap(make_user, tight):
    with mock.patch.dict(security.WINDOWS, {'write-user': (500, 60)}):
        u = make_user(); cid = u.new_campaign()
        codes = [u.json(f'/campaigns/{cid}/api/dice/roll', {'dice': {'20': 1}}).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429


def test_campaign_creation_is_capped_per_hour_and_in_total(make_user):
    u = make_user()
    with mock.patch.dict(security.WINDOWS, {'campaign-create': (2, 3600)}):
        u.new_campaign(); u.new_campaign()
        r = u.post('/campaigns/new', data={'name': 'Third'})
        assert r.status_code == 429 and 'too fast' in r.get_data(as_text=True)
    v = make_user()
    with mock.patch.object(security, 'MAX_OWNED_CAMPAIGNS', 2):
        v.new_campaign(); v.new_campaign()
        r = v.post('/campaigns/new', data={'name': 'Third'})
        assert r.status_code == 429 and 'most one account' in r.get_data(as_text=True)


def test_robots_security_txt_and_noindex(appmod, make_user, monkeypatch):
    anon = appmod.app.test_client()
    robots = anon.get('/robots.txt')
    assert robots.status_code == 200 and 'Disallow: /campaigns' in robots.get_data(as_text=True)
    monkeypatch.delenv('SECURITY_CONTACT', raising=False)
    assert anon.get('/.well-known/security.txt').status_code == 404               # nothing published until a contact is set
    monkeypatch.setenv('SECURITY_CONTACT', 'mailto:security@example.com')
    st = anon.get('/.well-known/security.txt').get_data(as_text=True)
    assert 'Contact: mailto:security@example.com' in st and 'Expires: ' in st
    monkeypatch.setenv('SECURITY_CONTACT', 'javascript:alert(1)')
    assert anon.get('/.well-known/security.txt').status_code == 404               # only mailto: and https: are accepted
    u = make_user()
    assert u.get('/campaigns').headers['X-Robots-Tag'] == 'noindex, nofollow'
    assert 'X-Robots-Tag' not in anon.get('/login').headers
