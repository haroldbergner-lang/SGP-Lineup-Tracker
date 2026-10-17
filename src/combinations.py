"""Same-team candidates for lineup research; a valid quote proves support."""
from itertools import combinations


def candidate_pairs(selections):
    for first, second in combinations(selections, 2):
        # Missing team identity is not evidence that two players share a team.
        if not first.get('team') or first['team'] != second.get('team'):
            continue
        if first['prop'] == second['prop'] == '1+ hit':
            continue
        yield first, second
