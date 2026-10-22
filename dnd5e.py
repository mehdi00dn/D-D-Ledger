"""The few 5e rules the character sheet needs: modifiers, proficiency bonus, saves, skills.

Everything here is derived from what a character stores (six scores, level, speed, which saves/skills it is
proficient in), so there is nothing to keep in sync.  The skill list and its abilities are the fixed SRD ones
(the same 18 skills dnd5eapi.co serves); they are written out here so the app needs no network at runtime.
"""
import json
import re

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


# --- Saved attacks (DL-150) -------------------------------------------------------------------------------------
# An attack is a name, a to-hit bonus and a damage roll of ONE kind of die plus a flat bonus ("1d8+3", "2d6-1").
# The server rolls it through the same dice route as everything else, so a damage roll needs nothing more than the
# dice map {sides: count} and the flat bonus that route already accepts.
DAMAGE_SIDES = (4, 6, 8, 10, 12, 20, 100)
MAX_DAMAGE_DICE = 30                    # the dice route's per-roll cap
MAX_ATTACKS = 12
_DAMAGE_RE = re.compile(r'^\s*(\d{1,2})\s*d\s*(\d{1,3})\s*(?:([+-])\s*(\d{1,3}))?\s*$', re.IGNORECASE)


def parse_damage(raw):
    """'2d6 + 3' -> (2, 6, 3); None when it is not one kind of die plus an optional flat bonus."""
    m = _DAMAGE_RE.match(raw) if isinstance(raw, str) else None
    if not m:
        return None
    count, sides = int(m.group(1)), int(m.group(2))
    if not 1 <= count <= MAX_DAMAGE_DICE or sides not in DAMAGE_SIDES:
        return None
    bonus = int(m.group(4) or 0) * (-1 if m.group(3) == '-' else 1)
    return count, sides, bonus


def clean_damage(raw):
    """The normal form of a damage roll ('2d6+3'), or '' when there is none or it is not understood."""
    parsed = parse_damage(raw)
    if not parsed:
        return ''
    count, sides, bonus = parsed
    return '%dd%d%s' % (count, sides, ('%+d' % bonus) if bonus else '')


def clean_attack(name, bonus, damage, damage_type):
    """One attack as stored, or None when it has no name."""
    name = ' '.join(str(name or '').split())[:40]
    if not name:
        return None
    try:
        bonus = max(-20, min(99, int(str(bonus).strip().lstrip('+') or 0)))
    except (TypeError, ValueError):
        bonus = 0
    return {'name': name, 'attack_bonus': bonus, 'damage': clean_damage(damage),
            'damage_type': ' '.join(str(damage_type or '').split())[:16]}


def clean_attacks(rows):
    """A list of (name, bonus, damage, type) or dicts -> at most MAX_ATTACKS cleaned attacks, nameless ones dropped."""
    out = []
    for r in rows or []:
        if isinstance(r, dict):
            r = (r.get('name'), r.get('attack_bonus'), r.get('damage'), r.get('damage_type'))
        a = clean_attack(*r)
        if a:
            out.append(a)
    return out[:MAX_ATTACKS]


def damage_roll_spec(damage):
    """The dice map and flat bonus for the dice route, e.g. '2d6+3' -> ({'6': 2}, 3); None without a damage roll."""
    parsed = parse_damage(damage)
    if not parsed:
        return None
    count, sides, bonus = parsed
    return {str(sides): count}, bonus
