"""Same-team, different-player run–RBI pairs; a valid quote proves support."""
from itertools import combinations

from .lineups import normalize_name


def candidate_pairs(selections):
    for first, second in combinations(selections, 2):
        if not first.get('team') or first['team'] != second.get('team'):
            continue
        if {first['prop'], second['prop']} != {'1+ run', '1+ RBI'}:
            continue
        names = [normalize_name(leg.get('player', '')) for leg in (first, second)]
        if not all(names) or names[0] == names[1]:
            continue
        # Consistent output: scorer first, RBI batter second. Both player directions
        # remain distinct when the source offers both props for each player.
        yield (first, second) if first['prop'] == '1+ run' else (second, first)
