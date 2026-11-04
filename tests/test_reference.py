"""Rules reference: spells, monsters and items from the 2024 D&D 5e API (faked here -- no network in tests)."""
import pytest
import pubid

import srd
from conftest import BACKEND

REAL_GET_JSON = srd._get_json          # the fixture below replaces srd._get_json with a fake; this is the real one

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

# The 2014 edition: its own collections and field names (desc lists, races, no species), a few entries of each kind.
LISTS_2014 = {
    '/spells': {'results': [{'index': 'fireball', 'name': 'Fireball', 'level': 3}]},
    '/monsters': {'results': [{'index': 'goblin', 'name': 'Goblin'}]},
    '/equipment': {'results': [{'index': 'longsword', 'name': 'Longsword'}]},
    '/magic-items': {'results': [{'index': 'bag-of-holding', 'name': 'Bag of Holding'}]},
    '/conditions': {'results': [{'index': 'blinded', 'name': 'Blinded'}]},
    '/classes': {'results': [{'index': 'wizard', 'name': 'Wizard'}]},
    '/subclasses': {'results': [{'index': 'evocation', 'name': 'Evocation'}]},
    '/races': {'results': [{'index': 'elf', 'name': 'Elf'}]},
    '/subraces': {'results': [{'index': 'high-elf', 'name': 'High Elf'}]},
    '/backgrounds': {'results': [{'index': 'acolyte', 'name': 'Acolyte'}]},
    '/feats': {'results': [{'index': 'grappler', 'name': 'Grappler'}]},
    '/skills': {'results': [{'index': 'acrobatics', 'name': 'Acrobatics'}]},
    '/rules': {'results': [{'index': 'adventuring', 'name': 'Adventuring'}]},
    '/rule-sections': {'results': [{'index': 'cover', 'name': 'Cover'}]},
    '/languages': {'results': [{'index': 'common', 'name': 'Common'}]},
}
DETAILS_2014 = {
    '/spells/fireball': {'name': 'Fireball', 'level': 3, 'school': {'name': 'Evocation'}, 'casting_time': '1 action', 'range': '150 feet',
                         'components': ['V', 'S', 'M'], 'material': 'a tiny ball of bat guano', 'duration': 'Instantaneous', 'concentration': False,
                         'classes': [{'name': 'Sorcerer'}, {'name': 'Wizard'}], 'desc': ['A bright streak flashes.', 'It explodes.'],
                         'higher_level': ['The damage increases by 1d6.']},
    '/monsters/goblin': {'name': 'Goblin', 'size': 'Small', 'type': 'humanoid', 'alignment': 'neutral evil', 'armor_class': [{'type': 'armor', 'value': 15}],
                         'hit_points': 7, 'hit_points_roll': '2d6', 'speed': {'walk': '30 ft.'}, 'strength': 8, 'dexterity': 14, 'constitution': 10,
                         'intelligence': 10, 'wisdom': 8, 'charisma': 8, 'challenge_rating': 0.25, 'xp': 50, 'proficiency_bonus': 2,
                         'proficiencies': [{'value': 6, 'proficiency': {'index': 'skill-stealth'}}],
                         'actions': [{'name': 'Scimitar', 'desc': 'Melee.', 'attack_bonus': 4,
                                      'damage': [{'damage_type': {'name': 'Slashing'}, 'damage_dice': '1d6+2'}]}]},
    '/equipment/longsword': {'name': 'Longsword', 'equipment_category': {'name': 'Weapon'}, 'category_range': 'Martial Melee',
                             'cost': {'quantity': 15, 'unit': 'gp'}, 'weight': 3, 'damage': {'damage_dice': '1d8', 'damage_type': {'name': 'Slashing'}},
                             'properties': [{'name': 'Versatile'}]},
    '/magic-items/bag-of-holding': {'name': 'Bag of Holding', 'equipment_category': {'name': 'Wondrous Items'}, 'rarity': {'name': 'Uncommon'},
                                    'desc': ['Wondrous item, uncommon (requires attunement)', 'Bigger inside.']},
    '/conditions/blinded': {'name': 'Blinded', 'desc': ['- A blinded creature cannot see.']},
    '/classes/wizard': {'name': 'Wizard', 'hit_die': 6, 'proficiencies': [{'name': 'Daggers'}, {'name': 'Saving Throw: INT'}],
                        'saving_throws': [{'name': 'INT'}, {'name': 'WIS'}], 'class_levels': '/api/2014/classes/wizard/levels',
                        'starting_equipment': [{'equipment': {'name': 'Spellbook'}, 'quantity': 1}],
                        'proficiency_choices': [{'desc': 'Choose two skills'}],
                        'spellcasting': {'level': 1, 'spellcasting_ability': {'name': 'INT'}, 'info': [{'name': 'Cantrips', 'desc': ['Know three.']}]},
                        'multi_classing': {'prerequisites': [{'ability_score': {'name': 'INT'}, 'minimum_score': 13}]},
                        'subclasses': [{'name': 'Evocation'}]},
    '/classes/wizard/levels': [{'level': 1, 'prof_bonus': 2, 'features': [{'name': 'Spellcasting'}]},
                               {'level': 2, 'prof_bonus': 2, 'features': [{'name': 'Arcane Tradition'}]}],
    '/subclasses/evocation': {'name': 'Evocation', 'class': {'name': 'Wizard'}, 'subclass_flavor': 'Arcane Tradition', 'desc': ['You study elemental magic.'],
                              'subclass_levels': '/api/2014/subclasses/evocation/levels'},
    '/subclasses/evocation/levels': [{'level': 2, 'features': [{'index': 'evocation-savant', 'name': 'Evocation Savant'}, {'index': '../bad'}]},
                                     {'level': 6, 'features': [{'index': 'missing-feature', 'name': 'Gone'}]}],
    '/features/evocation-savant': {'name': 'Evocation Savant', 'desc': ['Copy evocation spells for half the cost.']},
    '/races/elf': {'name': 'Elf', 'size': 'Medium', 'speed': 30, 'ability_bonuses': [{'ability_score': {'name': 'DEX'}, 'bonus': 2}],
                   'age': 'Live to 750.', 'alignment': 'Chaotic.', 'language_desc': 'Common and Elvish.', 'size_description': 'Slender.',
                   'traits': [{'index': 'darkvision', 'name': 'Darkvision'}, {'index': 'missing-trait'}], 'subraces': [{'name': 'High Elf'}]},
    '/traits/darkvision': {'name': 'Darkvision', 'desc': ['You can see in the dark.']},
    '/subraces/high-elf': {'name': 'High Elf', 'race': {'name': 'Elf'}, 'desc': 'Keen mind.', 'ability_bonuses': [{'ability_score': {'name': 'INT'}, 'bonus': 1}],
                           'racial_traits': [{'index': 'darkvision', 'name': 'Darkvision'}]},
    '/backgrounds/acolyte': {'name': 'Acolyte', 'starting_proficiencies': [{'name': 'Skill: Insight'}, {'name': 'Skill: Religion'}],
                             'starting_gold': {'quantity': 15, 'unit': 'gp'}, 'starting_equipment': [{'equipment': {'name': 'Holy symbol'}, 'quantity': 1}],
                             'feature': {'name': 'Shelter of the Faithful', 'desc': ['Free healing at a temple.']}},
    '/feats/grappler': {'name': 'Grappler', 'prerequisites': [{'ability_score': {'name': 'STR'}, 'minimum_score': 13}], 'desc': ['Advantage on grapples.']},
    '/skills/acrobatics': {'name': 'Acrobatics', 'desc': ['Stay on your feet.'], 'ability_score': {'name': 'DEX'}},
    '/rules/adventuring': {'name': 'Adventuring', 'desc': '# Adventuring\n\n**Time** matters.'},
    '/rule-sections/cover': {'name': 'Cover', 'desc': '## Cover\n\nHalf cover gives +2.'},
    '/languages/common': {'name': 'Common', 'type': 'Standard', 'script': 'Common', 'typical_speakers': ['Humans']},
}


@pytest.fixture(autouse=True)
def fake_api(monkeypatch):
    calls = []

    def fake(path, edition='2024'):
        calls.append(path if edition == '2024' else f'{edition}:{path}')
        lists, details = (LISTS, DETAILS) if edition == '2024' else (LISTS_2014, DETAILS_2014)
        if path in lists:
            return lists[path]
        if path in details:
            return details[path]
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
    def down(path, edition='2024'):
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
    def fake(path, edition='2024'):
        if path.endswith('/levels'):
            raise srd.SrdError('The rules reference is unavailable right now.')
        return real(path, edition)
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


# ---- the 2014 edition ----

def test_the_edition_is_checked_and_defaults_to_2024():
    assert srd.check_edition(None) == '2024' and srd.check_edition('2014') == '2014'
    assert srd.edition_of('2014') == '2014' and srd.edition_of('1999') == '2024'
    with pytest.raises(srd.SrdError):
        srd.search('spells', 'x', edition='1999')
    with pytest.raises(srd.SrdError):
        srd.detail('spells', 'spells/fireball', edition='1999')


def test_2014_has_its_own_lists_and_kinds(fake_api):
    assert [r['name'] for r in srd.search('spells', 'fire', edition='2014')] == ['Fireball']
    assert [r['id'] for r in srd.search('species', '', edition='2014')] == ['races/elf', 'subraces/high-elf']
    assert [r['sub'] for r in srd.search('species', '', edition='2014')] == ['Race', 'Subrace']
    assert [r['id'] for r in srd.search('rules', 'c', edition='2014')] == ['languages/common', 'rule-sections/cover', 'skills/acrobatics']
    assert '2014:/spells' in fake_api and '/spells' not in fake_api         # asked the 2014 API, not the 2024 one
    assert [r['name'] for r in srd.search('spells', 'fire')] == ['Fire Bolt', 'Fireball']          # no edition = 2024
    with pytest.raises(srd.SrdError):
        srd.detail('species', 'species/elf', edition='2014')                # 2014 has races, not species


def test_2014_spells_items_and_monsters_read_like_2024():
    sp = srd.detail('spells', 'spells/fireball', edition='2014')
    assert sp['subtitle'] == 'Level 3 evocation' and dict(sp['facts'])['Components'] == 'V, S, M (a tiny ball of bat guano)'
    assert sp['body'] == 'A bright streak flashes.\n\nIt explodes.\n\nUsing a higher-level spell slot: The damage increases by 1d6.'
    it = srd.detail('items', 'equipment/longsword', edition='2014')
    assert it['subtitle'] == 'Weapon' and dict(it['facts'])['Category'] == 'Martial Melee' and dict(it['facts'])['Damage'] == '1d8 Slashing'
    mi = srd.detail('items', 'magic-items/bag-of-holding', edition='2014')
    assert dict(mi['facts']) == {'Rarity': 'Uncommon', 'Attunement': 'Required'} and mi['body'] == 'Bigger inside.'
    c = srd.detail('monsters', 'monsters/goblin', edition='2014')['prefill']
    assert (c['max_hp'], c['armor_class'], c['dex_score'], c['skill_prof']) == (7, 15, 14, {'stealth': 2})
    assert srd.detail('conditions', 'conditions/blinded', edition='2014')['body'] == '- A blinded creature cannot see.'


def test_2014_classes_races_backgrounds_and_feats():
    cl = srd.detail('classes', 'classes/wizard', edition='2014')
    assert dict(cl['facts'])['Saving throws'] == 'INT, WIS' and 'Starting equipment: Spellbook x1' in cl['body']
    assert [l for l in cl['body'].splitlines() if l.startswith('| 2')] == ['| 2 | +2 | Arcane Tradition |']      # level table from the 2014 path
    sub = srd.detail('classes', 'subclasses/evocation', edition='2014')
    assert sub['subtitle'] == 'Wizard subclass' and 'Level 2: Evocation Savant. Copy evocation spells for half the cost.' in sub['body']
    assert 'Gone' not in sub['body'] and sub['body'].startswith('Arcane Tradition')               # a missing or bad feature is skipped
    race = srd.detail('species', 'races/elf', edition='2014')
    assert race['subtitle'] == 'Race' and dict(race['facts'])['Ability scores'] == 'DEX +2' and dict(race['facts'])['Subraces'] == 'High Elf'
    assert race['body'] == 'Slender.\n\nDarkvision. You can see in the dark.'
    sr = srd.detail('species', 'subraces/high-elf', edition='2014')
    assert sr['subtitle'] == 'Elf subrace' and dict(sr['facts'])['Ability scores'] == 'INT +1' and 'Keen mind.' in sr['body']
    bg = srd.detail('backgrounds', 'backgrounds/acolyte', edition='2014')
    assert dict(bg['facts']) == {'Proficiencies': 'Insight, Religion', 'Feature': 'Shelter of the Faithful', 'Starting gold': '15 gp'}
    assert 'Equipment: Holy symbol x1' in bg['body'] and bg['body'].startswith('Shelter of the Faithful. Free healing')
    assert dict(srd.detail('feats', 'feats/grappler', edition='2014')['facts']) == {'Prerequisite': 'STR 13'}


def test_2014_rules_drop_markdown_marks_and_describe_languages():
    assert srd.detail('rules', 'rules/adventuring', edition='2014')['body'] == 'Adventuring\n\nTime matters.'
    assert srd.detail('rules', 'rule-sections/cover', edition='2014')['body'] == 'Cover\n\nHalf cover gives +2.'
    assert dict(srd.detail('rules', 'languages/common', edition='2014')['facts']) == {'Type': 'Standard', 'Script': 'Common', 'Typical speakers': 'Humans'}


def test_cached_entries_are_kept_per_edition(monkeypatch):
    calls = []
    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {'results': []}
    monkeypatch.setattr(srd.requests, 'get', lambda url, **kw: calls.append(url) or R())
    srd.clear_cache()
    REAL_GET_JSON('/spells', '2014'); REAL_GET_JSON('/spells', '2024'); REAL_GET_JSON('/spells', '2014')
    assert [u.rsplit('/api/', 1)[1] for u in calls] == ['2014/spells', '2024/spells']


@new_code
def test_reference_outside_a_campaign_and_the_edition_switch(make_user):
    u = make_user()
    html = u.get('/reference').get_data(as_text=True)
    assert 'data-base="/api/reference/"' in html and 'data-edition="2024"' in html and '>Species</button>' in html
    assert 'System Reference Document 5.2.1' in html and 'data-ref-detail' in html
    assert u.get('/api/reference/search?kind=spells&q=fireb').json == [{'id': 'spells/fireball', 'name': 'Fireball', 'sub': 'Level 3'}]
    assert u.get('/api/reference/entry?kind=feats&ref=feats/alert').json['title'] == 'Alert'
    old = u.get('/reference?edition=2014').get_data(as_text=True)
    assert 'data-edition="2014"' in old and '>Races</button>' in old and 'System Reference Document 5.1' in old
    assert 'data-edition="2014"' in u.get('/reference').get_data(as_text=True)            # remembered for the next visit
    assert u.get('/api/reference/search?kind=species&edition=2014').json[0]['id'] == 'races/elf'
    assert u.get('/api/reference/entry?kind=species&ref=races/elf&edition=2014').json['title'] == 'Elf'
    assert u.get('/api/reference/search?kind=spells&edition=1999').status_code == 400
    assert u.get('/reference?edition=1999').status_code == 200                              # a bad value falls back, it never errors the page
    cid = u.new_campaign()
    assert 'data-base="/campaigns/%s/api/reference/"' % pubid.short('campaigns', cid) in u.get(f'/campaigns/{cid}/reference').get_data(as_text=True)
    assert u.get(f'/campaigns/{cid}/api/reference/entry?kind=classes&ref=classes/wizard&edition=2014').json['title'] == 'Wizard'


@new_code
def test_the_reference_is_in_the_menu_outside_a_campaign(make_user):
    u = make_user()
    html = u.get('/campaigns').get_data(as_text=True)
    assert 'href="/reference"' in html and html.count('href="/reference"') >= 2           # sidebar and the phone More sheet


@new_code
def test_the_reference_needs_a_login(appmod):
    anon = appmod.app.test_client()
    assert anon.get('/reference').status_code in (302, 401)
    assert anon.get('/api/reference/search?kind=spells').status_code in (302, 401)
