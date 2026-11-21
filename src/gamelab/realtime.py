"""A game in which time moves: pieces fall into a well on their own, and the loop that plays it (PLAN §29).

Every other way the harness plays is "look, one move, look again": the game waits. Here it does not, so the loop
is another one, and no model is in it:

    setup     a model is shown the first screen once (`watch_falling.md`) and says where the well is, how many
              cells it has, which keys (or, in a game played by touch, which taps and swipes) turn, move and
              drop a piece, and what starts the game
    learn     what an empty cell looks like, from that screen (`Well.learn`)
    tick      the well is captured and read as filled and empty cells; what is filled beyond the settled stack the
              code keeps is the falling piece
    plan      once a piece: every turn of it dropped in every column, weighed as such games long have been (the
              holes it leaves, the height, the unevenness, the lines it clears) (`best`)
    act       one step towards the plan each tick, by what the screen shows now: a turn while the piece is not
              yet the shape planned, then sideways, then down. Nothing is assumed of how the game turns a piece
    confirm   when the stack changed, it must be what the plan said (or the piece come down where it was last
              seen); else the stack is taken from the screen again

Nothing here knows a game: what differs from one to the next is what the model said of the first screen and the
looks learned from it.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import statistics
import time
from typing import Any, Awaitable, Callable

from . import pixels, resources
from .execution import TAP_MS
from .model import Model, Msg, TurnRequest
from .surface import DRAG, KEYS, TAP

SETUP_TOKENS, SETUP_TIMEOUT_S = 1500, 90.0
# a well has this many cells a side at least and at most, and a cell is this many pixels at least
CELLS_FEWEST, CELLS_MOST, CELL_LEAST = 4, 40, 6
# a key as a model may name it: one of a keyboard's names, nothing a page could be told to run
KEY = re.compile(r"[A-Za-z0-9]{1,12}")
# a control of a game played by touch, kept as a string as a key is (so that it is compared, kept and read back
# the same way): a tap at a point, or a swipe from one point to another over a few milliseconds
TAP_TOKEN = re.compile(r"tap:(\d{1,5}),(\d{1,5})")
SWIPE_TOKEN = re.compile(r"swipe:(\d{1,5}),(\d{1,5}),(\d{1,5}),(\d{1,5}),(\d{1,4})")
SWIPE_MS = (10, 1000)
# gestures sent in one tick at most: each takes a device a tenth of a second or more, in which nothing is looked at
GESTURES_MOST = 3
# a cell is read by its inner half: the well made SUB pixels a cell, the middle ones of each taken
SUB = 4
# a cell is filled when its colour is this far (the largest difference of a channel, of 255) from an empty one's,
# and at least CLEAR times as far as empty cells were from one another on the screen learned from
FILLED_LEAST, CLEAR = 28.0, 2.5
# what falls is one piece of at most this many cells
PIECE_MOST = 6
# the weights such games are played by (the height of all columns, lines cleared, holes, unevenness)
W_HEIGHT, W_LINES, W_HOLES, W_BUMPS = -0.510066, 0.760666, -0.35663, -0.184483
# turns asked of one piece before the shape it has is played as it is; a piece asked sideways this often without
# moving is dropped where it is
TURNS_MOST, SHIFTS_MOST = 5, 3
TURN_FAILS = 2
# keys that take a piece down one row are sent this many a tick at most (a game takes them a frame apart)
DOWN_BATCH = 4
# a stack that fits neither the plan nor the piece come straight down is looked at for this long (lines being
# cleared are drawn first) before it is taken from the screen as it is; so often in a row with no piece placed
# between, the well is not being read
SETTLE_S, RESYNCS_MOST = 1.2, 4
# no piece seen for this long: nothing falls any more
IDLE_S = 4.0
# after the game is started, its first piece is waited for this long
FIRST_S = 8.0
# after the input that starts the game, the screen is looked at again this much later
STARTED_S = 1.5
# a key that changed nothing where it should have is held down this long from then on, and let go as long before
# the next (a game that looks each frame at which keys are down misses one pressed and let go between two frames)
HOLD_MS = 40
# keys tried when the one the model named changes nothing (or it named none): those it gave besides (`also`),
# these when it gave none. One for dropping a piece at once is kept only when the piece then lay where it was
# planned at the next look; one for turning, when the piece then had another of its shapes
DROP_TRIED, TURN_TRIED, TRIED_MOST = ("Space",), ("ArrowUp", "x", "z"), 4
# a key for turning is given up for the next after it left the piece as it was this often
# it is tried only on a piece with this many rows still to fall, the piece must lie as planned within this many
# looks after, and one that has not moved at all this long after was stopped by the key
DROP_ROWS, DROP_LOOKS, DROP_UNDO_S = 4, 2, 1.5
# a look is taken no sooner than this after a key, so that it shows what the key did
SHOWN_S = 0.04
RT = "RT"

Cells = frozenset


def turns(cells: Cells) -> list[Cells]:
    """A piece's shapes as it turns a quarter at a time, each with its top-left at (0, 0), without repeats."""
    out: list[Cells] = []
    shape = normal(cells)
    for _ in range(4):
        if shape not in out:
            out.append(shape)
        shape = normal(Cells((c, -r) for r, c in shape))
    return out


def normal(cells: Any) -> Cells:
    top, left = min(r for r, _ in cells), min(c for _, c in cells)
    return Cells((r - top, c - left) for r, c in cells)


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


def weigh(stack: Cells, rows: int, cols: int, lines: int) -> float:
    heights = [rows - min((r for r, c in stack if c == col), default=rows) for col in range(cols)]
    holes = sum((r, c) not in stack for c in range(cols) for r in range(rows - heights[c], rows))
    bumps = sum(abs(a - b) for a, b in zip(heights, heights[1:]))
    return W_HEIGHT * sum(heights) + W_LINES * lines + W_HOLES * holes + W_BUMPS * bumps


def best(stack: Cells, piece: Cells, rows: int, cols: int, only: Cells | None = None) -> dict[str, Any] | None:
    """Where to put the piece: `{"shape", "column", "placed", "after", "lines"}`, the turn of it and the column of
    its leftmost cell that leave the best stack when it is dropped straight down from the top. `only`: this turn
    of it alone (the game would not turn it further). None when it fits nowhere."""
    found = None
    for shape in ([only] if only is not None else turns(piece)):
        wide = max(c for _, c in shape) + 1
        for column in range(cols - wide + 1):
            placed = fall(stack, Cells((r, c + column) for r, c in shape), rows, cols)
            if placed is None:
                continue
            after, lines = settle(stack, placed, rows, cols)
            score = weigh(after, rows, cols, lines)
            if found is None or score > found["score"]:
                found = {"shape": shape, "column": column, "placed": placed, "after": after, "lines": lines, "score": score}
    return found


def whole(cells: Cells) -> bool:
    """Whether the cells are one piece: a few of them, each beside another."""
    if not 1 <= len(cells) <= PIECE_MOST:
        return False
    reached, edge = set(), [next(iter(cells))]
    while edge:
        r, c = edge.pop()
        if (r, c) in reached:
            continue
        reached.add((r, c))
        edge.extend(n for n in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)) if n in cells)
    return len(reached) == len(cells)


def top_piece(filled: Cells) -> Cells:
    """The cells that hang together with the topmost filled one: the falling piece, when it touches nothing."""
    if not filled:
        return Cells()
    reached, edge = set(), [min(filled)]
    while edge:
        r, c = edge.pop()
        if (r, c) in reached:
            continue
        reached.add((r, c))
        edge.extend(n for n in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)) if n in filled)
    return Cells(reached)


def settled(filled: Cells, rows: int, cols: int, unlike: Callable[[Cells, Cells], bool] = lambda a, b: False
            ) -> Cells:
    """What of a well read as `filled` has come to lie: all but the piece at its top, and that piece's shadow
    where it would land (the same shape straight below it, resting on the rest, and drawn `unlike` the piece:
    by shape alone a shadow is not told from blocks that lie there)."""
    piece = top_piece(filled)
    if not whole(piece):
        return filled
    rest = filled - piece
    for down in range(1, rows):
        below = Cells((r + down, c) for r, c in piece)
        if below <= rest and fall(rest - below, piece, rows, cols) == below:
            return rest - below if unlike(below, piece) else rest
    return rest


def piece_in(stack: Cells, extra: Cells, rows: int, cols: int, known: list[Cells] | None = None) -> Cells | None:
    """The falling piece among the cells filled beyond the stack: those cells themselves when they are one piece,
    or the piece without the shadow a game draws of it where it would come to lie (the same shape straight below
    it, resting on the stack). A piece part of which is still above the well is given whole when its shadow shows
    its shape (rows above the well are negative). `known`: the shapes the piece now falling turns to. None when
    the cells are neither."""
    if not extra:
        return None
    if known and normal(extra) in known:
        return extra
    top = top_piece(extra)
    rest = extra - top
    if rest:
        if not whole(rest) or fall(stack, rest, rows, cols) != rest:
            return None
        up = max(r for r, _ in rest) - max(r for r, _ in top)
        piece = Cells((r - up, c) for r, c in rest)
        return piece if Cells(cell for cell in piece if cell[0] >= 0) == top else None
    if known:
        # a piece come so near its shadow that the two are one patch: a shape it turns to, at the patch's top
        high, left = min(r for r, _ in extra), min(c for _, c in extra)
        for shape in known:
            for column in range(left, max(c for _, c in extra) + 1):
                piece = Cells((r + high, c + column) for r, c in shape)
                if piece <= extra and extra - piece and extra - piece <= (fall(stack, piece, rows, cols) or Cells()):
                    return piece
    return extra if whole(extra) else None


class Well:
    """Where the well is on the screen and what its empty cells look like: a capture of it read as the set of
    its filled cells."""

    def __init__(self, box: list[int], rows: int, cols: int) -> None:
        self.box, self.rows, self.cols = box, rows, cols
        self.empty: tuple[float, float, float] | None = None
        self.near = FILLED_LEAST

    def _colours(self, png: bytes) -> list[tuple[float, float, float]]:
        """Each cell's colour, row by row, from a capture of the well alone or of the whole screen."""
        from PIL import Image

        image = pixels.decode(png, None)
        if image.size != (self.box[2] - self.box[0], self.box[3] - self.box[1]):
            image = image.crop(tuple(self.box))
        wide = self.cols * SUB
        raw = image.resize((wide, self.rows * SUB), Image.BOX).tobytes()
        # the middle of each cell alone: what lies between two cells (a line, a neighbour's edge) is left out
        inner = range(SUB // 4, SUB - SUB // 4)
        out = []
        for row in range(self.rows):
            for col in range(self.cols):
                taken = [3 * ((row * SUB + y) * wide + col * SUB + x) for y in inner for x in inner]
                out.append(tuple(sum(raw[at + band] for at in taken) / len(taken) for band in range(3)))
        return out

    def learn(self, png: bytes) -> None:
        """What an empty cell looks like, from a screen most of whose cells are empty: the colour in the middle of
        them all, and how far they lie from it."""
        colours = self._colours(png)
        # the top of a well is empty but for the piece coming in, whatever is stacked below: an empty cell is
        # what most cells of its top quarter look like
        top = colours[:max(1, self.rows // 4) * self.cols]
        self.empty = tuple(statistics.median(colour[band] for colour in top) for band in range(3))
        # how far empty cells lie from one another is taken of those that look empty alone: a first screen may
        # have half its cells filled (a game already going, a tutorial's stack), and those would raise it past
        # what any filled cell reaches
        apart = sorted(far for far in map(self._far, colours) if far <= FILLED_LEAST) or [0.0]
        self.near = max(FILLED_LEAST, CLEAR * apart[int(0.8 * (len(apart) - 1))])

    def _far(self, colour: tuple[int, int, int]) -> float:
        return max(abs(colour[band] - self.empty[band]) for band in range(3))

    def unlike(self, png: bytes) -> Callable[[Cells, Cells], bool]:
        """Whether two sets of cells of this capture are drawn in colours farther apart than empty cells are."""
        colours = self._colours(png)

        def mean(cells: Cells) -> tuple[float, ...]:
            return tuple(statistics.fmean(colours[r * self.cols + c][band] for r, c in cells) for band in range(3))

        def apart(one: Cells, other: Cells) -> bool:
            a, b = mean(one), mean(other)
            return max(abs(a[band] - b[band]) for band in range(3)) > self.near
        return apart

    def read(self, png: bytes) -> Cells:
        colours = self._colours(png)
        return Cells((index // self.cols, index % self.cols) for index, colour in enumerate(colours)
                     if self._far(colour) > self.near)

    def to_json(self) -> dict[str, Any]:
        return {"box": self.box, "rows": self.rows, "cols": self.cols,
                "empty": [round(band) for band in self.empty] if self.empty else None, "near": round(self.near, 1)}


def _key(value: Any) -> str | None:
    return value if isinstance(value, str) and KEY.fullmatch(value) else None


def _point(x: Any, y: Any, screen: list[int]) -> tuple[int, int] | None:
    if not (type(x) in (int, float) and type(y) in (int, float) and 0 <= x < screen[0] and 0 <= y < screen[1]):
        return None
    return round(x), round(y)


def _control(value: Any, screen: list[int], kinds: frozenset[str]) -> str | None:
    """A control as the loop sends it, or None when the surface cannot send it or it lies off the screen: a key
    name; `tap:X,Y`; `swipe:X0,Y0,X1,Y1,MS`. A model may give a gesture as such a string or as an object
    (`{"kind": "tap", "x", "y"}`, `{"kind": "swipe", "from": [x, y], "to": [x, y], "ms"}`)."""
    if isinstance(value, str):
        if KEY.fullmatch(value):
            return value if KEYS in kinds else None
        if found := TAP_TOKEN.fullmatch(value):
            value = {"kind": "tap", "x": int(found[1]), "y": int(found[2])}
        elif found := SWIPE_TOKEN.fullmatch(value):
            value = {"kind": "swipe", "from": [int(found[1]), int(found[2])], "to": [int(found[3]), int(found[4])],
                     "ms": int(found[5])}
        else:
            return None
    if not isinstance(value, dict):
        return None
    if value.get("kind") == "tap" and TAP in kinds:
        point = _point(value.get("x"), value.get("y"), screen)
        return f"tap:{point[0]},{point[1]}" if point else None
    if value.get("kind") in ("swipe", "drag") and DRAG in kinds:
        ends = [value.get("from"), value.get("to")]
        if not all(isinstance(end, list) and len(end) == 2 for end in ends):
            return None
        points = [_point(*end, screen) for end in ends]
        ms = value.get("ms")
        if None in points or type(ms) is not int or not SWIPE_MS[0] <= ms <= SWIPE_MS[1]:
            return None
        (x0, y0), (x1, y1) = points
        return f"swipe:{x0},{y0},{x1},{y1},{ms}" if (x0, y0) != (x1, y1) else None
    return None


def gesture(token: str) -> dict[str, Any] | None:
    """What a control sends when it is not a key: a tap or a drag; None for a key."""
    if found := TAP_TOKEN.fullmatch(token):
        return {"kind": "tap", "x": int(found[1]), "y": int(found[2])}
    if found := SWIPE_TOKEN.fullmatch(token):
        x0, y0, x1, y1, ms = map(int, found.groups())
        return {"kind": "drag", "x0": x0, "y0": y0, "x1": x1, "y1": y1, "ms": ms}
    return None


def understood(answer: Any, screen: list[int], kinds: frozenset[str]) -> dict[str, Any] | None:
    """A model's answer about the first screen as the loop can use it, or None when it cannot: the well inside
    the screen with cells of a size to read, the controls (keys or gestures, `_control`) that move a piece
    sideways and at least one that takes it down, and an input that starts the game of a kind the surface sends.
    Where keys can be sent, those such games use are tried when none other was given (`also`)."""
    if not isinstance(answer, dict) or not (KEYS in kinds or TAP in kinds):
        return None
    box, cols, rows = answer.get("board"), answer.get("columns"), answer.get("rows")
    if not (isinstance(box, list) and len(box) == 4 and all(type(part) in (int, float) for part in box)
            and type(cols) is int and type(rows) is int):
        return None
    left, top, right, bottom = (round(part) for part in box)
    if not (0 <= left < right <= screen[0] and 0 <= top < bottom <= screen[1]
            and CELLS_FEWEST <= cols <= CELLS_MOST and CELLS_FEWEST <= rows <= CELLS_MOST
            and (right - left) / cols >= CELL_LEAST and (bottom - top) / rows >= CELL_LEAST):
        return None
    said = answer.get("keys") if isinstance(answer.get("keys"), dict) else {}
    keys = {name: _control(said.get(name), screen, kinds) for name in ("left", "right", "rotate", "down", "drop")}
    if not (keys["left"] and keys["right"] and (keys["down"] or keys["drop"])):
        return None
    start = answer.get("start") if isinstance(answer.get("start"), dict) else None
    if start is not None:
        if start.get("kind") == "key" and KEYS in kinds and _key(start.get("key")):
            start = {"kind": "key", "key": start["key"]}
        elif start.get("kind") == "tap" and TAP in kinds and all(type(start.get(axis)) in (int, float) for axis in "xy") \
                and 0 <= start["x"] < screen[0] and 0 <= start["y"] < screen[1]:
            start = {"kind": "tap", "x": start["x"], "y": start["y"]}
        else:
            start = None
    situation = answer["situation"][:300] if isinstance(answer.get("situation"), str) else ""
    more = answer.get("also") if isinstance(answer.get("also"), dict) else {}
    also = {name: [control for control in dict.fromkeys(_control(value, screen, kinds) for value in
                                                        (more.get(name) if isinstance(more.get(name), list) else []))
                   if control][:TRIED_MOST]
            for name in ("rotate", "drop")}
    if KEYS in kinds:
        # (a game played by touch alone has no keys of its own to try: only what the model gave)
        also = {"rotate": also["rotate"] or list(TURN_TRIED), "drop": also["drop"] or list(DROP_TRIED)}
    return {"box": [left, top, right, bottom], "rows": rows, "cols": cols, "keys": keys, "start": start,
            "situation": situation, "also": also}


def record(setup: dict[str, Any], well: "Well") -> dict[str, Any]:
    """What is kept of a game once pieces came to lie where the code put them: the model's answer as the loop
    used it and the look of the well's empty cells on the screen it was given for."""
    return {"setup": setup, "empty": list(well.empty) if well.empty else None, "near": well.near}


def learned(loop: "Loop") -> dict[str, Any]:
    """What the loop found out of the game by playing it, kept with the model's words."""
    return {"hold_ms": loop.hold, "shadow": loop.shadow, "turning": loop.turning,
            "dropping": loop.dropping if loop.dropping in ("found", "none") else None}


def again(kept: Any, png: bytes, screen: list[int], kinds: frozenset[str]) -> dict[str, Any] | None:
    """What was kept of a game (`record`) as the loop can use it on this screen, or None when the screen is not
    the one it was said of: the well must lie inside the screen as before, and the cells in it must look as its
    empty cells did then."""
    setup = kept.get("setup") if isinstance(kept, dict) else None
    empty = kept.get("empty") if isinstance(kept, dict) else None
    if not (isinstance(setup, dict) and isinstance(empty, list) and len(empty) == 3
            and all(type(band) in (int, float) for band in empty) and type(kept.get("near")) in (int, float)):
        return None
    said = understood({"board": setup.get("box"), "columns": setup.get("cols"), "rows": setup.get("rows"),
                       "keys": setup.get("keys"), "start": setup.get("start"), "situation": setup.get("situation"),
                       "also": setup.get("also")},
                      screen, kinds)
    if said is None:
        return None
    well = Well(said["box"], said["rows"], said["cols"])
    try:
        well.learn(png)
    except Exception:
        return None
    if max(abs(well.empty[band] - empty[band]) for band in range(3)) > max(FILLED_LEAST, kept["near"]):
        return None
    hold = setup.get("hold_ms")
    said["hold_ms"] = hold if type(hold) is int and 0 <= hold <= 10 * HOLD_MS else 0
    said["shadow"] = setup.get("shadow") is True
    said["dropping"] = setup.get("dropping") if setup.get("dropping") in ("found", "none") else None
    if said["dropping"] == "found" and not said["keys"]["drop"]:
        said["dropping"] = None
    said["turning"] = setup.get("turning") if setup.get("turning") in ("found", "none") else None
    return said


async def ask(model: Model, png: bytes, screen: list[int], kinds: frozenset[str], hint: str | None = None
              ) -> dict[str, Any] | None:
    """The one question of a game: where its well is and how it is played (`understood`)."""
    prompt = (resources.FRAGMENT / "prompts" / "watch_falling.md").read_text(encoding="utf-8")
    context = {"screen": screen, "inputs": sorted(kinds), **({"hint": hint} if hint else {})}
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                          "data": base64.b64encode(png).decode("ascii")}}
    result = await model.complete(TurnRequest(
        system_stable=prompt, max_tokens=SETUP_TOKENS, timeout_s=SETUP_TIMEOUT_S,
        messages=[Msg("user", [{"type": "text", "text": json.dumps(context, ensure_ascii=False)}, image])]))
    try:
        answer = json.loads(result.text or "")
    except ValueError:
        return None
    return understood(answer, screen, kinds)


class Loop:
    """One game played tick by tick. `send(input)` carries an input out (and counts it), `check()` raises when the
    game must stop (a budget, a cancel), `seen(png)` is told each capture, and `placed(move)` each piece come to
    lie."""

    def __init__(self, surface: Any, setup: dict[str, Any], *, send: Callable[[dict[str, Any]], Awaitable[None]],
                 check: Callable[[], None], seen: Callable[[bytes], Awaitable[None]] | None = None,
                 placed: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.surface, self.setup, self.send, self.check, self.seen, self.placed = surface, setup, send, check, seen, placed
        self.well = Well(setup["box"], setup["rows"], setup["cols"])
        self.keys = setup["keys"]
        # learned as the game is played, and kept with what the model said (`record`): how long a key is held,
        # whether the game draws a piece's shadow, and what came of trying a key that drops a piece at once
        # (None: not tried yet)
        self.hold = setup.get("hold_ms") or 0
        self.shadow = bool(setup.get("shadow"))
        self.dropping: str | None = "named" if self.keys["drop"] else setup.get("dropping")
        also = setup.get("also") or {}
        self.drops = [key for key in (also.get("drop") or []) if key != self.keys["drop"]]
        # the keys a piece may be turned with, the one in use first; "found" once one was seen to turn a piece,
        # "none" when none did
        self.turning: str | None = setup.get("turning")
        self.turners = [key for key in dict.fromkeys([self.keys["rotate"], *(also.get("rotate") or [])]) if key]
        self.turn_fails = 0
        self.sent_at = 0.0
        self.stack: Cells = Cells()
        self.lines = self.pieces = self.resyncs = self.ticks = 0
        # how the pieces came to lie: as planned, straight down from where they were last seen, or unknown
        self.effects = {"confirmed": 0, "inconsistent": 0, "unknown": 0}
        self.capture_s: list[float] = []
        self.read_s: list[float] = []
        self.piece_s: list[float] = []
        self.started = 0.0

    async def look(self) -> Cells:
        self.check()
        early = SHOWN_S - (time.monotonic() - self.sent_at)
        if early > 0:
            await asyncio.sleep(early)
        at = time.monotonic()
        png = await self.surface.capture(self.well.box)
        took = time.monotonic()
        if self.seen is not None:
            await self.seen(png)
        filled = self.well.read(png)
        self.capture_s.append(took - at)
        self.read_s.append(time.monotonic() - took)
        self.ticks += 1
        return filled

    async def begin(self, first: bytes) -> None:
        """The empty cell's look learned from the first screen, and the game started when it has not."""
        self.well.learn(first)
        self.stack = Cells()
        if self.setup["start"] is not None:
            await self.send(self.setup["start"])
            # the screen the start was said of may be dimmed under a notice; and a game may begin with rows
            # already stacked: both are taken from the screen once it shows the game
            await asyncio.sleep(STARTED_S)
            first = await self.surface.capture(self.well.box)
            self.well.learn(first)
        # what is filled now, but for the piece on its way (and its shadow, where the game draws one), is the stack
        self.stack = settled(self.well.read(first), self.well.rows, self.well.cols, self.well.unlike(first))
        self.started = time.monotonic()

    async def play(self, done: Callable[["Loop"], bool]) -> str:
        """Pieces placed until `done` says so (returns "done"), nothing falls any more ("stopped_falling", or
        "no_piece" when none ever did), or the well cannot be read ("well_unread"). A budget ends it by `check`."""
        rows, cols = self.well.rows, self.well.cols
        plan: dict[str, Any] | None = None
        last: Cells | None = None
        began = idle_since = time.monotonic()
        odd_since: float | None = None
        turned = shifted = sent = streak = 0
        # what the last key was to change, as it stood then: a key after which it stands the same did nothing
        pushed: int | None = None
        spun: Cells | None = None
        # a key tried for dropping a piece at once: when, and where the piece stood then
        tried: tuple[float, Cells, int, str] | None = None
        while True:
            if done(self):
                return "done"
            filled = await self.look()
            now = time.monotonic()
            extra, missing = filled - self.stack, self.stack - filled
            if not missing and not extra:
                odd_since = None
                if now - idle_since > (IDLE_S if self.pieces else FIRST_S):
                    return "stopped_falling" if self.pieces else "no_piece"
                continue
            idle_since = now
            piece = None if missing else piece_in(self.stack, extra, rows, cols, plan["turns"] if plan else None)
            if piece is not None:
                # a piece on its way over the stack as the code knows it (with its shadow, where a game draws one)
                odd_since = None
                self.shadow = self.shadow or piece != extra
                shape = normal(piece)
                if plan is None or shape not in plan["turns"]:
                    found = best(self.stack, piece, rows, cols)
                    if found is None:
                        return "stopped_falling"
                    plan = {**found, "turns": turns(piece)}
                    began, turned, shifted, sent, pushed, spun = now, 0, 0, 0, None, None
                column = min(c for _, c in piece)
                if tried is not None:
                    if self.ticks - tried[2] <= DROP_LOOKS:
                        continue
                    if self.dropping == "tried" and piece == tried[1] and now - tried[0] > DROP_UNDO_S:
                        # the piece has not moved at all since: the key stopped the game, and is pressed once more
                        self.dropping, self.drops = "none", []
                        sent += await self._press(tried[3])
                        tried = None
                        continue
                    if piece != tried[1]:
                        self.dropping, tried = (None if self.drops else "none"), None
                stuck = ([self.keys["rotate"]] if spun == shape else []) + \
                    ([self.keys["left"], self.keys["right"]] if pushed == column else [])
                if stuck and not self.hold and any(gesture(control) is None for control in stuck if control):
                    # a key changed nothing: keys are held down from now on (and the piece is asked again). A
                    # gesture is not held: one that changed nothing is given up as a key held down would be
                    self.hold, turned, shifted = HOLD_MS, 0, 0
                elif spun == shape and self.turning is None:
                    # held down, the key still did not turn the piece: after a few times the next is tried
                    self.turn_fails += 1
                    if self.turn_fails >= TURN_FAILS:
                        self.turners, self.turn_fails, turned = self.turners[1:], 0, 0
                        self.keys["rotate"] = self.turners[0] if self.turners else None
                        if not self.turners:
                            self.turning = "none"
                elif spun is not None and spun != shape and self.turning is None:
                    self.turning = "found"
                pushed_now, spun_now = None, None
                if shape != plan["shape"] and self.keys["rotate"] and turned < TURNS_MOST:
                    turned += 1
                    spun_now = shape
                    sent += await self._press(self.keys["rotate"])
                elif shape != plan["shape"]:
                    # the game does not turn it further (a wall, no such key): the shape it has is placed
                    kept = best(self.stack, piece, rows, cols, only=shape)
                    if kept is None:
                        return "stopped_falling"
                    plan = {**plan, **kept}
                elif column != plan["column"] and shifted < SHIFTS_MOST:
                    # (a piece that did not move since it was last asked sideways is against something)
                    shifted = shifted + 1 if pushed == column else 0
                    pushed_now = column
                    sent += await self._press(self.keys["left" if column > plan["column"] else "right"],
                                              abs(column - plan["column"]))
                elif self.keys["drop"]:
                    sent += await self._press(self.keys["drop"])
                else:
                    lies = fall(self.stack, piece, rows, cols)
                    below = (min(r for r, _ in lies) - min(r for r, _ in piece)) if lies else 0
                    if self.dropping is None and self.drops and below >= DROP_ROWS and tried is None:
                        # no key was named for dropping a piece at once: one such games use is tried on this piece
                        key, self.drops = self.drops[0], self.drops[1:]
                        self.dropping, tried = "tried", (now, piece, self.ticks, key)
                        sent += await self._press(key)
                    else:
                        # one more than the rows left: where a key that cannot move the piece puts it down
                        sent += await self._press(self.keys["down"] or self.keys["drop"], min(below + 1, DOWN_BATCH))
                pushed, spun = pushed_now, spun_now
                last = piece
                continue
            # the stack is not what it was: the piece came to lie (and lines may have gone)
            lain = None
            for effect, cells in (("confirmed", plan["placed"] if plan else None),
                                  ("inconsistent", fall(self.stack, last, rows, cols) if last else None)):
                if cells is None:
                    continue
                after, lines = settle(self.stack, cells, rows, cols)
                rest = filled - after
                if after <= filled and (not rest or piece_in(after, rest, rows, cols) is not None):
                    lain = (effect, cells, after, lines)
                    break
            if lain is None:
                odd_since = odd_since or now
                if now - odd_since < SETTLE_S:
                    continue
                # neither: the stack is taken from the screen as it stands, but for what hangs at its top
                self.stack = self._standing(filled)
                self.resyncs += 1
                streak += 1
                if plan is not None or last is not None:
                    self._placed("unknown", plan, 0, sent, now - began)
                plan = last = odd_since = tried = None
                if streak >= RESYNCS_MOST:
                    return "well_unread"
                continue
            effect, _, after, lines = lain
            if tried is not None and self.dropping == "tried" and effect == "confirmed" \
                    and self.ticks - tried[2] <= DROP_LOOKS:
                # the piece lay where it was planned right after the key: the game drops with it
                self.dropping, self.keys["drop"] = "found", tried[3]
            elif tried is not None and self.dropping == "tried":
                self.dropping = None if self.drops else "none"
            tried = None
            self.stack, self.lines, odd_since, streak = after, self.lines + lines, None, 0
            self._placed(effect, plan, lines, sent, now - began)
            plan = last = None

    def _standing(self, filled: Cells) -> Cells:
        """The stack as the screen shows it, when the code's own is lost: all that is filled but the piece hanging
        at its top, and in a game seen to draw a piece's shadow, the nearest copy of that piece below it."""
        piece = top_piece(filled)
        if not whole(piece) or piece == filled:
            return filled
        stack = filled - piece
        if self.shadow:
            for down in range(1, self.well.rows):
                copy = Cells((r + down, c) for r, c in piece)
                if copy <= stack:
                    return stack - copy
        return stack

    def _placed(self, effect: str, plan: dict[str, Any] | None, lines: int, sent: int, seconds: float) -> None:
        self.pieces += 1
        self.effects[effect] += 1
        self.piece_s.append(seconds)
        if self.placed is not None:
            self.placed({"kind": "place", "effect": effect, "lines": lines, "keys": sent, "seconds": round(seconds, 2),
                         "action": {"kind": "place", "column": plan["column"], "shape": sorted(map(list, plan["shape"]))}
                         if plan else {"kind": "place"}})

    async def _press(self, key: str, times: int = 1) -> int:
        made = gesture(key)
        if made is not None:
            times = min(times, GESTURES_MOST)
        for _ in range(times):
            if made is not None:
                await self.send(dict(made))
            elif self.hold:
                await self.send({"kind": "key", "key": key, "hold_ms": self.hold})
                await asyncio.sleep(self.hold / 1000)
            else:
                await self.send({"kind": "key", "key": key})
        self.sent_at = time.monotonic()
        return times

    def summary(self) -> dict[str, Any]:
        def mean_ms(values: list[float]) -> float | None:
            return round(statistics.fmean(values) * 1000, 1) if values else None
        lasted = time.monotonic() - self.started if self.started else 0.0
        return {"well": self.well.to_json(), "keys": self.keys, "start": self.setup["start"],
                "hold_ms": self.hold, "shadow": self.shadow, "dropping": self.dropping, "turning": self.turning,
                "lines": self.lines, "pieces": self.pieces, "effects": dict(self.effects), "resyncs": self.resyncs,
                "ticks": self.ticks, "ticks_per_s": round(self.ticks / lasted, 1) if lasted else None,
                "capture_ms": mean_ms(self.capture_s), "read_ms": mean_ms(self.read_s),
                "piece_s": round(statistics.fmean(self.piece_s), 2) if self.piece_s else None,
                "stack_height": self.well.rows - min((r for r, _ in self.stack), default=self.well.rows)}


async def tap_or_key(surface: Any, sent: dict[str, Any]) -> None:
    if sent["kind"] == "key" and sent.get("hold_ms"):
        await surface.key(sent["key"], sent["hold_ms"])
    elif sent["kind"] == "key":
        await surface.key(sent["key"])
    elif sent["kind"] == "drag":
        await surface.drag(sent["x0"], sent["y0"], sent["x1"], sent["y1"], sent["ms"])
    else:
        await surface.tap(sent["x"], sent["y"], TAP_MS)
