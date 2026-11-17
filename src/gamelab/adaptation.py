"""Making a site adapter at run time: a model reads the first moves, then writes the code that reads the
rest (adaptive perception plan §5, stages 1 to 4; §4.4).

    explore   the model reads each screen into the rule model's state (checked by the rule model); the
              registered solver picks the moves; every look and move is recorded with what the rules say of it
    generate  the model writes the adapter from the recorded looks (the last ones kept back), told what was
              wrong with each attempt, until one passes or the attempts run out
    adopt     an attempt passes when, run in the sandbox over every recorded look, it finds the board, reads
              each state as recorded, each transition is one the rules allow, and it plays each recorded
              move with the input that was sent (the same key; a tap inside the same cell)

The model never picks a move and its code never sends an input: moves are the solver's, inputs the
harness's. A game is explored with its family's starting controls (`discovery.controls`): keys, or taps on
the cell a move names, placed from the board box the model gave and the grid's shape. What is adopted is
kept by site (`Store`) and tried again next time before anything is made.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import hashlib
from io import BytesIO
import json
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlsplit

import logging
from . import catalog, frames, resources, samples
from .adapters.generated import CELLS_UNREAD, FUNCTIONS, GeneratedAdapter, check_layout
from .contracts import NeedObservation, Observation
from .surface import Surface
from .execution import TAP_MS
from .model import Model, Msg, TurnRequest
from .recovery import MAX_TRIES, Recovery, view
from .sandbox import Sandbox, SandboxError

log = logging.getLogger("gamelab.adaptation")

# moves played while the model reads every screen: enough transitions to write the adapter from and to
# keep some back; each costs one model question
EXPLORE_MOVES = 6
# the last transitions the generation never sees: an adapter must read them right unseen
HELD_BACK = 2
# readings of one screen asked for again when the rule model refuses what the model read
LABEL_RETRIES = 2
# adapters written for one site before giving up
MAX_GENERATIONS = 5
# how long a played move is left to finish before its result is looked at
SETTLE_S = 0.8
# more captures of each screen read, taken with no question asked: an adapter must read them as it reads the
# screen itself, so code that leans on what changes from frame to frame (a tile that animates while the game
# waits) is not adopted. A capture that looks like another screen (a notice come up meanwhile) is not kept
FRAMES, FRAME_GAP_S, FRAME_DISTANCE = 2, 0.3, 10.0
# a reading's answer holds every cell of the board: room for a large one (2,500 cells are about 8,000 tokens)
LABEL_TOKENS, GENERATE_TOKENS = 16000, 16000
LABEL_TIMEOUT_S, GENERATE_TIMEOUT_S = 90.0, 300.0
# the board region as the model is shown it for reading cells: this many pixels on its longer side, more when
# its cells would be smaller than OVERLAY_CELL (each carries a label of 27 by 13 pixels), up to OVERLAY_MOST
OVERLAY_SIDE, OVERLAY_CELL, OVERLAY_MOST = 800, 56, 2400
# colours nearer than this are one run of a probe's scan line
# a drag the code asks for plays a recorded one when it starts within this share of the dragged thing's size
# around the box the model gave for it, and ends within this share of a cell of the recorded end
DRAG_FROM_MARGIN, DRAG_ONTO = 0.35, 0.34
# values a screen kept back from the writer may be left unread on and the adapter still be adopted
HELD_UNREAD = 2
# an input among a game's places plays a recorded one when each of its ends lies within this share of the
# place's size around the box the model gave for it
PLACE_MARGIN = 0.25
RUN_TOLERANCE = 6
MAX_RUNS = 120
# a board the model places further than this share of a cell from where it was is taken to have moved
BOARD_MOVED = 0.5
# a board read at another size than the game had is believed when its cells are this near square (width over
# height, either way)
SQUARE = 1.25
# the part of a cell the plain adapter leaves to the samples: this share of the cell is left out on every side
# (its edges, where a neighbour's border, a gutter or a highlight shows), the first of these that passes on the
# game's screens; none left out last, for a game whose cells differ only at their edges
PLAIN_INSETS = (0.2, 0.1, 0.0)
# what a question that writes an adapter costs, roughly: its text's characters to a token, and a picture
WRITE_CHARS, WRITE_PICTURE = 2, 800
# the controls a game can be explored with before any adapter exists: a key per direction, a tap on the cell a
# move names, a drag of the thing a move names (one entry of a state field, at the box the model gave for
# it) onto the cells the move names, or, for a game whose board is no grid, taps on and drags between the
# places the family names and the model gave boxes for (`place`)
EXPLORED_WITH = ("keys", "tap", "drag", "places")


class AdaptationFailed(Exception):
    """No adapter could be made: `reason` says where it stopped."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


@dataclass
class Look:
    png: bytes
    state: dict[str, Any]
    board: list[int]
    # the move played from this look and the input sent for it; None on the last look
    action: dict[str, Any] | None = None
    sent: dict[str, Any] | None = None
    # kept back from the writer: an adapter must read it right unseen
    held: bool = False
    # more captures of the same screen moments later (FRAMES), to be read the same
    frames: list[bytes] = field(default_factory=list)
    # for a game played by drags: the box around each thing that may be dragged, in the order of the state
    # field that lists them (None where the entry is empty), as the model gave them
    sources: list[list[int] | None] | None = None


@dataclass
class Trace:
    family_id: str
    rules: dict[str, Any]
    site: str
    looks: list[Look] = field(default_factory=list)

    @property
    def train(self) -> list[Look]:
        return [look for look in self.looks if not look.held]


@dataclass
class Made:
    source: str
    generations: int
    reused: bool
    report: dict[str, Any]
    seconds: float = 0.0
    questions: int = 0


def _prompt(name: str) -> str:
    return (resources.FRAGMENT / "prompts" / name).read_text(encoding="utf-8")


def _image(png: bytes) -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(png).decode("ascii")}}


def _json(text: str | None) -> Any:
    try:
        return json.loads(text or "")
    except ValueError:
        return None


def overlay(png: bytes, board: list[int], rows: int, cols: int) -> bytes:
    """The board region enlarged, a grid drawn over it and every cell marked "row,col"."""
    from PIL import Image, ImageDraw

    left, top, width, height = board
    with Image.open(BytesIO(png)) as image:
        region = image.convert("RGB").crop((left, top, left + width, top + height))
    # the side with the smallest cells sets the size: a cell is at least OVERLAY_CELL across while that fits
    scale = max(OVERLAY_SIDE / max(width, height), min(OVERLAY_CELL * cols / width, OVERLAY_CELL * rows / height,
                                                        OVERLAY_MOST / max(width, height)))
    region = region.resize((round(width * scale), round(height * scale)))
    draw = ImageDraw.Draw(region)
    for row in range(rows):
        for col in range(cols):
            x, y = col * region.width / cols, row * region.height / rows
            draw.rectangle((x, y, x + region.width / cols, y + region.height / rows), outline=(255, 0, 255), width=2)
            draw.rectangle((x + 3, y + 3, x + 30, y + 16), fill=(255, 255, 255))
            draw.text((x + 5, y + 3), f"{row},{col}", fill=(200, 0, 200))
    out = BytesIO()
    region.save(out, format="PNG")
    return out.getvalue()


def _grid(state: dict[str, Any]) -> list[list[Any]] | None:
    """The state's board, whatever the family calls it: its first field that is rows of cells (not a list of
    pieces, whose entries are themselves lists of cells)."""
    return next((value for value in state.values() if isinstance(value, list) and value and all(
        isinstance(row, list) and row and not any(isinstance(cell, list) for cell in row) for row in value)), None)


def _shape(family: Any, state: dict[str, Any]) -> tuple[int, int] | None:
    """The rows and columns of the state's board."""
    grid = _grid(state)
    return (len(grid), len(grid[0])) if grid else None


def _extent(piece: list[list[int]]) -> tuple[int, int]:
    """The rows and columns a piece's cells span."""
    return max(row for row, _ in piece) + 1, max(col for _, col in piece) + 1


def cell_box(board: list[int], shape: tuple[int, int], cell: list[int]) -> list[float]:
    """The box [left, top, width, height] of one cell of a board box divided evenly into `shape` cells."""
    left, top, width, height = board
    rows, cols = shape
    row, col = cell
    across, down = width / cols, height / rows
    return [left + col * across, top + row * down, across, down]


def place(places: dict[str, Any], named: list[Any], *, taking: bool) -> list[int]:
    """The box of the place a move names, among the boxes given for a game's places. `named` is the place's
    name, then, where the place is a list, which of them, then, where that is a pile of things, how many are
    taken from its top. Of a pile it is the box of the deepest thing taken, or, to put something on it, of its
    top one."""
    found = places[named[0]]
    if len(named) > 1 and isinstance(found[0], list):
        found = found[named[1]]
    if isinstance(found[0], list):
        found = found[-named[2]] if taking and len(named) > 2 else found[-1]
    return found


def _middle(box: list[int]) -> tuple[float, float]:
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def _within(point: tuple[float, float], box: list[int], margin: float) -> bool:
    return (box[0] - box[2] * margin <= point[0] < box[0] + box[2] * (1 + margin)
            and box[1] - box[3] * margin <= point[1] < box[1] + box[3] * (1 + margin))


def drag_for(source: list[int], board: list[int], shape: tuple[int, int], piece: list[list[int]],
             anchor: list[int], ms: int) -> dict[str, Any]:
    """The drag that puts `piece` (its cells, from the box `source` around it) on the board with its first row
    and column at the cell `anchor`: from the middle of one of its cells to where the middle of the whole
    piece is to lie. What carries it out makes up for how the game draws a held piece (`carry`)."""
    rows, cols = _extent(piece)
    left, top, width, height = source
    row, col = piece[0]
    first = cell_box(board, shape, anchor)
    return {"kind": "drag", "x0": left + (col + 0.5) * width / cols, "y0": top + (row + 0.5) * height / rows,
            "x1": first[0] + first[2] * cols / 2, "y1": first[1] + first[3] * rows / 2, "ms": ms}


def input_for(controls: dict[str, Any], action: dict[str, Any], board: list[int], shape: tuple[int, int],
              state: dict[str, Any] | None = None, sources: Any = None) -> dict[str, Any]:
    """The input a move is played with by a family's controls, as a generated adapter's `target()`
    would give it: the direction's key, a tap at the middle of the cell the move names, placed on the
    board box the model gave, or a drag from the middle of the thing the move names to the middle of the
    cells it is to cover."""
    if controls["kind"] == "keys":
        return {"kind": "key", "key": controls["keys"][action["direction"]]}
    if controls["kind"] == "places":
        if action["kind"] in controls["tap"]:
            x, y = _middle(place(sources, controls["tap"][action["kind"]], taking=False))
            return {"kind": "tap", "x": x, "y": y}
        start, end = controls["drag"][action["kind"]]
        (x0, y0), (x1, y1) = _middle(place(sources, action[start], taking=True)), _middle(place(sources, action[end], taking=False))
        return {"kind": "drag", "x0": x0, "y0": y0, "x1": x1, "y1": y1, "ms": controls["duration_ms"]}
    if action.get("kind") in controls.get("taps", {}):
        # a move that is a tap on the cell it names, in a game otherwise played by drags
        left, top, width, height = cell_box(board, shape, action[controls["taps"][action["kind"]]])
        return {"kind": "tap", "x": left + width / 2, "y": top + height / 2}
    if controls["kind"] == "drag" and "source" not in controls:
        # from one cell of the board to another: the middle of each
        one, other = cell_box(board, shape, action[controls["from"]]), cell_box(board, shape, action[controls["to"]])
        return {"kind": "drag", "x0": one[0] + one[2] / 2, "y0": one[1] + one[3] / 2,
                "x1": other[0] + other[2] / 2, "y1": other[1] + other[3] / 2, "ms": controls["duration_ms"]}
    if controls["kind"] == "drag":
        index = action[controls["index"]]
        return drag_for(sources[index], board, shape, state[controls["source"]][index], action[controls["onto"]],
                        controls["duration_ms"])
    left, top, width, height = cell_box(board, shape, action["cell"])
    return {"kind": "tap", "x": left + width / 2, "y": top + height / 2}


class Adaptation:
    def __init__(self, model: Model, family_id: str, *, policy: str | None = None, compute: int = 200,
                 recovery: Recovery | None = None, writer: Model | None = None) -> None:
        # `writer`: the model asked to write adapters, when it is another than the one that reads screens
        self.model, self.writer, self.family_id = model, writer or model, family_id
        # the model's way past a screen that shows no board (a notice over it), and the last board read
        self.recovery, self.last_state = recovery, None
        # the other captures of the screen last read (FRAMES)
        self.frames: list[bytes] = []
        # what each screen read said of the rule values a game may be played without knowing (`_told`)
        self.told: list[dict[str, Any]] = []
        # the first screen as read already (`probe`), for the exploring to start from
        self.first: tuple[bytes, dict[str, Any], list[int], dict[str, Any]] | None = None
        # where the last screen read shows each thing that may be dragged (`Look.sources`)
        self.sources: list[list[int] | None] | None = None
        # the last reading refused as no result of the move just played (`accept`): what the screen was read as
        self.refused: dict[str, Any] | None = None
        # set by who plays a family that has a reader of its own (`kits`). `arrange`: the things a reading lists
        # that may be dragged, each with its box, put into the places the reader knows them to wait in (a model
        # that lists only what is left says nothing of which place is empty). `gauge`: the rows and columns of the
        # board in a box of a screen, counted from the picture alone (None when it cannot tell): a screen whose
        # count is another than the game's is read with no grid drawn on it, and a reading of another size is
        # believed only when the count agrees with it
        self.arrange: Any = None
        self.gauge: Any = None
        self.descriptor = catalog.descriptor(family_id)
        self.policy = policy or self.descriptor["default_policy"]
        self.compute = compute
        self.questions = 0
        # what the game is played with while exploring: the family's starting controls
        self.controls = self.descriptor.get("discovery", {}).get("controls")
        if not self.controls or self.controls["kind"] not in EXPLORED_WITH:
            raise AdaptationFailed("family_unsupported", "the family names no controls its first moves can be played with")

    # ── explore ───────────────────────────────────────────────────────

    async def label(self, png: bytes, board: list[int] | None, shape: tuple[int, int] | None) -> dict[str, Any] | None:
        """What the model reads on one screen: `{"board", "rules", "state"}` or None when it shows no board
        or the answer is no answer. The rule model checks the state afterwards."""
        # a large screen is shown smaller (`view`): boxes go to the model and come back in what it is shown
        shown, scale = view(png)
        context = {"family": self.descriptor, **({"board": [round(value * scale) for value in board]} if board else {}),
                   **({"sources": self.controls["source"]} if "source" in self.controls else {}),
                   **({"places": self.controls["places"]} if "places" in self.controls else {})}
        content = [{"type": "text", "text": json.dumps(context)}, _image(shown)]
        if board and shape:
            content.append(_image(overlay(png, board, *shape)))
        self.questions += 1
        result = await self.model.complete(TurnRequest(system_stable=_prompt("label_screen.md"), max_tokens=LABEL_TOKENS,
                                                       timeout_s=LABEL_TIMEOUT_S, messages=[Msg("user", content)]))
        answer = _json(result.text)
        if not isinstance(answer, dict) or answer.get("screen") != "board" or not isinstance(answer.get("state"), dict):
            return None
        if scale != 1.0:
            def whole(box: Any) -> Any:
                return [round(value / scale) for value in box] if isinstance(box, list) and all(
                    type(value) in (int, float) for value in box) else box
            answer["board"] = whole(answer.get("board"))
            if isinstance(answer.get("sources"), list):
                answer["sources"] = [whole(box) for box in answer["sources"]]
        return answer

    async def _read(self, surface: Surface, board: list[int] | None, rules: dict[str, Any] | None,
                    accept: Any = None) -> tuple[bytes, dict[str, Any], list[int], dict[str, Any]]:
        """One screen read by the model (`_label`). A screen that shows no board is not the end of it: something
        may be in the way, which the model gets past with inputs of its own (PLAN §8) before it is read again."""
        tries = 0
        while True:
            try:
                read = await self._label(surface, board, rules, accept)
            except AdaptationFailed as error:
                if error.detail != "no_board" or self.recovery is None or tries >= MAX_TRIES or not await \
                        self.recovery.attempt(surface, error.reason, {"state": self.last_state, "recent": []}):
                    raise
                tries += 1
                continue
            if tries:
                self.recovery.succeeded()
            self.last_state = read[1]
            self.frames = await self._frames(surface, read[0])
            return read

    async def _frames(self, surface: Surface, png: bytes) -> list[bytes]:
        """FRAMES more captures of the screen just read, those that still look like it."""
        from .recovery import _distance, signature

        seen, kept = signature(png), []
        # frames as they are: what they are held to is how the screen differs from moment to moment
        source, after = frames.source_of(surface), time.monotonic() + FRAME_GAP_S
        for _ in range(FRAMES):
            frame = await source.frame(after)
            after = frame.at + FRAME_GAP_S
            if frame.png != png and _distance(seen, signature(frame.png)) <= FRAME_DISTANCE:
                kept.append(frame.png)
        return kept

    async def probe(self, surface: Surface) -> bool:
        """Whether the screen as it is shows a board of this family: read once by the model, with nothing done
        about a screen that shows none. The reading is kept for the exploring to start from."""
        self.told, self.first = [], None
        try:
            png, state, board, rules = await self._label(surface, None, None, retries=0)
        except AdaptationFailed:
            return False
        self.last_state, self.frames = state, await self._frames(surface, png)
        self.first = png, state, board, rules
        return True

    async def _label(self, surface: Surface, board: list[int] | None, rules: dict[str, Any] | None,
                     accept: Any = None, retries: int = LABEL_RETRIES, patient: bool = True,
                     prior: dict[str, Any] | None = None) -> tuple[bytes, dict[str, Any], list[int], dict[str, Any]]:
        """One screen read by the model into a state the rule model accepts whole (and `accept`, when given,
        takes as the last move's result); asked again on a fresh capture when it is not. `patient` off: a
        screen the model says shows no board is not asked about again (the caller has another way past it).
        `prior`: what is already known of this screen (the rules' prediction of the move just played, else the
        last state read); a value the model leaves unread takes the prior's, so a thing half hidden now (a card
        another was laid on) is not lost for want of a second reading."""
        problem = "no_board"
        for _ in range(1 + retries):
            png = await surface.capture()
            size = _size(png)
            shape = None
            if board and rules is not None:
                family = catalog.family(self.family_id).bind({"rules": rules})
                shape = _shape(family, family.unread_state(self.descriptor["scenes"][0]["id"]))
            # a screen whose board counts out to another size than the game had is shown with no grid drawn on
            # it: the board may be another now
            counted = self.gauge(png, board) if rules is not None and board and self.gauge is not None else None
            loose = counted is not None and "rows" in rules and counted != (rules["rows"], rules["cols"])
            answer = await self.label(png, None if loose else board, None if loose else shape)
            if answer is None:
                problem = "no_board"
                if not patient:
                    break
                await asyncio.sleep(SETTLE_S)
                continue
            found = answer.get("board") if board is None or loose else board
            if not loose and board is not None and shape and _moved(board, answer.get("board"), shape, size):
                # the game is elsewhere on the screen than it was (the page was laid out anew): the grid drawn
                # for the model lay beside the cells, so the screen is read once more with nothing drawn on it
                again = await self.label(png, None, None)
                if again is not None and check_layout({"board": again.get("board")}, size) is not None:
                    # read with nothing drawn on it, where the board is now: it may be of another size too
                    answer, found, loose = again, again["board"], True
            if check_layout({"board": found}, size) is None:
                problem = "board_off_screen"
                continue
            chosen = rules if rules is not None else self._rules(answer.get("rules"))
            if chosen is None:
                problem = "rules_refused"
                continue
            if rules is None:
                chosen = self._counted(chosen, answer)
            self._arranged(answer, chosen)
            resized = rules is not None and (chosen := self._resized(rules, answer, found, loose, png)) != rules
            family = catalog.family(self.family_id).bind({"rules": chosen})
            # the board is what is learned here: a score read off the page (in the site's own counting) would
            # only make a right board look like a wrong move
            read = {**_filled(answer["state"], prior), **({"score": None} if "score" in answer["state"] else {})}
            state = family.estimate(Observation("label", 0, "explore", 0, 0.0, "explore", public_facts={"state": read}))
            if isinstance(state, NeedObservation) or state.unknown_fields:
                problem = "state_refused"
                if not patient:
                    # asked again of the same screen, the model says the same (2026-11-17: 16 readings of one
                    # screen between levels, each refused): the caller has another way past it
                    break
                continue
            if accept is not None and not resized and not accept(state.payload):
                problem, self.refused = "transition_refused", state.payload
                await asyncio.sleep(SETTLE_S)
                continue
            if "source" in self.controls:
                self.sources = self._sources(answer.get("sources"), state.payload, size)
                if self.sources is None:
                    problem = "sources_refused"
                    continue
            if "places" in self.controls:
                self.sources = self._places(answer.get("places"), size)
                if self.sources is None:
                    problem = "places_refused"
                    continue
            self.told.append(self._told(answer.get("rules")))
            return png, state.payload, found, chosen
        raise AdaptationFailed("label_failed", problem)

    def _sources(self, seen: Any, state: dict[str, Any], size: list[int]) -> list[list[int] | None] | None:
        """The boxes the model gave for the things that may be dragged, when there is one on the screen for
        every entry the state lists; None otherwise."""
        from .adapters.generated import _box

        listed = state[self.controls["source"]]
        if not isinstance(seen, list) or len(seen) != len(listed):
            return None
        boxes = [box if entry else None for box, entry in zip(seen, listed)]
        return boxes if all(_box(box, size) for box, entry in zip(boxes, listed) if entry) else None

    def _counted(self, rules: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
        """A game's first reading that does not say how many dragged things the game has places for has as many
        as it lists: a tutorial shows one piece where the game will show three, and held to the family's usual
        three the reading would be refused and the family with it (2026-11-17: a web game's tutorial, then twenty
        moves of the model's own). The count grows when a later screen shows more (`_resized`)."""
        controls = self.controls
        count, listed = controls.get("count"), answer["state"].get(controls.get("source", ""))
        if not count or rules.get(count) is not None or not isinstance(listed, list) or not listed:
            return rules
        grown = {**rules, count: len(listed)}
        allowed = self.descriptor.get("discovery", {}).get("rules", {})
        if count in allowed and resources.validate(grown[count], allowed[count]):
            return rules
        return grown

    def _arranged(self, answer: dict[str, Any], rules: dict[str, Any]) -> None:
        """A reading that lists fewer dragged things than the game has places for them (only the pieces left, not
        the emptied places between them) is laid out by where each thing is: `arrange` knows the places."""
        controls = self.controls
        count = rules.get(controls.get("count", "")) if "source" in controls else None
        listed, boxes = answer["state"].get(controls.get("source", "")), answer.get("sources")
        if self.arrange is None or type(count) is not int or not isinstance(listed, list) or not isinstance(boxes, list) \
                or len(listed) != len(boxes) or len(listed) >= count:
            return
        placed = self.arrange([(thing, box) for thing, box in zip(listed, boxes) if thing])
        if placed is not None and len(placed[0]) == count:
            answer["state"] = {**answer["state"], controls["source"]: placed[0]}
            answer["sources"] = placed[1]

    def _resized(self, rules: dict[str, Any], answer: dict[str, Any], box: list[int], loose: bool,
                 png: bytes | None = None) -> dict[str, Any]:
        """The game's rule values as this reading shows them, when it shows a board of another size or more places
        for dragged things than the game had: `rules` themselves when it shows neither or may not be believed.
        A board of another size is believed only on a screen read with no grid drawn on it (`loose`), when its
        cells then come out about square (a miscounted row makes them oblong) and when the picture itself counts
        out to that size (`gauge`); places for dragged things only
        grow, each new one with a thing in it (a tutorial's single piece, then a tray of three)."""
        new, state, controls = dict(rules), answer["state"], self.controls
        grid = state.get("board")
        if loose and "rows" in rules and "cols" in rules and isinstance(grid, list) and grid \
                and all(isinstance(row, list) and len(row) == len(grid[0]) for row in grid) and grid[0] \
                and (len(grid), len(grid[0])) != (rules["rows"], rules["cols"]):
            across, down = box[2] / len(grid[0]), box[3] / len(grid)
            counted = self.gauge(png, box) if self.gauge is not None and png is not None else None
            if 1 / SQUARE <= across / down <= SQUARE and counted == (len(grid), len(grid[0])):
                new["rows"], new["cols"] = len(grid), len(grid[0])
        count, listed, boxes = controls.get("count"), state.get(controls.get("source", "")), answer.get("sources")
        if count in rules and type(rules[count]) is int and isinstance(listed, list) and isinstance(boxes, list) \
                and len(listed) == len(boxes) > rules[count] and all(thing and isinstance(box, list) for thing, box in zip(listed, boxes)):
            new[count] = len(listed)
        if new == rules or resources.validate({name: value for name, value in new.items() if value is not None},
                                              catalog.rules_schema(self.descriptor)):
            return rules
        allowed = self.descriptor.get("discovery", {}).get("rules", {})
        if resources.validate({name: new[name] for name in allowed if new.get(name) is not None},
                              {"type": "object", "properties": allowed}):
            return rules
        return new

    def _places(self, seen: Any, size: list[int]) -> dict[str, Any] | None:
        """The boxes the model gave for the places the family names, when every one is there and lies on the
        screen (a place may be one box, a list of them, or a list of lists); None otherwise."""
        from .adapters.generated import _box

        def boxes(value: Any, depth: int = 0) -> bool:
            if _box(value, size):
                return True
            return depth < 2 and isinstance(value, list) and bool(value) and all(boxes(part, depth + 1) for part in value)
        if not isinstance(seen, dict) or set(seen) != set(self.controls["places"]):
            return None
        return seen if all(boxes(value) for value in seen.values()) else None

    def _unknowable(self) -> list[str]:
        """The rule values a game may be played without knowing: those the family lets be null."""
        return [name for name, parameter in self.descriptor["rule_parameters"].items()
                if not resources.validate(None, parameter["values"])]

    def _rules(self, seen: Any) -> dict[str, Any] | None:
        """The rule values the model saw, when they are ones the family may be played with. A value the game
        may be played without knowing starts unknown whatever one screen says of it (`_told`)."""
        allowed = self.descriptor.get("discovery", {}).get("rules", {})
        unknowable = self._unknowable()
        # only what a screen can show of the rules (a board's size); anything else it names is left standard
        seen = {name: value for name, value in (seen if isinstance(seen, dict) else {}).items()
                if name in allowed and name not in unknowable}
        if resources.validate(seen, catalog.rules_schema(self.descriptor)):
            return None
        if resources.validate(seen, {"type": "object", "properties": allowed}):
            return None
        return {**seen, **{name: None for name in unknowable}}

    def _told(self, seen: Any) -> dict[str, Any]:
        """What one screen read said of each rule value a game may be played without knowing: the value, or
        None when it gave none the family takes."""
        allowed = self.descriptor.get("discovery", {}).get("rules", {})
        seen = seen if isinstance(seen, dict) else {}
        return {name: seen[name] if name in seen and not resources.validate(
                    {name: seen[name]}, catalog.rules_schema(self.descriptor)) and not resources.validate(
                    seen[name], allowed[name]) else None
                for name in self._unknowable() if name in allowed}

    def input_for(self, action: dict[str, Any], board: list[int], shape: tuple[int, int],
                  state: dict[str, Any] | None = None, sources: list[list[int] | None] | None = None) -> dict[str, Any]:
        """The input a move is played with before any adapter exists (`input_for`)."""
        return input_for(self.controls, action, board, shape, state, sources)

    async def _send(self, surface: Surface, sent: dict[str, Any]) -> None:
        if sent["kind"] == "key":
            await surface.key(sent["key"])
        elif sent["kind"] == "drag":
            await surface.drag(sent["x0"], sent["y0"], sent["x1"], sent["y1"], sent["ms"])
        else:
            await surface.tap(sent["x"], sent["y"], TAP_MS)

    async def explore(self, surface: Surface, moves: int = EXPLORE_MOVES) -> Trace:
        """The game's first moves, each screen read by the model and each move the solver's. A game that ends
        meanwhile (a mine under a first guess) ends the exploring: its last screen is a look like the others,
        and the trace is kept when enough was seen before it; nothing is started anew here."""
        if self.first is None:
            self.told = []
            self.first = await self._read(surface, None, None)
        (png, state, board, rules), self.first = self.first, None
        trace = Trace(self.family_id, rules, await surface.address())
        family = catalog.family(self.family_id).bind({"rules": rules})
        over = False
        for _ in range(moves):
            if not family.model.legal_actions(state, family.rules):
                over = True
                break
            action = family.choose(state, self.policy, self.compute)
            if action is None or action["kind"] in family.meta_actions:
                break
            sent = self.input_for(action, board, _shape(family, state), state, self.sources)
            trace.looks.append(Look(png, state, board, action, sent, frames=self.frames, sources=self.sources))
            await self._send(surface, sent)
            await asyncio.sleep(SETTLE_S)
            before = state
            # the move's result must be one the rules allow from the state read before it: for a game whose
            # moves reveal what was hidden, `assess` is what the rules can say of it (the cells a move may open)
            png, state, _, _ = await self._read(surface, board, rules, accept=lambda after: family.model.assess(
                before, action, after, family.rules)["effect"] == "confirmed")
        trace.looks.append(Look(png, state, board, frames=self.frames, sources=self.sources))
        if len(trace.looks) <= HELD_BACK + 2:
            raise AdaptationFailed("game_ended" if over else "game_too_short",
                                   f"{len(trace.looks) - 1} moves were played before the game ended" if over else "")
        for look in trace.looks[-HELD_BACK:]:
            look.held = True
        # a value the game may be played without knowing is taken from the screen only when every screen read
        # said the same (a count beside the board, not a clock): a wrong value is worse than none
        for name in self._unknowable():
            said = {told.get(name) for told in self.told}
            if len(said) == 1 and None not in said:
                trace.rules[name] = said.pop()
        return trace

    async def relabel(self, surface: Surface, trace: Trace, partial: dict[str, Any] | None) -> Look | None:
        """The screen as it is now, read by the model: a look to learn from when the adapter could not read
        it. What the adapter did read must agree with the model's reading. None when it shows no board."""
        try:
            png, state, board, _ = await self._read(
                surface, trace.looks[0].board, trace.rules,
                accept=None if partial is None else lambda read: _agrees(_board(partial), _board(read)))
        except AdaptationFailed:
            return None
        return Look(png, state, board, frames=self.frames, sources=self.sources)

    # ── generate and adopt ────────────────────────────────────────────

    def _context(self, trace: Trace, previous: tuple[str, list[str]] | None) -> dict[str, Any]:
        family = catalog.family(self.family_id)
        gridded = _grid(trace.looks[0].state) is not None
        examples = [{"state": look.state, "board": look.board, **({"cells": cells(look)} if gridded else {}),
                     "action": look.action, "sent": look.sent,
                     **({"places" if isinstance(look.sources, dict) else "sources": look.sources} if look.sources else {})}
                    for look in trace.train]
        return {"family": self.descriptor, "rules": trace.rules, "controls": self.controls,
                "rule_model": Path(family.model.__file__).read_text(encoding="utf-8"),
                "helpers": (Path(__file__).parent / "sandbox" / "helpers.py").read_text(encoding="utf-8"),
                "examples": examples, **({"probes": probes(trace.train[0])} if gridded else {}),
                **({"previous": {"module": previous[0], "problems": previous[1]}} if previous else {})}

    def cost(self, trace: Trace, previous: tuple[str, list[str]] | None = None) -> int:
        """About how many input tokens one question that writes an adapter from `trace` takes."""
        text = len(json.dumps(self._context(trace, previous))) + len(_prompt("generate_adapter.md"))
        return text // WRITE_CHARS + WRITE_PICTURE * len(trace.train)

    async def write(self, trace: Trace, previous: tuple[str, list[str]] | None = None) -> str:
        content = [{"type": "text", "text": json.dumps(self._context(trace, previous))},
                   *(_image(look.png) for look in trace.train)]
        self.questions += 1
        result = await self.writer.complete(TurnRequest(system_stable=_prompt("generate_adapter.md"),
                                                       max_tokens=GENERATE_TOKENS, timeout_s=GENERATE_TIMEOUT_S,
                                                       messages=[Msg("user", content)]))
        return result.text or ""

    async def make(self, trace: Trace, previous: tuple[str, list[str]] | None = None, allowed: Any = None) -> Made:
        """An adapter that passes every check on the trace, written in at most MAX_GENERATIONS attempts;
        `previous` is an earlier adapter and what is wrong with it now, to write from. `allowed`, when given,
        is told before each attempt about what it costs (`cost`) and says whether it may be asked for (a
        game's budget of tokens)."""
        started, before = time.monotonic(), self.questions
        attempts = []
        # a board that is a grid and nothing else needs no code written for it: the plain adapter is tried first
        for inset in PLAIN_INSETS:
            if (source := plain(trace, self.controls, inset)) is None:
                break
            report = adopt(source, trace)
            attempts.append({**{key: report[key] for key in ("passed", "failed_check", "transitions")}, "plain": inset,
                             "problems": [problem[:300] for problem in report["problems"]]})
            if report["passed"]:
                return Made(report["source"], 0, False, {**report, "attempts": attempts}, time.monotonic() - started, 0)
        for generation in range(1, MAX_GENERATIONS + 1):
            if allowed is not None and not allowed(self.cost(trace, previous)):
                raise AdaptationFailed("budget", json.dumps(attempts))
            try:
                source = await self.write(trace, previous)
            except Exception as error:
                # a model that could not be reached this once (a gateway's timeout) cost one attempt, not all of them
                log.info("no adapter was written: %s", error)
                attempts.append({"passed": False, "failed_check": "model", "problems": [f"{type(error).__name__}: {error}"[:300]]})
                continue
            report = adopt(source, trace)
            attempts.append({**{key: report[key] for key in ("passed", "failed_check", "transitions")},
                             "problems": [problem[:300] for problem in report["problems"]]})
            if report["passed"]:
                return Made(report["source"], generation, False, {**report, "attempts": attempts},
                            time.monotonic() - started, self.questions - before)
            previous = (report["source"], report["problems"])
        raise AdaptationFailed("no_adapter_passed", json.dumps(attempts))


PLAIN = """# The plain adapter (written by the host, not by a model): the board is the grid the first screen's reader
# boxed, divided evenly; what each cell holds is left to the host's samples.
BOARD, ROWS, COLS, FIELD, OTHERS, INSET = {board}, {rows}, {cols}, {field!r}, {others!r}, {inset}
CONTROLS = {controls!r}


def _cell(row, col):
    left, top, width, height = BOARD
    return left + col * width / COLS, top + row * height / ROWS, width / COLS, height / ROWS


def _middle(cell):
    x, y, across, down = _cell(*cell)
    return round(x + across / 2), round(y + down / 2)


def measure(picture):
    left, top, width, height = BOARD
    return {{"board": BOARD}} if left + width <= picture.width and top + height <= picture.height else None


def read(picture, layout):
    grid = []
    for row in range(ROWS):
        cells = []
        for col in range(COLS):
            x, y, across, down = _cell(row, col)
            cells.append({{"look": [round(x + across * INSET), round(y + down * INSET),
                                   round(across * (1 - 2 * INSET)), round(down * (1 - 2 * INSET))]}})
        grid.append(cells)
    return {{FIELD: grid, **OTHERS}}


def target(action, state, layout):
    if CONTROLS["kind"] == "keys":
        return {{"kind": "key", "key": CONTROLS["keys"][action["direction"]]}}
    if CONTROLS["kind"] == "tap":
        x, y = _middle(action["cell"])
        return {{"kind": "tap", "x": x, "y": y}}
    (x0, y0), (x1, y1) = _middle(action[CONTROLS["from"]]), _middle(action[CONTROLS["to"]])
    return {{"kind": "drag", "x0": x0, "y0": y0, "x1": x1, "y1": y1, "ms": CONTROLS["duration_ms"]}}
"""


def plain(trace: Trace, controls: dict[str, Any], inset: float = PLAIN_INSETS[0]) -> str | None:
    """The adapter no model need write: for a game whose state is one grid (and a score and scene, which it
    leaves unread and in play) played by keys, a tap on a cell or a drag from cell to cell. The board is the
    box the model gave on the first screen; every cell's value is left to the samples (`samples`), cut from
    the middle of the cell. None for any other game."""
    state = trace.looks[0].state
    grid = _grid(state)
    field_ = next((name for name, value in state.items() if value is grid), None) if grid is not None else None
    others = {name: {"score": None, "scene": "playing"}.get(name, ...) for name in state if name != field_}
    if field_ is None or ... in others.values() or controls["kind"] not in ("keys", "tap", "drag") or "source" in controls:
        return None
    return PLAIN.format(board=list(trace.looks[0].board), rows=len(grid), cols=len(grid[0]), field=field_, others=others,
                        inset=inset, controls={key: value for key, value in controls.items() if key != "then"})


def _size(png: bytes) -> list[int]:
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        return list(image.size)


def cells(look: Look) -> list[dict[str, Any]]:
    """For each cell of the look's board (at its approximate box): the value read there and the colours most
    common around its middle and its upper part, where a tile's face shows beside its numeral."""
    from PIL import Image

    grid = _grid(look.state)
    left, top, width, height = look.board
    rows, cols = len(grid), len(grid[0])
    found = []
    with Image.open(BytesIO(look.png)) as image:
        image = image.convert("RGB")
        for row in range(rows):
            for col in range(cols):
                across, down = width / cols, height / rows
                x, y = left + col * across, top + row * down

                def common(box: tuple[float, float, float, float]) -> list[int]:
                    crop = image.crop(tuple(round(value) for value in box))
                    return list(max(crop.getcolors(crop.width * crop.height), key=lambda item: item[0])[1])
                found.append({"cell": [row, col], "value": grid[row][col],
                              "middle": common((x + across * 0.35, y + down * 0.35, x + across * 0.65, y + down * 0.65)),
                              "upper": common((x + across * 0.3, y + down * 0.12, x + across * 0.7, y + down * 0.24))})
    return found


def probes(look: Look) -> dict[str, Any]:
    """Scan lines across the look's board region and a margin around it, as runs of one colour."""
    from PIL import Image

    left, top, width, height = look.board
    with Image.open(BytesIO(look.png)) as image:
        image = image.convert("RGB")
        margin_x, margin_y = width // 6, height // 6
        x0, x1 = max(0, left - margin_x), min(image.width, left + width + margin_x)
        y0, y1 = max(0, top - margin_y), min(image.height, top + height + margin_y)
        pixels = image.load()

        def runs(points: list[tuple[int, int]], start: int) -> list[list[Any]]:
            found: list[list[Any]] = []
            for offset, point in enumerate(points):
                colour = pixels[point]
                if found and max(abs(a - b) for a, b in zip(colour, found[-1][2])) <= RUN_TOLERANCE:
                    found[-1][1] += 1
                else:
                    found.append([start + offset, 1, list(colour)])
            return found[:MAX_RUNS]
        fractions = (0.12, 0.38, 0.5, 0.62, 0.88)
        return {"rows": [{"y": y, "runs": runs([(x, y) for x in range(x0, x1)], x0)}
                         for y in (round(top + height * f) for f in fractions)],
                "columns": [{"x": x, "runs": runs([(x, y) for y in range(y0, y1)], y0)}
                            for x in (round(left + width * f) for f in fractions)]}


def _source(text: str) -> str:
    """The module as written, without a Markdown fence around the whole of it."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped


def adopt(source: str, trace: Trace) -> dict[str, Any]:
    """The checks an adapter must pass on a trace (plan §5-4), run in the sandbox: it finds the board on
    every look, reads each state as recorded, each transition is one the rules allow, and each recorded move
    is played by the input that was sent. `problems` describes failures on the looks the generation saw;
    one on a held-back look is only said to exist."""
    source = _source(source)
    family = catalog.family(trace.family_id).bind({"rules": trace.rules})
    report: dict[str, Any] = {"passed": False, "failed_check": None, "problems": [], "source": source,
                              "transitions": {"checked": 0, "confirmed": 0}, "looks": len(trace.looks)}
    shown = [index for index, look in enumerate(trace.looks) if not look.held]

    def fail(check: str, index: int, text: str) -> None:
        report["failed_check"] = report["failed_check"] or check
        if len(report["problems"]) < 4:
            report["problems"].append(f"{check} on example {shown.index(index)}: {text}" if index in shown else
                                      f"{check} on a screen you were not shown: make the reading general")

    if any(f"def {name}(" not in source for name in FUNCTIONS):
        fail("functions", 0, f"the module must define {', '.join(FUNCTIONS)}")
        return report
    adapter = GeneratedAdapter(trace.family_id, source, rules=trace.rules, site=trace.site)
    try:
        # each look as the code reads it (marks and all, `samples`), and the samples it gives
        raws: list[dict[str, Any] | None] = []
        cuts: list[list[tuple[Any, list[bytes]]]] = []
        for index, look in enumerate(trace.looks):
            raws.append(None)
            cuts.append([])
            if isinstance(adapter.measure(look.png), NeedObservation):
                fail("measure", index, adapter.last_error or "measure() returned no board box that lies on the screen")
                continue
            raw = adapter.raw(look.png)
            if not isinstance(raw, dict):
                fail("read", index, adapter.last_error or "read() returned no state")
                adapter.last_error = None
                continue
            found, problem = samples.learn(raw, look.state, [look.png, *look.frames])
            if problem is not None:
                fail("read", index, f"{problem}; expected {json.dumps(_board(look.state))}")
                continue
            raws[index], cuts[index] = raw, found
        states: list[dict[str, Any] | None] = []
        for index, look in enumerate(trace.looks):
            raw = raws[index]
            states.append(None)
            if raw is None:
                continue
            # by the samples of the other screens it was shown: right, or not told (a thing seen here first)
            others, whole = samples.Bank(), samples.Bank()
            for other, found in enumerate(cuts):
                for label, group in found:
                    if other == index or not trace.looks[other].held:
                        whole.add(label, *group)
                    if other != index and not trace.looks[other].held:
                        others.add(label, *group)
            unseen = samples.resolve(raw, look.png, others)
            marked = {path for path, _ in samples.marks(raw)}
            if not _agrees(_board(unseen), _board(look.state)):
                fail("state", index, f"read() gave {json.dumps(_board(unseen))}, expected {json.dumps(_board(look.state))}")
                continue
            # by its own samples too: all of it, with nothing the code itself left unread
            own = samples.resolve(raw, look.png, whole)
            # a screen kept back may show what no screen the writer saw did (the first card on a place that was
            # empty till then): there the code may leave a value or two unread, as long as it reads nothing wrongly
            spared = look.held and _agrees(_board(own), _board(look.state)) and _unread_leaves(own) <= HELD_UNREAD
            if _board(own) != _board(look.state) and not spared:
                marked = bool(marked)
                fail("read" if not marked else "samples", index,
                     (f"read() left cells unread: {json.dumps(_board(own))}" if not marked else
                      "different things look the same inside the boxes read() left to the host (or read() left a "
                      f"value unread itself): it gave {json.dumps(_board(own))}") + f"; expected {json.dumps(_board(look.state))}")
                continue
            states[index] = look.state
            for frame in look.frames:
                again = adapter.raw(frame) if not isinstance(adapter.measure(frame), NeedObservation) else None
                seen = samples.resolve(again, frame, whole) if isinstance(again, dict) else None
                if seen is None or _board(seen) != _board(look.state):
                    got = "no board" if seen is None else json.dumps(_board(seen))
                    adapter.last_error = None
                    fail("frames", index, f"on another capture of the same screen {FRAME_GAP_S}s later (nothing played "
                                          f"between) the code gave {got}: read what stays the same while the screen animates")
                    break
            if look.action is not None:
                adapter.measure(look.png)
                try:
                    asked = adapter.sandbox.call("target", action=look.action, state=look.state, layout=adapter.layout)
                    adapter.checked_input(asked)
                except (SandboxError, ValueError) as error:
                    asked = f"{type(error).__name__}: {error}"
                if not _same_input(asked, look, adapter.layout["board"]):
                    fail("target", index, f"target() gave {json.dumps(asked)} for {json.dumps(look.action)}, expected {json.dumps(look.sent)}"
                                          + {"tap": " (a tap anywhere inside that cell)",
                                             "drag": (" (from anywhere on that piece to the middle of the cells it is to cover)"
                                                      if look.sources else " (from inside the one cell to inside the other)")
                                             }.get(look.sent["kind"], ""))
        for index, look in enumerate(trace.looks[:-1]):
            before, after = states[index], states[index + 1]
            if before is None or after is None or look.action is None:
                continue
            report["transitions"]["checked"] += 1
            if family.model.assess(before, look.action, after, family.rules)["effect"] == "confirmed":
                report["transitions"]["confirmed"] += 1
            else:
                fail("transition", index + 1, "the state read after the move is not one the rules allow from the state read before it")
    finally:
        adapter.close()
    report["passed"] = report["failed_check"] is None
    return report


def _same_input(asked: Any, look: Look, board: list[int]) -> bool:
    """Whether the code plays the look's move as it was played: the same key; or a tap inside the cell the move
    names, on the board as the code measured it (the pixel the harness tapped was one point of that cell, and
    code that picks another point of it plays the same move)."""
    if not isinstance(asked, dict) or asked.get("kind") != look.sent["kind"] or look.sent["kind"] == "key":
        return asked == look.sent
    if isinstance(look.sources, dict):
        # among a game's places: each end of the input in or near the box (a model's estimate) the recorded end was in
        def near(point: tuple[float, float], recorded: tuple[float, float]) -> bool:
            boxes = [box for box in _flat(look.sources) if _within(recorded, box, 0.0)]
            return any(_within(point, box, PLACE_MARGIN) for box in boxes)
        if look.sent["kind"] == "tap":
            return near((asked["x"], asked["y"]), (look.sent["x"], look.sent["y"]))
        return (near((asked["x0"], asked["y0"]), (look.sent["x0"], look.sent["y0"]))
                and near((asked["x1"], asked["y1"]), (look.sent["x1"], look.sent["y1"])))
    if look.sent["kind"] == "drag" and not look.sources:
        # between two cells of the board: each end inside the cell the recorded drag's end was in, on the board
        # as the code measured it
        rows, cols = _shape(None, look.state)

        def cell(x: float, y: float, box: list[int]) -> tuple[int, int]:
            return int((y - box[1]) // (box[3] / rows)), int((x - box[0]) // (box[2] / cols))
        return (cell(asked["x0"], asked["y0"], board) == cell(look.sent["x0"], look.sent["y0"], look.board)
                and cell(asked["x1"], asked["y1"], board) == cell(look.sent["x1"], look.sent["y1"], look.board))
    if look.sent["kind"] == "drag":
        # the same thing picked up (a point on it, or near: the box around it is a model's estimate) and taken
        # to the same cells: its middle within a third of a cell of theirs, on the board as the code measured it
        rows, cols = _shape(None, look.state)
        across, down = board[2] / cols, board[3] / rows
        scale_x, scale_y = board[2] / look.board[2], board[3] / look.board[3]
        goal = (board[0] + (look.sent["x1"] - look.board[0]) * scale_x, board[1] + (look.sent["y1"] - look.board[1]) * scale_y)
        source = next(box for box in look.sources or [] if box and box[0] <= look.sent["x0"] < box[0] + box[2]
                      and box[1] <= look.sent["y0"] < box[1] + box[3])
        margin = max(source[2], source[3]) * DRAG_FROM_MARGIN
        return (source[0] - margin <= asked["x0"] < source[0] + source[2] + margin
                and source[1] - margin <= asked["y0"] < source[1] + source[3] + margin
                and abs(asked["x1"] - goal[0]) <= across * DRAG_ONTO and abs(asked["y1"] - goal[1]) <= down * DRAG_ONTO)
    left, top, width, height = cell_box(board, _shape(None, look.state), look.action["cell"])
    return left <= asked["x"] < left + width and top <= asked["y"] < top + height


def _moved(board: list[int], said: Any, shape: tuple[int, int], size: list[int]) -> bool:
    """Whether the box a model gives for the board lies elsewhere than the one it was told: an edge further
    than BOARD_MOVED of a cell from where it was."""
    if check_layout({"board": said}, size) is None:
        return False
    rows, cols = shape
    across, down = board[2] / cols * BOARD_MOVED, board[3] / rows * BOARD_MOVED
    return (abs(said[0] - board[0]) > across or abs(said[0] + said[2] - board[0] - board[2]) > across
            or abs(said[1] - board[1]) > down or abs(said[1] + said[3] - board[1] - board[3]) > down)


def _filled(read: Any, prior: Any) -> Any:
    """A reading with each unread value taken from `prior` where that has one at the same place."""
    if read is None:
        return prior if not isinstance(prior, (dict, list)) else None
    if isinstance(read, dict) and isinstance(prior, dict):
        return {key: _filled(value, prior.get(key)) for key, value in read.items()}
    if isinstance(read, list) and isinstance(prior, list) and len(read) == len(prior):
        return [_filled(value, other) for value, other in zip(read, prior)]
    return read


def _unread_leaves(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_unread_leaves(part) for part in value.values())
    if isinstance(value, list):
        return sum(_unread_leaves(part) for part in value)
    return value is None


def _flat(places: Any) -> list[list[int]]:
    """Every box among a game's places."""
    if isinstance(places, dict):
        return [box for value in places.values() for box in _flat(value)]
    if isinstance(places, list) and places and isinstance(places[0], list):
        return [box for value in places for box in _flat(value)]
    return [places] if isinstance(places, list) and places else []


def _agrees(partial: Any, whole: Any) -> bool:
    """Whether a reading with gaps says nothing the whole reading does not."""
    if partial is None:
        return True
    if isinstance(partial, dict) and isinstance(whole, dict):
        return all(_agrees(value, whole.get(key)) for key, value in partial.items())
    if isinstance(partial, list) and isinstance(whole, list):
        return len(partial) == len(whole) and all(_agrees(a, b) for a, b in zip(partial, whole))
    return partial == whole


def _board(state: dict[str, Any]) -> Any:
    """What a reading is compared on: the board itself, not a score the model may have read from elsewhere."""
    return {key: value for key, value in state.items() if key not in ("score", "scene")}


class Store:
    """Adapters kept by site and family (plan §4.4): the code, and how it was made."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def _path(self, site: str, family_id: str) -> Path:
        parts = urlsplit(site)
        name = f"{parts.netloc or 'site'}-{hashlib.sha256(f'{parts.netloc}{parts.path}'.encode()).hexdigest()[:8]}"
        return self.folder / name.replace(":", "_") / family_id

    def load(self, site: str, family_id: str) -> tuple[str, dict[str, Any], Trace] | None:
        """The adapter kept for a site: its code, how it was made, and the looks it was made from."""
        return self.read(self._path(site, family_id))

    def kept(self) -> list[Path]:
        """The folder of every adapter kept here, whatever site it is for (`read` reads one)."""
        return sorted(path.parent for path in self.folder.glob("*/*/adapter.py")) if self.folder.is_dir() else []

    @staticmethod
    def read(path: Path) -> tuple[str, dict[str, Any], Trace] | None:
        """The adapter kept in one folder of a store: its code, how it was made, and the looks it was made from."""
        if not (path / "adapter.py").is_file():
            return None
        meta = json.loads((path / "adapter.json").read_text(encoding="utf-8"))
        looks = [Look((path / f"look-{index}.png").read_bytes(), **{**look, "frames": [
                     (path / f"look-{index}-frame-{number}.png").read_bytes() for number in range(look.get("frames", 0))]})
                 for index, look in enumerate(meta["trace"])]
        family_id = meta.get("family") or path.name
        return (path / "adapter.py").read_text(encoding="utf-8"), meta, Trace(family_id, meta["rules"], meta["site"], looks)

    def save(self, trace: Trace, made: Made) -> Path:
        path = self._path(trace.site, trace.family_id)
        path.mkdir(parents=True, exist_ok=True)
        (path / "adapter.py").write_text(made.source, encoding="utf-8")
        (path / "adapter.json").write_text(json.dumps({
            "site": trace.site, "family": trace.family_id, "rules": trace.rules,
            "digest": hashlib.sha256(made.source.encode()).hexdigest(), "generations": made.generations,
            "report": {key: value for key, value in made.report.items() if key != "source"},
            "trace": [{"state": look.state, "board": look.board, "action": look.action, "sent": look.sent,
                       "held": look.held, "frames": len(look.frames), "sources": look.sources}
                      for look in trace.looks]}, indent=2) + "\n",
            encoding="utf-8")
        for index, look in enumerate(trace.looks):
            (path / f"look-{index}.png").write_bytes(look.png)
            for number, frame in enumerate(look.frames):
                (path / f"look-{index}-frame-{number}.png").write_bytes(frame)
        return path

    def save_looks(self, trace: Trace) -> Path:
        """The looks of a game no adapter was adopted for, kept apart from any adapter (`unadopted/`)."""
        path = self._path(trace.site, trace.family_id) / "unadopted"
        path.mkdir(parents=True, exist_ok=True)
        (path / "trace.json").write_text(json.dumps({
            "site": trace.site, "family": trace.family_id, "rules": trace.rules,
            "trace": [{"state": look.state, "board": look.board, "action": look.action, "sent": look.sent,
                       "held": look.held, "frames": len(look.frames), "sources": look.sources}
                      for look in trace.looks]}, indent=2) + "\n", encoding="utf-8")
        for index, look in enumerate(trace.looks):
            (path / f"look-{index}.png").write_bytes(look.png)
            for number, frame in enumerate(look.frames):
                (path / f"look-{index}-frame-{number}.png").write_bytes(frame)
        return path

    def profile(self, site: str, family_id: str) -> dict[str, Any] | None:
        """What a family's own reader learned of a site (`kits`): numbers, no code."""
        path = self._path(site, family_id) / "profile.json"
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except ValueError:
            return None

    def save_profile(self, site: str, family_id: str, profile: dict[str, Any]) -> None:
        path = self._path(site, family_id)
        path.mkdir(parents=True, exist_ok=True)
        (path / "profile.json").write_text(json.dumps(profile) + "\n", encoding="utf-8")

    def recoveries(self, site: str, family_id: str) -> list[dict[str, Any]]:
        """The inputs that got past this site's screens before (`Recovery.known`)."""
        path = self._path(site, family_id) / "recoveries.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []

    def save_recoveries(self, site: str, family_id: str, known: list[dict[str, Any]]) -> None:
        path = self._path(site, family_id)
        path.mkdir(parents=True, exist_ok=True)
        (path / "recoveries.json").write_text(json.dumps(known) + "\n", encoding="utf-8")


# questions asked and inputs sent to reach a board before a game is given up as not shown
IDENTIFY_TOKENS, IDENTIFY_TIMEOUT_S = 1000, 60.0
# how long a screen that shows no game and offers nothing to press is left before it is asked about again
IDENTIFY_WAIT_S = 2.0
MOST_CANDIDATES = 3


def explorable() -> list[str]:
    """The families a game can be recognised as: those whose first moves can be played before an adapter exists."""
    return [family_id for family_id in sorted(catalog.MODELS)
            if catalog.descriptor(family_id).get("discovery", {}).get("controls", {}).get("kind") in EXPLORED_WITH]


async def identify(surface: Surface, model: Model, recovery: Recovery, *, hint: str | None = None,
                   tries: int = MAX_TRIES) -> dict[str, Any]:
    """Which families the game on the screen may be, as a model sees it (PLAN §10, game identification):
    `{"families": [ids, likeliest first], "questions", "situation"}`. A screen that shows no game in play is
    not the end of it: the model gets past it with inputs of its own (`tries` at most; none: one question and
    no input), and the screen is asked about again. The answer is a guess; the caller checks it by reading the
    board and playing moves by the family's rules."""
    listed = [{key: descriptor[key] for key in ("id", "summary", "board", "actions")}
              for descriptor in map(catalog.descriptor, explorable())]
    known = {family["id"] for family in listed}
    found: dict[str, Any] = {"families": [], "questions": 0, "situation": None}
    for tried in range(tries + 1):
        png = await surface.capture()
        context = {"families": listed, **({"hint": hint} if hint else {})}
        found["questions"] += 1
        result = await model.complete(TurnRequest(
            system_stable=_prompt("identify_game.md"), max_tokens=IDENTIFY_TOKENS, timeout_s=IDENTIFY_TIMEOUT_S,
            messages=[Msg("user", [{"type": "text", "text": json.dumps(context)}, _image(png)])]))
        answer = _json(result.text)
        answer = answer if isinstance(answer, dict) else {}
        if isinstance(answer.get("situation"), str):
            found["situation"] = answer["situation"][:300]
        named = answer.get("families") if isinstance(answer.get("families"), list) else []
        found["families"] = list(dict.fromkeys(name for name in named if name in known))[:MOST_CANDIDATES]
        if found["families"] or answer.get("screen") == "game":
            break
        if tried == tries:
            break
        # the person's words go with the question: where several games are offered, they say which one
        if not await recovery.attempt(surface, "game_not_shown",
                                      {"state": None, "recent": [], **({"hint": hint} if hint else {})}):
            # nothing to press is not nothing to come: a screen still loading is looked at again, unless the
            # model gave the game up
            if ((recovery.log[-1]["action"] or {}).get("kind")) == "give_up":
                break
            await asyncio.sleep(IDENTIFY_WAIT_S)
    if found["families"]:
        recovery.succeeded()
    found["inputs"] = recovery.summary()["inputs"]
    return found


# adapters made again in one game before giving up: a game shows a handful of new looks, not dozens
MAX_RELEARNS = 16
