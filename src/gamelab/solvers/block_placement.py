"""Block placement: the registered policies over the block_placement rule model (plan §7.1; adaptive
perception plan §4.2).

Pure like the rule model (standard library only, JSON in and out, no IO, no clock, no unseeded
randomness) and ported with it. Placements are tried in the rule model's order and a tie keeps the first.
"""
from __future__ import annotations

from ..rules.block_placement import apply, board_value as evaluate, legal_actions

# the search looks this many of the best-looking moves deeper at each level
SEARCH_WIDTH = 3
POLICIES = ("greedy", "search")


def _children(state: dict, rules: dict) -> list[tuple[int, dict, dict]]:
    """Every placement with the board it leaves, best-looking first; equal values keep move order."""
    scored = []
    for action in legal_actions(state, rules):
        after = apply(state, rules, action)["state"]
        scored.append((evaluate(after["board"]), action, after))
    return sorted(scored, key=lambda item: -item[0])


def choose(state: dict, rules: dict, policy: str, budget: int, excluded: list[dict] = ()) -> dict:
    """The move a policy picks: {"action": action | None, "nodes": boards evaluated}. `excluded`
    moves are not picked (moves the screen showed had no effect from this state).

    greedy: the best board after one placement. search: the order of the remaining pieces too: it
    places every piece left in the tray, in each order, and keeps the first move of the line that
    places the most pieces and leaves the best board. Only SEARCH_WIDTH best-looking moves are
    followed at each level, and no more than `budget` boards are evaluated in all."""
    if policy not in POLICIES:
        raise ValueError("unknown_policy")
    first = [child for child in _children(state, rules) if child[1] not in excluded]
    if not first:
        return {"action": None, "nodes": 0}
    if policy == "greedy":
        return {"action": first[0][1], "nodes": len(first)}
    nodes = [len(first)]
    best_key, best_action = None, None
    for value, action, after in first[:SEARCH_WIDTH]:
        key = _line(after, rules, 1, value, nodes, budget)
        if best_key is None or key > best_key:
            best_key, best_action = key, action
    return {"action": best_action, "nodes": nodes[0]}


def _line(state: dict, rules: dict, placed: int, value: int, nodes: list[int], budget: int) -> tuple[int, int]:
    """(pieces placed, final board value) of the best line from here, within the node budget."""
    if all(piece is None for piece in state["tray"]) or nodes[0] >= budget:
        return placed, value
    children = _children(state, rules)
    nodes[0] += len(children)
    if not children:
        return placed, value
    return max(_line(after, rules, placed + 1, v, nodes, budget) for v, _, after in children[:SEARCH_WIDTH])
