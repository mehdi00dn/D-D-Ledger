"""Encounter budget maths for the 2024 rules (Dungeon Master's Guide, "XP Budget per Character").

No group-size multiplier any more: the party's budget is the sum of each character's budget for their level, and the
monsters' XP is added up at face value and compared with it.
"""

# level -> (Low, Moderate, High) XP per character
XP_BUDGET = {
    1: (50, 75, 100), 2: (100, 150, 200), 3: (150, 225, 400), 4: (250, 375, 500), 5: (500, 750, 1100),
    6: (600, 1000, 1400), 7: (750, 1300, 1700), 8: (1000, 1700, 2100), 9: (1300, 2000, 2600), 10: (1600, 2300, 3100),
    11: (1900, 2900, 4100), 12: (2200, 3700, 4700), 13: (2600, 4200, 5400), 14: (2900, 4900, 6200), 15: (3300, 5400, 7800),
    16: (3800, 6100, 9800), 17: (4500, 7200, 11700), 18: (5000, 8700, 14200), 19: (5500, 10700, 17200), 20: (6400, 13200, 22000),
}
TIERS = ('low', 'moderate', 'high')
TIER_NAMES = {'none': 'No monsters', 'low': 'Low', 'moderate': 'Moderate', 'high': 'High', 'over': 'Over High'}


def clamp_level(level):
    try:
        return max(1, min(20, int(level)))
    except (TypeError, ValueError):
        return 1


def budget(levels):
    """{'low': n, 'moderate': n, 'high': n}: the party's XP budget for characters of these levels."""
    out = dict.fromkeys(TIERS, 0)
    for level in levels:
        for tier, xp in zip(TIERS, XP_BUDGET[clamp_level(level)]):
            out[tier] += xp
    return out


def difficulty(total_xp, bud):
    """The lowest tier whose budget holds the total ('low' includes anything lighter), 'over' above High, 'none' for
    an encounter without monsters."""
    if total_xp <= 0:
        return 'none'
    for tier in TIERS:
        if total_xp <= bud[tier]:
            return tier
    return 'over'


def total_xp(monsters):
    """Sum of xp * count over [{'xp': n, 'count': n}]."""
    return sum(int(m['xp']) * int(m['count']) for m in monsters)
