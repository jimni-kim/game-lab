"""Slide and merge: the rule model, its pure rules, legal moves and transition check (plan §3.2, §7.2;
adaptive perception plan §3). Its policies are solvers/slide_merge.py.

Standard library only, JSON in and out, no IO, no clock, no unseeded randomness: this file is what
moves to the plugin's Rust crate later, checked by the same fixtures (tests/fixtures/game/
slide_merge). Directions are always tried in DIRECTIONS order, cells row by row, and a tie keeps
the first.

State: {"grid": [[0 | 2 | 4 | … | null]], "score": int | null, "scene": "playing" | "won" | "over"}.
0 is an empty cell, null one nobody could read (never taken for empty). A "won" or "over" scene is the
game's own message that stops play: cells it covers stay null, and no move is chosen from them. Action: {"kind": "slide",
"direction"}, {"kind": "continue_after_win"} or {"kind": "new_game"}. A new tile is checked against
the set of tiles the game can add, not one predicted screen; the spawn weights only steer the search.
"""
from __future__ import annotations

from itertools import pairwise, product

DIRECTIONS = ("up", "down", "left", "right")
DEFAULT_RULES = {"size": 4, "target": 2048, "start_tiles": 2,
                 "spawn": [{"value": 2, "weight": 9}, {"value": 4, "weight": 1}]}
# what a general solver may rely on (`solvers.fits`): `outcomes` lists every state a move may leave
CAPABILITIES = ("chance_enumerable",)
# board evaluation, in tile ranks (log2) so it stays an integer: free cells, neighbours that can
# join, rows and columns that rise or fall one way, and the biggest tile kept in a corner
EMPTY_WEIGHT = 27
MERGE_WEIGHT = 7
MONOTONIC_PENALTY = 4
CORNER_WEIGHT = 10


# ── state ─────────────────────────────────────────────────────────────

def unknown_fields(state: dict) -> list[str]:
    """Cells nobody could read; while the game's own message stops play no move is chosen from the grid,
    so none is waited for."""
    if state.get("scene") in ("won", "over"):
        return []
    return _unread(state)


def _unread(state: dict) -> list[str]:
    grid = state.get("grid")
    if not isinstance(grid, list):
        return ["grid"]
    return [f"grid[{r}][{c}]" for r, row in enumerate(grid) for c, cell in enumerate(row) if cell is None]


def validate(state: dict, rules: dict) -> list[str]:
    grid, size = state.get("grid"), rules["size"]
    problems = []
    if not isinstance(grid, list) or len(grid) != size or any(not isinstance(row, list) or len(row) != size for row in grid):
        problems.append("grid_shape")
    elif any(cell is not None and (not isinstance(cell, int) or cell < 0 or (cell and cell & (cell - 1)))
             for row in grid for cell in row):
        problems.append("grid_cell")            # a tile is 0 or a power of two
    if state.get("scene", "playing") not in ("playing", "won", "over"):
        problems.append("scene")
    return problems


def _require_known(grid) -> None:
    if any(cell is None for row in grid for cell in row):
        raise ValueError("unknown_cells")


# ── rules ─────────────────────────────────────────────────────────────

def _slide_line(line: list[int]) -> tuple[list[int], int]:
    """One line toward its start: tiles close up, equal neighbours join once, the join's value scores."""
    tiles = [v for v in line if v]
    out, gained, i = [], 0, 0
    while i < len(tiles):
        if i + 1 < len(tiles) and tiles[i] == tiles[i + 1]:
            out.append(tiles[i] * 2)
            gained += tiles[i] * 2
            i += 2
        else:
            out.append(tiles[i])
            i += 1
    return out + [0] * (len(line) - len(out)), gained


def slide(grid, direction: str) -> dict:
    _require_known(grid)
    if direction not in DIRECTIONS:
        raise ValueError("illegal_action")
    size = len(grid)
    out = [[0] * size for _ in range(size)]
    gained = 0
    for i in range(size):
        if direction in ("left", "right"):
            cells = [(i, j) for j in range(size)]
        else:
            cells = [(j, i) for j in range(size)]
        if direction in ("right", "down"):
            cells.reverse()
        line, got = _slide_line([grid[r][c] for r, c in cells])
        gained += got
        for (r, c), v in zip(cells, line):
            out[r][c] = v
    return {"grid": out, "gained": gained, "moved": out != [list(row) for row in grid]}


def legal_actions(state: dict, rules: dict) -> list[dict]:
    scene = state.get("scene", "playing")
    if scene == "over":
        return []
    if scene == "won":
        return [{"kind": "continue_after_win"}]
    _require_known(state["grid"])
    return [{"kind": "slide", "direction": d} for d in DIRECTIONS if slide(state["grid"], d)["moved"]]


def spawn_outcomes(grid, rules: dict) -> list[dict]:
    """Every tile the game may add: each empty cell with each spawn value."""
    _require_known(grid)
    return [{"cell": [r, c], "value": s["value"], "weight": s["weight"]}
            for r, row in enumerate(grid) for c, cell in enumerate(row) if cell == 0
            for s in rules["spawn"]]


def max_tile(grid) -> int:
    return max(max(row) for row in grid)


def outcomes(state: dict, action: dict, rules: dict) -> list[list]:
    """Every state a move may leave, each with its weight: `[[weight, state]]`, the slide followed by each
    tile the game may add. The scene the game then shows (its win or end message) is not predicted."""
    if action.get("kind") == "continue_after_win":
        return [[1, {**state, "scene": "playing"}]]
    moved = slide(state["grid"], action.get("direction"))
    if action.get("kind") != "slide" or not moved["moved"]:
        raise ValueError("illegal_action")
    score = None if state.get("score") is None else state["score"] + moved["gained"]
    slid = {**state, "grid": moved["grid"], "score": score}
    found = []
    for spawned in spawn_outcomes(moved["grid"], rules):
        r, c = spawned["cell"]
        grid = [list(row) for row in moved["grid"]]
        grid[r][c] = spawned["value"]
        found.append([spawned["weight"], {**slid, "grid": grid}])
    return found or [[1, slid]]


def _rank(v: int) -> int:
    return v.bit_length() - 1 if v else 0


def grid_value(grid) -> int:
    """A grid's worth to the policies (not the game's score), as an integer."""
    size = len(grid)
    empties = sum(1 for row in grid for v in row if v == 0)
    merges = penalty = 0
    lines = [list(row) for row in grid] + [[grid[r][c] for r in range(size)] for c in range(size)]
    for line in lines:
        ranks = [_rank(v) for v in line]
        merges += sum(1 for a, b in pairwise(line) if a and a == b)
        rising = sum(max(0, b - a) for a, b in pairwise(ranks))
        falling = sum(max(0, a - b) for a, b in pairwise(ranks))
        penalty += min(rising, falling)
    top = max_tile(grid)
    corner = _rank(top) if top in (grid[0][0], grid[0][-1], grid[-1][0], grid[-1][-1]) else 0
    return EMPTY_WEIGHT * empties + MERGE_WEIGHT * merges - MONOTONIC_PENALTY * penalty + CORNER_WEIGHT * corner


def evaluate(state: dict, rules: dict) -> int:
    """A state's worth to a search (not the game's score)."""
    return grid_value(state["grid"])


def status(state: dict, rules: dict, goal: dict) -> dict:
    scene = state.get("scene", "playing")
    if scene == "playing":
        _require_known(state["grid"])
    terminal = scene == "over" or (scene == "playing" and not legal_actions(state, rules))
    reached = "not_reached"
    if goal.get("objective") == "reach_value":
        target = goal.get("target_value") or rules["target"]
        if not _unread(state):
            reached = "reached" if max_tile(state["grid"]) >= target else "not_reached"
        else:
            # a message over the tiles says only what it says: the win message, that the game's own target
            # tile was made; the end message, nothing about how far the game got
            reached = "reached" if scene == "won" and target <= rules["target"] else "unknown"
    return {"terminal": terminal, "goal": reached}


def assess(before: dict, action: dict, after: dict, rules: dict) -> dict:
    """Whether the screen after the move is one the move can lead to: the slid grid plus exactly one
    new tile of an allowed value on a cell the slide left empty, and the score raised by the joins."""
    if _unread(after):
        return {"effect": "unknown"}
    kind = action.get("kind")
    if kind == "continue_after_win":
        # behind a win message the grid was not seen, so only the message going away can be confirmed
        if after.get("scene") == "playing" and (_unread(before) or after["grid"] == before["grid"]):
            return {"effect": "confirmed"}
        return {"effect": "absent" if after == before else "inconsistent"}
    if kind == "new_game":
        tiles = sum(1 for row in after["grid"] for cell in row if cell)
        values = {s["value"] for s in rules["spawn"]}
        fresh = tiles == rules["start_tiles"] and all(cell in values or cell == 0 for row in after["grid"] for cell in row)
        return {"effect": "confirmed" if fresh else ("absent" if after == before else "inconsistent")}
    moved = slide(before["grid"], action["direction"])
    if after["grid"] == before["grid"]:
        return {"effect": "absent"}
    if not moved["moved"] or not _one_spawn(moved["grid"], after["grid"], rules):
        return {"effect": "inconsistent"}
    if before.get("score") is not None and after.get("score") is not None \
            and after["score"] != before["score"] + moved["gained"]:
        return {"effect": "inconsistent"}
    return {"effect": "confirmed"}


def explain(before: dict, action: dict, after: dict, rules: dict) -> list[dict]:
    """The values the rules give the cells nobody could read after a slide, when everything read agrees
    with it: the slid grid, and at most one new tile of an allowed value on a cell it left empty. A new
    tile can only come from the game's own joins, so a colour no profile lists shows the value the join
    made. Nothing ([]) when the slide leaves an unread cell empty or anything read disagrees."""
    grid = after.get("grid")
    if action.get("kind") != "slide" or action.get("direction") not in DIRECTIONS or _unread(before) \
            or after.get("scene") != "playing" or not isinstance(grid, list):
        return []
    slid = slide(before["grid"], action["direction"])["grid"]
    if len(grid) != len(slid) or any(not isinstance(row, list) or len(row) != len(slid) for row in grid):
        return []
    unread = [(r, c) for r, row in enumerate(grid) for c, cell in enumerate(row) if cell is None]
    changed = [(r, c) for r, row in enumerate(grid) for c, cell in enumerate(row)
               if cell is not None and cell != slid[r][c]]
    values = {s["value"] for s in rules["spawn"]}
    if not unread or any(slid[r][c] == 0 for r, c in unread) or len(changed) > 1 \
            or any(slid[r][c] != 0 or grid[r][c] not in values for r, c in changed):
        return []
    return [{"cell": [r, c], "value": slid[r][c]} for r, c in unread]


def ended(before: dict, action: dict, after: dict, rules: dict) -> bool:
    """Whether the rules alone show a slide ended the game though cells after it are unread (a tile
    still popping in, a message over some tiles): every read cell is the slid grid or its one new tile,
    and every way the rules fill the unread cells (the slid tiles, and the new tile on any cell the slide
    left empty, of any value it may have) leaves no move."""
    grid = after.get("grid")
    if action.get("kind") != "slide" or action.get("direction") not in DIRECTIONS \
            or after.get("scene", "playing") != "playing" or _unread(before) or not isinstance(grid, list) \
            or not _unread(after):
        return False
    moved = slide(before["grid"], action["direction"])
    slid = moved["grid"]
    if not moved["moved"] or len(grid) != len(slid) \
            or any(not isinstance(row, list) or len(row) != len(slid) for row in grid):
        return False
    values = sorted({s["value"] for s in rules["spawn"]})
    cells = list(product(range(len(slid)), repeat=2))
    changed = [(r, c) for r, c in cells if grid[r][c] is not None and grid[r][c] != slid[r][c]]
    if len(changed) > 1 or any(slid[r][c] != 0 or grid[r][c] not in values for r, c in changed):
        return False
    # the new tile is the one read cell the slide did not make, or else on an unread cell it left empty
    places = changed or [(r, c) for r, c in cells if grid[r][c] is None and slid[r][c] == 0]
    for r, c in places:
        for value in [grid[r][c]] if changed else values:
            filled = [list(row) for row in slid]
            filled[r][c] = value
            if legal_actions({"grid": filled, "scene": "playing"}, rules):
                return False
    return bool(places)


def _one_spawn(slid, seen, rules) -> bool:
    values = {s["value"] for s in rules["spawn"]}
    changed = [(r, c) for r, c in product(range(len(slid)), range(len(slid))) if slid[r][c] != seen[r][c]]
    return len(changed) == 1 and slid[changed[0][0]][changed[0][1]] == 0 and seen[changed[0][0]][changed[0][1]] in values


# ── the replay game's own spawning (seeded by the caller) ──────────────

def _spawn(grid, rules: dict, draw: int):
    outcomes = spawn_outcomes(grid, rules)
    if not outcomes:
        return grid
    cells = len(outcomes) // len(rules["spawn"])
    total = sum(s["weight"] for s in rules["spawn"])
    cell, pick = divmod(draw % (cells * total), total)
    for s in rules["spawn"]:
        if pick < s["weight"]:
            value = s["value"]
            break
        pick -= s["weight"]
    r, c = outcomes[cell * len(rules["spawn"])]["cell"]
    out = [list(row) for row in grid]
    out[r][c] = value
    return out


def unread_state(rules: dict, scene: str) -> dict:
    """A screen where the game's own message for `scene` fills the board's place: nothing under it read."""
    return {"grid": [[None] * rules["size"] for _ in range(rules["size"])], "score": None, "scene": scene}


def initial_state(rules: dict, draw: int) -> dict:
    grid = [[0] * rules["size"] for _ in range(rules["size"])]
    for _ in range(rules["start_tiles"]):
        draw, part = divmod(draw, 1 << 16)
        grid = _spawn(grid, rules, part)
    return {"grid": grid, "score": 0, "scene": "playing"}


def next_state(state: dict, rules: dict, action: dict, draw: int) -> dict:
    """The whole move as the replay game plays it: slide, score, one new tile, then the scene."""
    kind = action.get("kind")
    if kind == "new_game":
        return initial_state(rules, draw)
    if kind == "continue_after_win":
        return {**state, "scene": "playing"}
    moved = slide(state["grid"], action["direction"])
    if not moved["moved"]:
        raise ValueError("illegal_action")
    grid = _spawn(moved["grid"], rules, draw)
    after = {**state, "grid": grid, "score": (state.get("score") or 0) + moved["gained"]}
    if max_tile(state["grid"]) < rules["target"] <= max_tile(grid):      # the win shows once
        return {**after, "scene": "won"}
    if not legal_actions({**after, "scene": "playing"}, rules):
        return {**after, "scene": "over"}
    return after


def shown(state: dict) -> dict:
    """The state as a screen shows it: every tile the replay game holds is on its screen."""
    return state


def hidden_state(state: dict) -> dict:
    """The replay's screen with one cell hidden: unread, never empty."""
    grid = [list(row) for row in state["grid"]]
    grid[0][0] = None
    return {**state, "grid": grid}


def stray_state(state: dict) -> dict:
    """The replay's stray change: one more tile than the move made, on the first empty cell, of a value
    already on the grid."""
    grid = [list(row) for row in state["grid"]]
    value = max_tile(grid)
    for row in grid:
        if 0 in row:
            row[row.index(0)] = value
            break
    return {**state, "grid": grid}
