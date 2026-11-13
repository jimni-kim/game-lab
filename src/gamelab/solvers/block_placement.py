"""Block placement: the registered policies over the block_placement rule model (plan §7.1; adaptive
perception plan §4.2).

Pure like the rule model (standard library only, JSON in and out, no IO, no clock, no unseeded
randomness) and ported with it. Placements are tried in the rule model's order and a tie keeps the first.
"""
from __future__ import annotations

from itertools import product

from ..rules.block_placement import EMPTY, apply, legal_actions

# board evaluation: free cells are worth keeping, single shut-in holes and ragged edges are not
EMPTY_WEIGHT = 2
HOLE_PENALTY = 5
EDGE_PENALTY = 1
# the search looks this many of the best-looking moves deeper at each level
SEARCH_WIDTH = 3
POLICIES = ("greedy", "search")


def evaluate(board) -> int:
    """A board's worth to the policies (not the game's score): free cells, minus shut-in holes and edges."""
    rows, cols = len(board), len(board[0])
    empties = holes = edges = 0
    for r, c in product(range(rows), range(cols)):
        if board[r][c] == EMPTY:
            empties += 1
            around = [(r + dr, c + dc) for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1))]
            if all(not (0 <= y < rows and 0 <= x < cols) or board[y][x] != EMPTY for y, x in around):
                holes += 1
        if c + 1 < cols and (board[r][c] == EMPTY) != (board[r][c + 1] == EMPTY):
            edges += 1
        if r + 1 < rows and (board[r][c] == EMPTY) != (board[r + 1][c] == EMPTY):
            edges += 1
    return EMPTY_WEIGHT * empties - HOLE_PENALTY * holes - EDGE_PENALTY * edges


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
