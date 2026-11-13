"""Expectimax over any rule model that lists a move's outcomes (adaptive perception plan §4.2).

A general solver: it knows no game. It asks the rule model for the legal moves, each move's weighted
outcomes and a state's worth (`legal_actions`, `outcomes`, `evaluate`, `status`), so a rule model that
offers them is played without a solver of its own. Pure like the rule models: standard library only, no
IO, no clock, no randomness; moves are tried in the rule model's order and a tie keeps the first.
"""
from __future__ import annotations

NAME = "generic_expectimax"
# what the rule model must offer: every state a move may leave (one certain state counts)
REQUIRES = ("chance_enumerable",)
# a line the search sees end with the game lost
DEAD_END = -(1 << 30)
# the deepest look-ahead in moves, whatever the budget allows
MAX_DEPTH = 6
# the game's end is asked of the rule model with no goal of the run's: the search only tells a lost line apart
_GOAL = {"objective": "play_until_end"}


class _OverBudget(Exception):
    pass


def choose(state: dict, rules: dict, model, budget: int, excluded: list[dict] = ()) -> dict:
    """The move it picks: {"action": action | None, "depth": moves looked ahead, "nodes": states valued}.
    `excluded` moves are not picked. It deepens one move at a time while the whole search fits in `budget`
    valued states and keeps the deepest finished answer (always at least one move deep)."""
    legal = [action for action in model.legal_actions(state, rules) if action not in excluded]
    if not legal:
        return {"action": None, "depth": 0, "nodes": 0}
    if len(legal) == 1:
        return {"action": legal[0], "depth": 0, "nodes": 0}
    best, depth, spent = legal[0], 0, 0
    for d in range(1, MAX_DEPTH + 1):
        nodes = [0]
        try:
            values = [_after(state, action, rules, model, d, nodes, budget if d > 1 else None) for action in legal]
        except _OverBudget:
            break
        if nodes[0] == spent:
            break                        # nothing lies deeper: every line has ended
        best, depth, spent = legal[values.index(max(values))], d, nodes[0]
    return {"action": best, "depth": depth, "nodes": spent}


def _after(state, action, rules, model, depth, nodes, budget) -> int:
    """A move's worth: the weighted mean over every state it may leave (integer division keeps it exact)."""
    total = weight = 0
    for share, after in model.outcomes(state, action, rules):
        total += share * _value(after, rules, model, depth - 1, nodes, budget)
        weight += share
    return total // weight


def _value(state, rules, model, depth, nodes, budget) -> int:
    if depth == 0:
        nodes[0] += 1
        if budget is not None and nodes[0] > budget:
            raise _OverBudget
        return model.evaluate(state, rules)
    legal = model.legal_actions(state, rules)
    if not legal:
        # no move left: the game lost, or a point the rules do not look past (a tray waiting to be dealt)
        nodes[0] += 1
        return DEAD_END if model.status(state, rules, _GOAL)["terminal"] else model.evaluate(state, rules)
    return max(_after(state, action, rules, model, depth, nodes, budget) for action in legal)
