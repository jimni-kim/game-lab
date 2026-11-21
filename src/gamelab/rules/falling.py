"""Falling blocks: the rule model, its pure rules, legal moves and transition check (PLAN §34). Its policies are
solvers/falling.py.

Pieces fall into a well; a full row from side to side vanishes and what was above it moves down a row. One move is
one piece come to lie: which turn of it, and the column of its leftmost cell, dropped straight down from the top of
the well. That is all of the game the code plays by: a piece is never slid under an overhang or spun into place
(PLAN §34, what is not done), and how a real game turns or moves a piece is the realtime loop's to find on its
screen (`realtime.Loop`), not this module's. There is no kit: the loop reads the well, and the rules and the solver
are what it asks of "what does the well become" and "where does the piece go".

Two forms of the same rules:

  cells   a well as a frozenset of (row, column) filled cells, row 0 at the top: what the realtime loop reads and
          keeps (`turns`, `normal`, `fall`, `settle`, `drops`)
  state   the family's JSON state, for the general harness and the simulated game (`eval policies`):
          {"board": [[0 | 1 | null]], "piece": cells | null, "next": [cells], "lines": int, "pieces": int,
           "score": int}. `piece` is the piece to place now, `next` the coming ones the game shows (its preview),
          `lines` the rows cleared so far and `pieces` the pieces placed; `score` is the lines (the simulated
          game keeps no other). A piece is a list of [row, column] cells shifted so its top-left is (0, 0).
          Action: {"kind": "place", "shape": cells, "column": int} (`shape` one of the piece's turns, `normal`).

A level's goal (rows to clear, within so many pieces) is a rule value (`goal_lines`, `goal_pieces`), null when the
game sets none or it is not known. Standard library only, JSON in and out, no IO, no clock, no unseeded randomness.
"""
from __future__ import annotations

from typing import Any

Cells = frozenset
EMPTY, FILLED = 0, 1
# the standard well, the pieces it shows coming, the rows a game begins with (each full but for one cell), and a
# level's goal
DEFAULT_RULES = {"rows": 20, "cols": 10, "preview": 1, "garbage": 0, "goal_lines": None, "goal_pieces": None}
# the pieces the simulated game deals from: the seven of four cells, each as it first shows
STANDARD_PIECES = (
    ((0, 0), (0, 1), (0, 2), (0, 3)),          # I
    ((0, 0), (0, 1), (1, 0), (1, 1)),          # O
    ((0, 1), (1, 0), (1, 1), (1, 2)),          # T
    ((0, 1), (0, 2), (1, 0), (1, 1)),          # S
    ((0, 0), (0, 1), (1, 1), (1, 2)),          # Z
    ((0, 0), (1, 0), (1, 1), (1, 2)),          # J
    ((0, 2), (1, 0), (1, 1), (1, 2)),          # L
)
# a placement leaves one state; the piece dealt after it is chance (the general solvers are not offered it: a piece
# drawn after each move is a chance node whose outcomes a search would weigh by a dealing nobody knows)
CAPABILITIES = ("chance_sampled",)
PLAYING, OVER = "playing", "over"


# ── the rules on cells ───────────────────────────────────────────────

def normal(cells: Any) -> Cells:
    top, left = min(r for r, _ in cells), min(c for _, c in cells)
    return Cells((r - top, c - left) for r, c in cells)


def turns(cells: Any) -> list[Cells]:
    """A piece's shapes as it turns a quarter at a time, each with its top-left at (0, 0), without repeats."""
    out: list[Cells] = []
    shape = normal(cells)
    for _ in range(4):
        if shape not in out:
            out.append(shape)
        shape = normal(Cells((c, -r) for r, c in shape))
    return out


def fall(stack: Cells, cells: Cells, rows: int, cols: int) -> Cells | None:
    """Where a piece standing at `cells` comes to lie when it goes straight down; None when it does not fit where
    it stands."""
    if any(not (0 <= c < cols and r < rows) or (r, c) in stack for r, c in cells):
        return None
    down = 0
    while all(r + down + 1 < rows and (r + down + 1, c) not in stack for r, c in cells):
        down += 1
    return Cells((r + down, c) for r, c in cells)


def settle(stack: Cells, placed: Cells, rows: int, cols: int) -> tuple[Cells, int]:
    """The stack with a piece come to lie on it, its full rows gone and what was above them moved down; and how
    many rows went."""
    filled = stack | placed
    full = [r for r in range(rows) if all((r, c) in filled for c in range(cols))]
    if not full:
        return Cells(filled), 0
    return Cells((r + sum(row > r for row in full), c) for r, c in filled if r not in full), len(full)


def placements(stack: Cells, piece: Cells, rows: int, cols: int, only: Cells | None = None
               ) -> list[tuple[Cells, int, Cells]]:
    """Every (shape, column, cells it comes to lie on) of the piece dropped straight down from the top: its turns
    in `turns` order (or `only` that one), the columns from the left."""
    out = []
    for shape in ([only] if only is not None else turns(piece)):
        for column in range(cols - max(c for _, c in shape)):
            placed = fall(stack, Cells((r, c + column) for r, c in shape), rows, cols)
            if placed is not None:
                out.append((shape, column, placed))
    return out


def drops(stack: Cells, piece: Cells, rows: int, cols: int) -> list[Cells]:
    """Every place the piece may come to lie when it is dropped straight down from the top, in any turn of it."""
    out: list[Cells] = []
    for _, _, placed in placements(stack, piece, rows, cols):
        if placed not in out:
            out.append(placed)
    return out


# ── the state ────────────────────────────────────────────────────────

def stack_of(board: list[list[Any]]) -> Cells:
    return Cells((r, c) for r, row in enumerate(board) for c, cell in enumerate(row) if cell == FILLED)


def board_of(stack: Cells, rules: dict) -> list[list[int]]:
    return [[FILLED if (r, c) in stack else EMPTY for c in range(rules["cols"])] for r in range(rules["rows"])]


def listed(cells: Any) -> list[list[int]]:
    return sorted([r, c] for r, c in cells)


def _cells(piece: Any) -> Cells | None:
    if not (isinstance(piece, list) and piece and all(
            isinstance(cell, list) and len(cell) == 2 and all(type(v) is int for v in cell) for cell in piece)):
        return None
    return normal(Cells(tuple(cell) for cell in piece))


def unknown_fields(state: dict) -> list[str]:
    if state.get("scene") == OVER:
        return []
    board = state.get("board")
    if not isinstance(board, list):
        return ["board"]
    found = [f"board[{r}][{c}]" for r, row in enumerate(board) for c, cell in enumerate(row) if cell is None]
    if state.get("piece") is not None and _cells(state["piece"]) is None:
        found.append("piece")
    return found


def validate(state: dict, rules: dict) -> list[str]:
    board = state.get("board")
    problems = []
    if not isinstance(board, list) or len(board) != rules["rows"] \
            or any(not isinstance(row, list) or len(row) != rules["cols"] for row in board):
        problems.append("board_shape")
    elif any(cell not in (EMPTY, FILLED, None) for row in board for cell in row):
        problems.append("board_cell")
    if not isinstance(state.get("next", []), list):
        problems.append("next")
    if state.get("scene", PLAYING) not in (PLAYING, OVER):
        problems.append("scene")
    return problems


def _require_known(state: dict) -> None:
    if unknown_fields(state):
        raise ValueError("unknown_cells")


def level_spent(state: dict, rules: dict) -> bool:
    """Whether the level's goal is met or its pieces are used up: no piece is placed after that."""
    lines, pieces = state.get("lines") or 0, state.get("pieces") or 0
    return rules.get("goal_lines") is not None and lines >= rules["goal_lines"] \
        or rules.get("goal_pieces") is not None and pieces >= rules["goal_pieces"]


def legal_actions(state: dict, rules: dict) -> list[dict]:
    if state.get("scene") == OVER or state.get("piece") is None or level_spent(state, rules):
        return []
    _require_known(state)
    stack = stack_of(state["board"])
    return [{"kind": "place", "shape": listed(shape), "column": column}
            for shape, column, _ in placements(stack, _cells(state["piece"]), rules["rows"], rules["cols"])]


def apply(state: dict, rules: dict, action: dict) -> dict:
    """The well a placement leaves, before the next piece is dealt: {"state", "lines", "placed"}."""
    _require_known(state)
    piece, shape = _cells(state.get("piece")), _cells(action.get("shape"))
    column = action.get("column")
    if action.get("kind") != "place" or piece is None or shape is None or shape not in turns(piece) \
            or type(column) is not int:
        raise ValueError("illegal_action")
    stack = stack_of(state["board"])
    placed = fall(stack, Cells((r, c + column) for r, c in shape), rules["rows"], rules["cols"])
    if placed is None:
        raise ValueError("illegal_action")
    after, lines = settle(stack, placed, rules["rows"], rules["cols"])
    total = (state.get("lines") or 0) + lines
    moved = {**state, "board": board_of(after, rules), "lines": total, "pieces": (state.get("pieces") or 0) + 1,
             "score": total}
    return {"state": moved, "lines": lines, "placed": listed(placed)}


def explain(before: dict, action: dict, after: dict, rules: dict) -> list[dict]:
    """Nothing to explain: a block's colour carries no rule."""
    return []


def ended(before: dict, action: dict, after: dict, rules: dict) -> bool:
    """Whether the rules alone show a placement ended the game: the next piece fits nowhere, or the level's goal
    or its pieces are spent."""
    if action.get("kind") != "place" or unknown_fields(before):
        return False
    try:
        predicted = apply(before, rules, action)["state"]
    except ValueError:
        return False
    if level_spent(predicted, rules):
        return True
    coming = _cells((after.get("piece") if isinstance(after, dict) else None) or (before.get("next") or [None])[0])
    return coming is not None and not placements(stack_of(predicted["board"]), coming, rules["rows"], rules["cols"])


def status(state: dict, rules: dict, goal: dict) -> dict:
    """Whether the game is over (its end shows, the piece fits nowhere, the level is spent) and whether the goal is
    reached: the level's rows (`goal_lines`), or the lines a run asked for (`reach_value`)."""
    over = state.get("scene") == OVER
    if not over:
        _require_known(state)
    terminal = over or not legal_actions(state, rules)
    lines = state.get("lines") or 0
    reached = "not_reached"
    if goal.get("objective") == "reach_value" and goal.get("target_value") is not None:
        reached = "reached" if lines >= goal["target_value"] else "not_reached"
    elif rules.get("goal_lines") is not None:
        reached = "reached" if lines >= rules["goal_lines"] else "not_reached"
    return {"terminal": terminal, "goal": reached}


def won(state: dict, rules: dict) -> bool | None:
    """Whether a level with a goal of rows was cleared; None when the game sets no such goal."""
    return None if rules.get("goal_lines") is None else (state.get("lines") or 0) >= rules["goal_lines"]


def assess(before: dict, action: dict, after: dict, rules: dict) -> dict:
    """Whether the well after the move is the one the placement leaves."""
    if unknown_fields(after):
        return {"effect": "unknown"}
    predicted = apply(before, rules, action)["state"]
    if after["board"] == predicted["board"]:
        return {"effect": "confirmed"}
    if after["board"] == before["board"]:
        return {"effect": "absent"}
    return {"effect": "inconsistent"}


# ── the simulated game's own dealing (seeded by the caller) ───────────

def _piece(draw: int) -> tuple[list[list[int]], int]:
    draw, pick = divmod(draw, len(STANDARD_PIECES))
    return [list(cell) for cell in STANDARD_PIECES[pick]], draw


def unread_state(rules: dict, scene: str) -> dict:
    """A screen where the game's own message for `scene` fills the well's place: nothing under it read."""
    return {"board": [[None] * rules["cols"] for _ in range(rules["rows"])], "piece": None, "next": [],
            "lines": 0, "pieces": 0, "score": None, "scene": scene}


def initial_state(rules: dict, draw: int) -> dict:
    """An empty well, or one with `garbage` rows at its floor each full but for one cell, and the first piece with
    those the preview shows."""
    board = [[EMPTY] * rules["cols"] for _ in range(rules["rows"])]
    for row in range(rules["rows"] - rules["garbage"], rules["rows"]):
        draw, hole = divmod(draw, rules["cols"])
        board[row] = [EMPTY if c == hole else FILLED for c in range(rules["cols"])]
    dealt = []
    for _ in range(1 + rules["preview"]):
        piece, draw = _piece(draw)
        dealt.append(piece)
    return {"board": board, "piece": dealt[0], "next": dealt[1:], "lines": 0, "pieces": 0, "score": 0}


def next_state(state: dict, rules: dict, action: dict, draw: int) -> dict:
    """The whole move as the simulated game plays it: the placement, then the next piece from the preview and a
    new one at the preview's end."""
    if action.get("kind") == "new_game":
        return initial_state(rules, draw)
    after = apply(state, rules, action)["state"]
    coming = [*(state.get("next") or []), _piece(draw)[0]]
    return {**after, "piece": coming[0], "next": coming[1:1 + rules["preview"]]}


def shown(state: dict) -> dict:
    """The state as a screen shows it: the well, the piece, and the preview."""
    return state


def hidden_state(state: dict) -> dict:
    board = [list(row) for row in state["board"]]
    board[0][0] = None
    return {**state, "board": board}


def stray_state(state: dict) -> dict:
    """One more filled cell than any move made, on the lowest row's first empty cell."""
    board = [list(row) for row in state["board"]]
    for row in reversed(board):
        if EMPTY in row:
            row[row.index(EMPTY)] = FILLED
            break
    return {**state, "board": board}
