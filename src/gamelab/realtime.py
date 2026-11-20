"""A game in which time moves: pieces fall into a well on their own, and the loop that plays it (PLAN §29).

Every other way the harness plays is "look, one move, look again": the game waits. Here it does not, so the loop
is another one, and no model is in it:

    setup     a model is shown the first screen once (`watch_falling.md`) and says where the well is, how many
              cells it has, which keys turn, move and drop a piece, and what starts the game
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
from .surface import KEYS, TAP

SETUP_TOKENS, SETUP_TIMEOUT_S = 1500, 90.0
# a well has this many cells a side at least and at most, and a cell is this many pixels at least
CELLS_FEWEST, CELLS_MOST, CELL_LEAST = 4, 40, 6
# a key as a model may name it: one of a keyboard's names, nothing a page could be told to run
KEY = re.compile(r"[A-Za-z0-9]{1,12}")
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
        self.empty = tuple(statistics.median(colour[band] for colour in colours) for band in range(3))
        apart = sorted(self._far(colour) for colour in colours)
        self.near = max(FILLED_LEAST, CLEAR * apart[int(0.8 * (len(apart) - 1))])

    def _far(self, colour: tuple[int, int, int]) -> float:
        return max(abs(colour[band] - self.empty[band]) for band in range(3))

    def read(self, png: bytes) -> Cells:
        colours = self._colours(png)
        return Cells((index // self.cols, index % self.cols) for index, colour in enumerate(colours)
                     if self._far(colour) > self.near)

    def to_json(self) -> dict[str, Any]:
        return {"box": self.box, "rows": self.rows, "cols": self.cols,
                "empty": [round(band) for band in self.empty] if self.empty else None, "near": round(self.near, 1)}


def _key(value: Any) -> str | None:
    return value if isinstance(value, str) and KEY.fullmatch(value) else None


def understood(answer: Any, screen: list[int], kinds: frozenset[str]) -> dict[str, Any] | None:
    """A model's answer about the first screen as the loop can use it, or None when it cannot: the well inside
    the screen with cells of a size to read, the keys that move a piece sideways and at least one that takes it
    down, and an input that starts the game of a kind the surface sends."""
    if not isinstance(answer, dict) or KEYS not in kinds:
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
    keys = {name: _key(said.get(name)) for name in ("left", "right", "rotate", "down", "drop")}
    if not (keys["left"] and keys["right"] and (keys["down"] or keys["drop"])):
        return None
    start = answer.get("start") if isinstance(answer.get("start"), dict) else None
    if start is not None:
        if start.get("kind") == "key" and _key(start.get("key")):
            start = {"kind": "key", "key": start["key"]}
        elif start.get("kind") == "tap" and TAP in kinds and all(type(start.get(axis)) in (int, float) for axis in "xy") \
                and 0 <= start["x"] < screen[0] and 0 <= start["y"] < screen[1]:
            start = {"kind": "tap", "x": start["x"], "y": start["y"]}
        else:
            start = None
    situation = answer["situation"][:300] if isinstance(answer.get("situation"), str) else ""
    return {"box": [left, top, right, bottom], "rows": rows, "cols": cols, "keys": keys, "start": start,
            "situation": situation}


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
        else:
            # a game already going: what is filled now, but for the piece on its way, is the stack
            filled = self.well.read(first)
            piece = top_piece(filled)
            self.stack = filled - piece if whole(piece) else filled
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
        pushed: int | None = None
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
            if not missing and whole(extra):
                # a piece on its way over the stack as the code knows it
                odd_since = None
                shape = normal(extra)
                if plan is None or shape not in plan["turns"]:
                    found = best(self.stack, extra, rows, cols)
                    if found is None:
                        return "stopped_falling"
                    plan = {**found, "turns": turns(extra)}
                    began, turned, shifted, sent, pushed = now, 0, 0, 0, None
                column = min(c for _, c in extra)
                if shape != plan["shape"] and self.keys["rotate"] and turned < TURNS_MOST:
                    turned += 1
                    sent += await self._press(self.keys["rotate"])
                elif shape != plan["shape"]:
                    # the game does not turn it further (a wall, no such key): the shape it has is placed
                    kept = best(self.stack, extra, rows, cols, only=shape)
                    if kept is None:
                        return "stopped_falling"
                    plan = {**plan, **kept}
                elif column != plan["column"] and shifted < SHIFTS_MOST:
                    # (a piece that did not move since it was last asked sideways is against something)
                    shifted = shifted + 1 if pushed == column else 0
                    pushed = column
                    sent += await self._press(self.keys["left" if column > plan["column"] else "right"],
                                              abs(column - plan["column"]))
                elif self.keys["drop"]:
                    sent += await self._press(self.keys["drop"])
                else:
                    lies = fall(self.stack, extra, rows, cols)
                    below = (min(r for r, _ in lies) - min(r for r, _ in extra)) if lies else 0
                    # one more than the rows left: where a key that cannot move the piece puts it down
                    sent += await self._press(self.keys["down"], min(below + 1, DOWN_BATCH))
                last = extra
                continue
            # the stack is not what it was: the piece came to lie (and lines may have gone)
            lain = None
            for effect, cells in (("confirmed", plan["placed"] if plan else None),
                                  ("inconsistent", fall(self.stack, last, rows, cols) if last else None)):
                if cells is None:
                    continue
                after, lines = settle(self.stack, cells, rows, cols)
                rest = filled - after
                if after <= filled and (not rest or whole(rest)):
                    lain = (effect, cells, after, lines)
                    break
            if lain is None:
                odd_since = odd_since or now
                if now - odd_since < SETTLE_S:
                    continue
                # neither: the stack is taken from the screen as it stands, but for what hangs at its top
                piece = top_piece(filled)
                self.stack = filled - piece if whole(piece) and piece != filled else filled
                self.resyncs += 1
                streak += 1
                if plan is not None or last is not None:
                    self._placed("unknown", plan, 0, sent, now - began)
                plan = last = odd_since = None
                if streak >= RESYNCS_MOST:
                    return "well_unread"
                continue
            effect, _, after, lines = lain
            self.stack, self.lines, odd_since, streak = after, self.lines + lines, None, 0
            self._placed(effect, plan, lines, sent, now - began)
            plan = last = None

    def _placed(self, effect: str, plan: dict[str, Any] | None, lines: int, sent: int, seconds: float) -> None:
        self.pieces += 1
        self.effects[effect] += 1
        self.piece_s.append(seconds)
        if self.placed is not None:
            self.placed({"kind": "place", "effect": effect, "lines": lines, "keys": sent, "seconds": round(seconds, 2),
                         "action": {"kind": "place", "column": plan["column"], "shape": sorted(map(list, plan["shape"]))}
                         if plan else {"kind": "place"}})

    async def _press(self, key: str, times: int = 1) -> int:
        for _ in range(times):
            await self.send({"kind": "key", "key": key})
        return times

    def summary(self) -> dict[str, Any]:
        def mean_ms(values: list[float]) -> float | None:
            return round(statistics.fmean(values) * 1000, 1) if values else None
        lasted = time.monotonic() - self.started if self.started else 0.0
        return {"well": self.well.to_json(), "keys": self.keys, "start": self.setup["start"],
                "lines": self.lines, "pieces": self.pieces, "effects": dict(self.effects), "resyncs": self.resyncs,
                "ticks": self.ticks, "ticks_per_s": round(self.ticks / lasted, 1) if lasted else None,
                "capture_ms": mean_ms(self.capture_s), "read_ms": mean_ms(self.read_s),
                "piece_s": round(statistics.fmean(self.piece_s), 2) if self.piece_s else None,
                "stack_height": self.well.rows - min((r for r, _ in self.stack), default=self.well.rows)}


async def tap_or_key(surface: Any, sent: dict[str, Any]) -> None:
    if sent["kind"] == "key":
        await surface.key(sent["key"])
    else:
        await surface.tap(sent["x"], sent["y"], TAP_MS)
