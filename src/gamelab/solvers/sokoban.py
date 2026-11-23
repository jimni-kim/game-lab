"""Sokoban: the registered policies over the sokoban rule model (adaptive perception plan §4.2).

Pure like the rule model (standard library only, JSON in and out, no IO, no clock, no unseeded randomness) and
ported with it. Boxes are tried row by row, directions in DIRECTIONS order, and a tie keeps the first found.

search: A* over pushes. A node is where the boxes stand and the part of the floor the player can walk to
without pushing; its children are the pushes the player can walk up to and make. A push onto a dead cell (one
from which no push can bring a box to any goal, found by pulling a box back from every goal over the walls
alone) or into a square of four cells all boxes or walls with a box off its goal is never made: no level is won
after it. The heuristic, the pushes the boxes need at least when each is brought to a goal of its own by the same
pulls (the cheapest pairing of boxes with goals; with each box to its nearest goal instead, open rooms with
several boxes ran past 20000 nodes), never counts more than are needed, so the first plan found takes the fewest
pushes. A plan is played one step at a time, the walks between pushes included; the walks are the shortest, but
their total is not what the search makes least.

What a policy keeps from move to move is the plan (`recall`): the grid it starts from and its steps. A screen
that is the one the plan's next step leads to keeps the rest of it; any other starts afresh. `choose` writes the
plan it finds into the memory it was given, so the screens after it are played from it with no search.
"""
from __future__ import annotations

import heapq
from collections import deque

from ..rules.sokoban import BOXES, DIRECTIONS, GOALS, PLAYERS, STEPS, WALL, legal_actions, move, solved, step

POLICIES = ("baseline", "search")
# the pushes the heuristic gives a box that cannot reach a goal: more than any level needs
FAR = 1 << 20


# ── the level's fixed parts ───────────────────────────────────────────

def _parts(grid) -> tuple[set, set, frozenset, tuple[int, int]]:
    """The floor (every cell not wall), the goals, the boxes and the player."""
    floor, goals, boxes, at = set(), set(), set(), None
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if cell == WALL:
                continue
            floor.add((r, c))
            if cell in GOALS:
                goals.add((r, c))
            if cell in BOXES:
                boxes.add((r, c))
            if cell in PLAYERS:
                at = (r, c)
    return floor, goals, frozenset(boxes), at


def distances(floor: set, goals: set) -> dict[tuple[int, int], dict[tuple[int, int], int]]:
    """For each goal, every cell a box can be pushed from to it with the fewest pushes it takes, walls alone in the
    way: a box pulled back from the goal (a box pulled from b to b-d leaves the player at b-2d). A cell no goal
    reaches is dead."""
    found = {}
    for goal in sorted(goals):
        near = {goal: 0}
        todo = deque([goal])
        while todo:
            r, c = todo.popleft()
            for dr, dc in STEPS.values():
                back, stand = (r - dr, c - dc), (r - 2 * dr, c - 2 * dc)
                if back in floor and stand in floor and back not in near:
                    near[back] = near[(r, c)] + 1
                    todo.append(back)
        found[goal] = near
    return found


def bound(boxes: frozenset, each: dict, kept: dict) -> int | None:
    """The fewest pushes the boxes need at least: each box brought to a goal of its own by the pushes it needs alone,
    over the cheapest such pairing (the Hungarian method). None when no pairing brings every box to a goal. `kept`
    holds what was worked out."""
    if boxes in kept:
        return kept[boxes]
    goals, rows = list(each), sorted(boxes)
    cost = [[each[goal].get(box, FAR) for goal in goals] for box in rows]
    found = _assign(cost)
    kept[boxes] = found if found < FAR else None
    return kept[boxes]


def _assign(cost: list[list[int]]) -> int:
    """The least total of a square cost matrix taking one entry in each row and each column (Kuhn and Munkres, with
    potentials: n**3 steps)."""
    n = len(cost)
    row_low, col_low = [0] * (n + 1), [0] * (n + 1)
    owner, way = [0] * (n + 1), [0] * (n + 1)
    for row in range(1, n + 1):
        owner[0], col = row, 0
        least, used = [FAR * (n + 1)] * (n + 1), [False] * (n + 1)
        while owner[col]:
            used[col] = True
            here, delta, nearest = owner[col], FAR * (n + 1), 0
            for other in range(1, n + 1):
                if not used[other]:
                    gap = cost[here - 1][other - 1] - row_low[here] - col_low[other]
                    if gap < least[other]:
                        least[other], way[other] = gap, col
                    if least[other] < delta:
                        delta, nearest = least[other], other
            for other in range(n + 1):
                if used[other]:
                    row_low[owner[other]] += delta
                    col_low[other] -= delta
                else:
                    least[other] -= delta
            col = nearest
        while col:
            previous = way[col]
            owner[col] = owner[previous]
            col = previous
    return sum(cost[owner[col] - 1][col - 1] for col in range(1, n + 1))


def _frozen(cell: tuple[int, int], boxes: frozenset, floor: set, goals: set) -> bool:
    """Whether a box just pushed to `cell` stands in a square of four cells each a wall or a box, one of those
    boxes off its goal: none of them can ever move again."""
    r, c = cell
    for top, left in ((r - 1, c - 1), (r - 1, c), (r, c - 1), (r, c)):
        square = ((top, left), (top, left + 1), (top + 1, left), (top + 1, left + 1))
        if all(near not in floor or near in boxes for near in square) \
                and any(near in boxes and near not in goals for near in square):
            return True
    return False


# ── walking ───────────────────────────────────────────────────────────

def _reach(start: tuple[int, int], boxes: frozenset, floor: set) -> dict:
    """Every cell the player can walk to from `start` without pushing, each with the step that first reached it."""
    came = {start: None}
    todo = deque([start])
    while todo:
        r, c = todo.popleft()
        for direction in DIRECTIONS:
            dr, dc = STEPS[direction]
            near = (r + dr, c + dc)
            if near in floor and near not in boxes and near not in came:
                came[near] = direction
                todo.append(near)
    return came


def _walk(came: dict, to: tuple[int, int]) -> list[str]:
    path = []
    while came[to] is not None:
        direction = came[to]
        path.append(direction)
        dr, dc = STEPS[direction]
        to = (to[0] - dr, to[1] - dc)
    return path[::-1]


# ── the search ────────────────────────────────────────────────────────

def plan(grid, budget: int) -> dict:
    """The steps from `grid` to the level won: {"steps": [direction] | None, "solved", "nodes": nodes expanded}.
    With `budget` nodes spent before a plan is found, the steps to the node nearest a win by the heuristic (none
    when no push was weighed); None when every position the pushes reach is searched and none wins."""
    floor, goals, boxes, at = _parts(grid)
    each, kept = distances(floor, goals), {}
    live = set().union(*each.values())
    start = bound(boxes, each, kept)
    if start is None:
        return {"steps": None, "solved": False, "nodes": 0}
    # a node: its boxes, the player's cell, the parent node and the push that made it (box, direction)
    nodes: list[tuple[frozenset, tuple[int, int], int, tuple | None]] = [(boxes, at, -1, None)]
    heap = [(start, start, 0, 0)]
    best_g = {(boxes, at): 0}
    closed: set = set()
    expanded, nearest = 0, None
    while heap:
        _, h, g, index = heapq.heappop(heap)
        here, at = nodes[index][0], nodes[index][1]
        if h == 0:
            return {"steps": _steps(nodes, index, grid), "solved": True, "nodes": expanded}
        came = _reach(at, here, floor)
        key = (here, min(came))
        if key in closed:
            continue
        if expanded >= budget:
            break
        closed.add(key)
        expanded += 1
        if index and (nearest is None or (h, -g) < nearest[0]):
            nearest = ((h, -g), index)
        for box in sorted(here):
            for direction in DIRECTIONS:
                dr, dc = STEPS[direction]
                stand, to = (box[0] - dr, box[1] - dc), (box[0] + dr, box[1] + dc)
                if stand not in came or to not in live or to in here:
                    continue
                moved = (here - {box}) | {to}
                if to not in goals and _frozen(to, moved, floor, goals):
                    continue
                if best_g.get((moved, box), g + 2) <= g + 1 or (child := bound(moved, each, kept)) is None:
                    continue
                best_g[(moved, box)] = g + 1
                nodes.append((moved, box, index, (box, direction)))
                heapq.heappush(heap, (g + 1 + child, child, g + 1, len(nodes) - 1))
    else:
        return {"steps": None, "solved": False, "nodes": expanded}
    return {"steps": _steps(nodes, nearest[1], grid) if nearest else [], "solved": False, "nodes": expanded}


def _steps(nodes: list, index: int, grid) -> list[str]:
    """A node's pushes as the player's steps from the start, each walk to the next push included."""
    pushes = []
    while nodes[index][3] is not None:
        pushes.append(nodes[index][3])
        index = nodes[index][2]
    floor, _, boxes, at = _parts(grid)
    steps = []
    for box, direction in reversed(pushes):
        dr, dc = STEPS[direction]
        steps += _walk(_reach(at, boxes, floor), (box[0] - dr, box[1] - dc))
        steps.append(direction)
        boxes, at = (boxes - {box}) | {(box[0] + dr, box[1] + dc)}, box
    return steps


def _actions(grid, steps: list[str]) -> list[dict]:
    """Directions as the moves the rules list, each from the grid the steps before it leave."""
    found = []
    for direction in steps:
        found.append(move(grid, direction))
        grid = step(grid, direction)
    return found


# ── what is kept from move to move ────────────────────────────────────

def recall(memory: dict | None, state: dict, rules: dict) -> dict | None:
    """The plan once `state` is seen too: {"grid": the grid it is played from, "plan": the moves left}. A game starts
    with None. The screen the plan's next move leads to keeps the rest; any other screen starts with no plan; a
    screen with unread cells, or the game's own message, keeps it as it was."""
    grid = state.get("grid")
    if state.get("scene", "playing") != "playing" or not isinstance(grid, list) \
            or any(cell is None for row in grid for cell in row):
        return memory
    if memory is not None:
        if memory["grid"] == grid:
            return memory
        if memory["plan"] and step(memory["grid"], memory["plan"][0]["direction"]) == grid:
            return {"grid": [list(row) for row in grid], "plan": memory["plan"][1:]}
    return {"grid": [list(row) for row in grid], "plan": []}


# ── the policies ──────────────────────────────────────────────────────

def choose(state: dict, rules: dict, policy: str, budget: int, excluded: list[dict] = (), memory: dict | None = None) -> dict:
    """The move a policy picks: {"action": action | None, "nodes": nodes expanded}. `excluded` moves are not picked
    (moves the screen showed had no effect from this state). `memory` is what `recall` kept of the game; the plan
    found is written into it.

    baseline: the first move the rules list. search: the first move of the plan with the fewest pushes, found
    within `budget` nodes; past it, the first move towards the position nearest a win the search reached, else
    the baseline's. None when no position the pushes reach wins: no move is worth playing."""
    if policy not in POLICIES:
        raise ValueError("unknown_policy")
    legal = [action for action in legal_actions(state, rules) if action not in excluded]
    if not legal:
        return {"action": None, "nodes": 0}
    if policy == "baseline":
        return {"action": legal[0], "nodes": 1}
    grid = state["grid"]
    if memory is not None and memory.get("grid") == grid and memory.get("plan") and memory["plan"][0] in legal:
        return {"action": memory["plan"][0], "nodes": 0}
    moves, nodes = _search(grid, budget, legal, excluded)
    if memory is not None:
        memory["grid"], memory["plan"] = [list(row) for row in grid], moves or []
    return {"action": moves[0] if moves else None, "nodes": nodes}


def _search(grid, budget: int, legal: list[dict], excluded) -> tuple[list[dict] | None, int]:
    """The moves to play from `grid` and the nodes spent; None when no win is left. With moves excluded and the plan
    starting with one, each move left is searched from with a share of the budget and the shortest plan kept."""
    found = plan(grid, budget)
    if found["steps"] is None:
        return None, found["nodes"]
    moves = _actions(grid, found["steps"]) or [legal[0]]
    if moves[0] in legal:
        return moves, found["nodes"]
    nodes, kept = found["nodes"], None
    share = max(1, budget // len(legal))
    for action in legal:
        after = step(grid, action["direction"])
        if solved(after):
            return [action], nodes
        tried = plan(after, share)
        nodes += tried["nodes"]
        if tried["solved"] and (kept is None or len(tried["steps"]) < len(kept) - 1):
            kept = [action, *_actions(after, tried["steps"])]
    return kept or [legal[0]], nodes
