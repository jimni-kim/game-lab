"""Swap-and-match: the registered policies over the match3 rule model (PLAN §10, G8; §23, Y4).

Pure like the rule model and ported with it. Swaps are tried in the rule model's order and a tie keeps the
first.

A move is worth, in this order and each far above the next (PLAN §23, Y4; the weights of `match3-lab`'s greedy
bot): meeting the level's goals, getting on with them, a piece with a power (made, or two set off together), the
pieces it clears. What comes in at the top is not known, so a move is also worth a part of the best move it
leaves among the pieces known: a line that takes a cell nobody knows yet is no line (`_glance`). Lower on the
board only parts moves otherwise alike.
"""
from __future__ import annotations

from ..rules.match3 import (AREA, BLOCK, COLOUR, COUNTED, HELD, LINE, POWERED, SEEKER, _fall, _line, _piece, _runs,
                            _square_through, _swapped, advance, apply, colour_of, legal_actions, progress, way)

POLICIES = ("first", "greedy", "sampled")
# what a piece with a power is counted as when a move earns one: about the pieces it clears when used
WORTH = {LINE: 6, SEEKER: 3, AREA: 7, COLOUR: 10}
# a move that meets every goal; one goal's thing taken; a piece with a power, by what it is worth (WORTH: 6 a line);
# two set off together; a piece cleared
WIN, GOAL, POWER, PAIR, PIECE = 100_000.0, 100.0, 2.5, 60.0, 1.0
# a piece held in place set free: more than a piece cleared, since it opens the board
FREED = 3.0
# the moves a model is shown to pick among when the goals are none the code can weigh (`rank`)
RANKED = 6
# a move is also worth this share of the best move it leaves among the pieces known, of the best moves it was
# found among this many
AHEAD, AHEAD_MOST = 0.5, 12
# lower on the board parts moves worth the same, by at most this much
LOW = 0.01
# sampled (PLAN §33 P6): the greedy policy's best this many moves, each played on as many fills of what comes in
# at the top, the same fills for every move (a move is never weighed by pieces it alone was shown), and of the
# best moves each filled board offers, this many weighed by the move they leave too
SAMPLED_MOST, SAMPLES, SAMPLED_NEXT = 5, 6, 3
# and the share of the best move a filled board leaves that a move is worth
NEXT_SHARE = 0.5
# the fills' streams: fixed, mixed with the board so that one board's fills are not every board's
SAMPLE_SEEDS = (0x9E3779B97F4A7C15, 0xBF58476D1CE4E5B9, 0x94D049BB133111EB, 0xD6E8FEB86659FD93,
                0xA0761D6478BD642F, 0xE7037ED1A0B428DB, 0x8EBC6AF09C88C6E3, 0x589965CC75374CC3)


def choose(state: dict, rules: dict, policy: str, budget: int, excluded: list[dict] = ()) -> dict:
    """The move a policy picks: {"action": action | None, "nodes": boards evaluated}. `excluded` moves are
    not picked (moves the screen showed had no effect from this state).

    first: the first swap that lines pieces up, the baseline. greedy: the swap worth most (see the module), each
    weighed by what the known pieces decide and, for the best AHEAD_MOST, by the best move it leaves; within
    `budget` boards."""
    if policy not in POLICIES:
        raise ValueError("unknown_policy")
    moves = [action for action in legal_actions(state, rules) if action not in excluded]
    if not moves:
        return {"action": None, "nodes": 0}
    if policy == "first":
        return {"action": moves[0], "nodes": 1}
    ranked, nodes = _ranked(state, rules, moves, budget)
    if policy == "sampled" and len(ranked) > 1:
        return _sampled(state, rules, ranked[:SAMPLED_MOST], nodes)
    return {"action": ranked[0]["action"], "nodes": nodes}


def _sampled(state: dict, rules: dict, ranked: list[dict], nodes: int) -> dict:
    """Of the greedy policy's best moves, the one worth most over SAMPLES fills of what comes in: what the move
    takes with the lines its fill sets off, then the best move the filled board leaves, weighed by what is still
    to go once this one is played and by the move that one leaves among the pieces known."""
    goals = rules.get("goals") or []
    rows = len(state["grid"])
    colours = rules.get("colours") or max(1, len({colour_of(cell) for row in state["grid"] for cell in row
                                                  if _piece(cell) and colour_of(cell)}))
    mixed = sum((index + 1) * (cell or 0) for index, cell in enumerate(cell for row in state["grid"] for cell in row))
    best, chosen = None, ranked[0]["action"]
    for entry in ranked:
        action = entry["action"]
        result = apply(state, rules, action)
        pair = PAIR if way(state, rules, action) == "pair" else 0.0
        total = 0.0
        for seed in SAMPLE_SEEDS[:SAMPLES]:
            grid, taken, cleared = _filled(result, colours, (seed ^ mixed) & ((1 << 64) - 1))
            worth = _worth(taken, cleared, result["made"], goals, rules, result["lowered"]) + pair
            left = advance(goals, taken) if goals else goals
            done = goals and all(goal.get("count") == 0 for goal in left if goal.get("kind") in COUNTED)
            nodes += 1
            total += worth + (0.0 if done else NEXT_SHARE * _next({**result["state"], "grid": grid}, rules, left))
        low = (action["cell"][0] + action.get("to", action["cell"])[0]) / (2 * rows)
        key = total / SAMPLES + LOW * low
        if best is None or key > best:
            best, chosen = key, action
    return {"action": chosen, "nodes": nodes}


def _filled(result: dict, colours: int, seed: int) -> tuple[list, dict, int]:
    """The board a move leaves once the cells it left unread are filled from `seed` and every line the new pieces
    make is removed in turn, with what the move took and cleared in all."""
    grid, taken, cleared = result["state"]["grid"], dict(result["taken"]), result["cleared"]
    value = seed
    while True:
        filled = []
        for row in grid:
            out = []
            for cell in row:
                if cell is None:
                    value = (value * 6364136223846793005 + 1442695040888963407) % (1 << 64)
                    cell = 1 + (value >> 33) % colours
                out.append(cell)
            filled.append(out)
        grid = filled
        removed = _runs(grid)
        if not removed:
            return grid, taken, cleared
        cleared += len(removed)
        for r, c in removed:
            taken[colour_of(grid[r][c])] = taken.get(colour_of(grid[r][c]), 0) + 1
        grid = _fall(grid, removed)


def _next(state: dict, rules: dict, goals: list) -> float:
    """The worth of the best move a filled board leaves under what is still to go, its SAMPLED_NEXT best each also
    worth AHEAD of the move it leaves among the pieces known; 0 when it leaves none. The layer under the pieces is
    taken as the move left it, before its fill's lines."""
    scored = []
    for action in legal_actions(state, rules):
        result = apply(state, rules, action)
        worth = _worth(result["taken"], result["cleared"], result["made"], goals, rules, result["lowered"])
        scored.append((worth + (PAIR if way(state, rules, action) == "pair" else 0.0), result))
    scored.sort(key=lambda entry: -entry[0])
    return max((worth + AHEAD * _ahead(result["state"]["grid"], rules, goals) for worth, result in scored[:SAMPLED_NEXT]),
               default=0.0)


def rank(state: dict, rules: dict, budget: int, excluded: list[dict] = (), most: int = RANKED) -> list[dict]:
    """The best moves as the greedy policy weighs them, best first, each with what the rules say it does: for
    whoever picks among them knowing what the code does not (PLAN §31, L3)."""
    moves = [action for action in legal_actions(state, rules) if action not in excluded]
    return _ranked(state, rules, moves, budget)[0][:most] if moves else []


def _ranked(state: dict, rules: dict, moves: list[dict], budget: int) -> tuple[list[dict], int]:
    goals = rules.get("goals") or []
    rows = len(state["grid"])
    scored, nodes = [], 0
    for index, action in enumerate(moves):
        if nodes >= budget and scored:
            break
        nodes += 1
        result = apply(state, rules, action)
        worth = _worth(result["taken"], result["cleared"], result["made"], goals, rules, result["lowered"])
        if way(state, rules, action) == "pair":
            worth += PAIR
        scored.append((worth, index, action, result))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    ranked = []
    for worth, index, action, result in scored[:AHEAD_MOST] if nodes < budget else scored[:1]:
        nodes += 1
        later = _ahead(result["state"]["grid"], rules, goals)
        low = (action["cell"][0] + action.get("to", action["cell"])[0]) / (2 * rows)
        ranked.append({"action": action, "key": worth + AHEAD * later + LOW * low, "index": index,
                       "cleared": result["cleared"], "made": result["made"] // POWERED, "taken": result["taken"],
                       "towards_goals": progress(goals, result["taken"], result["lowered"], rules.get("rows", 8))})
    ranked.sort(key=lambda entry: (-entry["key"], entry["index"]))
    return ranked, nodes


def _worth(taken: dict, cleared: int, made: int, goals: list, rules: dict, lowered: dict | None = None) -> float:
    """What a move that takes `taken`, clears `cleared` and earns `made` is worth (see the module). A thing fixed
    hit beside a line counts towards a goal about it; when no goal says what such things are for, a little, like
    a piece cleared."""
    gained = progress(goals, taken, lowered, rules.get("rows", 8))
    worth = GOAL * gained + POWER * WORTH.get(made // POWERED, 0) + PIECE * cleared
    if not any(goal.get("target") == BLOCK for goal in goals):
        worth += PIECE * taken.get(BLOCK, 0)
    worth += FREED * taken.get(HELD, 0)
    collect = [goal for goal in goals if goal.get("kind") in COUNTED and goal.get("target") is not None
               and goal.get("count") is not None]
    if collect and all(taken.get(goal["target"], 0) >= goal["count"] for goal in collect) \
            and len(collect) == len([goal for goal in goals if goal.get("kind") != "other"]):
        worth += WIN
    return worth


def _ahead(grid: list, rules: dict, goals: list) -> float:
    """The worth of the best move a board leaves among the pieces known (`_glance`); 0 when it leaves none."""
    best = 0.0
    for r in range(len(grid)):
        for c in range(len(grid[0])):
            for r2, c2 in ((r, c + 1), (r + 1, c)):
                if r2 < len(grid) and c2 < len(grid[0]):
                    seen = _glance(grid, (r, c), (r2, c2), rules)
                    if seen is not None:
                        best = max(best, _worth(seen[0], seen[1], seen[2], goals, rules))
    return best


def _glance(grid: list, cell: tuple[int, int], to: tuple[int, int], rules: dict) -> tuple[dict, int, int] | None:
    """What swapping two known pieces of a board some of whose cells nobody knows yet takes at once: the lines
    through the two, made of known pieces only (a cell nobody knows lines up with nothing), and the power the
    first earns; nothing of what falls after. None for a swap that lines nothing up so."""
    (r, c), (r2, c2) = cell, to
    if not _piece(grid[r][c]) or not _piece(grid[r2][c2]) or grid[r][c] == grid[r2][c2]:
        return None
    swapped = _swapped(grid, cell, to)
    taken, cleared, made = {}, 0, 0
    for y, x in (cell, to):
        across, down = _line(swapped, (y, x))
        colour = colour_of(swapped[y][x])
        count = (across if across >= 3 else 0) + (down if down >= 3 else 0)
        if not count and rules.get("square") and _square_through(swapped, (y, x)):
            count = 4
        if count:
            cleared += count
            taken[colour] = taken.get(colour, 0) + count
            if not made:
                made = POWERED * (COLOUR if max(across, down) >= 5 else AREA if across >= 3 and down >= 3
                                  else LINE if max(across, down) == 4 else SEEKER if count == 4 else 0)
    return (taken, cleared, made) if cleared else None
