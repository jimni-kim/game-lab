"""Swap-and-match: a grid of coloured pieces; a move swaps two neighbours so that three or more of one
colour line up, which are removed; what is above falls and new pieces come in at the top (PLAN §10, G8).

A piece is its colour, a small whole number (`COLOURS` names them for whoever reads a screen); 0 is a place
that holds no piece, and BLOCK a cell filled by something that is no piece: it is not swapped, lines up with
nothing and does not fall, and pieces rest on it. How a game gets rid of such a thing is the game's own and is
not modelled: a board on which one is gone is read from the screen like any other result. What comes in at the top is not known before it shows: `apply` leaves those cells
unread (None) and settles only what the known pieces settle among themselves, so a move's result on the
screen may differ from it where the new pieces set off more removals. `assess` therefore takes a settled
board that changed as the move's result, and only an unchanged board as the move not taken.

A cell is one whole number, and what the rules ask of it is what it does, not what a game calls it:

    1..10        a piece of that colour: it is swapped, lines up and falls
    11 (BLOCK)   something fixed: not swapped, lines up with nothing, does not fall, pieces rest on it
    12 (LOOSE)   something that falls and is neither swapped nor lined up (an obstacle that drops)
    20 + colour  a piece held in place (chained, frozen): it lines up as its colour, and is not swapped nor falls.
                 A line it stands in sets it free: it stays, a piece of its colour from then on
    100 * kind + colour   a piece with a power (`KINDS`), of that colour or of none (0): swapped and falls like
                 a piece, lines up as its colour when it has one

A piece with a power comes of a move that lines up more than three: four in a line (LINE: it clears a row or a
column), four in a square (SEEKER: it goes for something on the board), five in an L or a T (AREA: what is
around it), five in a line (COLOUR: every piece of one colour); it is left where the moved piece came to lie.
POWER is one whose kind nobody knows yet. What a power clears is the game's and is only estimated here (`apply`
leaves those cells unread, and counts them for a policy to weigh); the board after is read from the screen.

How a game lets a power be used differs, and each way is a rule value that starts unknown (None), is tried,
and is settled by what the game did (`learned`): `square` (four in a square is a move), `swap_power` (a power
swapped with any neighbour goes off), `pair_power` (two powers swapped with each other go off, and one that
clears a colour swapped with anything: games that want a power lined up to set it off still take these),
`tap_power` (a power tapped goes off).

Pure like every rule model: JSON in and out, no IO, no clock, randomness only from the caller's `draw`.
"""
from __future__ import annotations

# the colours a piece is read as, by the number a state holds for it (1-based; 0 is no piece)
COLOURS = ("red", "orange", "yellow", "green", "blue", "purple", "pink", "white", "cyan", "brown")
EMPTY = 0
# a cell filled by something that is not a piece (an obstacle, a locked or covered cell)
BLOCK = len(COLOURS) + 1
# something that falls and is no piece; a piece held in place is HELD + its colour
LOOSE = BLOCK + 1
HELD = 20
# what a goal about the layers laid under pieces (jelly, grass) is about, and what a move takes of them is counted
# as: no cell holds it. A state may say how many such layers lie under each cell (`under`, a grid like `grid`): the
# piece cleared at a cell takes one off it. In some games the layer is laid, not taken (grass or carpet to be
# spread: rule value `spread`): a line with a cell on the layer lays it under the line's other cells, and what a
# move takes of UNDER is then the cells it newly lays it under (PLAN §31, L2)
UNDER = 13
# the kinds of goal about how many of something: things to take off the board, and a layer to lay under more cells
COUNTED = ("collect", "cover")
# a piece with a power is POWERED * its kind + its colour (0: none)
POWERED = 100
LINE, SEEKER, AREA, COLOUR, POWER = 1, 2, 3, 4, 5
KINDS = (LINE, SEEKER, AREA, COLOUR, POWER)
RUN = 3
PLAYING, OVER, WON = "playing", "over", "won"
# the ways of using a power, each unknown (None) until the game showed whether it takes it
WAYS = ("square", "swap_power", "pair_power", "tap_power")
# whether something fixed (BLOCK) is broken by a line cleared beside it, unknown (None) until the screen showed it
# (PLAN §23, Y3): each is hit once by each wave of lines that clears a cell above, below or beside it
DEFAULT_RULES = {"rows": 8, "cols": 8, "colours": 6, "square": None, "swap_power": None, "pair_power": None, "tap_power": None,
                 "goals": None, "beside": None, "spread": None}
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
    if any(cell is not None and not _value(cell) for row in grid for cell in row):
        problems.append("cell_value")
    elif state.get("scene", PLAYING) == PLAYING and not _unread(state) and _runs(grid):
        # a board at rest holds no line of three: one that does is still moving, or was misread
        problems.append("unsettled")
    if state.get("scene", PLAYING) not in (PLAYING, OVER, WON):
        problems.append("scene")
    under = state.get("under")
    if under is not None and (not isinstance(under, list) or len(under) != rows or any(
            not isinstance(row, list) or len(row) != cols or any(type(cell) is not int or cell < 0 for cell in row)
            for row in under)):
        problems.append("under")
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
            colour = colour_of(grid[r][c])
            if not colour:
                continue
            for dr, dc in STEPS:
                if 0 <= r - dr and 0 <= c - dc and colour_of(grid[r - dr][c - dc]) == colour:
                    continue                 # counted from the line's first cell
                length = 1
                while r + dr * length < rows and c + dc * length < cols and colour_of(grid[r + dr * length][c + dc * length]) == colour:
                    length += 1
                if length >= RUN:
                    found.update((r + dr * step, c + dc * step) for step in range(length))
    return found


def _value(cell) -> bool:
    """Whether a number is one a cell may hold."""
    if type(cell) is not int:
        return False
    if 0 <= cell <= LOOSE or HELD < cell <= HELD + len(COLOURS):
        return True
    return cell // POWERED in KINDS and cell % POWERED <= len(COLOURS)


def power_of(cell) -> int:
    """The kind of power a cell's piece has; 0 for none."""
    return cell // POWERED if cell is not None and cell >= POWERED else 0


def colour_of(cell) -> int:
    """The colour a cell lines up as; 0 when it lines up with nothing."""
    if cell is None or cell <= EMPTY:
        return 0
    if cell < BLOCK:
        return cell
    if HELD < cell <= HELD + len(COLOURS):
        return cell - HELD
    return cell % POWERED if cell >= POWERED else 0


def _piece(cell) -> bool:
    """Whether a cell holds something that is swapped: a piece, with a power or none."""
    return cell is not None and (EMPTY < cell < BLOCK or cell >= POWERED)


def _rests(cell) -> bool:
    """Whether what a cell holds stays where it is when the cells under it empty (nothing passes it either)."""
    return cell is not None and (cell in (EMPTY, BLOCK) or HELD < cell < POWERED)


def _squares(grid) -> set:
    """The cells of every two-by-two square of one colour."""
    found = set()
    for r in range(len(grid) - 1):
        for c in range(len(grid[0]) - 1):
            colour = colour_of(grid[r][c])
            if colour and all(colour_of(grid[y][x]) == colour for y, x in ((r, c + 1), (r + 1, c), (r + 1, c + 1))):
                found.update(((r, c), (r, c + 1), (r + 1, c), (r + 1, c + 1)))
    return found


def _square_through(grid, cell) -> bool:
    """Whether the piece at `cell` stands in a two-by-two square of its colour."""
    r, c = cell
    colour = colour_of(grid[r][c])
    for top in (r - 1, r):
        for left in (c - 1, c):
            if colour and 0 <= top < len(grid) - 1 and 0 <= left < len(grid[0]) - 1 and all(
                    colour_of(grid[y][x]) == colour for y in (top, top + 1) for x in (left, left + 1)):
                return True
    return False


def _swapped(grid, cell, to):
    out = [list(row) for row in grid]
    (r, c), (r2, c2) = cell, to
    out[r][c], out[r2][c2] = out[r2][c2], out[r][c]
    return out


def _lines_through(grid, cell) -> bool:
    """Whether the piece at `cell` stands in a line of RUN or more."""
    return _line(grid, cell)[0] >= RUN or _line(grid, cell)[1] >= RUN


def _line(grid, cell) -> tuple[int, int]:
    """How many of its colour the piece at `cell` stands among, across and down (itself counted)."""
    r, c = cell
    colour = colour_of(grid[r][c])
    if not colour:
        return 0, 0
    lengths = []
    for dr, dc in STEPS:
        length = 1
        for sign in (1, -1):
            step = 1
            while (0 <= r + sign * dr * step < len(grid) and 0 <= c + sign * dc * step < len(grid[0])
                   and colour_of(grid[r + sign * dr * step][c + sign * dc * step]) == colour):
                length, step = length + 1, step + 1
        lengths.append(length)
    return lengths[0], lengths[1]


def way(state: dict, rules: dict, action: dict) -> str | None:
    """What makes a move one the game may take: "line" (three or more line up), else "square" (four in a
    square), else "pair" (two pieces with a power swapped, or one that clears a colour with anything), "power"
    (a piece with a power swapped with a neighbour), "tap" (one tapped); None for a move
    nothing makes one. A way the game was seen not to take (its rule value False) makes none."""
    grid = state["grid"]
    if action.get("kind") == "pop":
        r, c = action["cell"]
        return "tap" if power_of(grid[r][c]) and rules.get("tap_power") is not False else None
    (r, c), (r2, c2) = action["cell"], action["to"]
    if not _piece(grid[r][c]) or not _piece(grid[r2][c2]):
        return None
    swapped = _swapped(grid, (r, c), (r2, c2))
    if grid[r][c] != grid[r2][c2] and (_lines_through(swapped, (r, c)) or _lines_through(swapped, (r2, c2))):
        return "line"
    if rules.get("square") is not False and grid[r][c] != grid[r2][c2] \
            and (_square_through(swapped, (r, c)) or _square_through(swapped, (r2, c2))):
        return "square"
    kinds = power_of(grid[r][c]), power_of(grid[r2][c2])
    if rules.get("pair_power") is not False and (all(kinds) or COLOUR in kinds):
        return "pair"
    if rules.get("swap_power") is not False and any(kinds):
        return "power"
    return None


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
                if r2 >= len(grid) or c2 >= len(grid[0]):
                    continue
                action = {"kind": "swap", "cell": [r, c], "to": [r2, c2]}
                if way(state, rules, action) is not None:
                    found.append(action)
    # a power is tapped only by a game seen to take it, or when it can be used no other way
    powers = [[r, c] for r in range(len(grid)) for c in range(len(grid[0])) if power_of(grid[r][c])]
    if rules.get("tap_power") or (rules.get("tap_power") is None and rules.get("swap_power") is False):
        found.extend({"kind": "pop", "cell": cell} for cell in powers)
    return found


def learned(before: dict, action: dict, effect: str, rules: dict) -> dict:
    """The rule values as a move's outcome settles them: a way of moving the game took is one it takes, and
    one it did not take (the board stayed as it was) is one it does not. Only a move nothing else made one says
    anything, and only of a way not settled yet."""
    if effect not in ("confirmed", "absent"):
        return rules
    try:
        made = way(before, rules, action)
    except (KeyError, IndexError, TypeError, ValueError):
        return rules
    cells = [action[name] for name in ("cell", "to") if name in action]
    if any(power_of(before["grid"][r][c]) == POWER for r, c in cells):
        return rules                 # a thing only taken for a piece with a power says nothing of how powers go off
    if any(power_of(before["grid"][r][c]) not in (0, COLOUR) and not colour_of(before["grid"][r][c]) for r, c in cells):
        # a piece with a power whose colour is not known may have lined up by its colour: the game taking the move
        # says nothing of how powers go off (2026-11-17, a striped candy named by a light model taught a game that
        # takes only lines that it sets a power off swapped with anything)
        return rules
    name = {"square": "square", "power": "swap_power", "pair": "pair_power", "tap": "tap_power"}.get(made)
    if name is None or rules.get(name) is not None:
        return rules
    return {**rules, name: effect == "confirmed"}


def _fall(grid, removed) -> list:
    """The grid with `removed` cells gone and what was above them fallen; what comes in at the top is unread.
    An empty place and whatever stays where it is let nothing through: pieces settle on them, and what comes in
    above a stretch they close off from the top is as unread as anything else that comes in."""
    out = [list(row) for row in grid]
    for c in range(len(grid[0])):
        r = len(grid) - 1
        while r >= 0:
            if _rests(grid[r][c]) and (r, c) not in removed:
                r -= 1
                continue
            top = r                      # one stretch of the column between such cells, bottom to top
            while top - 1 >= 0 and not (_rests(grid[top - 1][c]) and (top - 1, c) not in removed):
                top -= 1
            kept = [grid[y][c] for y in range(r, top - 1, -1) if (y, c) not in removed]
            for offset, y in enumerate(range(r, top - 1, -1)):
                out[y][c] = kept[offset] if offset < len(kept) else None
            r = top - 1
    return out


def _made(grid, cell) -> int:
    """The piece a move leaves at `cell` in place of the one that lined up there: one with the power its line
    earns, of the line's colour; 0 when the line earns none."""
    across, down = _line(grid, cell)
    colour = colour_of(grid[cell[0]][cell[1]])
    if max(across, down) >= 5:
        return POWERED * COLOUR                  # it has no colour of its own from here on
    if across >= RUN and down >= RUN:
        return POWERED * AREA + colour
    if max(across, down) == 4:
        return POWERED * LINE + colour
    if _square_through(grid, cell) and max(across, down) < RUN:
        return POWERED * SEEKER + colour
    return 0


def _reach(grid, cell, other=None) -> set:
    """The cells a power going off at `cell` is taken to clear (an estimate: the game's own reach differs from
    game to game). `other`: the colour of the piece it was swapped with, for one that clears a colour."""
    r, c = cell
    rows, cols = len(grid), len(grid[0])
    kind = power_of(grid[r][c])
    if kind == LINE:
        return {(r, x) for x in range(cols)} | {(y, c) for y in range(rows)}
    if kind == AREA:
        return {(y, x) for y in range(max(0, r - 1), min(rows, r + 2)) for x in range(max(0, c - 1), min(cols, c + 2))}
    if kind == COLOUR:
        colour = other or colour_of(grid[r][c])
        return {(y, x) for y in range(rows) for x in range(cols) if colour and colour_of(grid[y][x]) == colour} | {(r, c)}
    return {(r, c)}


def apply(state: dict, rules: dict, action: dict) -> dict:
    """What a move leaves as far as the pieces on the board decide it: the lines removed (a piece with a power
    left where a line earned one), what was above fallen, the cells new pieces fill left unread, and so on
    until nothing known lines up. Where a power goes off, what it reaches is left unread from there up. `cleared`
    counts the pieces removed, those a power is taken to reach among them; `made` is the power the move earns;
    `taken` what was taken off by what a goal may be about (a piece by its colour), each thing fixed hit beside a
    line counted as BLOCK; `hit` the things fixed the move's own lines hit. A piece held in place that a line or a
    power reaches is set free and stays (counted as HELD in `taken`). Where the layer under the pieces is one
    that spreads (`spread`), each line with a cell on it lays it under its other cells, and a power set off on
    it under what it reaches."""
    grid = state["grid"]
    _require_known(grid)
    made_by = way(state, rules, action)
    if made_by is None:
        raise ValueError("illegal_action")
    moved = [tuple(action["cell"])] if action["kind"] == "pop" else [tuple(action["cell"]), tuple(action["to"])]
    if action["kind"] != "pop":
        grid = _swapped(grid, action["cell"], action["to"])
    cleared, made, blown, taken, hit, lowered = 0, 0, set(), {}, [], {}
    under = [list(row) for row in state["under"]] if isinstance(state.get("under"), list) else None
    if made_by in ("power", "pair", "tap"):
        for cell in moved:
            if power_of(grid[cell[0]][cell[1]]):
                partner = next((colour_of(grid[y][x]) for y, x in moved if (y, x) != cell), 0)
                blown |= _reach(grid, cell, partner)
    spreads = rules.get("spread") is True and under is not None
    # a power set off by the move itself lays the layer it stands on under what it reaches
    laid = spreads and any(power_of(grid[r][c]) and under[r][c] > 0 for r, c in moved)
    first = True
    while True:
        removed = _runs(grid) | (_squares(grid) if first and made_by == "square" else set())
        if not removed and not (first and blown):
            break
        kept = {}
        if first:
            for cell in moved:
                if cell in removed and (earned := _made(grid, cell)):
                    kept[cell], made = earned, made or earned
        # a power caught in a line goes off too
        for r, c in sorted(removed):
            if power_of(grid[r][c]):
                blown |= _reach(grid, (r, c))
        gone = (removed | (blown if first else set())) - set(kept)
        gone = {(r, c) for r, c in gone if grid[r][c] is not None and grid[r][c] not in (EMPTY, BLOCK)}
        # a piece held in place is set free by what reaches it, and stays
        freed = {(r, c) for r, c in gone if HELD < grid[r][c] < POWERED}
        gone -= freed
        if freed:
            taken[HELD] = taken.get(HELD, 0) + len(freed)
        cleared += len(gone)
        if spreads:
            for part in _parts(removed):
                if any(under[r][c] > 0 for r, c in part):
                    _lay(under, part, taken)
            if first and laid:
                _lay(under, {cell for cell in blown if grid[cell[0]][cell[1]] not in (None, EMPTY)}, taken)
        elif under is not None:
            # each piece cleared takes one layer off what lies under it (the layer stays where it is: it does not fall)
            for r, c in gone | (removed & set(kept)):
                if under[r][c] > 0:
                    under[r][c] -= 1
                    taken[UNDER] = taken.get(UNDER, 0) + 1
        if rules.get("beside") is not False:
            # each thing fixed beside a line cleared is hit once by this wave (it stays where it is: whether it
            # broke is the screen's to show)
            struck = {(r + dr, c + dc) for r, c in removed for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1))
                      if 0 <= r + dr < len(grid) and 0 <= c + dc < len(grid[0]) and grid[r + dr][c + dc] == BLOCK}
            taken[BLOCK] = taken.get(BLOCK, 0) + len(struck)
            if first:
                hit = sorted([list(cell) for cell in struck])
        for r, c in gone | (removed & set(kept)):
            # what was taken off the board, by what a goal may be about: a piece by its colour, else its value
            what = colour_of(grid[r][c]) or grid[r][c]
            taken[what] = taken.get(what, 0) + 1
        grid = [list(row) for row in grid]
        for (r, c), earned in kept.items():
            grid[r][c] = earned
        for r, c in freed:
            grid[r][c] -= HELD
        for what, rows in _lowering(grid, gone).items():
            lowered[what] = lowered.get(what, 0) + rows
        grid = _fall(grid, gone)
        first = False
    if blown:
        # what a power reached is the game's to say: unread from the lowest cell reached up, in each column
        for c in {c for _, c in blown}:
            for r in range(max(r for r, x in blown if x == c) + 1):
                if not _rests(grid[r][c]):
                    grid[r][c] = None
    if not taken.get(BLOCK):
        taken.pop(BLOCK, None)
    return {"state": {**state, "grid": grid, **({"under": under} if under is not None else {})}, "cleared": cleared,
            "made": made, "taken": taken, "hit": hit, "lowered": lowered}


def _parts(cells: set) -> list[set]:
    """The cells gathered into those that touch one another by a side."""
    left, parts = set(cells), []
    while left:
        part, edge = set(), [left.pop()]
        while edge:
            r, c = edge.pop()
            part.add((r, c))
            for cell in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                if cell in left:
                    left.discard(cell)
                    edge.append(cell)
        parts.append(part)
    return parts


def _lay(under: list, cells: set, taken: dict) -> None:
    """The layer laid under each of `cells` that has none, counted as UNDER taken."""
    for r, c in cells:
        if under[r][c] == 0:
            under[r][c] = 1
            taken[UNDER] = taken.get(UNDER, 0) + 1


def _layered(state: dict) -> int | None:
    """How many cells have a layer under them; None for a state that says nothing of layers."""
    under = state.get("under")
    if not isinstance(under, list):
        return None
    return sum(1 for row in under for cell in row if isinstance(cell, int) and cell > 0)


def meant(goals, state: dict, rules: dict) -> dict:
    """What a level's goals as read say of the game's ways, with the goals as they are then meant (PLAN §31, L1):
    a goal about the layer under the pieces that asks for more cells than have one cannot be about taking it
    off: the layer is one that spreads, and the goal is to lay it under that many more (`cover`). A goal read as
    `cover` says so itself. Returns the rule values to set (none when nothing is settled)."""
    if not isinstance(goals, list):
        return {}
    layered, spread = _layered(state), rules.get("spread")
    out = []
    for goal in goals:
        if goal.get("target") == UNDER and goal.get("kind") in COUNTED:
            asks = goal.get("count")
            if goal["kind"] == "cover" or (spread is None and layered is not None and type(asks) is int and asks > layered):
                spread = True
            goal = {**goal, "kind": "cover" if spread is True else "collect"}
        out.append(goal)
    changed = {}
    if out != goals:
        changed["goals"] = out
    if spread is not None and spread != rules.get("spread"):
        changed["spread"] = spread
    return changed


def observed(before: dict, action: dict, after: dict, rules: dict) -> dict:
    """The rule values as a move the game took settles them by what the screen showed after it: whether the layer
    under the pieces spreads, by whether more cells or fewer had one after the move (by two at least: a single
    cell is as often a misreading). Only a value not settled yet is set."""
    if rules.get("spread") is not None:
        return rules
    was, now = _layered(before), _layered(after)
    if was is None or now is None or abs(now - was) < 2 or not was:
        return rules
    return {**rules, "spread": now > was}


def _lowering(grid, gone) -> dict:
    """How many rows the things that fall and are no piece (LOOSE) come down as `gone` cells empty below them:
    the cells gone under each, down to whatever stays where it is."""
    lowered = {}
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if cell != LOOSE or (r, c) in gone:
                continue
            rows, y = 0, r + 1
            while y < len(grid) and ((y, c) in gone or not _rests(grid[y][c])):
                rows += (y, c) in gone
                y += 1
            if rows:
                lowered[cell] = lowered.get(cell, 0) + rows
    return lowered


def progress(goals, taken: dict, lowered: dict | None = None, rows: int = 8) -> float:
    """How far a move's `taken` and `lowered` (`apply`) go towards a level's goals: the things a goal to collect
    is about that it takes (for a layer to spread, the cells it lays it under), each goal's counted no further than what it still needs (a goal met gains nothing
    more); and for a goal to bring something down, the rows that thing comes down, a board's height counting as
    one brought down."""
    gained = 0.0
    for goal in goals or ():
        if goal.get("target") is None or goal.get("count") == 0:
            continue
        if goal.get("kind") in COUNTED:
            gained += min(taken.get(goal["target"], 0), goal["count"] if goal.get("count") is not None else len(COLOURS) ** 2)
        elif goal.get("kind") == "bring_down":
            gained += (lowered or {}).get(goal["target"], 0) / max(1, rows)
    return gained


def advance(goals, taken: dict):
    """A level's goals after a move that took `taken`: each goal to collect needs that many fewer, down to none."""
    if not goals:
        return goals
    return [{**goal, "count": max(0, goal["count"] - taken.get(goal["target"], 0))}
            if goal.get("kind") in COUNTED and goal.get("target") is not None and goal.get("count") is not None else goal
            for goal in goals]


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
    """Whether the screen after a move shows it taken. The pieces that come in are the game's, and they may
    set off removals nobody can predict, so a settled board that is no longer the one before is the move's
    result; `settled_as_predicted` says whether every cell the rules could predict reads as predicted."""
    if _unread(after):
        return {"effect": "unknown"}
    if after["grid"] == before["grid"]:
        return {"effect": "absent"}
    try:
        predicted = apply(before, rules, action)["state"]["grid"]
    except (ValueError, KeyError, IndexError, TypeError):
        return {"effect": "inconsistent"}
    if _runs(after["grid"]):
        return {"effect": "inconsistent"}        # still moving, or misread
    if action.get("kind") == "pop":
        # a power that went off is gone from where it stood: one still standing there was not set off, whatever
        # else on the board reads otherwise than before
        r, c = action["cell"]
        if after["grid"][r][c] == before["grid"][r][c]:
            return {"effect": "absent"}
    # a swap that was taken removed pieces: where nothing known fell, the board cannot be just the two swapped back
    same = all(seen == told for row, guess in zip(after["grid"], predicted) for seen, told in zip(row, guess) if told is not None)
    return {"effect": "confirmed", "settled_as_predicted": same}


def unread_state(rules: dict, scene: str) -> dict:
    return {"grid": [[None] * rules["cols"] for _ in range(rules["rows"])], "score": None, "scene": scene, "under": None}


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
    """The game's own move: the move as `apply` has it, every cell left unread filled with pieces drawn from
    `draw` and every line they make removed in turn, and a board with no move left dealt anew. The score counts
    the pieces removed."""
    if action not in legal_actions(state, rules):
        raise ValueError("illegal_action")
    numbers = _stream(draw)
    result = apply(state, rules, action)
    grid, cleared, under = result["state"]["grid"], result["cleared"], result["state"].get("under")
    while True:
        grid = [[1 + next(numbers) % rules["colours"] if cell is None else cell for cell in row] for row in grid]
        removed = _runs(grid)
        if not removed:
            break
        cleared += len(removed)
        if under is not None and rules.get("spread") is True:
            for part in _parts(removed):
                if any(under[r][c] > 0 for r, c in part):
                    _lay(under, part, {})
        else:
            for r, c in removed if under is not None else ():
                under[r][c] = max(0, under[r][c] - 1)
        grid = _fall(grid, removed)
    if not legal_actions({"grid": grid}, rules):
        grid = _deal(rules, numbers)
    return {**state, "grid": grid, "score": (state.get("score") or 0) + cleared, **({"under": under} if under is not None else {})}


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
