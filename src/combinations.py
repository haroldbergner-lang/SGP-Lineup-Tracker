"""Generate candidate pairs; only a verified FanDuel quote proves support."""
from itertools import combinations


def candidate_pairs(selections):
    # Include same-player and opposing-team pairs; let FanDuel determine support.
    return combinations(selections, 2)
