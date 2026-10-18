"""Prioritize changed players without changing the saved wager universe."""
from .lineups import normalize_name

REPEAT_OFFSETS = (10, 20, 30, 40, 50, 60)
REPEAT_PAIR_LIMIT = 8


def ranked_pairs(manifest, state, side):
    projected = state.get('projected', {}).get(side, {}).get('players')
    confirmed = state.get('confirmed', {}).get(side, {}).get('players', [])
    if projected is None or not confirmed:
        return list(manifest['pairs']), []
    before = {normalize_name(p['name']): p['position'] for p in projected}
    after = {normalize_name(p['name']): p['position'] for p in confirmed}
    changes = {name: (100 if name not in before or name not in after
                      else 10 * abs(after[name] - before[name]))
               for name in before.keys() | after.keys()}

    def score(pair):
        return sum(changes.get(normalize_name(leg['player']), 0) for leg in pair)

    pairs = sorted(manifest['pairs'], key=score, reverse=True)
    priority = [pair for pair in pairs if score(pair) > 0][:REPEAT_PAIR_LIMIT]
    return pairs, priority
