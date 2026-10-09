"""Rules reference: spells, monsters and items from the 2024 D&D 5e API (faked here -- no network in tests)."""
import pytest

import srd
from conftest import BACKEND

new_code = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')

GOBLIN = {
    'index': 'goblin-warrior', 'name': 'Goblin Warrior', 'size': 'Small', 'type': 'humanoid', 'alignment': 'chaotic neutral',
    'armor_class': [{'value': 15}], 'hit_points': 10, 'hit_points_roll': '3d6', 'speed': {'walk': '30 ft.'},
    'strength': 8, 'dexterity': 15, 'constitution': 10, 'intelligence': 10, 'wisdom': 8, 'charisma': 8,
    'proficiencies': [{'value': 6, 'proficiency': {'index': 'skill-stealth'}}, {'value': 3, 'proficiency': {'index': 'saving-throw-dex'}},
                      {'value': 1, 'proficiency': {'index': 'skill-bogus'}}],
    'challenge_rating': 0.25, 'xp': 50, 'proficiency_bonus': 2, 'senses': {'darkvision': '60 ft.', 'passive_perception': 9},
    'special_abilities': [{'name': 'Nimble Escape', 'desc': 'Disengage <script>x</script> as a bonus action.'}],
    'actions': [{'name': 'Scimitar', 'desc': 'Melee Attack Roll: +4'}],
}
LISTS = {
    '/spells': {'results': [{'index': 'fireball', 'name': 'Fireball', 'level': 3}, {'index': 'fire-bolt', 'name': 'Fire Bolt', 'level': 0},
                            {'index': 'bless', 'name': 'Bless', 'level': 1}]},
    '/monsters': {'results': [{'index': 'goblin-warrior', 'name': 'Goblin Warrior'}, {'index': 'aboleth', 'name': 'Aboleth'}]},
    '/equipment': {'results': [{'index': 'longsword', 'name': 'Longsword'}]},
    '/magic-items': {'results': [{'index': 'bag-of-holding', 'name': 'Bag of Holding'}, {'index': 'longsword-1', 'name': 'Longsword +1'}]},
}
DETAILS = {
    '/monsters/goblin-warrior': GOBLIN,
    '/spells/fireball': {'name': 'Fireball', 'level': 3, 'school': {'name': 'Evocation'}, 'casting_time': 'Action', 'range': '150 feet',
                         'components': ['V', 'S', 'M'], 'material': 'a ball of bat guano', 'duration': 'Instantaneous', 'concentration': False,
                         'ritual': False, 'classes': [{'name': 'Wizard'}], 'description': 'A bright streak flashes.', 'higher_level': 'More dice.'},
    '/equipment/longsword': {'name': 'Longsword', 'cost': {'quantity': 15, 'unit': 'gp'}, 'weight': 3,
                             'equipment_categories': [{'name': 'Weapons'}, {'name': 'Martial Melee Weapons'}],
                             'damage': {'damage_dice': '1d8', 'damage_type': {'name': 'Slashing'}}, 'properties': [{'name': 'Versatile'}]},
    '/magic-items/bag-of-holding': {'name': 'Bag of Holding', 'rarity': {'name': 'Uncommon'}, 'equipment_category': {'name': 'Wondrous Items'},
                                    'desc': ['Wondrous Item', 'This bag is bigger inside.']},
}


@pytest.fixture(autouse=True)
def fake_api(monkeypatch):
    calls = []

    def fake(path):
        calls.append(path)
        if path in LISTS:
            return LISTS[path]
        if path in DETAILS:
            return DETAILS[path]
        raise srd.SrdError('not found')
    monkeypatch.setattr(srd, '_get_json', fake)
    return calls


def test_it_uses_the_2024_rules():
    assert srd.API.endswith('/api/2024')


def test_search_matches_names_and_ranks_prefix_matches_first():
    assert [r['name'] for r in srd.search('spells', 'fire')] == ['Fire Bolt', 'Fireball']
    assert srd.search('spells', 'FIREB')[0]['sub'] == 'Level 3' and srd.search('spells', 'fire bolt')[0]['sub'] == 'Cantrip'
    assert srd.search('spells', 'zzz') == []
    assert len(srd.search('spells', '')) == 3
    items = srd.search('items', 'longsword')
    assert [r['id'] for r in items] == ['equipment/longsword', 'magic-items/longsword-1']      # both collections, one list
    with pytest.raises(srd.SrdError):
        srd.search('bogus', 'x')


def test_a_monster_becomes_a_ready_character():
    c = srd.detail('monsters', 'monsters/goblin-warrior')['prefill']
    assert (c['name'], c['max_hp'], c['armor_class'], c['speed'], c['level'], c['is_npc']) == ('Goblin Warrior', 10, 15, 30, 1, True)
    assert (c['str_score'], c['dex_score'], c['wis_score']) == (8, 15, 8)
    assert c['save_prof'] == ['dex'] and c['skill_prof'] == {'stealth': 2}      # +6 = DEX +2 and double proficiency; unknown skills dropped


def test_a_big_monsters_level_gives_its_proficiency_bonus():
    assert [srd._level_for_prof(p) for p in (2, 3, 4, 6, None)] == [1, 5, 9, 17, 1]


def test_monster_notes_are_escaped_whitelisted_html():
    notes = srd.detail('monsters', 'monsters/goblin-warrior')['notes']
    text = ''.join(notes)
    assert 'Small humanoid, chaotic neutral' in text and 'Challenge 1/4' in text and 'Speed: walk 30 ft.' in text
    assert '<script>' not in text and '&lt;script&gt;' in text
    assert '<b>Actions</b><ul><li><b>Scimitar.</b>' in text


def test_spell_and_item_details_read_well():
    s = srd.detail('spells', 'spells/fireball')
    assert s['subtitle'] == 'Level 3 evocation' and ('Components', 'V, S, M (a ball of bat guano)') in s['facts']
    assert 'More dice.' in s['body'] and s['body'].startswith('A bright streak')
    i = srd.detail('items', 'equipment/longsword')
    assert ('Cost', '15 gp') in i['facts'] and ('Damage', '1d8 Slashing') in i['facts'] and i['subtitle'] == 'Martial Melee Weapons'
    m = srd.detail('items', 'magic-items/bag-of-holding')
    assert ('Rarity', 'Uncommon') in m['facts'] and m['body'] == 'This bag is bigger inside.'


@pytest.mark.parametrize('kind,ref', [('spells', 'monsters/goblin-warrior'), ('monsters', '../etc/passwd'), ('items', 'equipment/'),
                                      ('monsters', 'monsters/Goblin'), ('nope', 'x/y'), ('monsters', 'monsters')])
def test_bad_references_are_rejected_before_any_request(kind, ref, fake_api):
    with pytest.raises(srd.SrdError):
        srd.detail(kind, ref)
    assert fake_api == []


@new_code
def test_the_routes_need_a_campaign_and_return_json(make_user):
    dm, player, cid = make_user(), make_user(), None
    cid = dm.new_campaign(); dm.add_member(cid, player)
    for who in (dm, player):
        assert who.get(f'/campaigns/{cid}/reference').status_code == 200
        rows = who.get(f'/campaigns/{cid}/api/reference/search?kind=monsters&q=gob').json
        assert [r['name'] for r in rows] == ['Goblin Warrior']
        d = who.get(f'/campaigns/{cid}/api/reference/entry?kind=monsters&ref=monsters/goblin-warrior').json
        assert d['prefill']['max_hp'] == 10
    assert dm.get(f'/campaigns/{cid}/api/reference/search?kind=bogus').status_code == 400
    assert dm.get(f'/campaigns/{cid}/api/reference/entry?kind=monsters&ref=monsters/nope').status_code == 404
    stranger = make_user()
    assert stranger.get(f'/campaigns/{cid}/api/reference/search?kind=monsters').status_code in (302, 403, 404)


@new_code
def test_an_unreachable_api_gives_a_clean_error(make_user, monkeypatch):
    def down(path):
        raise srd.SrdError('The rules reference is unavailable right now.')
    monkeypatch.setattr(srd, '_get_json', down)
    dm = make_user(); cid = dm.new_campaign()
    r = dm.get(f'/campaigns/{cid}/api/reference/search?kind=spells&q=x')
    assert r.status_code == 502 and 'unavailable' in r.json['error']
    assert dm.get(f'/campaigns/{cid}/reference').status_code == 200            # the page itself never depends on the API
