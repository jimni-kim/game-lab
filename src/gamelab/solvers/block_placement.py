"""Block placement: the registered policies over the block_placement rule model (plan §7.1; adaptive
perception plan §4.2).

Pure like the rule model (standard library only, JSON in and out, no IO, no clock, no unseeded
randomness) and ported with it. Placements are tried in the rule model's order and a tie keeps the first.
"""
from __future__ import annotations

from ..rules.block_placement import EMPTY, STANDARD_PIECES, apply, board_value as evaluate, legal_actions, orientations

# the search looks this many of the best-looking moves deeper at each level
SEARCH_WIDTH = 3
POLICIES = ("greedy", "search")
# where a line of the search ends, its board is also worth this much for each piece of the replay game's set that
# still fits somewhere on it (PLAN §33 P5): the next tray is not known, and a board that takes most pieces is
# the one a tray dealt at random least often ends
ROOM_WEIGHT = 30
_MASKS: dict = {}


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
    """(pieces placed, final board value) of the best line from here, within the node budget; the board a line
    ends on is worth the room it leaves too (`_room`)."""
    if all(piece is None for piece in state["tray"]) or nodes[0] >= budget:
        return placed, value + ROOM_WEIGHT * _room(state["board"], rules)
    children = _children(state, rules)
    nodes[0] += len(children)
    if not children:
        return placed, value + ROOM_WEIGHT * _room(state["board"], rules)
    return max(_line(after, rules, placed + 1, v, nodes, budget) for v, _, after in children[:SEARCH_WIDTH])


def _room(board, rules: dict) -> int:
    """How many pieces of the replay game's set (`STANDARD_PIECES`) fit somewhere on the board."""
    rows, cols = len(board), len(board[0])
    masks = _MASKS.get((rows, cols, rules["rotation"]))
    if masks is None:
        masks = _MASKS[(rows, cols, rules["rotation"])] = [
            [sum(1 << ((r + dr) * cols + c + dc) for dr, dc in cells)
             for cells in orientations(piece, rules["rotation"])
             for r in range(rows - max(dr for dr, _ in cells)) for c in range(cols - max(dc for _, dc in cells))]
            for piece in STANDARD_PIECES]
    filled = sum(1 << (r * cols + c) for r in range(rows) for c in range(cols) if board[r][c] != EMPTY)
    return sum(any(not mask & filled for mask in piece) for piece in masks)
