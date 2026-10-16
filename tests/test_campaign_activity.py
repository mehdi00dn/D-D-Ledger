import re
from conftest import q


def feed(user, cid):
    r = user.get(f'/campaigns/{cid}/activity')
    assert r.status_code == 200
    return re.sub(r'<[^>]+>', ' ', r.get_data(as_text=True))


def test_activity_records_additions_removals_and_membership(appmod, make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign()
    assert 'Nothing yet' in feed(dm, cid)
    ch = dm.new_character(cid, name='Mira Thornwood')
    gid = dm.new_group(cid, name='The Ashen Court')
    text = feed(dm, cid)
    assert 'Mira Thornwood' in text and 'The Ashen Court' in text and dm.name in text
    dm.add_member(cid, pl, 'player')
    pl.post(f'/campaigns/{cid}/leave')
    dm.post(f'/campaigns/{cid}/characters/{ch}/delete')
    text = feed(dm, cid)
    assert 'removed the character Mira Thornwood' in text and 'left the campaign' in text


def test_activity_is_members_only_and_per_campaign(appmod, make_user):
    a, b = make_user(), make_user()
    cid = a.new_campaign(); other = b.new_campaign()
    a.new_character(cid, name='Secretive Sam')
    assert b.get(f'/campaigns/{cid}/activity').status_code in (403, 404, 302)
    assert 'Secretive Sam' not in feed(b, other)


def test_activity_keeps_only_the_latest_events(appmod, make_user, monkeypatch):
    a = make_user(); cid = a.new_campaign()
    monkeypatch.setattr(appmod, 'ACTIVITY_KEEP', 3)
    for i in range(6):
        a.new_character(cid, name=f'Hero{i}')
    assert q('SELECT COUNT(*) AS n FROM campaign_activity WHERE campaign_id = ?', cid)[0]['n'] == 3


def test_deleted_account_leaves_no_name_in_the_feed(appmod, make_user):
    a, b = make_user(), make_user()
    cid = a.new_campaign(); a.add_member(cid, b, 'dm')
    b.new_character(cid, name='Echo')
    b.post('/account/delete', data={'current_password': b.password, 'confirm_delete': 'DELETE'})
    text = feed(a, cid)
    assert 'Echo' in text and b.name not in text and 'A former member' in text
