"""Swap-and-match: a grid of coloured pieces; a move swaps two neighbours so that three or more of one
colour line up, which are removed; what is above falls and new pieces come in at the top (PLAN §10, G8).

A piece is its colour, a small whole number (`COLOURS` names them for whoever reads a screen); 0 is a place
that holds no piece. What comes in at the top is not known before it shows: `apply` leaves those cells
unread (None) and settles only what the known pieces settle among themselves, so a move's result on the
screen may differ from it where the new pieces set off more removals. `assess` therefore takes a settled
board that changed as the move's result, and only an unchanged board as the move not taken.

Pure like every rule model: JSON in and out, no IO, no clock, randomness only from the caller's `draw`.
"""
from __future__ import annotations

# the colours a piece is read as, by the number a state holds for it (1-based; 0 is no piece)
COLOURS = ("red", "orange", "yellow", "green", "blue", "purple", "pink", "white", "cyan", "brown")
EMPTY = 0
RUN = 3
PLAYING, OVER, WON = "playing", "over", "won"
DEFAULT_RULES = {"rows": 8, "cols": 8, "colours": 6}
# what comes in at the top is drawn by the game: it can be sampled, not listed
CAPABILITIES = ("chance_sampled", "hidden")
STEPS = ((0, 1), (1, 0))


def unknown_fields(state: dict) -> list[str]:
    return _unread(state) if state.get("scene", PLAYING) == PLAYING else []


def _unread(state: dict) -> list[str]:
    return [f"grid[{r}][{c}]" for r, row in enumerate(state["grid"]) for c, cell in enumerate(row) if cell is None]


def validate(state: dict, rules: dict) -> list[str]:
    grid = state.get("grid")
    rows, cols = rules["rows"], rules["cols"]
    if not isinstance(grid, list) or len(grid) != rows or any(not isinstance(row, list) or len(row) != cols for row in grid):
        return ["grid_shape"]
    problems = []
    if any(cell is not None and (type(cell) is not int or not 0 <= cell <= len(COLOURS)) for row in grid for cell in row):
        problems.append("cell_value")
    elif state.get("scene", PLAYING) == PLAYING and not _unread(state) and _runs(grid):
        # a board at rest holds no line of three: one that does is still moving, or was misread
        problems.append("unsettled")
    if state.get("scene", PLAYING) not in (PLAYING, OVER, WON):
        problems.append("scene")
    return problems


def _require_known(grid) -> None:
    if any(cell is None for row in grid for cell in row):
        raise ValueError("unknown_cells")


def _runs(grid) -> set:
    """The cells of every line of RUN or more pieces of one colour (unread cells and empty places break a line)."""
    rows, cols = len(grid), len(grid[0])
    found = set()
    for r in range(rows):
        for c in range(cols):
            colour = grid[r][c]
            if not colour:
                continue
            for dr, dc in STEPS:
                if 0 <= r - dr and 0 <= c - dc and grid[r - dr][c - dc] == colour:
                    continue                 # counted from the line's first cell
                length = 1
                while r + dr * length < rows and c + dc * length < cols and grid[r + dr * length][c + dc * length] == colour:
                    length += 1
                if length >= RUN:
                    found.update((r + dr * step, c + dc * step) for step in range(length))
    return found


def _swapped(grid, cell, to):
    out = [list(row) for row in grid]
    (r, c), (r2, c2) = cell, to
    out[r][c], out[r2][c2] = out[r2][c2], out[r][c]
    return out


def _lines_through(grid, cell) -> bool:
    """Whether the piece at `cell` stands in a line of RUN or more."""
    r, c = cell
    colour = grid[r][c]
    if not colour:
        return False
    for dr, dc in STEPS:
        length = 1
        for sign in (1, -1):
            step = 1
            while (0 <= r + sign * dr * step < len(grid) and 0 <= c + sign * dc * step < len(grid[0])
                   and grid[r + sign * dr * step][c + sign * dc * step] == colour):
                length, step = length + 1, step + 1
        if length >= RUN:
            return True
    return False


def legal_actions(state: dict, rules: dict) -> list[dict]:
    if state.get("scene", PLAYING) != PLAYING:
        return []
    grid = state["grid"]
    _require_known(grid)
    found = []
    for r in range(len(grid)):
        for c in range(len(grid[0])):
            for dr, dc in STEPS:
                r2, c2 = r + dr, c + dc
                if r2 >= len(grid) or c2 >= len(grid[0]) or not grid[r][c] or not grid[r2][c2] or grid[r][c] == grid[r2][c2]:
                    continue
                swapped = _swapped(grid, (r, c), (r2, c2))
                if _lines_through(swapped, (r, c)) or _lines_through(swapped, (r2, c2)):
                    found.append({"kind": "swap", "cell": [r, c], "to": [r2, c2]})
    return found


def _fall(grid, removed) -> list:
    """The grid with `removed` cells gone and what was above them fallen; what comes in at the top is unread.
    An empty place (0) holds nothing and lets nothing through: pieces settle on it."""
    out = [list(row) for row in grid]
    for c in range(len(grid[0])):
        r = len(grid) - 1
        while r >= 0:
            if grid[r][c] == EMPTY:
                r -= 1
                continue
            top = r                      # one stretch of the column between empty places, bottom to top
            while top - 1 >= 0 and grid[top - 1][c] != EMPTY:
                top -= 1
            kept = [grid[y][c] for y in range(r, top - 1, -1) if (y, c) not in removed]
            for offset, y in enumerate(range(r, top - 1, -1)):
                out[y][c] = kept[offset] if offset < len(kept) else None
            r = top - 1
    return out


def apply(state: dict, rules: dict, action: dict) -> dict:
    """What a swap leaves as far as the pieces on the board decide it: the lines removed, what was above
    fallen, the cells new pieces fill left unread, and so on until nothing known lines up. `cleared` counts
    the pieces removed."""
    grid = state["grid"]
    _require_known(grid)
    if action not in legal_actions(state, rules):
        raise ValueError("illegal_action")
    grid = _swapped(grid, action["cell"], action["to"])
    cleared = 0
    while removed := _runs(grid):
        cleared += len(removed)
        grid = _fall(grid, removed)
    return {"state": {**state, "grid": grid}, "cleared": cleared}


def evaluate(state: dict, rules: dict) -> int:
    """A board's worth to a search: the moves it offers."""
    return len(legal_actions(state, rules)) if not _unread(state) else 0


def explain(before: dict, action: dict, after: dict, rules: dict) -> list[dict]:
    return []


def ended(before: dict, action: dict, after: dict, rules: dict) -> bool:
    return False


def status(state: dict, rules: dict, goal: dict) -> dict:
    """The game ends where its own screen says so; a board with no move left is the game's to deal anew.
    A score goal is judged on the score as read."""
    scene = state.get("scene", PLAYING)
    reached = "reached" if scene == WON and goal.get("objective") == "play_until_end" else "not_reached"
    if goal.get("objective") == "reach_value":
        score, target = state.get("score"), goal.get("target_value")
        reached = "unknown" if score is None or target is None else ("reached" if score >= target else "not_reached")
    return {"terminal": scene != PLAYING, "goal": reached}


def assess(before: dict, action: dict, after: dict, rules: dict) -> dict:
    """Whether the screen after a swap shows it taken. The pieces that come in are the game's, and they may
    set off removals nobody can predict, so a settled board that is no longer the one before is the move's
    result; `settled_as_predicted` says whether every cell the rules could predict reads as predicted."""
    if _unread(after):
        return {"effect": "unknown"}
    if after["grid"] == before["grid"]:
        return {"effect": "absent"}
    try:
        predicted = apply(before, rules, action)["state"]["grid"]
    except ValueError:
        return {"effect": "inconsistent"}
    if _runs(after["grid"]):
        return {"effect": "inconsistent"}        # still moving, or misread
    # a swap that was taken removed pieces: where nothing known fell, the board cannot be just the two swapped back
    same = all(seen == told for row, guess in zip(after["grid"], predicted) for seen, told in zip(row, guess) if told is not None)
    return {"effect": "confirmed", "settled_as_predicted": same}


def unread_state(rules: dict, scene: str) -> dict:
    return {"grid": [[None] * rules["cols"] for _ in range(rules["rows"])], "score": None, "scene": scene}


# ── the replay game (seeded by the caller) ─────────────────────────────

def _stream(draw: int):
    value = draw & ((1 << 64) - 1)
    while True:
        value = (value * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        yield value >> 33


def _deal(rules: dict, numbers) -> list:
    """A board at rest that offers a move."""
    rows, cols, colours = rules["rows"], rules["cols"], rules["colours"]
    while True:
        grid = [[0] * cols for _ in range(rows)]
        for r in range(rows):
            for c in range(cols):
                while True:
                    grid[r][c] = 1 + next(numbers) % colours
                    if not _lines_through(grid, (r, c)):
                        break
        if legal_actions({"grid": grid}, rules):
            return grid


def initial_state(rules: dict, draw: int) -> dict:
    return {"grid": _deal(rules, _stream(draw)), "score": 0, "scene": PLAYING}


def next_state(state: dict, rules: dict, action: dict, draw: int) -> dict:
    """The game's own move: the swap, every removal with new pieces drawn from `draw`, and a board with no
    move left dealt anew. The score counts the pieces removed."""
    if action not in legal_actions(state, rules):
        raise ValueError("illegal_action")
    numbers = _stream(draw)
    grid = _swapped(state["grid"], action["cell"], action["to"])
    cleared = 0
    while removed := _runs(grid):
        cleared += len(removed)
        grid = [[1 + next(numbers) % rules["colours"] if cell is None else cell for cell in row] for row in _fall(grid, removed)]
    if not legal_actions({"grid": grid}, rules):
        grid = _deal(rules, numbers)
    return {**state, "grid": grid, "score": (state.get("score") or 0) + cleared}


def shown(state: dict) -> dict:
    return state


def hidden_state(state: dict) -> dict:
    grid = [list(row) for row in state["grid"]]
    grid[0][0] = None
    return {**state, "grid": grid}


def stray_state(state: dict) -> dict:
    """One piece another colour than any move leaves it: a fault the replay injects."""
    grid = [list(row) for row in state["grid"]]
    rows, cols = len(grid), len(grid[0])
    for r in range(rows):
        for c in range(cols):
            for colour in range(1, len(COLOURS) + 1):
                if colour != grid[r][c]:
                    changed = [list(row) for row in grid]
                    changed[r][c] = colour
                    if not _runs(changed):
                        return {**state, "grid": changed}
    return state
