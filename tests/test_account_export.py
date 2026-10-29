import json
from conftest import q


def test_export_contains_own_data_only_and_never_secrets(appmod, make_user):
    a, b = make_user(), make_user()
    cid = a.new_campaign(); ch = a.new_character(cid)
    other = b.new_campaign(); b.new_character(other)
    a.post('/account', data={'display_name': 'Sir Pip', 'bio': 'hello'})
    r = a.post('/account/export')
    assert r.status_code == 200 and r.mimetype == 'application/json'
    assert 'attachment' in r.headers['Content-Disposition']
    data = json.loads(r.get_data(as_text=True))
    assert data['account']['username'] == a.name and data['account']['display_name'] == 'Sir Pip'
    assert any(m['role'] == 'owner' for m in data['campaign_memberships'])
    assert [c['id'] for c in data['characters_you_made']] == [ch]
    raw = r.get_data(as_text=True)
    assert b.name not in raw and 'password' not in raw.lower()


def test_export_needs_login_and_a_post(appmod, make_user):
    anon = appmod.app.test_client()
    assert anon.post('/account/export').status_code in (302, 403)
    u = make_user()
    assert u.get('/account/export').status_code == 405
