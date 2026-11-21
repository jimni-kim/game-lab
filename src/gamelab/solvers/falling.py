"""Falling blocks: the registered policies over the falling rule model (PLAN §34).

What a policy is given is the well, the piece to place and the coming pieces the game shows (its preview), and the
level's goal when one is known: the same whether the well was read off a screen by the realtime loop or is a
simulated game's (`choose`, over the family's JSON state). One placement is a turn of the piece and a column,
dropped straight down (`rules.falling.placements`); a placement's worth is how such games have long been played (the
height of all columns, the rows cleared, the holes, the unevenness: `weigh`).

  greedy      the best well after this piece alone
  lookahead   the best well after this piece and the next one the preview shows, the next placed as well as it can
              be on each well this one leaves (no preview: as greedy)

A goal (`{"lines": rows still to clear, "pieces": pieces left for it}`, either null) shifts the weight: once the
pieces are few, rows cleared on the way count above a well kept low, and a line of placements that meets the goal
counts above any that does not (`GOAL_MET`). With no piece limit the goal changes nothing: a level with all the
pieces it likes is played as a game with no end.

Pure like the rule model and ported with it. The well is kept as one integer a row here (a bit a column) so that a
look ahead stays well under a tick of the loop; what is returned is in the rule model's cells. Placements are tried
in the rule model's order and a tie keeps the first.
"""
from __future__ import annotations

from typing import Any

from ..rules.falling import STANDARD_PIECES, Cells, fall, level_spent, listed, normal, settle, stack_of, turns

POLICIES = ("greedy", "lookahead")
# the weights such games are played by (the height of all columns, lines cleared, holes, unevenness): Yiyuan Lee's,
# found by a genetic search over games of 500 pieces with one piece previewed, as ours ("Tetris AI - The (Near)
# Perfect Bot", 2013, github.com/LeeYiyuan/tetrisai, MIT). Other published weighings (Dellacherie, El-Tetris,
# BCTS's DT-10/DT-20) did no better on a level's goal, two to nine points worse on short ones (PLAN §34 F5,
# docs/reports/2026-11-21-falling-solvers-survey.md)
W_HEIGHT, W_LINES, W_HOLES, W_BUMPS = -0.510066, 0.760666, -0.35663, -0.184483
# wells weighed for one decision when none is said: a look ahead over every placement of two pieces of four cells
# in a well 10 wide is about 34 x 34
DEFAULT_BUDGET = 2000
# a line of placements that meets the level's goal, one after which the next piece fits nowhere, and one that uses
# the level's last piece without meeting it
GOAL_MET, DEAD = 1000.0, -10000.0
# with a goal and pieces this few or fewer left, each row cleared counts this much more, and each filled cell of the
# rows nearest to full (as many as rows are still to clear) this much
GOAL_SOON, W_GOAL, W_NEAR = 12, 2.0, 0.15
# (F5) on a level with a piece limit and GOAL_SOON pieces or fewer left, `lookahead` also weighs the piece after the
# one previewed, unseen: the average over the seven pieces of the best each makes of the well, on the best FIRSTS
# placements of this piece and the best SECONDS of the next on each. The goal's bonus averaged so is about the
# chance of meeting it within three pieces (as solution-finder's `percent` reckons a perfect clear's). Simulated, it
# met a level's goal 2 to 5 points more often than the two pieces alone (90.8 -> 96.0% of 500 levels of 2 rows in
# 9 pieces), at about 25 ms a piece; the third pieces are drawn evenly from the seven, as the simulated game does
EXPECT_FIRSTS, EXPECT_SECONDS = 3, 2


def weigh(stack: Cells, rows: int, cols: int, lines: int) -> float:
    heights = [rows - min((r for r, c in stack if c == col), default=rows) for col in range(cols)]
    holes = sum((r, c) not in stack for c in range(cols) for r in range(rows - heights[c], rows))
    bumps = sum(abs(a - b) for a, b in zip(heights, heights[1:]))
    return W_HEIGHT * sum(heights) + W_LINES * lines + W_HOLES * holes + W_BUMPS * bumps


# ── the well as rows of bits ─────────────────────────────────────────

def _masks(stack: Cells, rows: int) -> list[int]:
    out = [0] * rows
    for r, c in stack:
        out[r] |= 1 << c
    return out


def _shape_rows(shape: Cells) -> list[tuple[int, int]]:
    """(row, bits) of each row a shape has, its leftmost column at bit 0."""
    out: dict[int, int] = {}
    for r, c in shape:
        out[r] = out.get(r, 0) | 1 << c
    return sorted(out.items())


def _drop(well: list[int], shape: list[tuple[int, int]], column: int, rows: int) -> int | None:
    """The top row the shape comes to lie at when dropped in at `column` from the top; None when it does not fit
    at the top (as `fall`)."""
    low = shape[-1][0]
    def fits(top: int) -> bool:
        return top + low < rows and all(not well[top + r] & bits << column for r, bits in shape)
    if not fits(0):
        return None
    top = 0
    while fits(top + 1):
        top += 1
    return top


def _settle(well: list[int], shape: list[tuple[int, int]], column: int, top: int, full: int) -> tuple[list[int], int]:
    after = list(well)
    for r, bits in shape:
        after[top + r] |= bits << column
    kept = [row for row in after if row != full]
    gone = len(after) - len(kept)
    return ([0] * gone + kept if gone else kept), gone


def _weigh(well: list[int], cols: int, lines: int) -> tuple[float, list[int]]:
    """`weigh` of the well, with the cells each row holds (for the goal's weighing)."""
    rows = len(well)
    heights = [0] * cols
    seen = holes = 0
    counts = []
    for r, row in enumerate(well):
        holes += bin(seen & ~row).count("1")
        new = row & ~seen
        while new:
            low = new & -new
            heights[low.bit_length() - 1] = rows - r
            new ^= low
        seen |= row
        counts.append(bin(row).count("1"))
    bumps = sum(abs(a - b) for a, b in zip(heights, heights[1:]))
    return W_HEIGHT * sum(heights) + W_LINES * lines + W_HOLES * holes + W_BUMPS * bumps, counts


def _value(well: list[int], cols: int, lines: int, goal: dict | None, depth: int) -> float:
    """A well's worth after `depth` placements that cleared `lines` rows, towards the goal when there is one."""
    value, counts = _weigh(well, cols, lines)
    need, left = (goal or {}).get("lines"), (goal or {}).get("pieces")
    if need is None or left is None:
        return value
    if lines >= need:
        return GOAL_MET + value
    if left - depth <= 0:
        return -GOAL_MET + value + W_GOAL * lines
    if left > GOAL_SOON:
        return value
    near = sum(sorted(counts, reverse=True)[:need - lines])
    return value + W_GOAL * lines + W_NEAR * near


def _options(well: list[int], piece: Cells, rows: int, cols: int, only: Cells | None = None
             ) -> list[tuple[Cells, int, list[tuple[int, int]], int]]:
    """(shape, column, shape rows, top row) of each placement, in the rule model's order."""
    out = []
    for shape in ([only] if only is not None else turns(piece)):
        rows_of = _shape_rows(shape)
        for column in range(cols - max(c for _, c in shape)):
            top = _drop(well, rows_of, column, rows)
            if top is not None:
                out.append((shape, column, rows_of, top))
    return out


def place(stack: Cells, piece: Cells, rows: int, cols: int, *, policy: str = "greedy", queue: list[Cells] = (),
          goal: dict | None = None, only: Cells | None = None, budget: int = DEFAULT_BUDGET,
          excluded: list[tuple[Cells, int]] = ()) -> dict[str, Any] | None:
    """Where to put the piece: `{"shape", "column", "placed", "after", "lines", "score", "nodes"}`, the turn of it
    and the column of its leftmost cell that leave the best well when it is dropped straight down from the top.
    `queue`: the coming pieces the game shows, first first (`lookahead` places the first of them too). `goal`: the
    level's (`{"lines", "pieces"}`, what is still to clear and the pieces left for it, this one among them).
    `only`: this turn of it alone (the game would not turn it further). `excluded`: (shape, column) not to choose.
    None when it fits nowhere."""
    if policy not in POLICIES:
        raise ValueError("unknown_policy")
    full = (1 << cols) - 1
    well = _masks(stack, rows)
    first = []
    for shape, column, rows_of, top in _options(well, piece, rows, cols, only):
        if (shape, column) in excluded:
            continue
        after, lines = _settle(well, rows_of, column, top, full)
        first.append((shape, column, after, lines, _value(after, cols, lines, goal, 1)))
    if not first:
        return None
    nodes = len(first)
    coming = queue[0] if policy == "lookahead" and queue else None
    if coming is None:
        chosen = max(range(len(first)), key=lambda i: (first[i][4], -i))
        score = first[chosen][4]
    else:
        # each well this piece leaves, weighed by the best the next piece makes of it; within the budget, the
        # wells that look best now are looked ahead from first
        each = max(1, len(turns(coming)) * cols)
        ahead = sorted(range(len(first)), key=lambda i: (-first[i][4], i))[:max(1, budget // each)]
        scores, seconds = {}, {}
        met = goal and goal.get("lines") is not None
        for i in ahead:
            _, _, after, lines, alone = first[i]
            found, kept = None, []
            for _, column, rows_of, top in _options(after, coming, rows, cols):
                then, more = _settle(after, rows_of, column, top, full)
                value = _value(then, cols, lines + more, goal, 2)
                found = value if found is None or value > found else found
                kept.append((value, then, lines + more))
                nodes += 1
            if met and lines >= goal["lines"]:
                # the goal is met by this piece: nothing after it matters
                found, kept = (alone if found is None else max(found, alone)), []
            scores[i], seconds[i] = (DEAD + alone if found is None else found), kept
        if goal and goal.get("pieces") is not None and met and goal["pieces"] <= GOAL_SOON and nodes < budget:
            # near a level's end: the piece after the next weighed too, unseen (`EXPECT_FIRSTS`)
            deep = sorted(scores, key=lambda i: (-scores[i], i))[:EXPECT_FIRSTS]
            for i in deep:
                best_here = None
                for value, then, lines in sorted(seconds[i], key=lambda kept: -kept[0])[:EXPECT_SECONDS]:
                    if lines >= goal["lines"]:
                        worth = value
                    else:
                        total = 0.0
                        for third in STANDARD_PIECES:
                            most = None
                            for _, column, rows_of, top in _options(then, Cells(map(tuple, third)), rows, cols):
                                after3, more = _settle(then, rows_of, column, top, full)
                                value3 = _value(after3, cols, lines + more, goal, 3)
                                most = value3 if most is None or value3 > most else most
                                nodes += 1
                            total += DEAD + value if most is None else most
                        worth = total / len(STANDARD_PIECES)
                    best_here = worth if best_here is None or worth > best_here else best_here
                if best_here is not None:
                    scores[i] = best_here
            # the placements not looked so far ahead are left out: they were the worse on two pieces
            chosen = max(deep, key=lambda i: (scores[i], -i))
        else:
            chosen = max(scores, key=lambda i: (scores[i], -i))
        score = scores[chosen]
    shape, column = first[chosen][0], first[chosen][1]
    placed = fall(stack, Cells((r, c + column) for r, c in shape), rows, cols)
    after, lines = settle(stack, placed, rows, cols)
    return {"shape": shape, "column": column, "placed": placed, "after": after, "lines": lines, "score": score,
            "nodes": nodes}


def best(stack: Cells, piece: Cells, rows: int, cols: int, only: Cells | None = None) -> dict[str, Any] | None:
    """`place` by the greedy policy with nothing known of what comes (the loop's first policy)."""
    return place(stack, piece, rows, cols, only=only)


def goal_of(rules: dict, lines: int, pieces: int) -> dict | None:
    """The goal still to meet, from a level's (`goal_lines`, `goal_pieces`) and what was cleared and placed."""
    need, most = rules.get("goal_lines"), rules.get("goal_pieces")
    if need is None and most is None:
        return None
    return {"lines": None if need is None else max(0, need - lines),
            "pieces": None if most is None else max(0, most - pieces)}


def choose(state: dict, rules: dict, policy: str, budget: int, excluded: list[dict] = ()) -> dict:
    """The move a policy picks on the family's state: {"action": action | None, "nodes": wells weighed}."""
    if policy not in POLICIES:
        raise ValueError("unknown_policy")
    piece = state.get("piece")
    if not piece or level_spent(state, rules):
        return {"action": None, "nodes": 0}
    stack = stack_of(state["board"])
    queue = [normal(Cells(map(tuple, cells))) for cells in state.get("next") or [] if cells]
    left = [(normal(Cells(map(tuple, move["shape"]))), move["column"]) for move in excluded
            if move.get("kind") == "place" and move.get("shape")]
    found = place(stack, normal(Cells(map(tuple, piece))), rules["rows"], rules["cols"], policy=policy, queue=queue,
                  goal=goal_of(rules, state.get("lines") or 0, state.get("pieces") or 0), budget=budget,
                  excluded=left)
    if found is None:
        return {"action": None, "nodes": 0}
    return {"action": {"kind": "place", "shape": listed(found["shape"]), "column": found["column"]},
            "nodes": found["nodes"]}
