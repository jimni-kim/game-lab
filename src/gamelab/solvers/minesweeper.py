"""Minesweeper: the registered policies over the minesweeper rule model (family expansion plan §7.1; adaptive
perception plan §4.2).

Pure like the rule model (standard library only, JSON in and out, no IO, no clock, no unseeded randomness)
and ported with it. Cells are tried row by row and a tie keeps the first.
"""
from __future__ import annotations

from fractions import Fraction
from math import comb

from ..rules.minesweeper import COVERED, MARKED, is_open, legal_actions, neighbours

POLICIES = ("baseline", "solver")
# with the count of mines unknown, how likely a cell nothing is known of holds one, as mine : safe odds
# (one cell in six: between the usual small and large games' one in eight and one in five). A guess: on the
# replay game it wins as often as knowing the count (PLAN §10)
UNKNOWN_ODDS = (1, 5)


class _OverBudget(Exception):
    pass


def _preference(cell: tuple[int, int], rows: int, cols: int) -> tuple[int, int, int]:
    """Among equally likely cells, the one nearest the middle (a first open there clears the most when a game
    keeps the cells around it free), then row by row."""
    r, c = cell
    return (2 * r - rows + 1) ** 2 + (2 * c - cols + 1) ** 2, r, c


def _constraints(grid, rows: int, cols: int, safe: set, mines: set) -> list[tuple[frozenset, int]] | None:
    """Each opened number's covered neighbours not yet decided, with how many of them hold a mine; None when
    the numbers contradict what is decided (a misread board)."""
    found = []
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if not is_open(cell):
                continue
            near = neighbours(r, c, rows, cols)
            left = cell - sum(grid[y][x] in MARKED or (y, x) in mines for y, x in near)
            cells = frozenset((y, x) for y, x in near if grid[y][x] == COVERED and (y, x) not in safe | mines)
            if not 0 <= left <= len(cells):
                return None
            if cells:
                found.append((cells, left))
    return found


def _deduce(grid, rules: dict, thorough: bool) -> tuple[set, set] | None:
    """The covered cells the numbers prove safe and those they prove mines: each number alone, and, when
    `thorough`, one number's cells inside another's and the count of mines left (when the rules give the
    count). None for a misread board."""
    rows, cols = rules["rows"], rules["cols"]
    covered = [(r, c) for r, row in enumerate(grid) for c, cell in enumerate(row) if cell == COVERED]
    counted = rules["mines"] is not None
    left_total = rules["mines"] - sum(cell in MARKED for row in grid for cell in row) if counted else None
    safe: set = set()
    mines: set = set()
    while True:
        found = _constraints(grid, rows, cols, safe, mines)
        if found is None:
            return None
        before = len(safe) + len(mines)
        for cells, left in found:
            if left == 0:
                safe |= cells
            elif left == len(cells):
                mines |= cells
        if thorough:
            for cells, left in found:
                for others, more in found:
                    if cells < others:
                        rest = others - cells
                        if more - left == 0:
                            safe |= rest
                        elif more - left == len(rest):
                            mines |= rest
            undecided = [cell for cell in covered if cell not in safe | mines]
            if counted and left_total - len(mines) == 0:
                safe |= set(undecided)
            elif counted and left_total - len(mines) == len(undecided):
                mines |= set(undecided)
        if safe & mines or (counted and len(mines) > left_total):
            return None
        if len(safe) + len(mines) == before:
            return safe, mines


def _components(found: list[tuple[frozenset, int]]) -> list[list[tuple[frozenset, int]]]:
    """The constraints in groups that share no cell, each in the order found."""
    groups: list[tuple[set, list]] = []
    for constraint in found:
        joined = [group for group in groups if group[0] & constraint[0]]
        cells, members = set(constraint[0]), [constraint]
        for group in joined:
            cells |= group[0]
            members = group[1] + members
            groups.remove(group)
        groups.append((cells, members))
    return [sorted(members, key=lambda item: sorted(item[0])) for _, members in groups]


def _layouts(members: list[tuple[frozenset, int]], nodes: list[int], budget: int) -> tuple[list, dict]:
    """Every way to lay mines on one group's cells that agrees with its numbers: by mine count, how many
    ways and how many of them put a mine on each cell."""
    cells = sorted(set().union(*(cells for cells, _ in members)))
    index = {cell: i for i, cell in enumerate(cells)}
    checks = [([index[cell] for cell in group], left) for group, left in members]
    by_cell: list[list[int]] = [[] for _ in cells]
    for number, (positions, _) in enumerate(checks):
        for position in positions:
            by_cell[position].append(number)
    placed = [0] * len(checks)
    open_left = [len(positions) for positions, _ in checks]
    chosen = [0] * len(cells)
    ways: dict[int, list] = {}

    def walk(at: int, count: int) -> None:
        nodes[0] += 1
        if nodes[0] > budget:
            raise _OverBudget
        if at == len(cells):
            tally = ways.setdefault(count, [0, [0] * len(cells)])
            tally[0] += 1
            for position, mine in enumerate(chosen):
                tally[1][position] += mine
            return
        for mine in (0, 1):
            fits = True
            for number in by_cell[at]:
                left = checks[number][1] - placed[number] - mine
                if left < 0 or left > open_left[number] - 1:
                    fits = False
                    break
            if not fits:
                continue
            chosen[at] = mine
            for number in by_cell[at]:
                placed[number] += mine
                open_left[number] -= 1
            walk(at + 1, count + mine)
            for number in by_cell[at]:
                placed[number] -= mine
                open_left[number] += 1
        chosen[at] = 0

    walk(0, 0)
    return cells, ways


def _convolve(left: dict[int, int], right: dict[int, int]) -> dict[int, int]:
    out: dict[int, int] = {}
    for a, ways_a in left.items():
        for b, ways_b in right.items():
            out[a + b] = out.get(a + b, 0) + ways_a * ways_b
    return out


def _odds(grid, rules: dict, safe: set, mines: set, budget: int) -> tuple[dict, int] | None:
    """How many of all the layouts that agree with the screen put a mine on each covered cell, and how many
    there are (cells beside no number share the mines the numbers leave, every way equally likely). Integers,
    so two hosts compare them alike; None when counting them would take more than `budget` steps."""
    rows, cols = rules["rows"], rules["cols"]
    found = _constraints(grid, rows, cols, safe, mines)
    covered = [(r, c) for r, row in enumerate(grid) for c, cell in enumerate(row)
               if cell == COVERED and (r, c) not in safe | mines]
    edge = set().union(*(cells for cells, _ in found)) if found else set()
    inner = [cell for cell in covered if cell not in edge]
    left = rules["mines"] - sum(cell in MARKED for row in grid for cell in row) - len(mines)
    nodes = [0]
    try:
        groups = [_layouts(members, nodes, budget) for members in _components(found or [])]
    except _OverBudget:
        return None
    counts = [{k: tally[0] for k, tally in ways.items()} for _, ways in groups]
    every = {0: 1}
    for count in counts:
        every = _convolve(every, count)
    total = sum(ways * comb(len(inner), left - k) for k, ways in every.items() if 0 <= left - k <= len(inner))
    odds = {cell: 0 for cell in safe}
    odds.update({cell: total for cell in mines})
    for number, (cells, ways) in enumerate(groups):
        others = {0: 1}
        for other, count in enumerate(counts):
            if other != number:
                others = _convolve(others, count)
        for k, (_, per_cell) in ways.items():
            weight = sum(o * comb(len(inner), left - k - kk) for kk, o in others.items() if 0 <= left - k - kk <= len(inner))
            for cell, mine_ways in zip(cells, per_cell):
                odds[cell] = odds.get(cell, 0) + mine_ways * weight
    if inner:
        share = sum(ways * comb(len(inner) - 1, left - k - 1) for k, ways in every.items() if 1 <= left - k <= len(inner))
        odds.update({cell: share for cell in inner})
    return odds, total


def _odds_uncounted(grid, rules: dict, safe: set, mines: set, budget: int) -> tuple[dict, int] | None:
    """`_odds` with the count of mines unknown: each cell's chance of a mine among the layouts of its own
    group of numbers, a layout weighted by UNKNOWN_ODDS for each mine it lays (cells beside no number have
    that chance itself). Exact fractions; the total is 1, or 0 when a group has no layout."""
    rows, cols = rules["rows"], rules["cols"]
    found = _constraints(grid, rows, cols, safe, mines)
    covered = [(r, c) for r, row in enumerate(grid) for c, cell in enumerate(row)
               if cell == COVERED and (r, c) not in safe | mines]
    edge = set().union(*(cells for cells, _ in found)) if found else set()
    nodes = [0]
    try:
        groups = [_layouts(members, nodes, budget) for members in _components(found or [])]
    except _OverBudget:
        return None
    mine, clear = UNKNOWN_ODDS
    odds = {cell: Fraction(0) for cell in safe}
    odds.update({cell: Fraction(1) for cell in mines})
    for cells, ways in groups:
        most = max(ways, default=0)
        weights = {k: mine ** k * clear ** (most - k) for k in ways}
        total = sum(tally[0] * weights[k] for k, tally in ways.items())
        if total == 0:
            return odds, 0
        for position, cell in enumerate(cells):
            odds[cell] = Fraction(sum(tally[1][position] * weights[k] for k, tally in ways.items()), total)
    odds.update({cell: Fraction(mine, mine + clear) for cell in covered if cell not in edge})
    return odds, 1


def choose(state: dict, rules: dict, policy: str, budget: int, excluded: list[dict] = ()) -> dict:
    """The cell a policy opens: {"action": action | None, "certain": whether the screen proves it safe}.
    `excluded` moves are not picked (moves the screen showed had no effect from this state).

    baseline: a cell each number alone proves safe, else the free cell nearest the middle. solver: also what
    numbers prove together and the count of mines left, then the cell with the fewest layouts that put a mine
    on it, counting at most `budget` steps (past it, the baseline's guess). None when no layout agrees with
    the screen: the board was misread, and no cell is opened on it.

    The rules' count of mines may be unknown (null): the numbers alone then decide. A count that leaves no
    cell to open in a game the screen does not show won is a wrong count, and that move is chosen without it."""
    if policy not in POLICIES:
        raise ValueError("unknown_policy")
    legal = [action for action in legal_actions(state, rules) if action not in excluded]
    if not legal:
        return {"action": None, "certain": False}
    chosen = _choose(state, rules, policy, budget, legal)
    if chosen["action"] is None and rules["mines"] is not None:
        chosen = _choose(state, {**rules, "mines": None}, policy, budget, legal)
    return chosen


def _choose(state: dict, rules: dict, policy: str, budget: int, legal: list[dict]) -> dict:
    grid, rows, cols = state["grid"], rules["rows"], rules["cols"]
    open_cells = sorted((tuple(action["cell"]) for action in legal), key=lambda cell: _preference(cell, rows, cols))
    decided = _deduce(grid, rules, policy == "solver")
    if decided is None:
        return {"action": None, "certain": False}
    safe, mines = decided
    pick = next((cell for cell in sorted(open_cells) if cell in safe), None)
    if pick is not None:
        return {"action": {"kind": "open", "cell": list(pick)}, "certain": True}
    free = [cell for cell in open_cells if cell not in mines]
    if not free:
        return {"action": None, "certain": False}
    count = _odds if rules["mines"] is not None else _odds_uncounted
    if policy == "solver" and (counted := count(grid, rules, safe, mines, budget)) is not None:
        odds, total = counted
        if total == 0:
            return {"action": None, "certain": False}
        pick = min(free, key=lambda cell: odds[cell])        # `free` is in preference order: a tie keeps the first
        return {"action": {"kind": "open", "cell": list(pick)}, "certain": odds[pick] == 0}
    return {"action": {"kind": "open", "cell": list(free[0])}, "certain": False}
