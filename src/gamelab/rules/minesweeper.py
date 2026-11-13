"""Minesweeper: the rule model, its pure rules, legal moves and transition check (plan §3.2; family expansion
plan §7.1; adaptive perception plan §3). Its policies are solvers/minesweeper.py.

Standard library only, JSON in and out, no IO, no clock, no unseeded randomness: this file is what moves
to the plugin's Rust crate later, checked by the same fixtures (tests/fixtures/game/minesweeper). Cells are
always tried row by row and neighbours in NEIGHBOURS order, and a tie keeps the first.

State: {"grid": [[cell]], "score": null, "scene": "playing" | "won" | "over"}. A cell is 0 to 8 (opened: the
mines around it), COVERED, FLAG (a covered cell the game shows marked), MINE (a mine shown on a cell nobody
opened: a page that tells where mines lie, or a game showing them once it is lost), EXPLODED (the mine
opened, which ends the game) or null (nobody could read it, never taken for covered). The game keeps no
score. Action: {"kind": "open", "cell": [row, col]} or {"kind": "new_game"}.

The replay game's state also holds `mines` (where they lie, null until the first cell is opened), which no
screen shows: `shown` leaves it out, so a policy never sees it.
"""
from __future__ import annotations

COVERED, FLAG, MINE, EXPLODED = 9, 10, 11, 12
# cells that stand for a mine whatever lies under them: a flag is the game's own mark (it marks every covered
# cell once a game is won; with `flags` off nobody else places one) and a mine shown is one
MARKED = (FLAG, MINE)
NEIGHBOURS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
DEFAULT_RULES = {"rows": 9, "cols": 9, "mines": 10, "flags": False}
# the replay game's stream of draws: a fixed linear congruential generator (Knuth's MMIX constants)
LCG_MULTIPLIER, LCG_INCREMENT = 6364136223846793005, 1442695040888963407


# ── state ─────────────────────────────────────────────────────────────

def is_open(cell) -> bool:
    """An opened cell: the number of mines around it."""
    return type(cell) is int and 0 <= cell <= 8


def _unread(state: dict) -> list[str]:
    grid = state.get("grid")
    if not isinstance(grid, list):
        return ["grid"]
    return [f"grid[{r}][{c}]" for r, row in enumerate(grid) for c, cell in enumerate(row) if cell is None]


def _lost(grid) -> bool:
    return any(cell == EXPLODED for row in grid for cell in row)


def _won(grid, rules: dict) -> bool:
    return sum(is_open(cell) for row in grid for cell in row) == rules["rows"] * rules["cols"] - rules["mines"]


def neighbours(r: int, c: int, rows: int, cols: int) -> list[tuple[int, int]]:
    return [(r + dr, c + dc) for dr, dc in NEIGHBOURS if 0 <= r + dr < rows and 0 <= c + dc < cols]


def unknown_fields(state: dict) -> list[str]:
    """Cells nobody could read; once the game is over (a mine opened, or the game's own message) no move
    is chosen from the grid, so none is waited for."""
    if state.get("scene") in ("won", "over") or (isinstance(state.get("grid"), list) and _lost(state["grid"])):
        return []
    return _unread(state)


def shown(state: dict) -> dict:
    """The state as a screen shows it: where the mines lie is not on it."""
    return {key: value for key, value in state.items() if key != "mines"}


def validate(state: dict, rules: dict) -> list[str]:
    grid, rows, cols = state.get("grid"), rules["rows"], rules["cols"]
    problems = []
    if rules["mines"] >= rows * cols:
        problems.append("rules")
    if not isinstance(grid, list) or len(grid) != rows or any(not isinstance(row, list) or len(row) != cols for row in grid):
        problems.append("grid_shape")
        return problems
    if any(cell is not None and (type(cell) is not int or not 0 <= cell <= EXPLODED) for row in grid for cell in row):
        problems.append("grid_cell")
    elif sum(cell == EXPLODED for row in grid for cell in row) > 1:
        problems.append("grid_exploded")        # the first mine opened ends the game: a screen shows one at most
    elif any(is_open(cell) and cell > sum(not is_open(grid[y][x]) for y, x in neighbours(r, c, rows, cols))
             for r, row in enumerate(grid) for c, cell in enumerate(row)):
        problems.append("grid_numbers")         # more mines around a cell than cells around it that could hold one
    if state.get("scene", "playing") not in ("playing", "won", "over"):
        problems.append("scene")
    return problems


def _require_known(grid) -> None:
    if any(cell is None for row in grid for cell in row):
        raise ValueError("unknown_cells")


# ── rules ─────────────────────────────────────────────────────────────

def legal_actions(state: dict, rules: dict) -> list[dict]:
    """Every covered cell, while the game goes on. A cell shown as a mine or marked is never opened."""
    grid = state["grid"]
    if state.get("scene", "playing") != "playing" or _lost(grid):
        return []
    _require_known(grid)
    if _won(grid, rules):
        return []
    return [{"kind": "open", "cell": [r, c]} for r, row in enumerate(grid) for c, cell in enumerate(row) if cell == COVERED]


def status(state: dict, rules: dict, goal: dict) -> dict:
    """Won when every cell without a mine is open, lost when a mine was opened. `reach_value` counts the
    cells opened."""
    scene, grid = state.get("scene", "playing"), state["grid"]
    if scene != "playing":
        return {"terminal": True, "goal": "reached" if scene == "won" else "not_reached"}
    if _lost(grid):
        return {"terminal": True, "goal": "not_reached"}
    _require_known(grid)
    won = _won(grid, rules)
    if goal.get("objective") == "reach_value":
        target = goal.get("target_value") or rules["rows"] * rules["cols"] - rules["mines"]
        reached = won or sum(is_open(cell) for row in grid for cell in row) >= target
    else:
        reached = goal.get("objective") == "play_until_end" and won
    return {"terminal": won, "goal": "reached" if reached else "not_reached"}


def assess(before: dict, action: dict, after: dict, rules: dict) -> dict:
    """Whether the screen after the move is one the move can lead to: the opened cell a number and, from
    every 0 among the cells it opened, its covered neighbours opened as well, nothing else changed; or the
    opened cell a mine, which ends the game. Once every cell without a mine is open, the game may mark the
    covered ones."""
    grid, seen = before["grid"], after.get("grid")
    if action.get("kind") == "new_game":
        if _unread(after):
            return {"effect": "unknown"}
        fresh = all(cell == COVERED for row in seen for cell in row)
        return {"effect": "confirmed" if fresh else ("absent" if seen == grid else "inconsistent")}
    r, c = action["cell"]
    if not isinstance(seen, list) or grid[r][c] != COVERED:
        return {"effect": "inconsistent"}
    if seen[r][c] == EXPLODED:
        # a game may show every other mine once lost: the cells opened before stay as they were
        kept = all(after_cell is None or after_cell == cell for row, after_row in zip(grid, seen)
                   for cell, after_cell in zip(row, after_row) if is_open(cell))
        return {"effect": "confirmed" if kept else "inconsistent"}
    if _unread(after):
        return {"effect": "unknown"}
    if seen == grid:
        return {"effect": "absent"}
    rows, cols = rules["rows"], rules["cols"]
    changed = {(y, x) for y in range(rows) for x in range(cols) if seen[y][x] != grid[y][x]}
    if not is_open(seen[r][c]) or any(grid[y][x] != COVERED for y, x in changed):
        return {"effect": "inconsistent"}
    expected, todo = set(), [(r, c)]
    while todo:
        y, x = todo.pop()
        if (y, x) in expected or grid[y][x] != COVERED or not is_open(seen[y][x]):
            continue
        expected.add((y, x))
        if seen[y][x] == 0:
            todo.extend(neighbours(y, x, rows, cols))
    opened = {(y, x) for y, x in changed if is_open(seen[y][x])}
    marked = changed - opened
    # a 0 the move opened leaves no neighbour covered
    shut = any(not is_open(seen[v][u]) for y, x in expected if seen[y][x] == 0 for v, u in neighbours(y, x, rows, cols))
    if opened != expected or shut or any(seen[y][x] not in MARKED for y, x in marked) or (marked and not _won(seen, rules)):
        return {"effect": "inconsistent"}
    return {"effect": "confirmed"}


def explain(before: dict, action: dict, after: dict, rules: dict) -> list[dict]:
    """Nothing: a cell's value is what the screen shows around it, never something the rules alone give."""
    return []


def ended(before: dict, action: dict, after: dict, rules: dict) -> bool:
    """Whether the rules alone show a move ended the game though cells after it are unread: a mine opened
    ends it whatever else the screen hides."""
    grid = after.get("grid")
    return action.get("kind") == "open" and isinstance(grid, list) and _lost(grid)


def unread_state(rules: dict, scene: str) -> dict:
    """A screen where the game's own message for `scene` fills the board's place: nothing under it read."""
    return {"grid": [[None] * rules["cols"] for _ in range(rules["rows"])], "score": None, "scene": scene}


# ── the replay game (seeded by the caller) ─────────────────────────────

def _numbers(mines: set, rows: int, cols: int, r: int, c: int) -> int:
    return sum(cell in mines for cell in neighbours(r, c, rows, cols))


def _place(rules: dict, draw: int, clear: list[tuple[int, int]]) -> list[list[int]]:
    """The mines, drawn once the first cell is opened: never on it, nor around it while there is room (the
    replay game's own guarantee; games differ in what their first move promises)."""
    rows, cols = rules["rows"], rules["cols"]
    if rows * cols - len(clear) < rules["mines"]:
        clear = clear[:1]
    cells = [(r, c) for r in range(rows) for c in range(cols) if (r, c) not in clear]
    value = draw
    for index in range(len(cells) - 1, 0, -1):
        value = (value * LCG_MULTIPLIER + LCG_INCREMENT) % (1 << 64)
        pick = (value >> 33) % (index + 1)
        cells[index], cells[pick] = cells[pick], cells[index]
    return sorted([list(cell) for cell in cells[:rules["mines"]]])


def initial_state(rules: dict, draw: int) -> dict:
    return {"grid": [[COVERED] * rules["cols"] for _ in range(rules["rows"])], "score": None, "scene": "playing",
            "mines": None}


def next_state(state: dict, rules: dict, action: dict, draw: int) -> dict:
    """The whole move as the replay game plays it: a mine opened ends the game, a number opens alone and a 0
    opens its neighbours in turn."""
    if action.get("kind") == "new_game":
        return initial_state(rules, draw)
    rows, cols = rules["rows"], rules["cols"]
    r, c = action["cell"]
    if state["grid"][r][c] != COVERED or _lost(state["grid"]):
        raise ValueError("illegal_action")
    placed = state["mines"] or _place(rules, draw, [(r, c), *neighbours(r, c, rows, cols)])
    mines = {tuple(cell) for cell in placed}
    grid = [list(row) for row in state["grid"]]
    if (r, c) in mines:
        grid[r][c] = EXPLODED
    todo = [] if (r, c) in mines else [(r, c)]
    while todo:
        y, x = todo.pop()
        if grid[y][x] != COVERED:
            continue
        grid[y][x] = _numbers(mines, rows, cols, y, x)
        if grid[y][x] == 0:
            todo.extend(neighbours(y, x, rows, cols))
    return {**state, "grid": grid, "mines": placed}


def hidden_state(state: dict) -> dict:
    """The replay's screen with one cell hidden: unread, never covered."""
    grid = [list(row) for row in state["grid"]]
    grid[0][0] = None
    return {**state, "grid": grid}


def stray_state(state: dict) -> dict:
    """The replay's stray change: a mark on the first covered cell, which no open makes before the game is won."""
    grid = [list(row) for row in state["grid"]]
    for row in grid:
        if COVERED in row:
            row[row.index(COVERED)] = FLAG
            break
    return {**state, "grid": grid}
