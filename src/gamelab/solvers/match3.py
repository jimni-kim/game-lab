"""Swap-and-match: the registered policies over the match3 rule model (PLAN §10, G8).

Pure like the rule model and ported with it. Swaps are tried in the rule model's order and a tie keeps the
first.
"""
from __future__ import annotations

from ..rules.match3 import apply, legal_actions

POLICIES = ("first", "greedy")


def choose(state: dict, rules: dict, policy: str, budget: int, excluded: list[dict] = ()) -> dict:
    """The move a policy picks: {"action": action | None, "nodes": boards evaluated}. `excluded` moves are
    not picked (moves the screen showed had no effect from this state).

    first: the first swap that lines pieces up, the baseline. greedy: the swap that removes the most pieces
    as far as the board decides it (what the known pieces set off as they fall), and of those the one lowest
    on the board, where what falls afterwards is most; within `budget` boards."""
    if policy not in POLICIES:
        raise ValueError("unknown_policy")
    moves = [action for action in legal_actions(state, rules) if action not in excluded]
    if not moves:
        return {"action": None, "nodes": 0}
    if policy == "first":
        return {"action": moves[0], "nodes": 1}
    best, best_key, nodes = None, None, 0
    for action in moves:
        if nodes >= budget and best is not None:
            break
        nodes += 1
        key = (apply(state, rules, action)["cleared"], action["cell"][0] + action["to"][0])
        if best_key is None or key > best_key:
            best, best_key = action, key
    return {"action": best, "nodes": nodes}
