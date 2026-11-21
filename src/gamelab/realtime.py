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
              holes it leaves, the height, the unevenness, the lines it clears), with the next piece where the game
              shows it and the level's goal where the model read one: the falling family's solver
              (`solvers/falling.py`, over the rules of `rules/falling.py`), given to the loop as a module
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
import collections
import json
import re
import statistics
import time
from typing import Any, Awaitable, Callable

from . import pixels, resources
from .execution import TAP_MS
from .model import Model, Msg, TurnRequest
from .rules import falling as falling_rules
# the rules and the solver the loop plays by (PLAN §34, F3): what a well becomes when a piece comes to lie, and where
# a piece goes. They are the falling family's, given to the loop as modules (`Loop(rules=, solver=)`); the names are
# kept here for what reads them of this module
from .rules.falling import Cells, drops, fall, normal, settle, turns  # noqa: F401
from .solvers import falling as falling_solver
from .solvers.falling import best, weigh  # noqa: F401
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
# (2026-11-21, PLAN §34 F2) the stack is taken from the screen sooner than that once two looks in a row read the
# same below the piece; and a stack other than expected does not end the game while a piece can still be told from
# it: the well is let go only after RESYNCS_MOST such readings with no piece told between, or this many with no
# piece seen to move
BARREN_MOST = 3 * RESYNCS_MOST
# a piece with fewer cells than the game's pieces have had, at the well's top row, is still coming in from above:
# it is planned for once it is whole, or has stood so for this long
COMING_S = 1.5
# what a gesture did is judged once the piece shows it (and then stands the same two looks in a row), or after this
# long with nothing shown: a game on a phone showed a tap half a second late, and one sent again meanwhile turned
# the piece twice. The wait follows how late the game was seen to show its inputs (twice that, within these)
WAIT_S, WAIT_LEAST, WAIT_MOST, LAGS_KEPT = 0.8, 0.4, 1.2, 8
# how a sideways swipe is taken, learned from what the first ones did: one cell a swipe whatever its length
# (STEP), or as many cells as the finger went (DRAG, so many pixels a cell). One that moved nothing (or was taken
# for a tap) is made this much longer and slower, up to so many cells and milliseconds; a way once found is given
# up after so many swipes in a row that moved nothing
STEP, DRAG = "step", "drag"
REACH_GROW, REACH_CELLS_MOST, REACH_MS_MOST, MISSES_MOST = 1.5, 3.0, 500, 3
# a swipe ends this far inside the screen's edge
EDGE = 12
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
# a piece is dropped once: one seen lying where it was dropped to is waited for (the game has taken it, and what is
# sent now goes to the piece coming next, which may not be on the screen yet). One still in the air this long after
# was not dropped by what was sent, and is dropped again
DROP_AGAIN_S = 0.8
# a look is taken no sooner than this after a key, so that it shows what the key did
SHOWN_S = 0.04
# a well whose top quarter is more than half filled, this many looks in a row, shows no game: something lies over
# it (a notice, a result), or the game is over
COVERED_LOOKS = 2
# a piece that stands as it stood for this long, whatever was sent to it, is not being played: the game waits for
# something else (a notice the well shows through, a tutorial that takes one input only, a pause)
STILL_S = 3.0
# (PLAN §34, F5) a game whose pieces fall slowly stands a piece still that long between two rows: the time it was
# seen to take for a row (the middle of the last so many) counts this many times over before a piece is still
STILL_ROWS, FALLS_KEPT = 2, 8
# (F5) a piece planned to clear rows is shown clearing them (a white band, a glow over the row above, words over the
# well): the well is taken as other than planned only after this long at most (`_clear_wait_s`)
CLEAR_WAIT_S = 2.0
# what is kept for looking at afterwards: the last captures of the well and what was made of each look, written
# out where the well was read otherwise than the code expected (at most so often a game)
FRAMES_KEPT, TICKS_KEPT, DUMPS_MOST = 12, 240, 5
# what the loop lets the game go for is set aside whatever was set aside before (F5: a let-go was left unseen)
LET_GO = ("piece_still", "covered", "idle")
# (PLAN §34, F3) the box where a game shows the next piece is read at most this many pixels a side, a pixel lit when
# a channel lies this far from the box's edge; fewer lit than so many is no piece. A split into cells is square
# within this share. A preview read wrong this often, and more often than right, is not read any more
PREVIEW_PX, PREVIEW_FAR, PREVIEW_LIT, PREVIEW_SQUARE, PREVIEW_MISSES = 96, 40, 6, 0.25, 3
# colours are taken as one for the preview's background when each channel falls in the same step of this many
PREVIEW_SHADE = 24
# the cells of a piece in the games the loop's rules are of
PIECE_CELLS = 4
# what a model says of the next piece's box when the screen does not show the game going yet
NEXT_LATER = "after_start"
# a level's goal is believed up to this many rows or pieces
GOAL_MOST = 999
# what makes rows vanish in the games the loop's rules are of
ROWS_CLEAR = "rows"
RT = "RT"

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


def piece_in(stack: Cells, extra: Cells, rows: int, cols: int, known: list[Cells] | None = None,
             unlike: Callable[[Cells, Cells], bool] | None = None) -> Cells | None:
    """The falling piece among the cells filled beyond the stack: those cells themselves when they are one piece,
    or the piece without the shadow a game draws of it where it would come to lie (the same shape straight below
    it, resting on the stack). A piece part of which is still above the well is given whole when its shadow shows
    its shape (rows above the well are negative). `known`: the shapes the piece now falling turns to. `unlike`: a
    shadow is drawn otherwise than its piece, so cells the same as it are no shadow (a piece of the same shape that
    came to lie straight below, F5). None when the cells are neither."""
    if not extra:
        return None
    if known and normal(extra) in known:
        return extra
    top = top_piece(extra)
    rest = extra - top
    if rest:
        if not whole(rest) or fall(stack, rest, rows, cols) != rest or unlike is not None and not unlike(rest, top):
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


def within(image: Any, box: list[int], region: list[int] | None = None) -> Any:
    """The part of a capture that shows `box` (screen pixels): the capture itself when it is of the box alone, the
    box within it when it is of `region`, else the box within the whole screen."""
    if image.size == (box[2] - box[0], box[3] - box[1]):
        return image
    if region is not None and image.size == (region[2] - region[0], region[3] - region[1]):
        return image.crop((box[0] - region[0], box[1] - region[1], box[2] - region[0], box[3] - region[1]))
    return image.crop(tuple(box))


class Well:
    """Where the well is on the screen and what its empty cells look like: a capture of it read as the set of
    its filled cells."""

    def __init__(self, box: list[int], rows: int, cols: int) -> None:
        self.box, self.rows, self.cols = box, rows, cols
        self.empty: tuple[float, float, float] | None = None
        self.near = FILLED_LEAST
        # each cell's colour in the capture read last
        self.colours: list[tuple[float, float, float]] = []

    def _colours(self, png: bytes, region: list[int] | None = None) -> list[tuple[float, float, float]]:
        """Each cell's colour, row by row, from a capture of the well alone, of `region`, or of the whole screen."""
        from PIL import Image

        image = within(pixels.decode(png, None), self.box, region)
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

    def unlike(self, png: bytes | None = None) -> Callable[[Cells, Cells], bool]:
        """Whether two sets of cells of this capture (none: the one read last) are drawn in colours farther apart
        than empty cells are."""
        colours = self._colours(png) if png is not None else self.colours

        def mean(cells: Cells) -> tuple[float, ...]:
            return tuple(statistics.fmean(colours[r * self.cols + c][band] for r, c in cells) for band in range(3))

        def apart(one: Cells, other: Cells) -> bool:
            a, b = mean(one), mean(other)
            return max(abs(a[band] - b[band]) for band in range(3)) > self.near
        return apart

    def read(self, png: bytes, region: list[int] | None = None) -> Cells:
        colours = self.colours = self._colours(png, region)
        return Cells((index // self.cols, index % self.cols) for index, colour in enumerate(colours)
                     if self._far(colour) > self.near)

    def to_json(self) -> dict[str, Any]:
        return {"box": self.box, "rows": self.rows, "cols": self.cols,
                "empty": [round(band) for band in self.empty] if self.empty else None, "near": round(self.near, 1)}


class Preview:
    """Where the game shows the piece that comes next: a box around it alone. Its cells are told from the box's
    background (the colour most of its edge has) without knowing their size: the piece's extent, split into as many columns
    and rows as make square cells that each are filled or empty, is the piece (a split into more cells than a piece
    has is none)."""

    def __init__(self, box: list[int]) -> None:
        self.box = box

    def read(self, png: bytes, region: list[int] | None = None, size: int | None = None) -> Cells | None:
        """The next piece's shape, or None when the box shows none that can be told. `size`: the cells the game's
        pieces have had (four, the rules' own, until one came to lie), preferred over any other split."""
        from PIL import Image

        image = within(pixels.decode(png, None), self.box, region)
        scale = min(1.0, PREVIEW_PX / max(image.size))
        if scale < 1.0:
            image = image.resize((max(1, round(image.size[0] * scale)), max(1, round(image.size[1] * scale))), Image.BOX)
        wide, high = image.size
        raw = image.tobytes()
        colour = [tuple(raw[3 * i:3 * i + 3]) for i in range(wide * high)]
        # the box's background is the colour its edge mostly has (F5: not the middle of its edge's colours, which a
        # box said a little wide takes partly from the frame drawn round the preview, and then the background
        # itself is read as lit)
        edge = [colour[i] for i in range(wide * high) if i < wide or i >= wide * (high - 1) or i % wide in (0, wide - 1)]
        common = collections.Counter(tuple(band // PREVIEW_SHADE for band in c) for c in edge).most_common(1)[0][0]
        alike = [c for c in edge if tuple(band // PREVIEW_SHADE for band in c) == common]
        ground = tuple(statistics.median(c[band] for c in alike) for band in range(3))
        lit = [max(abs(c[band] - ground[band]) for band in range(3)) > PREVIEW_FAR for c in colour]
        # what is lit and touches the box's edge is the frame round it or words beside it, not the piece (F5); a box
        # said tight about the piece has the piece itself at its edge, and is read with all that is lit
        inner = list(lit)
        edge = [i for i in range(wide * high) if inner[i] and (i < wide or i >= wide * (high - 1) or i % wide in (0, wide - 1))]
        while edge:
            i = edge.pop()
            if not inner[i]:
                continue
            inner[i] = False
            x, y = i % wide, i // wide
            edge.extend(j for j, ok in ((i - 1, x > 0), (i + 1, x < wide - 1), (i - wide, y > 0), (i + wide, y < high - 1))
                        if ok and inner[j])
        for shown in ((inner, lit) if inner != lit else (lit,)):
            found = self._split(shown, wide, size)
            if found is not None:
                return found
        return None

    @staticmethod
    def _split(lit: list[bool], wide: int, size: int | None) -> Cells | None:
        """The piece the lit pixels make: their extent split into square cells each filled or empty."""
        xs = [i % wide for i, on in enumerate(lit) if on]
        ys = [i // wide for i, on in enumerate(lit) if on]
        if len(xs) < PREVIEW_LIT:
            return None
        x0, x1, y0, y1 = min(xs), max(xs) + 1, min(ys), max(ys) + 1
        found = None
        for across in range(1, PIECE_MOST + 1):
            for down in range(1, PIECE_MOST + 1):
                cell_x, cell_y = (x1 - x0) / across, (y1 - y0) / down
                if abs(cell_x - cell_y) > PREVIEW_SQUARE * max(cell_x, cell_y) or cell_x < 2:
                    continue
                cells, unsure = set(), 0
                for row in range(down):
                    for col in range(across):
                        # the middle half of the cell
                        left, top = x0 + (col + 0.25) * cell_x, y0 + (row + 0.25) * cell_y
                        taken = [lit[y * wide + x] for y in range(int(top), max(int(top) + 1, int(top + cell_y / 2)))
                                 for x in range(int(left), max(int(left) + 1, int(left + cell_x / 2)))]
                        share = sum(taken) / len(taken)
                        unsure += 0.25 < share < 0.75
                        if share >= 0.5:
                            cells.add((row, col))
                if not whole(Cells(cells)) or {r for r, _ in cells} != set(range(down)) \
                        or {c for _, c in cells} != set(range(across)):
                    continue
                key = (len(cells) == (size or PIECE_CELLS), len(cells), -unsure,
                       -abs(cell_x - cell_y) / max(cell_x, cell_y))
                if found is None or key > found[0]:
                    found = (key, Cells(cells))
        return found[1] if found else None


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
    # what vanishes in this game: full rows (the rules the loop plays by), or what the model said instead
    clears = answer.get("clears")
    clears = ROWS_CLEAR if not isinstance(clears, str) or not clears.strip() or clears.strip().lower() == ROWS_CLEAR \
        else clears.strip()[:300]
    more = answer.get("also") if isinstance(answer.get("also"), dict) else {}
    also = {name: [control for control in dict.fromkeys(_control(value, screen, kinds) for value in
                                                        (more.get(name) if isinstance(more.get(name), list) else []))
                   if control][:TRIED_MOST]
            for name in ("rotate", "drop")}
    if KEYS in kinds:
        # (a game played by touch alone has no keys of its own to try: only what the model gave)
        also = {"rotate": also["rotate"] or list(TURN_TRIED), "drop": also["drop"] or list(DROP_TRIED)}
    return {"box": [left, top, right, bottom], "rows": rows, "cols": cols, "keys": keys, "start": start,
            "situation": situation, "also": also, "clears": clears,
            "next": _next_box(answer.get("next"), screen, [left, top, right, bottom], cols),
            # the screen does not show the game going yet (a menu over the well): where its next piece shows is to be
            # asked once it goes
            **({"next_later": True} if answer.get("next") == NEXT_LATER else {}),
            "goal": _goal(answer.get("goal"))}


def _next_box(value: Any, screen: list[int], well: list[int], cols: int) -> list[int] | None:
    """The box the game shows its next piece in, as the model gave it: inside the screen, outside the well, big
    enough for a piece's cells to be told (a cell no smaller than a quarter of the well's); else None."""
    if isinstance(value, dict):
        value = value.get("box")
    if not (isinstance(value, list) and len(value) == 4 and all(type(part) in (int, float) for part in value)):
        return None
    left, top, right, bottom = (round(part) for part in value)
    least = max(2 * CELL_LEAST, (well[2] - well[0]) / cols / 2)
    inside = left < well[2] and well[0] < right and top < well[3] and well[1] < bottom
    if not (0 <= left < right <= screen[0] and 0 <= top < bottom <= screen[1]) or inside \
            or right - left < least or bottom - top < least:
        return None
    return [left, top, right, bottom]


def same_well(box: list[int], other: list[int], cols: int) -> bool:
    """Whether two answers put the well in one place: each edge within half a cell."""
    return all(abs(a - b) <= (other[2] - other[0]) / cols / 2 for a, b in zip(box, other))


def _goal(value: Any) -> dict[str, int | None] | None:
    """A level's goal as the model read it: rows to clear and the pieces given for it, each a count or null; None
    when it said neither."""
    if not isinstance(value, dict):
        return None
    got = {name: value.get(name) if type(value.get(name)) is int and 1 <= value.get(name) <= GOAL_MOST else None
           for name in ("lines", "pieces")}
    return got if got["lines"] is not None else None


def record(setup: dict[str, Any], well: "Well") -> dict[str, Any]:
    """What is kept of a game once pieces came to lie where the code put them: the model's answer as the loop
    used it and the look of the well's empty cells on the screen it was given for. A level's goal is not kept: the
    next game kept words are played in may be another level."""
    return {"setup": {name: value for name, value in setup.items() if name not in ("goal", "goal_at")},
            "empty": list(well.empty) if well.empty else None, "near": well.near}


def learned(loop: "Loop") -> dict[str, Any]:
    """What the loop found out of the game by playing it, kept with the model's words."""
    side = loop.side
    return {"hold_ms": loop.hold, "shadow": loop.shadow, "turning": loop.turning,
            "dropping": loop.dropping if loop.dropping in ("found", "none") else None,
            "reach": {"mode": side["mode"], "px": round(side["px"], 1), "ms": round(side["ms"])}
            if side is not None and side["mode"] else None}


def _level(token: Any) -> tuple[int, int, int, int] | None:
    """A level swipe as where it starts, how far it goes (to the right when more than 0) and how long it takes;
    None for any other control."""
    found = SWIPE_TOKEN.fullmatch(token) if isinstance(token, str) else None
    if found is None:
        return None
    x0, y0, x1, y1, ms = map(int, found.groups())
    return (x0, y0, x1 - x0, ms) if abs(x1 - x0) > abs(y1 - y0) else None


def _reach(kept: Any) -> dict[str, Any] | None:
    """What was learned of a game's sideways swipes, as it was kept, or None when it is not that."""
    if not (isinstance(kept, dict) and kept.get("mode") in (STEP, DRAG) and type(kept.get("px")) in (int, float)
            and type(kept.get("ms")) in (int, float) and kept["px"] >= 1 and SWIPE_MS[0] <= kept["ms"] <= SWIPE_MS[1]):
        return None
    return {"mode": kept["mode"], "px": float(kept["px"]), "ms": round(kept["ms"])}


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
                       "also": setup.get("also"), "clears": setup.get("clears"), "next": setup.get("next")},
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
    said["reach"] = _reach(setup.get("reach"))
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
                 placed: Callable[[dict[str, Any]], None] | None = None, rules: Any = falling_rules,
                 solver: Any = falling_solver, policy: str = "lookahead", counted: tuple[int, int] = (0, 0)) -> None:
        self.surface, self.setup, self.send, self.check, self.seen, self.placed = surface, setup, send, check, seen, placed
        # what a well becomes when a piece comes to lie (`fall`, `settle`, `drops`, `turns`) and where a piece goes
        # (`place`, by `policy`): the family's modules, or any that offer the same
        self.rules, self.solver, self.policy = rules, solver, policy
        self.well = Well(setup["box"], setup["rows"], setup["cols"])
        # where the game shows the next piece, read when a piece is planned for and held against the piece that
        # then comes (`Preview`); and the level's goal as the model read it, with the rows and pieces counted
        # towards it before this loop
        self.preview = Preview(setup["next"]) if setup.get("next") else None
        self.coming: Cells | None = None
        self.foreseen = {"read": 0, "matched": 0, "missed": 0, "unread": 0}
        self.goal, self.counted = setup.get("goal"), counted
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
        # the sideways swipes of a game played by touch, as they are sent: where each starts, how long one is (the
        # model's first; in a game that takes a swipe by its length, the pixels a cell), how long it takes, and
        # which of the two ways the game takes them (None until seen)
        self.side: dict[str, Any] | None = None
        left, right = _level(self.keys["left"]), _level(self.keys["right"])
        if left and right and left[2] < 0 < right[2]:
            kept = _reach(setup.get("reach")) or {"mode": None, "px": float(right[2]), "ms": right[3]}
            self.side = {**kept, "from": {"left": left[:2], "right": right[:2]}, "misses": 0}
        self.screen: list[int] | None = None
        self.wait, self.lags = WAIT_S, collections.deque(maxlen=LAGS_KEPT)
        # how many cells the pieces that came to lie had
        self.sizes: collections.Counter = collections.Counter()
        # how long a piece was seen to take to fall a row, the last few
        self.falls: collections.deque = collections.deque(maxlen=FALLS_KEPT)
        self.sent_at = 0.0
        self.stack: Cells = Cells()
        self.lines = self.pieces = self.resyncs = self.ticks = 0
        # how the pieces came to lie: as planned, straight down from where they were last seen, or unknown
        self.effects = {"confirmed": 0, "inconsistent": 0, "elsewhere": 0, "unknown": 0}
        self.capture_s: list[float] = []
        self.read_s: list[float] = []
        self.piece_s: list[float] = []
        self.started = 0.0
        # kept for looking at afterwards (`_dump`): the last captures, a line a look, and what was written out
        self.frames: collections.deque = collections.deque(maxlen=FRAMES_KEPT)
        self.trace: collections.deque = collections.deque(maxlen=TICKS_KEPT)
        self.dumps: list[dict[str, Any]] = []

    async def look(self) -> Cells:
        self.check()
        early = SHOWN_S - (time.monotonic() - self.sent_at)
        if early > 0:
            await asyncio.sleep(early)
        at = time.monotonic()
        png = await self.surface.capture(self.region)
        took = time.monotonic()
        if self.seen is not None:
            await self.seen(png)
        filled = self.well.read(png, self.region)
        self.png = png
        self.capture_s.append(took - at)
        self.read_s.append(time.monotonic() - took)
        self.ticks += 1
        self.frames.append((self.ticks, png))
        self.trace.append({"tick": self.ticks, "t": round(took - self.started, 3), "filled": _listed(filled),
                           "stack": len(self.stack)})
        return filled

    def _note(self, **what: Any) -> None:
        """Said of the look just taken: what was made of it."""
        if self.trace:
            self.trace[-1].update(what)

    def _dump(self, why: str) -> None:
        """The last captures and looks are set aside as they are, with why."""
        if len(self.dumps) < DUMPS_MOST or why in LET_GO:
            self.dumps.append({"why": why, "tick": self.ticks, "frames": list(self.frames),
                               "looks": [dict(row) for row in self.trace][-4 * FRAMES_KEPT:]})

    async def begin(self, first: bytes, start: bool = True) -> None:
        """The empty cell's look learned from the first screen, and the game started when it has not. `start`
        false: the game is going already, whatever was said of starting it (it is taken up again)."""
        self.well.learn(first)
        self.stack = Cells()
        if self.side is not None:
            self.screen = list(await self.surface.size())
        if start and self.setup["start"] is not None:
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
        "no_piece" when none ever did), the well cannot be read ("well_unread"), or it shows no game any more
        ("covered"), or its piece neither falls nor takes any input ("piece_still"). A budget ends it by `check`."""
        rows, cols = self.well.rows, self.well.cols
        plan: dict[str, Any] | None = None
        last: Cells | None = None
        began = idle_since = time.monotonic()
        odd_since: float | None = None
        # the stack as the look before read it, while it is not what was expected
        odd_last: Cells | None = None
        turned = shifted = sent = streak = barren = covered = 0
        # a gesture whose effect is waited for: when it was sent, how the piece stood then, and for a sideways
        # one what was sent; and whether the piece now planned for was seen to fall
        waiting: dict[str, Any] | None = None
        moved = False
        # what the last key was to change, as it stood then: a key after which it stands the same did nothing
        pushed: int | None = None
        spun: Cells | None = None
        # a key tried for dropping a piece at once: when, and where the piece stood then
        tried: tuple[float, Cells, int, str] | None = None
        # the piece as it last stood otherwise than now, and since when it stands so
        still: tuple[Cells | None, float] = (None, began)
        # when the piece now planned for was dropped at once
        dropped: float | None = None
        # (F5) when the piece now planned for was last seen a row lower; since when what hangs over the stack has
        # more cells than the game's pieces (or a planned row being cleared shows otherwise); the look before, while
        # a stack other than planned is waited on to read the same twice
        fell_at: float | None = None
        doubt_since: float | None = None
        clear_since: float | None = None
        twice: Cells | None = None
        while True:
            if done(self):
                return "done"
            filled = await self.look()
            now = time.monotonic()
            covered = covered + 1 if self._covered(filled) else 0
            if covered >= COVERED_LOOKS:
                self._note(event="covered")
                self._dump("covered")
                return "covered"
            extra, missing = filled - self.stack, self.stack - filled
            if not missing and not extra:
                odd_since = odd_last = None
                if now - idle_since > (IDLE_S if self.pieces else FIRST_S):
                    self._dump("idle")
                    return "stopped_falling" if self.pieces else "no_piece"
                continue
            idle_since = now
            piece = None if missing else piece_in(self.stack, extra, rows, cols, plan["turns"] if plan else None,
                                                  self.well.unlike())
            usual = self.sizes.most_common(1)
            if piece is not None and usual and len(piece) > usual[0][0]:
                # more cells than the game's pieces have had (F5: a placed piece with a row-clear's words and a drop's
                # trail drawn on it read as one piece of five, planned for, and the drop sent for it took the next
                # piece unplanned): looked at again until it passes, or has stood so long
                doubt_since = doubt_since or now
                if now - doubt_since < COMING_S:
                    self._note(event="odd_piece")
                    continue
            else:
                doubt_since = None
            if piece is not None:
                # a piece on its way over the stack as the code knows it (with its shadow, where a game draws one)
                odd_since = odd_last = None
                if piece != still[0]:
                    still = (piece, now)
                elif now - still[1] > self._still_s() and sent and piece != self.rules.fall(self.stack, piece, rows, cols):
                    self._note(event="piece_still")
                    self._dump("piece_still")
                    return "piece_still"
                self.shadow = self.shadow or piece != extra
                shape = normal(piece)
                if plan is None or shape not in plan["turns"]:
                    if usual and len(piece) < usual[0][0] and piece == extra and min(r for r, _ in piece) == 0 \
                            and now - still[1] < COMING_S:
                        # part of it is still above the well: what is sent now would go to a piece not yet seen
                        self._note(event="coming")
                        continue
                    self._foresaw(piece)
                    found = self._place(piece)
                    if found is None:
                        return "stopped_falling"
                    plan = {**found, "turns": self.rules.turns(piece)}
                    self._note(event="plan", piece=_listed(piece), column=found["column"],
                               shape=_listed(found["shape"]),
                               **({"next": _listed(self.coming)} if self.coming is not None else {}))
                    began, turned, shifted, sent, pushed, spun = now, 0, 0, 0, None, None
                    dropped = waiting = fell_at = clear_since = twice = None
                    streak, moved = 0, False
                elif last is not None and normal(last) == shape and min(c for _, c in last) == min(c for _, c in piece) \
                        and min(r for r, _ in last) < min(r for r, _ in piece):
                    # it went down as it stood: something does fall here, so long a row
                    moved = True
                    if fell_at is not None:
                        self.falls.append((now - fell_at) / (min(r for r, _ in piece) - min(r for r, _ in last)))
                    fell_at = now
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
                if waiting is not None:
                    if (shape, column) == waiting["pose"]:
                        if now - waiting["at"] < self.wait:
                            last = piece
                            continue
                    elif (shape, column) != waiting.get("seen"):
                        # the gesture shows: looked at once more, in case more of it is still to show
                        waiting["seen"], waiting["shown"] = (shape, column), now
                        last = piece
                        continue
                    else:
                        self._late(waiting["shown"] - waiting["at"])
                    if waiting["shift"] is not None:
                        self._reached(waiting["shift"], (column - waiting["pose"][1]) * waiting["shift"]["way"],
                                      shape != waiting["pose"][0])
                    waiting = None
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
                    if gesture(self.keys["rotate"]) is not None:
                        waiting = {"at": time.monotonic(), "pose": (shape, column), "shift": None}
                elif shape != plan["shape"]:
                    # the game does not turn it further (a wall, no such key): the shape it has is placed
                    kept = self._place(piece, only=shape)
                    if kept is None:
                        return "stopped_falling"
                    plan = {**plan, **kept}
                elif column != plan["column"] and shifted < SHIFTS_MOST:
                    # (a piece that did not move since it was last asked sideways is against something)
                    shifted = shifted + 1 if pushed == column else 0
                    pushed_now = column
                    count, swiped = await self._shift("left" if column > plan["column"] else "right",
                                                      abs(column - plan["column"]))
                    sent += count
                    if swiped is not None:
                        waiting = {"at": time.monotonic(), "pose": (shape, column), "shift": swiped}
                elif self.keys["drop"]:
                    # (a gesture is shown later than a key: it is given longer before it is sent again)
                    again = DROP_AGAIN_S if gesture(self.keys["drop"]) is None else max(DROP_AGAIN_S, 2 * self.wait)
                    if dropped is None or piece != self.rules.fall(self.stack, piece, rows, cols) and now - dropped > again:
                        dropped = now
                        sent += await self._press(self.keys["drop"])
                else:
                    lies = self.rules.fall(self.stack, piece, rows, cols)
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
            lain, clearing = None, False
            # where it may lie: as planned, straight down from where it was last seen, or dropped anywhere else
            # (the game moved it on while the code was not looking: its lines count all the same)
            for effect, cells in (("confirmed", plan["placed"] if plan else None),
                                  ("inconsistent", self.rules.fall(self.stack, last, rows, cols) if last else None),
                                  *(("elsewhere", cells)
                                    for cells in (self.rules.drops(self.stack, last, rows, cols) if last else []))):
                if cells is None:
                    continue
                after, lines = self.rules.settle(self.stack, cells, rows, cols)
                rest = filled - after
                if after <= filled and (not rest or piece_in(after, rest, rows, cols) is not None):
                    lain = (effect, cells, after, lines)
                    break
                if lines:
                    # it lies there with its full rows still drawn (a game shows them going): waited for
                    full = self.stack | cells
                    rest = filled - full
                    clearing = clearing or full <= filled and (not rest or piece_in(full, rest, rows, cols) is not None)
            if plan is not None and plan["lines"] and (lain is None or lain[0] != "confirmed"):
                # (F5) the piece was planned to clear rows and the well is not yet as planned: what a game draws as
                # rows go (a band, a glow over the row above, words, a dropped piece's trail) is waited out first
                clear_since = clear_since or now
                if now - clear_since < self._clear_wait_s():
                    self._note(event="clearing")
                    continue
            if lain is not None and lain[0] != "confirmed":
                # come to lie otherwise than planned: taken once the well reads the same twice in a row (F5)
                odd_since = odd_since or now
                if filled != twice and now - odd_since < SETTLE_S:
                    twice = filled
                    continue
            if lain is None:
                # neither. The game may be drawing something that passes (rows going, a piece's trail): the stack
                # is taken from the screen, but for what hangs at its top, once two looks in a row read it the same
                standing = self._standing(filled)
                odd_since = odd_since or now
                if now - odd_since < SETTLE_S and (clearing or standing != odd_last):
                    odd_last = None if clearing else standing
                    continue
                before, self.stack = self.stack, standing
                self.resyncs += 1
                streak += 1
                barren += 1
                self._note(event="resync", stack=len(self.stack))
                self._dump("resync")
                # the piece is on its way still when what hangs over the stack now is it, no higher than it was
                hangs = top_piece(filled) if standing != filled else None
                came = (plan is not None or last is not None) and not (
                    last is not None and hangs is not None and normal(hangs) == normal(last)
                    and min(c for _, c in hangs) == min(c for _, c in last)
                    and min(r for r, _ in hangs) >= min(r for r, _ in last))
                # a piece that came to lie adds its cells, and each row gone takes a row's: what went is counted
                gone = len(before) + (len(last or plan["shape"]) if came else 0) - len(self.stack)
                lines = gone // cols if gone > 0 and gone % cols == 0 and gone // cols <= PIECE_MOST else 0
                self.lines += lines
                if came:
                    self._placed("unknown", plan, lines, sent, now - began, last)
                    barren = 0 if moved else barren
                plan = last = odd_since = odd_last = tried = waiting = fell_at = clear_since = twice = None
                if streak >= RESYNCS_MOST or barren >= BARREN_MOST:
                    # no piece could be told from what the well shows, so often in a row: it is not being read
                    return "well_unread"
                continue
            effect, _, after, lines = lain
            self._note(event=effect, lines=lines)
            if effect != "confirmed":
                self._dump(effect)
            if tried is not None and self.dropping == "tried" and effect == "confirmed" \
                    and self.ticks - tried[2] <= DROP_LOOKS:
                # the piece lay where it was planned right after the key: the game drops with it
                self.dropping, self.keys["drop"] = "found", tried[3]
            elif tried is not None and self.dropping == "tried":
                self.dropping = None if self.drops else "none"
            tried = None
            self.stack, self.lines, odd_since, odd_last, streak, barren = after, self.lines + lines, None, None, 0, 0
            self._placed(effect, plan, lines, sent, now - began, last)
            plan = last = waiting = fell_at = clear_since = twice = None

    def foresee(self, box: list[int] | None, goal: dict[str, Any] | None) -> None:
        """Where the game shows the next piece and what the level asks, said of the screen once the game went."""
        self.setup = {**self.setup, "next": box or self.setup.get("next"), "goal": goal}
        self.preview = Preview(self.setup["next"]) if self.setup["next"] else None
        self.goal = goal

    @property
    def region(self) -> list[int]:
        """What is captured each look: the well, and the box of the next piece with it where the game shows one."""
        box = self.well.box
        if self.preview is None:
            return box
        other = self.preview.box
        return [min(box[0], other[0]), min(box[1], other[1]), max(box[2], other[2]), max(box[3], other[3])]

    def _place(self, piece: Cells, only: Cells | None = None) -> dict[str, Any] | None:
        """Where the solver puts the piece on the stack as it stands, with the next piece where it was read and what
        is left of the level's goal."""
        return self.solver.place(self.stack, piece, self.well.rows, self.well.cols, policy=self.policy,
                                 queue=[self.coming] if self.coming is not None else [], goal=self._left(), only=only)

    def _left(self) -> dict[str, Any] | None:
        """The level's goal still to meet: rows to clear and pieces left for them (this one among them)."""
        if not self.goal:
            return None
        lines, pieces = self.counted[0] + self.lines, self.counted[1] + self.pieces
        need, most = self.goal.get("lines"), self.goal.get("pieces")
        return {"lines": None if need is None else max(0, need - lines),
                "pieces": None if most is None else max(1, most - pieces)}

    def _foresaw(self, piece: Cells) -> None:
        """The next piece as the game shows it now, read for the piece about to be planned; and the one read before
        held against this one, which it was to be. A preview read wrong more often than right is not read again."""
        if self.coming is not None:
            hit = normal(piece) in self.rules.turns(self.coming)
            self.foreseen["matched" if hit else "missed"] += 1
            if self.foreseen["missed"] >= PREVIEW_MISSES and self.foreseen["missed"] > self.foreseen["matched"]:
                self.preview = None
        self.coming = None
        if self.preview is None or getattr(self, "png", None) is None:
            return
        try:
            self.coming = self.preview.read(self.png, self.region, self.sizes.most_common(1)[0][0]
                                            if self.sizes else None)
        except Exception:
            self.coming = None
        self.foreseen["read" if self.coming is not None else "unread"] += 1

    def _still_s(self) -> float:
        """How long a piece stands as it stood before it is not being played: longer in a game seen to let its
        pieces fall slowly (F5)."""
        if not self.falls:
            return STILL_S
        return max(STILL_S, STILL_ROWS * statistics.median(self.falls))

    def _clear_wait_s(self) -> float:
        """How long rows planned to go are waited for: no longer than the game was seen to let a piece fall a row
        (the next piece, come meanwhile, is then barely on its way), and at most CLEAR_WAIT_S."""
        return min(CLEAR_WAIT_S, max(self.falls)) if self.falls else CLEAR_WAIT_S

    def _covered(self, filled: Cells) -> bool:
        """Whether the well's top quarter is more than half filled: no game leaves it so and goes on."""
        high = max(1, self.well.rows // 4)
        return sum(r < high for r, _ in filled) * 2 > high * self.well.cols

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

    def _late(self, seconds: float) -> None:
        """A gesture was shown so long after it was sent: the next are waited for accordingly."""
        self.lags.append(seconds)
        self.wait = min(WAIT_MOST, max(WAIT_LEAST, 2 * max(self.lags)))

    async def _shift(self, way: str, cells: int) -> tuple[int, dict[str, Any] | None]:
        """The piece asked `cells` columns to the left or right: how many inputs went out, and for swipes what
        they were (to be held against what the piece then did, `_reached`). Keys and buttons go a cell each. A
        swipe goes a cell each too in a game seen to take it so; else one swipe as long as the cells asked (the
        way to find out how the game takes it, and the way to go on where the piece follows the finger)."""
        side = self.side
        if side is None or self.screen is None:
            return await self._press(self.keys[way], cells), None
        (x, y), sign = side["from"][way], (-1 if way == "left" else 1)
        times = min(cells, GESTURES_MOST) if side["mode"] == STEP else 1
        over = 1 if side["mode"] == STEP else cells
        length = min(side["px"] * over, self.screen[0] - 2 * EDGE)
        ms = min(SWIPE_MS[1], round(side["ms"] * (1 + (over - 1) / 2)))
        # kept inside the screen: begun further back when it would end past the edge
        x = min(max(x, EDGE + (length if sign < 0 else 0)), self.screen[0] - EDGE - (length if sign > 0 else 0))
        count = await self._press(f"swipe:{round(x)},{y},{round(x + sign * length)},{y},{ms}", times)
        return count, {"way": sign, "times": times, "px": length, "cells": cells}

    def _reached(self, swiped: dict[str, Any], moved: int, turned: bool) -> None:
        """What sideways swipes did, held against what was sent: `moved` cells the way asked (less than 1: none,
        or the other way), `turned` when the piece then had another shape (a swipe taken for a tap)."""
        side = self.side
        cell = (self.well.box[2] - self.well.box[0]) / self.well.cols
        if moved <= 0:
            side["misses"] += 1
            if side["mode"] is None or side["misses"] >= MISSES_MOST:
                # too short or too quick for this game: longer and slower, and how it is taken is open again
                side.update(mode=None, misses=0, px=min(side["px"] * REACH_GROW, REACH_CELLS_MOST * cell),
                            ms=min(round(side["ms"] * REACH_GROW), REACH_MS_MOST))
            return
        if turned:
            return
        side["misses"] = 0
        if moved > swiped["times"] or side["mode"] == DRAG:
            # more cells than swipes: the piece goes as far as the finger does, so many pixels a cell
            each = swiped["px"] * swiped["times"] / moved
            if side["mode"] == DRAG:
                each = (side["px"] + min(max(each, side["px"] / REACH_GROW), side["px"] * REACH_GROW)) / 2
            if 0.3 * cell <= each <= REACH_CELLS_MOST * cell:
                side.update(mode=DRAG, px=each)
        elif swiped["times"] == 1 and swiped["cells"] >= 2 and side["mode"] is None:
            # one long swipe, one cell: a cell a swipe whatever its length
            side["mode"] = STEP

    def _placed(self, effect: str, plan: dict[str, Any] | None, lines: int, sent: int, seconds: float,
                piece: Cells | None = None) -> None:
        if piece is not None:
            self.sizes[len(piece)] += 1
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
        if self.trace:
            self.trace[-1].setdefault("sent", []).append(key if times == 1 else f"{key} x{times}")
        return times

    def summary(self) -> dict[str, Any]:
        def mean_ms(values: list[float]) -> float | None:
            return round(statistics.fmean(values) * 1000, 1) if values else None
        lasted = time.monotonic() - self.started if self.started else 0.0
        return {"well": self.well.to_json(), "keys": self.keys, "start": self.setup["start"],
                "hold_ms": self.hold, "shadow": self.shadow, "dropping": self.dropping, "turning": self.turning,
                "reach": {"mode": self.side["mode"], "px": round(self.side["px"], 1), "ms": round(self.side["ms"])}
                if self.side is not None else None,
                "wait_ms": round(self.wait * 1000) if self.lags else None,
                "policy": self.policy, "goal": self.goal,
                "next": {**self.foreseen, "box": self.setup["next"], "given_up": self.preview is None}
                if self.setup.get("next") else None,
                "lines": self.lines, "pieces": self.pieces, "effects": dict(self.effects), "resyncs": self.resyncs,
                "ticks": self.ticks, "ticks_per_s": round(self.ticks / lasted, 1) if lasted else None,
                "capture_ms": mean_ms(self.capture_s), "read_ms": mean_ms(self.read_s),
                "piece_s": round(statistics.fmean(self.piece_s), 2) if self.piece_s else None,
                "stack_height": self.well.rows - min((r for r, _ in self.stack), default=self.well.rows)}


def _listed(cells: Any) -> list[list[int]]:
    return sorted([r, c] for r, c in cells)


async def tap_or_key(surface: Any, sent: dict[str, Any]) -> None:
    if sent["kind"] == "key" and sent.get("hold_ms"):
        await surface.key(sent["key"], sent["hold_ms"])
    elif sent["kind"] == "key":
        await surface.key(sent["key"])
    elif sent["kind"] == "drag":
        await surface.drag(sent["x0"], sent["y0"], sent["x1"], sent["y1"], sent["ms"])
    else:
        await surface.tap(sent["x"], sent["y"], TAP_MS)
