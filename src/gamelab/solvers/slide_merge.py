"""Slide and merge: the registered policies over the slide_merge rule model (plan §7.2; adaptive
perception plan §4.2).

Pure like the rule model (standard library only, JSON in and out, no IO, no clock, no unseeded
randomness) and ported with it. Directions are tried in DIRECTIONS order and a tie keeps the first.
"""
from __future__ import annotations

from ..rules.slide_merge import DIRECTIONS, grid_value as evaluate, legal_actions, slide, spawn_outcomes

# the baseline's fixed preference: keep the big tiles down and to the left
BASELINE_ORDER = ("down", "left", "right", "up")
POLICIES = ("baseline", "expectimax")
# a line the search sees end with no move left
DEAD_END = -(1 << 30)
# the deepest look-ahead in moves, whatever the budget allows
MAX_DEPTH = 6


class _OverBudget(Exception):
    pass


def choose(state: dict, rules: dict, policy: str, budget: int, excluded: list[dict] = ()) -> dict:
    """The move a policy picks: {"action": action | None, "depth": moves looked ahead}. `excluded`
    moves are not picked (moves the screen showed had no effect from this state).

    baseline: the first legal direction in BASELINE_ORDER. expectimax: deepens one move at a time
    while the whole search fits in `budget` evaluated grids, and keeps the deepest finished answer
    (always at least one move deep)."""
    if policy not in POLICIES:
        raise ValueError("unknown_policy")
    legal = [action for action in legal_actions(state, rules) if action not in excluded]
    if not legal:
        return {"action": None, "depth": 0}
    if legal[0]["kind"] != "slide":
        return {"action": legal[0], "depth": 0}
    if policy == "baseline":
        directions = {a["direction"] for a in legal}
        return {"action": {"kind": "slide", "direction": next(d for d in BASELINE_ORDER if d in directions)}, "depth": 0}
    best, depth = None, 0
    allowed = {a["direction"] for a in legal}
    for d in range(1, MAX_DEPTH + 1):
        nodes = [0]
        try:
            found = _root(state["grid"], rules, d, nodes, budget if d > 1 else None, allowed)
        except _OverBudget:
            break
        best, depth = found, d
    return {"action": {"kind": "slide", "direction": best}, "depth": depth}


def _root(grid, rules, depth, nodes, budget, allowed) -> str:
    best_value, best = None, None
    for d in DIRECTIONS:
        if d not in allowed:
            continue
        moved = slide(grid, d)
        if not moved["moved"]:
            continue
        value = _chance(moved["grid"], rules, depth - 1, nodes, budget)
        if best_value is None or value > best_value:
            best_value, best = value, d
    return best


def _count(nodes, budget) -> None:
    nodes[0] += 1
    if budget is not None and nodes[0] > budget:
        raise _OverBudget


def _chance(grid, rules, depth, nodes, budget) -> int:
    """The weighted mean over every tile the game may add (integer division keeps it exact)."""
    _count(nodes, budget)
    if depth == 0:
        return evaluate(grid)
    outcomes = spawn_outcomes(grid, rules)
    if not outcomes:
        return evaluate(grid)
    total = weight = 0
    for o in outcomes:
        r, c = o["cell"]
        child = [list(row) for row in grid]
        child[r][c] = o["value"]
        total += o["weight"] * _best(child, rules, depth, nodes, budget)
        weight += o["weight"]
    return total // weight


def _best(grid, rules, depth, nodes, budget) -> int:
    _count(nodes, budget)
    values = [_chance(moved["grid"], rules, depth - 1, nodes, budget)
              for d in DIRECTIONS if (moved := slide(grid, d))["moved"]]
    return max(values) if values else DEAD_END
