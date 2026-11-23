"""Sokoban: the rule model, its pure rules, legal moves and transition check (plan §3.2; adaptive perception plan
§3). Its policies are solvers/sokoban.py.

Standard library only, JSON in and out, no IO, no clock, no unseeded randomness: this file is what moves to the
plugin's Rust crate later, checked by the same fixtures (tests/fixtures/game/sokoban). Directions are always tried
in DIRECTIONS order and cells row by row.

State: {"grid": [[cell]], "score": null, "scene": "playing" | "won"}. A cell is FLOOR, WALL (a wall, or anything
outside the walkable area: the rules treat both alike), GOAL (floor a box belongs on), BOX, BOX_ON_GOAL, PLAYER,
PLAYER_ON_GOAL, or null (nobody could read it, never taken for floor). Outside the grid is wall. The game keeps no
score. Action: {"kind": "move", "direction", "cell": [row, col]}, `cell` the neighbouring cell the player steps
into (the box's, when the step pushes it: a tap there on a touch screen), or {"kind": "new_game"}.

A step goes onto floor, or pushes the one box in that cell one cell further when the cell behind it is floor
(not a wall, not another box); boxes are never pulled. The level is won once every box stands on a goal. The game
has no losing end: a position no push can win from is still a position to move in.
"""
from __future__ import annotations

FLOOR, WALL, GOAL, BOX, BOX_ON_GOAL, PLAYER, PLAYER_ON_GOAL = 0, 1, 2, 3, 4, 5, 6
DIRECTIONS = ("up", "down", "left", "right")
STEPS = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}
# what each kind of cell holds: a goal under it, a box on it, the player on it
GOALS = (GOAL, BOX_ON_GOAL, PLAYER_ON_GOAL)
BOXES = (BOX, BOX_ON_GOAL)
PLAYERS = (PLAYER, PLAYER_ON_GOAL)
OPEN = (FLOOR, GOAL)
# levels differ in size: the game's rows and columns are bound per level
DEFAULT_RULES = {"rows": 9, "cols": 9}
# what a general solver may rely on (`solvers.fits`): a move leaves one state, which `outcomes` gives
CAPABILITIES = ("deterministic",)
# a won level's worth to a search, above any position still to play
WIN_VALUE = 1_000_000
# the replay game's levels: one cell in WALL_ODDS a wall inside the room, a box for every BOX_AREA cells, this many
# reverse moves a cell from the solved position, and this many rooms tried before none is found
WALL_ODDS, BOX_AREA, PULLS, ATTEMPTS = 5, 25, 10, 64
# the replay game's stream of draws: a fixed linear congruential generator (Knuth's MMIX constants)
LCG_MULTIPLIER, LCG_INCREMENT = 6364136223846793005, 1442695040888963407


# ── state ─────────────────────────────────────────────────────────────

def _unread(state: dict) -> list[str]:
    grid = state.get("grid")
    if not isinstance(grid, list):
        return ["grid"]
    return [f"grid[{r}][{c}]" for r, row in enumerate(grid) for c, cell in enumerate(row) if cell is None]


def _require_known(grid) -> None:
    if any(cell is None for row in grid for cell in row):
        raise ValueError("unknown_cells")


def at(grid, r: int, c: int):
    """The cell at (r, c); outside the grid is wall."""
    return grid[r][c] if 0 <= r < len(grid) and 0 <= c < len(grid[0]) else WALL


def player(grid) -> tuple[int, int]:
    found = next(((r, c) for r, row in enumerate(grid) for c, cell in enumerate(row) if cell in PLAYERS), None)
    if found is None:
        raise ValueError("no_player")
    return found


def solved(grid) -> bool:
    """Every box stands on a goal (and there is one)."""
    return not any(cell == BOX for row in grid for cell in row) and any(BOX_ON_GOAL in row for row in grid)


def on_goals(grid) -> int:
    return sum(cell == BOX_ON_GOAL for row in grid for cell in row)


def beneath(state: dict, prior: dict | None = None) -> dict:
    """A reading with the cell under the player told by what is known, not by what the screen shows: the figure
    hides the mark of the goal it stands on, and a cell is a goal or not for the whole level. Where `prior` (a
    state of this level: the one before the move, or what the rules say the move leaves) has a goal at the
    player's cell, the player is on a goal, and where it has plain floor, on floor. With nothing before to go by,
    a board read whole whose goals are one fewer than its boxes has the player on the goal that is missing (and
    one whose goals are one more has it on floor): a level has as many goals as boxes."""
    grid = state.get("grid") if isinstance(state, dict) else None
    if not isinstance(grid, list) or any(not isinstance(row, list) for row in grid):
        return state
    found = [(r, c) for r, row in enumerate(grid) for c, cell in enumerate(row) if cell in PLAYERS]
    if len(found) != 1:
        return state
    r, c = found[0]
    before = prior.get("grid") if isinstance(prior, dict) else None
    known = None
    if isinstance(before, list) and len(before) == len(grid) and all(
            isinstance(row, list) and len(row) == len(mine) for row, mine in zip(before, grid)):
        known = before[r][c]
    if known in GOALS:
        value = PLAYER_ON_GOAL
    elif known in (FLOOR, BOX, PLAYER):
        value = PLAYER
    elif any(cell is None for row in grid for cell in row):
        return state
    else:
        cells = [cell for row in grid for cell in row]
        short = sum(cell in BOXES for cell in cells) - sum(cell in GOALS for cell in cells)
        value = PLAYER_ON_GOAL if short == 1 and grid[r][c] == PLAYER else \
            PLAYER if short == -1 and grid[r][c] == PLAYER_ON_GOAL else grid[r][c]
    if value == grid[r][c]:
        return state
    return {**state, "grid": [[value if (y, x) == (r, c) else cell for x, cell in enumerate(row)]
                              for y, row in enumerate(grid)]}


def unknown_fields(state: dict) -> list[str]:
    """Cells nobody could read; once the game shows its level won no move is chosen from the grid, so none is
    waited for."""
    if state.get("scene") == "won":
        return []
    return _unread(state)


def shown(state: dict) -> dict:
    """The state as a screen shows it: the game hides nothing."""
    return state


def validate(state: dict, rules: dict) -> list[str]:
    grid, rows, cols = state.get("grid"), rules["rows"], rules["cols"]
    problems = []
    if not isinstance(grid, list) or len(grid) != rows or any(not isinstance(row, list) or len(row) != cols for row in grid):
        return ["grid_shape"]
    cells = [cell for row in grid for cell in row]
    unread = sum(cell is None for cell in cells)
    if any(cell is not None and (type(cell) is not int or not FLOOR <= cell <= PLAYER_ON_GOAL) for cell in cells):
        problems.append("grid_cell")
    else:
        players = sum(cell in PLAYERS for cell in cells)
        boxes = sum(cell in BOXES for cell in cells)
        goals = sum(cell in GOALS for cell in cells)
        # an unread cell may be the player, a box or a goal: the counts need only be reachable by what it hides
        if players > 1 or players + unread < 1:
            problems.append("grid_player")
        elif abs(boxes - goals) > unread or boxes + unread < 1:
            problems.append("grid_boxes")
    if state.get("scene", "playing") not in ("playing", "won"):
        problems.append("scene")
    return problems


# ── rules ─────────────────────────────────────────────────────────────

def step(grid, direction: str) -> list[list[int]] | None:
    """The grid after the player steps `direction`, pushing a box ahead where the rules let it; None when the
    step leaves the grid as it was (a wall, or a box that cannot move)."""
    dr, dc = STEPS[direction]
    pr, pc = player(grid)
    r, c = pr + dr, pc + dc
    ahead = at(grid, r, c)
    out = [list(row) for row in grid]
    if ahead in BOXES:
        beyond = at(grid, r + dr, c + dc)
        if beyond not in OPEN:
            return None
        out[r + dr][c + dc] = BOX_ON_GOAL if beyond == GOAL else BOX
    elif ahead not in OPEN:
        return None
    out[pr][pc] = GOAL if grid[pr][pc] == PLAYER_ON_GOAL else FLOOR
    out[r][c] = PLAYER_ON_GOAL if ahead in GOALS else PLAYER
    return out


def move(grid, direction: str) -> dict:
    """The action that steps `direction` from `grid`'s player."""
    dr, dc = STEPS[direction]
    pr, pc = player(grid)
    return {"kind": "move", "direction": direction, "cell": [pr + dr, pc + dc]}


def _moved(state: dict, action: dict) -> list[list[int]]:
    """The grid a move leaves; ValueError when it is no move the rules allow from `state`."""
    grid = state["grid"]
    _require_known(grid)
    if action.get("kind") != "move" or action.get("direction") not in STEPS or action != move(grid, action["direction"]):
        raise ValueError("illegal_action")
    after = step(grid, action["direction"])
    if after is None:
        raise ValueError("illegal_action")
    return after


def legal_actions(state: dict, rules: dict) -> list[dict]:
    """Every step that changes the grid, while the level goes on."""
    grid = state["grid"]
    if state.get("scene", "playing") != "playing":
        return []
    _require_known(grid)
    if solved(grid):
        return []
    return [move(grid, d) for d in DIRECTIONS if step(grid, d) is not None]


def outcomes(state: dict, action: dict, rules: dict) -> list[list]:
    """The one state a move leaves: `[[1, state]]`. The scene the game then shows (its win message) is not
    predicted."""
    return [[1, {**state, "grid": _moved(state, action)}]]


def evaluate(state: dict, rules: dict) -> int:
    """A state's worth to a search: WIN_VALUE once won, else minus how far the boxes stand from their nearest
    goals (steps along rows and columns, walls ignored)."""
    grid = state["grid"]
    if solved(grid):
        return WIN_VALUE
    goals = [(r, c) for r, row in enumerate(grid) for c, cell in enumerate(row) if cell in GOALS]
    boxes = [(r, c) for r, row in enumerate(grid) for c, cell in enumerate(row) if cell == BOX]
    return -sum(min(abs(r - y) + abs(c - x) for y, x in goals) for r, c in boxes) if goals else 0


def status(state: dict, rules: dict, goal: dict) -> dict:
    """Won when every box stands on a goal, or the game's own screen says so; nothing else ends a level.
    `reach_value` counts the boxes on goals; with no target it is every box, which is the level won."""
    if state.get("scene", "playing") == "won":
        return {"terminal": True, "goal": "reached"}
    grid = state["grid"]
    _require_known(grid)
    done = solved(grid)
    if goal.get("objective") == "reach_value":
        target = goal.get("target_value")
        reached = done or (target is not None and on_goals(grid) >= target)
    else:
        reached = goal.get("objective") == "play_until_end" and done
    return {"terminal": done, "goal": "reached" if reached else "not_reached"}


def assess(before: dict, action: dict, after: dict, rules: dict) -> dict:
    """Whether the screen after the move is the one the rules give: the player one cell on, and the box it
    pushed one cell further, nothing else changed. A new level is any whole level not yet won."""
    seen = after.get("grid")
    if action.get("kind") == "new_game":
        if _unread(after):
            return {"effect": "unknown"}
        if seen == before.get("grid"):
            return {"effect": "absent"}
        fresh = not validate(after, {"rows": len(seen), "cols": len(seen[0]) if seen else 0}) and not solved(seen)
        return {"effect": "confirmed" if fresh else "inconsistent"}
    try:
        expected = _moved(before, action)
    except (ValueError, KeyError, TypeError, IndexError):
        return {"effect": "inconsistent"}
    if _unread(after):
        return {"effect": "unknown"}
    if seen == before["grid"]:
        return {"effect": "absent"}
    return {"effect": "confirmed" if seen == expected else "inconsistent"}


def _agrees(expected, seen) -> bool:
    """Whether every read cell of `seen` is the one the rules give."""
    return isinstance(seen, list) and len(seen) == len(expected) and all(
        isinstance(row, list) and len(row) == len(want) and all(cell is None or cell == value for cell, value in zip(row, want))
        for row, want in zip(seen, expected))


def explain(before: dict, action: dict, after: dict, rules: dict) -> list[dict]:
    """The values the rules give the cells nobody could read after a move, when everything read agrees with it:
    a move leaves one grid, so each unread cell is that grid's. Nothing ([]) when anything read disagrees."""
    if before.get("scene", "playing") != "playing" or after.get("scene", "playing") != "playing":
        return []
    try:
        expected = _moved(before, action)
    except (ValueError, KeyError, TypeError, IndexError):
        return []
    grid = after.get("grid")
    if not _agrees(expected, grid):
        return []
    return [{"cell": [r, c], "value": expected[r][c]} for r, row in enumerate(grid) for c, cell in enumerate(row)
            if cell is None]


def ended(before: dict, action: dict, after: dict, rules: dict) -> bool:
    """Whether the rules alone show a move ended the level though cells after it are unread (the win message
    over part of the board): the move puts the last box on its goal and every read cell agrees."""
    if action.get("kind") != "move" or before.get("scene", "playing") != "playing":
        return False
    try:
        expected = _moved(before, action)
    except (ValueError, KeyError, TypeError, IndexError):
        return False
    grid = after.get("grid")
    return solved(expected) and (after.get("scene") == "won" or _agrees(expected, grid))


def unread_state(rules: dict, scene: str) -> dict:
    """A screen where the game's own message for `scene` fills the board's place: nothing under it read."""
    return {"grid": [[None] * rules["cols"] for _ in range(rules["rows"])], "score": None, "scene": scene}


# ── the replay game (seeded by the caller) ─────────────────────────────

def _draws(draw: int):
    value = draw
    while True:
        value = (value * LCG_MULTIPLIER + LCG_INCREMENT) % (1 << 64)
        yield value >> 33


def _region(floor: set, start: tuple[int, int]) -> set:
    found, todo = {start}, [start]
    while todo:
        r, c = todo.pop()
        for dr, dc in STEPS.values():
            near = (r + dr, c + dc)
            if near in floor and near not in found:
                found.add(near)
                todo.append(near)
    return found


def compose(rows: int, cols: int, floor: set, goals: set, boxes: set, at_cell: tuple[int, int]) -> list[list[int]]:
    """The grid of a level laid out as sets of cells: everything not floor is wall."""
    grid = [[WALL] * cols for _ in range(rows)]
    for r, c in floor:
        grid[r][c] = FLOOR
    for r, c in goals:
        grid[r][c] = GOAL
    for r, c in boxes:
        grid[r][c] = BOX_ON_GOAL if (r, c) in goals else BOX
    r, c = at_cell
    grid[r][c] = PLAYER_ON_GOAL if at_cell in goals else PLAYER
    return grid


def initial_state(rules: dict, draw: int) -> dict:
    """A level that can be won: a room with walls drawn at random, its boxes first on their goals, then moved off
    by the player's moves run backwards (a step, pulling the box behind it), so pushing them back wins."""
    rows, cols = rules["rows"], rules["cols"]
    draws = _draws(draw)
    count = max(1, rows * cols // BOX_AREA)
    for _ in range(ATTEMPTS):
        open_ = {(r, c) for r in range(rows) for c in range(cols) if next(draws) % WALL_ODDS}
        regions = []
        for cell in sorted(open_):
            if not any(cell in region for region in regions):
                regions.append(_region(open_, cell))
        if not regions:
            continue
        floor = max(regions, key=len)            # the first of the largest: walls fill the rest
        if len(floor) < 2 * count + 2:
            continue
        cells = sorted(floor)
        for index in range(len(cells) - 1, 0, -1):
            pick = next(draws) % (index + 1)
            cells[index], cells[pick] = cells[pick], cells[index]
        goals, (pr, pc) = set(cells[:count]), cells[count]
        boxes = set(goals)
        for _ in range(PULLS * rows * cols):
            dr, dc = STEPS[DIRECTIONS[next(draws) % 4]]
            ahead, behind = (pr + dr, pc + dc), (pr - dr, pc - dc)
            if ahead not in floor or ahead in boxes:
                continue
            if behind in boxes:
                boxes.remove(behind)
                boxes.add((pr, pc))
            pr, pc = ahead
        if boxes != goals:
            return {"grid": compose(rows, cols, floor, goals, boxes, (pr, pc)), "score": None, "scene": "playing"}
    raise ValueError("no_level")


def next_state(state: dict, rules: dict, action: dict, draw: int) -> dict:
    """The whole move as the replay game plays it: the step, then the win message once every box is home."""
    if action.get("kind") == "new_game":
        return initial_state(rules, draw)
    if state.get("scene", "playing") != "playing":
        raise ValueError("illegal_action")
    grid = _moved(state, action)
    return {**state, "grid": grid, **({"scene": "won"} if solved(grid) else {})}


def hidden_state(state: dict) -> dict:
    """The replay's screen with one cell hidden: unread, never floor."""
    grid = [list(row) for row in state["grid"]]
    grid[0][0] = None
    return {**state, "grid": grid}


def stray_state(state: dict) -> dict:
    """The replay's stray change: the first box on plain floor moved to the first empty floor cell, which no
    single step does."""
    grid = [list(row) for row in state["grid"]]
    box = next(((r, c) for r, row in enumerate(grid) for c, cell in enumerate(row) if cell == BOX), None)
    free = next(((r, c) for r, row in enumerate(grid) for c, cell in enumerate(row) if cell == FLOOR), None)
    if box is not None and free is not None:
        grid[box[0]][box[1]], grid[free[0]][free[1]] = FLOOR, BOX
    return {**state, "grid": grid}
