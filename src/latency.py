"""Use only lineup information to choose a diverse first-minute research sample."""
from .lineups import normalize_name

REPEAT_OFFSETS = (10, 20, 30, 40, 50, 60)
REPEAT_PAIR_LIMIT = 12


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
        names = [normalize_name(leg['player']) for leg in pair]
        weights = [changes.get(name, 0) for name in names]
        if not any(weights):
            return 0
        # Favor confirmed starters and scorer-before-RBI relationships. Cap the
        # second leg's contribution so two new starters do not crowd out movers.
        if not all(name in after for name in names):
            return 1
        run = next(normalize_name(leg['player']) for leg in pair if leg['prop'] == '1+ run')
        rbi = next(normalize_name(leg['player']) for leg in pair if leg['prop'] == '1+ RBI')
        gap = after[rbi] - after[run]
        directional = max(0, 50 - 10 * gap) if gap > 0 else 0
        return max(weights) + min(weights) / 10 + directional

    pairs = sorted(manifest['pairs'], key=score, reverse=True)
    eligible = [pair for pair in pairs if score(pair) > 1]
    priority, scorers, rbi_batters = [], {}, {}
    # First cover different scorers; then add a second partner per scorer.
    for per_scorer in (1, 2):
        for pair in eligible:
            if pair in priority:
                continue
            run = next(normalize_name(s['player']) for s in pair if s['prop'] == '1+ run')
            rbi = next(normalize_name(s['player']) for s in pair if s['prop'] == '1+ RBI')
            if scorers.get(run, 0) >= per_scorer or rbi_batters.get(rbi, 0) >= 3:
                continue
            priority.append(pair)
            scorers[run] = scorers.get(run, 0) + 1
            rbi_batters[rbi] = rbi_batters.get(rbi, 0) + 1
            if len(priority) == REPEAT_PAIR_LIMIT:
                return priority + [pair for pair in pairs if pair not in priority], priority
    return priority + [pair for pair in pairs if pair not in priority], priority
