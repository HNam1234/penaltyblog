"""Exact best-of series math, conditional on supplied game probabilities.

Constant p assumes independent, identically distributed games. A mapping can
instead supply P(A wins next game | current series score). Neither estimates p
from data, models a veto/draft, nor validates the independence assumption.
"""
from collections import defaultdict
from math import isfinite
from numbers import Real


def series_distribution(best_of, probability, score=(0, 0)):
    if type(best_of) is not int or best_of < 1 or best_of > 9 or best_of % 2 != 1:
        raise ValueError('best_of_must_be_odd_1_to_9')
    target = best_of // 2 + 1
    if (not isinstance(score, tuple) or len(score) != 2
            or any(type(s) is not int or not 0 <= s <= target for s in score)
            or min(score) == target):
        raise ValueError('invalid_series_score')
    pending, terminal = {score: 1.0}, defaultdict(float)
    while pending:
        next_states = defaultdict(float)
        for (a, b), mass in pending.items():
            if a == target or b == target:
                terminal[a, b] += mass
                continue
            if mass == 0:
                continue
            p = probability if isinstance(probability, Real) else probability[a, b]
            if isinstance(p, bool) or not isinstance(p, Real) or not isfinite(p) or not 0 <= p <= 1:
                raise ValueError('invalid_game_probability')
            next_states[a + 1, b] += mass * p
            next_states[a, b + 1] += mass * (1 - p)
        pending = next_states
    return dict(sorted(terminal.items()))


def series_win_probability(best_of, probability, score=(0, 0)):
    return sum(p for (a, b), p in series_distribution(best_of, probability, score).items() if a > b)
