"""The few 5e rules the character sheet needs: modifiers, proficiency bonus, saves, skills.

Everything here is derived from what a character stores (six scores, level, speed, which saves/skills it is
proficient in), so there is nothing to keep in sync.  The skill list and its abilities are the fixed SRD ones
(the same 18 skills dnd5eapi.co serves); they are written out here so the app needs no network at runtime.
"""
import json

ABILITIES = [('str', 'STR', 'Strength'), ('dex', 'DEX', 'Dexterity'), ('con', 'CON', 'Constitution'),
             ('int', 'INT', 'Intelligence'), ('wis', 'WIS', 'Wisdom'), ('cha', 'CHA', 'Charisma')]
ABILITY_KEYS = [k for k, _s, _n in ABILITIES]
ABILITY_SHORT = {k: s for k, s, _n in ABILITIES}

SKILLS = [('acrobatics', 'Acrobatics', 'dex'), ('animal-handling', 'Animal Handling', 'wis'),
          ('arcana', 'Arcana', 'int'), ('athletics', 'Athletics', 'str'), ('deception', 'Deception', 'cha'),
          ('history', 'History', 'int'), ('insight', 'Insight', 'wis'), ('intimidation', 'Intimidation', 'cha'),
          ('investigation', 'Investigation', 'int'), ('medicine', 'Medicine', 'wis'), ('nature', 'Nature', 'int'),
          ('perception', 'Perception', 'wis'), ('performance', 'Performance', 'cha'),
          ('persuasion', 'Persuasion', 'cha'), ('religion', 'Religion', 'int'),
          ('sleight-of-hand', 'Sleight of Hand', 'dex'), ('stealth', 'Stealth', 'dex'), ('survival', 'Survival', 'wis')]
SKILL_KEYS = [k for k, _l, _a in SKILLS]

DEFAULT_SPEED = 30
MAX_SPEED = 500


def modifier(score):
    """Ability modifier: floor((score - 10) / 2)."""
    try:
        return (int(score) - 10) // 2
    except (TypeError, ValueError):
        return 0


def proficiency_bonus(level):
    """+2 at levels 1-4, rising by one every four levels, +6 from level 17 on."""
    try:
        level = int(level)
    except (TypeError, ValueError):
        level = 1
    return 2 + (max(1, min(level, 20)) - 1) // 4


def signed(n):
    return f'+{n}' if n >= 0 else f'-{abs(n)}'


def _loads(raw, default):
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return default
    return raw if raw is not None else default


def clean_speed(raw, default=DEFAULT_SPEED):
    try:
        return max(0, min(int(str(raw).strip()), MAX_SPEED))
    except (TypeError, ValueError):
        return default


def clean_skill_prof(raw):
    """{skill key: 1 (proficient) | 2 (expertise)} -- unknown skills and other values are dropped."""
    data = _loads(raw, {})
    if not isinstance(data, dict):
        return {}
    out = {}
    for key in SKILL_KEYS:
        try:
            level = int(data.get(key, 0))
        except (TypeError, ValueError):
            continue
        if level in (1, 2):
            out[key] = level
    return out


def clean_save_prof(raw):
    """The abilities a character is proficient in saving throws for, in the standard order."""
    data = _loads(raw, [])
    if not isinstance(data, (list, tuple, set)):
        return []
    return [k for k in ABILITY_KEYS if k in data]


def derive(c):
    """Everything the sheet shows, from a character row/dict.  Pure: no database access."""
    c = dict(c)
    scores = {k: c[f'{k}_score'] for k in ABILITY_KEYS}
    mods = {k: modifier(v) for k, v in scores.items()}
    prof = proficiency_bonus(c['level'])
    saves_with = clean_save_prof(c.get('save_prof'))
    skills_with = clean_skill_prof(c.get('skill_prof'))
    speed = clean_speed(c.get('speed'))
    skills = []
    for key, label, ability in SKILLS:
        lvl = skills_with.get(key, 0)
        bonus = mods[ability] + prof * lvl
        skills.append({'key': key, 'label': label, 'ability': ABILITY_SHORT[ability], 'level': lvl,
                       'bonus': bonus, 'text': signed(bonus)})
    perception = next(s for s in skills if s['key'] == 'perception')
    return {
        'speed': speed,
        'prof_bonus': prof, 'prof_text': signed(prof),
        'initiative': mods['dex'], 'initiative_text': signed(mods['dex']),
        'passive_perception': 10 + perception['bonus'],
        'abilities': [{'key': k, 'label': s, 'name': n, 'score': scores[k], 'mod': mods[k], 'text': signed(mods[k])}
                      for k, s, n in ABILITIES],
        'saves': [{'key': k, 'label': s, 'proficient': k in saves_with,
                   'bonus': mods[k] + (prof if k in saves_with else 0),
                   'text': signed(mods[k] + (prof if k in saves_with else 0))} for k, s, _n in ABILITIES],
        'skills': skills,
    }
