"""Slide and merge: the registered policies over the slide_merge rule model (plan §7.2; adaptive
perception plan §4.2; PLAN §33 P2).

Pure like the rule model (standard library only, JSON in and out, no IO, no clock, no unseeded
randomness) and ported with it. Directions are tried in DIRECTIONS order and a tie keeps the first.

The search works on tile ranks (log2 of a tile, 0 for an empty cell), a grid as a tuple of rows. A line's
slide and its worth depend on that line alone, so both are worked out once per line met and kept
(`_LEFT`, `_RIGHT`, `_WORTH`): what is kept is a function of the line only, so it changes no answer.
"""
from __future__ import annotations

from math import isqrt

from ..rules.slide_merge import DIRECTIONS, _slide_line, legal_actions

# the baseline's fixed preference: keep the big tiles down and to the left
BASELINE_ORDER = ("down", "left", "right", "up")
POLICIES = ("baseline", "expectimax")
# a line of play the search sees end with no move left is worth nothing; a grid's worth stays above it
DEAD_END = 0
# the deepest look-ahead in moves, whatever the budget allows
MAX_DEPTH = 8
# the look-ahead no budget cuts short: this evaluation is made for a search, and one move deep it plays worse
# than the one before it did (PLAN §33 session P: 2048 reached in 10 games of 100 against 25 at budget 50)
MIN_DEPTH = 2
# a line's worth (PLAN §33 P2, the published hand-made evaluation: free cells, neighbours that can join, a
# line that rises or falls one way, and small tiles over large ones), in integers. A grid's worth is the sum
# over its rows and columns
LINE_BASE = 200000
EMPTY_WEIGHT = 270
MERGE_WEIGHT = 700
MONOTONIC_WEIGHT = 47
SUM_WEIGHT = 11
# a branch the game takes less often than this many times in a million is valued as it stands, not searched
RARE = 100
MILLION = 1_000_000


class _OverBudget(Exception):
    pass


def _rank(value: int) -> int:
    return value.bit_length() - 1 if value else 0


class _Left(dict):
    """A line of ranks slid toward its start."""
    def __missing__(self, line: tuple) -> tuple:
        slid, _ = _slide_line([1 << rank if rank else 0 for rank in line])
        self[line] = found = tuple(_rank(value) for value in slid)
        return found


class _Right(dict):
    def __missing__(self, line: tuple) -> tuple:
        self[line] = found = _LEFT[line[::-1]][::-1]
        return found


class _Worth(dict):
    def __missing__(self, line: tuple) -> int:
        empty = merges = counter = previous = rising = falling = total = 0
        for rank in line:
            total += isqrt(rank ** 7)                 # rank to the power 3.5, whole
            if rank == 0:
                empty += 1
                continue
            if previous == rank:
                counter += 1
            elif counter:
                merges += 1 + counter
                counter = 0
            previous = rank
        if counter:
            merges += 1 + counter
        for a, b in zip(line, line[1:]):
            if a > b:
                falling += a ** 4 - b ** 4
            else:
                rising += b ** 4 - a ** 4
        self[line] = found = (LINE_BASE + EMPTY_WEIGHT * empty + MERGE_WEIGHT * merges
                              - MONOTONIC_WEIGHT * min(rising, falling) - SUM_WEIGHT * total)
        return found


_LEFT, _RIGHT, _WORTH = _Left(), _Right(), _Worth()


def evaluate(board: tuple) -> int:
    """A grid's worth to the search (not the game's score): its rows' and columns' worth."""
    worth = _WORTH
    return sum([worth[row] for row in board]) + sum([worth[column] for column in zip(*board)])


def _slides(board: tuple) -> list[tuple[str, tuple]]:
    """Each direction that moves a tile, in DIRECTIONS order, with the grid it leaves."""
    columns = tuple(zip(*board))
    found = []
    moved = tuple([_LEFT[column] for column in columns])
    if moved != columns:
        found.append(("up", tuple(zip(*moved))))
    moved = tuple([_RIGHT[column] for column in columns])
    if moved != columns:
        found.append(("down", tuple(zip(*moved))))
    moved = tuple([_LEFT[row] for row in board])
    if moved != board:
        found.append(("left", moved))
    moved = tuple([_RIGHT[row] for row in board])
    if moved != board:
        found.append(("right", moved))
    return found


def choose(state: dict, rules: dict, policy: str, budget: int, excluded: list[dict] = ()) -> dict:
    """The move a policy picks: {"action": action | None, "depth": moves looked ahead}. `excluded`
    moves are not picked (moves the screen showed had no effect from this state).

    baseline: the first legal direction in BASELINE_ORDER. expectimax: deepens one move at a time
    while the whole search fits in `budget` grids looked at, and keeps the deepest finished answer
    (always at least MIN_DEPTH moves deep). A depth the last one's growth says will not fit is not begun."""
    if policy not in POLICIES:
        raise ValueError("unknown_policy")
    legal = [action for action in legal_actions(state, rules) if action not in excluded]
    if not legal:
        return {"action": None, "depth": 0}
    if legal[0]["kind"] != "slide":
        return {"action": legal[0], "depth": 0}
    allowed = {a["direction"] for a in legal}
    if policy == "baseline":
        return {"action": {"kind": "slide", "direction": next(d for d in BASELINE_ORDER if d in allowed)}, "depth": 0}
    board = tuple(tuple(_rank(value) for value in row) for row in state["grid"])
    weight = sum(s["weight"] for s in rules["spawn"])
    # the tiles the game may add, the likeliest first: (rank, weight)
    spawn = sorted(((_rank(s["value"]), s["weight"]) for s in rules["spawn"]), key=lambda item: -item[1])
    roots = [(d, after) for d, after in _slides(board) if d in allowed]
    best, depth, spent, before = roots[0][0], 0, 0, 0
    for d in range(1, MAX_DEPTH + 1):
        # the next depth costs about as many times this one as this one cost the one before
        if d > MIN_DEPTH and d > 2 and spent * spent > budget * max(before, 1):
            break
        nodes, seen = [0], {}
        try:
            values = [_chance(after, spawn, weight, d - 1, MILLION, nodes, budget if d > MIN_DEPTH else None, seen)
                      for _, after in roots]
        except _OverBudget:
            break
        if nodes[0] == spent:
            break                         # nothing lies deeper: every line was cut or has ended
        best, depth, before, spent = roots[values.index(max(values))][0], d, spent, nodes[0]
    return {"action": {"kind": "slide", "direction": best}, "depth": depth}


def _chance(board, spawn, weight, depth, share, nodes, budget, seen) -> int:
    """A grid's worth before the game adds its tile: the weighted mean over every tile it may add (integer
    division keeps it exact). `share` is how often play comes this way, in a million."""
    nodes[0] += 1
    if budget is not None and nodes[0] > budget:
        raise _OverBudget
    if depth == 0 or share < RARE:
        return evaluate(board)
    key = (board, depth)
    if key in seen:
        return seen[key]
    cells = [(r, c) for r, row in enumerate(board) for c, rank in enumerate(row) if rank == 0]
    if not cells:
        return evaluate(board)
    total = 0
    for rank, part in spawn:
        onward = share * part // (weight * len(cells))
        for r, c in cells:
            row = board[r]
            child = board[:r] + (row[:c] + (rank,) + row[c + 1:],) + board[r + 1:]
            total += part * _best(child, spawn, weight, depth, onward, nodes, budget, seen)
    seen[key] = found = total // (weight * len(cells))
    return found


def _best(board, spawn, weight, depth, share, nodes, budget, seen) -> int:
    nodes[0] += 1
    if budget is not None and nodes[0] > budget:
        raise _OverBudget
    values = [_chance(after, spawn, weight, depth - 1, share, nodes, budget, seen) for _, after in _slides(board)]
    return max(values) if values else DEAD_END
