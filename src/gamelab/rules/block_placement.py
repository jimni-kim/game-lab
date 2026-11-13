"""Block placement: the rule model, its pure rules, legal moves and transition check (plan §3.2, §7.1;
adaptive perception plan §3). Its policies are solvers/block_placement.py.

Standard library only, JSON in and out, no IO, no clock, no unseeded randomness: this file is what
moves to the plugin's Rust crate later, checked by the same fixtures (tests/fixtures/game/
block_placement). Every order is explicit: slots, then orientations, then rows, then columns, and a
tie keeps the first.

State: {"board": [[0 | 1 | null]], "tray": [piece | null], "score"?: int | null,
"scene"?: "playing" | "over"}. A piece is a list of [row, col] cells; null in the tray is a slot already
used; null on the board is a cell nobody could read, never an empty one. An "over" scene is the game's own
end screen: what it covers is left unread, and nothing is decided from it. Action: {"kind": "place", "slot", "anchor": [row, col], "orientation"} or
{"kind": "new_game"}. The site's scoring formula is not known, so the core never predicts a score;
`next_state` (the replay game's own rules) keeps a simple one so a whole game can be measured.
"""
from __future__ import annotations

from itertools import product

EMPTY, FILLED = 0, 1
PLAYING, OVER = "playing", "over"
# the standard game: an 8x8 board, rows and columns cleared together, a tray of three, no turning
DEFAULT_RULES = {"rows": 8, "cols": 8, "clear_rows": True, "clear_cols": True, "rotation": False, "tray_size": 3}
# the pieces the replay game deals from (a simulated stream; a real game's pieces are read from its screen)
STANDARD_PIECES = (
    ((0, 0),),
    ((0, 0), (0, 1)), ((0, 0), (1, 0)),
    ((0, 0), (0, 1), (0, 2)), ((0, 0), (1, 0), (2, 0)),
    ((0, 0), (0, 1), (1, 0), (1, 1)),
    ((0, 0), (1, 0), (1, 1)), ((0, 1), (1, 0), (1, 1)), ((0, 0), (0, 1), (1, 0)), ((0, 0), (0, 1), (1, 1)),
    ((0, 0), (0, 1), (0, 2), (0, 3)), ((0, 0), (1, 0), (2, 0), (3, 0)),
    ((0, 0), (1, 0), (2, 0), (2, 1)), ((0, 0), (0, 1), (0, 2), (1, 0)),
    ((0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (2, 0), (2, 1), (2, 2)),
)
# replay scoring: a point per placed cell and a bonus per cleared line
LINE_POINTS = 10
# what a general solver may rely on (`solvers.fits`): a placement leaves one state. The tray dealt once
# every piece is placed is not predicted: a search ends its line there
CAPABILITIES = ("deterministic",)
# board evaluation: free cells are worth keeping, single shut-in holes and ragged edges are not
EMPTY_WEIGHT = 2
HOLE_PENALTY = 5
EDGE_PENALTY = 1


# ── state ─────────────────────────────────────────────────────────────

def unknown_fields(state: dict) -> list[str]:
    """Paths of everything the reader could not read; a policy plays nothing while one remains. On the
    game's end screen nothing is left to decide, so what that screen covers is not waited for."""
    if state.get("scene") == OVER:
        return []
    return _unread(state)


def _unread(state: dict) -> list[str]:
    board = state.get("board")
    tray = state.get("tray")
    if not isinstance(board, list) or not isinstance(tray, list):
        return ["board" if not isinstance(board, list) else "tray"]
    found = [f"board[{r}][{c}]" for r, row in enumerate(board) for c, cell in enumerate(row) if cell is None]
    return found + [f"tray[{s}]" for s, piece in enumerate(tray) if piece is not None and not _readable(piece)]


def validate(state: dict, rules: dict) -> list[str]:
    """What makes this state impossible under these rules (wrong board size, a cell value no rule knows)."""
    board, tray = state.get("board"), state.get("tray")
    problems = []
    if not isinstance(board, list) or len(board) != rules["rows"] \
            or any(not isinstance(row, list) or len(row) != rules["cols"] for row in board):
        problems.append("board_shape")
    elif any(cell not in (EMPTY, FILLED, None) for row in board for cell in row):
        problems.append("board_cell")
    if not isinstance(tray, list) or len(tray) != rules["tray_size"]:
        problems.append("tray_shape")
    if state.get("scene", PLAYING) not in (PLAYING, OVER):
        problems.append("scene")
    return problems


def _readable(piece: object) -> bool:
    return isinstance(piece, list) and bool(piece) and all(
        isinstance(cell, list) and len(cell) == 2 and all(isinstance(v, int) for v in cell) for cell in piece)


def _require_known(state: dict) -> None:
    if _unread(state):
        raise ValueError("unknown_cells")


# ── rules ─────────────────────────────────────────────────────────────

def _normalize(cells) -> tuple[tuple[int, int], ...]:
    top = min(r for r, _ in cells)
    left = min(c for _, c in cells)
    return tuple(sorted((r - top, c - left) for r, c in cells))


def orientations(piece, rotation: bool) -> list[tuple[tuple[int, int], ...]]:
    """The piece as placed: itself, then each distinct quarter turn when turning is allowed."""
    shapes = [_normalize(piece)]
    if rotation:
        cells = shapes[0]
        for _ in range(3):
            cells = _normalize([(c, -r) for r, c in cells])
            if cells not in shapes:
                shapes.append(cells)
    return shapes


def _fits(board, cells, anchor) -> bool:
    rows, cols = len(board), len(board[0])
    for dr, dc in cells:
        r, c = anchor[0] + dr, anchor[1] + dc
        if not (0 <= r < rows and 0 <= c < cols) or board[r][c] != EMPTY:
            return False
    return True


def _placements(board, piece, rotation):
    rows, cols = len(board), len(board[0])
    for k, cells in enumerate(orientations(piece, rotation)):
        for r, c in product(range(rows), range(cols)):
            if _fits(board, cells, (r, c)):
                yield k, cells, (r, c)


def legal_actions(state: dict, rules: dict) -> list[dict]:
    if state.get("scene") == OVER:
        return []
    _require_known(state)
    return [{"kind": "place", "slot": slot, "anchor": [r, c], "orientation": k}
            for slot, piece in enumerate(state["tray"]) if piece is not None
            for k, _, (r, c) in _placements(state["board"], piece, rules["rotation"])]


def _place(board, cells, anchor, rules):
    grid = [list(row) for row in board]
    for dr, dc in cells:
        grid[anchor[0] + dr][anchor[1] + dc] = FILLED
    full_rows = [r for r, row in enumerate(grid) if all(row)] if rules["clear_rows"] else []
    full_cols = [c for c in range(len(grid[0])) if all(row[c] for row in grid)] if rules["clear_cols"] else []
    for r in full_rows:                  # every full line goes at once, judged before any is cleared
        grid[r] = [EMPTY] * len(grid[r])
    for c in full_cols:
        for row in grid:
            row[c] = EMPTY
    return grid, full_rows, full_cols


def apply(state: dict, rules: dict, action: dict) -> dict:
    """The board and tray a placement leaves, before any new tray is dealt (that is not predicted)."""
    _require_known(state)
    slot = action.get("slot")
    tray = state["tray"]
    if action.get("kind") != "place" or not isinstance(slot, int) or not 0 <= slot < len(tray) or tray[slot] is None:
        raise ValueError("illegal_action")
    shapes = orientations(tray[slot], rules["rotation"])
    k, anchor = action.get("orientation", 0), tuple(action.get("anchor") or ())
    if not 0 <= k < len(shapes) or len(anchor) != 2 or not _fits(state["board"], shapes[k], anchor):
        raise ValueError("illegal_action")
    board, rows, cols = _place(state["board"], shapes[k], anchor, rules)
    left = [None if s == slot else piece for s, piece in enumerate(tray)]
    after = {**state, "board": board, "tray": left}
    return {"state": after, "cleared_rows": rows, "cleared_cols": cols}


def outcomes(state: dict, action: dict, rules: dict) -> list[list]:
    """The one state a placement leaves, `[[1, state]]`, before any new tray is dealt."""
    return [[1, apply(state, rules, action)["state"]]]


def board_value(board) -> int:
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


def evaluate(state: dict, rules: dict) -> int:
    """A state's worth to a search (not the game's score)."""
    return board_value(state["board"])


def explain(before: dict, action: dict, after: dict, rules: dict) -> list[dict]:
    """Nothing to explain: a block's colour is never what it means (pieces take any colour), so no cell
    waits for a colour to be learned."""
    return []


def ended(before: dict, action: dict, after: dict, rules: dict) -> bool:
    """Whether the rules alone show a placement ended the game though the screen after it is partly
    unread (an end banner over the tray): the board reads as the move predicts, every slot that reads
    holds what the move left there, and none of the pieces left fits anywhere. A move that used the last
    piece leaves a fresh tray nobody can predict, so it shows nothing; nor does a slot left unread decide
    any move, it only lets the game be seen to have ended."""
    if action.get("kind") != "place" or after.get("scene", PLAYING) != PLAYING or _unread(before) \
            or not isinstance(after.get("tray"), list) or not _unread(after):
        return False
    try:
        predicted = apply(before, rules, action)["state"]
    except ValueError:
        return False
    left = predicted["tray"]
    if after.get("board") != predicted["board"] or len(after["tray"]) != len(left) \
            or all(piece is None for piece in left) \
            or any(seen != [] and seen != piece for seen, piece in zip(after["tray"], left)):
        return False
    return not legal_actions(predicted, rules)


def status(state: dict, rules: dict, goal: dict) -> dict:
    """Whether the game is over (pieces wait and none fits, or its end screen shows) and whether the goal
    is reached."""
    over = state.get("scene") == OVER
    if not over:
        _require_known(state)
    terminal = over or (any(piece is not None for piece in state["tray"]) and not legal_actions(state, rules))
    reached = "not_reached"
    if goal.get("objective") == "reach_value":
        score = state.get("score")
        target = goal.get("target_value")
        if score is None or target is None:
            reached = "unknown"
        else:
            reached = "reached" if score >= target else "not_reached"
    return {"terminal": terminal, "goal": reached}


def assess(before: dict, action: dict, after: dict, rules: dict) -> dict:
    """Whether the screen after the move shows that move: the predicted board and the tray with the
    piece used (or a fresh tray once the last one is used, whatever it holds)."""
    if _unread(after):
        return {"effect": "unknown"}           # an end screen over the tray cannot confirm the move
    if action.get("kind") == "new_game":
        fresh = all(cell == EMPTY for row in after["board"] for cell in row)
        return {"effect": "confirmed" if fresh else ("absent" if after == before else "inconsistent")}
    predicted = apply(before, rules, action)["state"]
    if after["board"] == predicted["board"] and _tray_follows(predicted["tray"], after["tray"]):
        return {"effect": "confirmed"}
    if after["board"] == before["board"] and after["tray"] == before["tray"]:
        return {"effect": "absent"}
    return {"effect": "inconsistent"}


def _tray_follows(predicted: list, seen: list) -> bool:
    if len(predicted) != len(seen):
        return False
    if all(piece is None for piece in predicted):
        # the last piece was used: either the old empty tray or an entirely new one
        return all(piece is None for piece in seen) or all(piece is not None for piece in seen)
    return seen == predicted


# ── the replay game's own dealing (seeded by the caller) ───────────────

def _deal(rules: dict, draw: int) -> list:
    tray = []
    for _ in range(rules["tray_size"]):
        draw, pick = divmod(draw, len(STANDARD_PIECES))
        tray.append([list(cell) for cell in STANDARD_PIECES[pick]])
    return tray


def unread_state(rules: dict, scene: str) -> dict:
    """A screen where the game's own message for `scene` fills the board's place: nothing under it read."""
    return {"board": [[None] * rules["cols"] for _ in range(rules["rows"])], "tray": [[]] * rules["tray_size"],
            "score": None, "scene": scene}


def initial_state(rules: dict, draw: int) -> dict:
    board = [[EMPTY] * rules["cols"] for _ in range(rules["rows"])]
    return {"board": board, "tray": _deal(rules, draw), "score": 0}


def next_state(state: dict, rules: dict, action: dict, draw: int) -> dict:
    """The whole move as the replay game plays it: the placement, its score, a new tray when empty."""
    if action.get("kind") == "new_game":
        return initial_state(rules, draw)
    moved = apply(state, rules, action)
    after = moved["state"]
    placed = len(state["tray"][action["slot"]])
    lines = len(moved["cleared_rows"]) + len(moved["cleared_cols"])
    score = (state.get("score") or 0) + placed + LINE_POINTS * lines
    tray = after["tray"] if any(p is not None for p in after["tray"]) else _deal(rules, draw)
    return {**after, "tray": tray, "score": score}


def shown(state: dict) -> dict:
    """The state as a screen shows it: every cell and piece the replay game holds is on its screen."""
    return state


def hidden_state(state: dict) -> dict:
    """The replay's screen with one board cell hidden: unread, never empty."""
    board = [list(row) for row in state["board"]]
    board[0][0] = None
    return {**state, "board": board}


def stray_state(state: dict) -> dict:
    """The replay's stray change: one more filled cell than any move made, on the first empty one."""
    board = [list(row) for row in state["board"]]
    for row in board:
        if EMPTY in row:
            row[row.index(EMPTY)] = FILLED
            break
    return {**state, "board": board}
