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
    'actions': [
        {'name': 'Multiattack', 'desc': 'The goblin makes two attacks.', 'actions': [], 'attacks': []},
        {'name': 'Scimitar', 'desc': 'Melee Attack Roll: +4, reach 5 ft. Hit: 5 (1d6 + 2) Slashing damage.', 'attack_bonus': 4,
         'damage': [{'damage_type': {'index': 'slashing', 'name': 'Slashing'}, 'damage_dice': '1d6+2'},
                    {'damage_type': {'index': 'slashing', 'name': 'Slashing'}, 'damage_dice': '1d4'}]},
        {'name': 'Shortbow', 'desc': 'Ranged.', 'attack_bonus': 4,
         'damage': [{'damage_type': {'index': 'piercing', 'name': 'Piercing'}, 'damage_dice': '1d6+2'}]},
        {'name': 'Weird Gaze', 'desc': 'odd dice', 'attack_bonus': 3,
         'damage': [{'damage_type': {'name': 'Psychic'}, 'damage_dice': '1d6+1d4'}]},
    ],
}
LISTS = {
    '/spells': {'results': [{'index': 'fireball', 'name': 'Fireball', 'level': 3}, {'index': 'fire-bolt', 'name': 'Fire Bolt', 'level': 0},
                            {'index': 'bless', 'name': 'Bless', 'level': 1}]},
    '/monsters': {'results': [{'index': 'goblin-warrior', 'name': 'Goblin Warrior'}, {'index': 'aboleth', 'name': 'Aboleth'}]},
    '/conditions': {'results': [{'index': 'blinded', 'name': 'Blinded'}, {'index': 'prone', 'name': 'Prone'}]},
    '/equipment': {'results': [{'index': 'longsword', 'name': 'Longsword'}]},
    '/magic-items': {'results': [{'index': 'bag-of-holding', 'name': 'Bag of Holding'}, {'index': 'longsword-1', 'name': 'Longsword +1'}]},
}
DETAILS = {
    '/monsters/goblin-warrior': GOBLIN,
    '/spells/fireball': {'name': 'Fireball', 'level': 3, 'school': {'name': 'Evocation'}, 'casting_time': 'Action', 'range': '150 feet',
                         'components': ['V', 'S', 'M'], 'material': 'a ball of bat guano', 'duration': 'Instantaneous', 'concentration': False,
                         'ritual': False, 'classes': [{'name': 'Wizard'}], 'description': 'A bright streak flashes.', 'higher_level': 'More dice.'},
    '/conditions/blinded': {'name': 'Blinded', 'description': 'While you have the Blinded condition, you experience the following effects.\n**Can\'t See.** You can\'t see.'},
    '/equipment/longsword': {'name': 'Longsword', 'cost': {'quantity': 15, 'unit': 'gp'}, 'weight': 3,
                             'equipment_categories': [{'name': 'Weapons'}, {'name': 'Martial Melee Weapons'}],
                             'damage': {'damage_dice': '1d8', 'damage_type': {'name': 'Slashing'}}, 'properties': [{'name': 'Versatile'}]},
    '/magic-items/bag-of-holding': {'name': 'Bag of Holding', 'rarity': {'name': 'Uncommon'}, 'equipment_category': {'name': 'Wondrous Items'},
                                    'desc': ['Wondrous Item', 'This bag is bigger inside.']},
    '/classes': {'results': [{'index': 'wizard', 'name': 'Wizard'}]},
    '/subclasses': {'results': [{'index': 'evoker', 'name': 'Evoker'}]},
    '/species': {'results': [{'index': 'elf', 'name': 'Elf'}]},
    '/backgrounds': {'results': [{'index': 'acolyte', 'name': 'Acolyte'}]},
    '/feats': {'results': [{'index': 'alert', 'name': 'Alert'}]},
    '/skills': {'results': [{'index': 'acrobatics', 'name': 'Acrobatics'}]},
    '/ability-scores': {'results': [{'index': 'str', 'name': 'STR'}]},
    '/languages': {'results': [{'index': 'common', 'name': 'Common'}]},
    '/poisons': {'results': [{'index': 'assassins-blood', 'name': "Assassin's Blood"}]},
}
for _c in ('damage-types', 'weapon-properties', 'weapon-mastery-properties', 'magic-schools', 'alignments'):
    LISTS['/' + _c] = {'results': []}
EXTRA = {
    '/classes/wizard': {'name': 'Wizard', 'hit_die': 6, 'primary_ability': {'desc': 'Intelligence'},
                        'saving_throws': [{'name': 'INT'}, {'name': 'WIS'}],
                        'proficiencies': [{'name': 'Simple Weapons'}, {'name': 'Saving Throw: INT'}],
                        'proficiency_choices': [{'desc': 'Choose 2: Arcana, History'}],
                        'multi_classing': {'prerequisites': [{'ability_score': {'name': 'INT'}, 'minimum_score': 13}]},
                        'spellcasting': {'level': 1, 'spellcasting_ability': {'name': 'INT'}, 'info': [{'name': 'Cantrips', 'desc': ['You know **three** cantrips.']}]},
                        'subclasses': [{'name': 'Evoker'}], 'class_levels': '/api/2024/classes/wizard/levels'},
    '/classes/wizard/levels': [
        {'level': 2, 'prof_bonus': 2, 'features': [{'name': 'Scholar'}]},
        {'level': 1, 'prof_bonus': 2, 'features': [{'name': 'Spellcasting'}, {'name': 'Ritual | Adept'}]},
        {'level': 2, 'features': [{'name': 'Subclass row without a bonus'}]}],
    '/subclasses/evoker': {'name': 'Evoker', 'class': {'name': 'Wizard'}, 'description': 'Masters of **force**.',
                           'features': [{'name': 'Potent Cantrip', 'level': 3, 'description': 'Hit harder.'}]},
    '/species/elf': {'name': 'Elf', 'type': 'Humanoid', 'size': 'Medium', 'speed': 30,
                     'traits': [{'index': 'darkvision-60'}, {'index': 'missing-trait'}, {'index': '../bad'}],
                     'subspecies': [{'name': 'Drow'}]},
    '/traits/darkvision-60': {'name': 'Darkvision', 'description': 'You see in the dark.'},
    '/backgrounds/acolyte': {'name': 'Acolyte', 'ability_scores': [{'name': 'INT'}, {'name': 'WIS'}],
                             'feat': {'name': 'Magic Initiate', 'note': 'Cleric'},
                             'proficiencies': [{'name': 'Skill: Insight'}, {'name': "Tool: Calligrapher's Supplies"}],
                             'equipment_options': [{'desc': 'Choose A or B: (A) Robe; or (B) 50 GP'}]},
    '/feats/alert': {'name': 'Alert', 'type': 'origin', 'description': 'You gain **Initiative Proficiency.**'},
    '/skills/acrobatics': {'name': 'Acrobatics', 'description': 'Stay on your feet.', 'ability_score': {'name': 'DEX'}},
    '/ability-scores/str': {'name': 'STR', 'full_name': 'Strength', 'description': 'Physical might', 'skills': [{'name': 'Athletics'}]},
    '/languages/common': {'name': 'Common', 'is_rare': False, 'note': ''},
    '/poisons/assassins-blood': {'name': "Assassin's Blood", 'type': 'ingested', 'cost': 150, 'description': 'Save or suffer.'},
}
DETAILS.update(EXTRA)


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
    assert '<b>Actions</b><ul><li><b>Multiattack.</b>' in text


def test_condition_rules_are_readable_plain_text():
    assert [r['name'] for r in srd.search('conditions', '')] == ['Blinded', 'Prone']
    c = srd.detail('conditions', 'conditions/blinded')
    assert c['title'] == 'Blinded' and c['subtitle'] == 'Condition' and '**' not in c['body']
    assert "Can't See. You can't see." in c['body']


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


@new_code
def test_a_monster_goes_straight_into_the_battle_as_an_npc(make_user):
    from conftest import q
    dm, player = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, player)
    url = f'/campaigns/{cid}/api/battle/add-monster'
    rows = dm.json(url, {'ref': 'monsters/goblin-warrior'}).get_json()
    assert [(r['char_name'], r['is_npc'], r['current_hp'], r['char_max_hp'], r['effective_ac']) for r in rows] == [('Goblin Warrior', 1, 10, 10, 15)]
    note = q('SELECT notes, dex_score, save_prof FROM characters WHERE campaign_id = ?', cid)[0]
    assert '"h": 1' in note['notes'] and 'Nimble Escape' in note['notes'] and '<script>' not in note['notes'] and note['dex_score'] == 15
    rows = dm.json(url, {'ref': 'monsters/goblin-warrior', 'count': 3, 'hp': 'rolled'}).get_json()
    assert len(rows) == 4 and all(r['char_name'].startswith('Goblin Warrior') for r in rows)
    assert all(3 <= r['char_max_hp'] <= 18 and r['current_hp'] == r['char_max_hp'] for r in rows[1:])      # 3d6, one roll per copy
    assert all(r['char_name'] == 'Goblin Warrior' or r['char_name'] == f"Goblin Warrior ({r['char_max_hp']} HP)" for r in rows)   # a character per hit point total
    again = dm.json(url, {'ref': 'monsters/goblin-warrior', 'count': 2}).get_json()                          # average hit points: the first goblin is used again
    assert len(again) == 6 and len(q("SELECT id FROM characters WHERE campaign_id = ? AND name = 'Goblin Warrior'", cid)) == 1


@new_code
def test_adding_a_monster_is_dm_only_and_validated(make_user):
    dm, player = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, player)
    url = f'/campaigns/{cid}/api/battle/add-monster'
    assert player.json(url, {'ref': 'monsters/goblin-warrior'}).status_code in (302, 403)
    assert dm.json(url, {'ref': 'monsters/goblin-warrior', 'count': 0}).status_code == 400
    assert dm.json(url, {'ref': 'monsters/goblin-warrior', 'count': 13}).status_code == 400
    assert dm.json(url, {'ref': 'monsters/goblin-warrior', 'count': 'x'}).status_code == 400
    assert dm.json(url, {'ref': '../etc/passwd'}).status_code == 400
    assert dm.json(url, {'ref': 'monsters/nope'}).status_code == 404
    assert dm.get(f'/campaigns/{cid}/api/battle').get_json() == []                                  # nothing half-made


def test_unreadable_hit_dice_fall_back_to_the_average():
    import app as ledger
    assert ledger._roll_monster_hp('weird', 22) == 22 and ledger._roll_monster_hp('', 22) == 22
    assert all(5 <= ledger._roll_monster_hp('2d6+3', 0) <= 15 for _ in range(40))
    assert ledger._roll_monster_hp('1d4-9', 0) == 1


def test_a_monsters_attack_rolls_become_attacks():
    atk = srd.detail('monsters', 'monsters/goblin-warrior')['prefill']['attacks']
    assert [a['name'] for a in atk] == ['Scimitar', 'Shortbow', 'Weird Gaze']          # Multiattack has no to-hit: skipped
    assert atk[0] == {'name': 'Scimitar', 'attack_bonus': 4, 'damage': '1d6+2', 'damage_type': 'slashing'}   # first damage entry only
    assert atk[1]['damage_type'] == 'piercing'
    assert atk[2]['damage'] == '' and atk[2]['attack_bonus'] == 3                       # dice the card cannot hold: still listed, no roll


@new_code
def test_a_monster_added_to_the_battle_keeps_its_attacks(make_user):
    from conftest import q
    dm = make_user()
    cid = dm.new_campaign()
    dm.json(f'/campaigns/{cid}/api/battle/add-monster', {'ref': 'monsters/goblin-warrior', 'count': 2})
    rows = q('SELECT c.id, a.name, a.damage FROM characters c JOIN character_attacks a ON a.character_id = c.id WHERE c.campaign_id = ? ORDER BY c.id, a.position', cid)
    assert [r['name'] for r in rows] == ['Scimitar', 'Shortbow', 'Weird Gaze']              # two goblins share one character


def test_every_kind_is_listed_and_labelled():
    assert set(srd.KINDS) >= {'classes', 'species', 'backgrounds', 'feats', 'rules'}
    assert [(r['id'], r['sub']) for r in srd.search('classes')] == [('subclasses/evoker', 'Subclass'), ('classes/wizard', 'Class')]
    assert srd.search('rules', 'str')[0]['sub'] == 'Ability score'
    assert {r['sub'] for r in srd.search('rules')} == {'Skill', 'Ability score', 'Language', 'Poison'}


def test_a_class_reads_with_its_level_table():
    d = srd.detail('classes', 'classes/wizard')
    facts = dict(d['facts'])
    assert facts['Hit die'] == 'd6' and facts['Saving throws'] == 'INT, WIS' and facts['Proficiencies'] == 'Simple Weapons'
    assert facts['Spellcasting'] == 'INT (from level 1)' and facts['Multiclassing'] == 'INT 13'
    assert '**' not in d['body'] and 'Cantrips. You know three cantrips.' in d['body']
    rows = [l for l in d['body'].splitlines() if l.startswith('| ')]
    assert rows == ['| Level | Proficiency bonus | Features |', '| 1 | +2 | Spellcasting, Ritual / Adept |', '| 2 | +2 | Scholar |']


def test_a_class_survives_a_missing_level_table(monkeypatch):
    real = srd._get_json
    def fake(path):
        if path.endswith('/levels'):
            raise srd.SrdError('The rules reference is unavailable right now.')
        return real(path)
    monkeypatch.setattr(srd, '_get_json', fake)
    assert 'Choose 2' in srd.detail('classes', 'classes/wizard')['body']


def test_subclass_species_background_and_feat():
    sub = srd.detail('classes', 'subclasses/evoker')
    assert sub['subtitle'] == 'Wizard subclass' and 'Level 3: Potent Cantrip. Hit harder.' in sub['body'] and '**' not in sub['body']
    sp = srd.detail('species', 'species/elf')
    assert dict(sp['facts'])['Speed'] == '30 ft.' and sp['body'] == 'Darkvision. You see in the dark.'     # the missing trait is skipped
    bg = srd.detail('backgrounds', 'backgrounds/acolyte')
    assert dict(bg['facts'])['Feat'] == 'Magic Initiate (Cleric)' and dict(bg['facts'])['Proficiencies'] == "Insight, Calligrapher's Supplies"
    assert 'Equipment: Choose A or B' in bg['body']
    ft = srd.detail('feats', 'feats/alert')
    assert ft['subtitle'] == 'Origin feat' and ft['body'] == 'You gain Initiative Proficiency.'


def test_the_rules_group():
    assert srd.detail('rules', 'ability-scores/str')['title'] == 'Strength'
    assert dict(srd.detail('rules', 'skills/acrobatics')['facts']) == {'Ability': 'DEX'}
    assert dict(srd.detail('rules', 'languages/common')['facts']) == {'Rarity': 'Standard'}
    assert dict(srd.detail('rules', 'poisons/assassins-blood')['facts']) == {'Type': 'Ingested', 'Cost': '150 gp'}
    with pytest.raises(srd.SrdError):
        srd.detail('rules', 'classes/wizard')          # a reference must belong to its kind


def test_the_page_has_a_tab_for_every_kind(make_user):
    dm = make_user()
    cid = dm.new_campaign()
    html = dm.get(f'/campaigns/{cid}/reference').get_data(as_text=True)
    for kind in srd.KINDS:
        assert f'data-kind="{kind}"' in html
    rows = dm.get(f'/campaigns/{cid}/api/reference/search?kind=classes').json
    assert [r['name'] for r in rows] == ['Evoker', 'Wizard']
    assert dm.get(f'/campaigns/{cid}/api/reference/entry?kind=feats&ref=feats/alert').json['title'] == 'Alert'
