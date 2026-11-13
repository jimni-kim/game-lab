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
from . import catalog, resources
from .adapters.generated import CELLS_UNREAD, FUNCTIONS, GeneratedAdapter, check_layout
from .contracts import NeedObservation, Observation
from .environments.screen import Surface
from .execution import TAP_MS
from .model import Model, Msg, TurnRequest
from .recovery import MAX_TRIES, Recovery
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
RUN_TOLERANCE = 6
MAX_RUNS = 120
# the controls a game can be explored with before any adapter exists: a key per direction, or a tap on the
# cell a move names (a drag's start and end are the family code's to place: not yet)
EXPLORED_WITH = ("keys", "tap")


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


def _shape(family: Any, state: dict[str, Any]) -> tuple[int, int] | None:
    """The rows and columns of the state's board, whatever the family calls it."""
    grid = next((value for value in state.values() if isinstance(value, list) and value and isinstance(value[0], list)), None)
    return (len(grid), len(grid[0])) if grid else None


def cell_box(board: list[int], shape: tuple[int, int], cell: list[int]) -> list[float]:
    """The box [left, top, width, height] of one cell of a board box divided evenly into `shape` cells."""
    left, top, width, height = board
    rows, cols = shape
    row, col = cell
    across, down = width / cols, height / rows
    return [left + col * across, top + row * down, across, down]


class Adaptation:
    def __init__(self, model: Model, family_id: str, *, policy: str | None = None, compute: int = 200,
                 recovery: Recovery | None = None) -> None:
        self.model, self.family_id = model, family_id
        # the model's way past a screen that shows no board (a notice over it), and the last board read
        self.recovery, self.last_state = recovery, None
        # the other captures of the screen last read (FRAMES)
        self.frames: list[bytes] = []
        # what each screen read said of the rule values a game may be played without knowing (`_told`)
        self.told: list[dict[str, Any]] = []
        # the first screen as read already (`probe`), for the exploring to start from
        self.first: tuple[bytes, dict[str, Any], list[int], dict[str, Any]] | None = None
        self.descriptor = catalog.descriptor(family_id)
        self.policy = policy or self.descriptor["default_policy"]
        self.compute = compute
        self.questions = 0
        # what the game is played with while exploring: the family's starting controls
        self.controls = self.descriptor.get("discovery", {}).get("controls")
        if not self.controls or self.controls["kind"] not in EXPLORED_WITH:
            raise AdaptationFailed("family_unsupported", "only games played with keys or taps can be explored yet")

    # ── explore ───────────────────────────────────────────────────────

    async def label(self, png: bytes, board: list[int] | None, shape: tuple[int, int] | None) -> dict[str, Any] | None:
        """What the model reads on one screen: `{"board", "rules", "state"}` or None when it shows no board
        or the answer is no answer. The rule model checks the state afterwards."""
        context = {"family": self.descriptor, **({"board": board} if board else {})}
        content = [{"type": "text", "text": json.dumps(context)}, _image(png)]
        if board and shape:
            content.append(_image(overlay(png, board, *shape)))
        self.questions += 1
        result = await self.model.complete(TurnRequest(system_stable=_prompt("label_screen.md"), max_tokens=LABEL_TOKENS,
                                                       timeout_s=LABEL_TIMEOUT_S, messages=[Msg("user", content)]))
        answer = _json(result.text)
        if not isinstance(answer, dict) or answer.get("screen") != "board" or not isinstance(answer.get("state"), dict):
            return None
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

        seen, frames = signature(png), []
        for _ in range(FRAMES):
            await asyncio.sleep(FRAME_GAP_S)
            frame = await surface.capture()
            if frame != png and _distance(seen, signature(frame)) <= FRAME_DISTANCE:
                frames.append(frame)
        return frames

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
                     accept: Any = None, retries: int = LABEL_RETRIES) -> tuple[bytes, dict[str, Any], list[int], dict[str, Any]]:
        """One screen read by the model into a state the rule model accepts whole (and `accept`, when given,
        takes as the last move's result); asked again on a fresh capture when it is not."""
        problem = "no_board"
        for _ in range(1 + retries):
            png = await surface.capture()
            size = _size(png)
            shape = None
            if board and rules is not None:
                family = catalog.family(self.family_id).bind({"rules": rules})
                shape = _shape(family, family.unread_state(self.descriptor["scenes"][0]["id"]))
            answer = await self.label(png, board, shape)
            if answer is None:
                problem = "no_board"
                await asyncio.sleep(SETTLE_S)
                continue
            found = answer.get("board") if board is None else board
            if check_layout({"board": found}, size) is None:
                problem = "board_off_screen"
                continue
            chosen = rules if rules is not None else self._rules(answer.get("rules"))
            if chosen is None:
                problem = "rules_refused"
                continue
            family = catalog.family(self.family_id).bind({"rules": chosen})
            # the board is what is learned here: a score read off the page (in the site's own counting) would
            # only make a right board look like a wrong move
            read = {**answer["state"], **({"score": None} if "score" in answer["state"] else {})}
            state = family.estimate(Observation("label", 0, "explore", 0, 0.0, "explore", public_facts={"state": read}))
            if isinstance(state, NeedObservation) or state.unknown_fields:
                problem = "state_refused"
                continue
            if accept is not None and not accept(state.payload):
                problem = "transition_refused"
                await asyncio.sleep(SETTLE_S)
                continue
            self.told.append(self._told(answer.get("rules")))
            return png, state.payload, found, chosen
        raise AdaptationFailed("label_failed", problem)

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

    def input_for(self, action: dict[str, Any], board: list[int], shape: tuple[int, int]) -> dict[str, Any]:
        """The input a move is played with before any adapter exists, as a generated adapter's `target()`
        would give it: the direction's key, or a tap at the middle of the cell the move names, placed on the
        board box the model gave."""
        if self.controls["kind"] == "keys":
            return {"kind": "key", "key": self.controls["keys"][action["direction"]]}
        left, top, width, height = cell_box(board, shape, action["cell"])
        return {"kind": "tap", "x": left + width / 2, "y": top + height / 2}

    async def _send(self, surface: Surface, sent: dict[str, Any]) -> None:
        if sent["kind"] == "key":
            await surface.key(sent["key"])
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
            sent = self.input_for(action, board, _shape(family, state))
            trace.looks.append(Look(png, state, board, action, sent, frames=self.frames))
            await self._send(surface, sent)
            await asyncio.sleep(SETTLE_S)
            before = state
            # the move's result must be one the rules allow from the state read before it: for a game whose
            # moves reveal what was hidden, `assess` is what the rules can say of it (the cells a move may open)
            png, state, _, _ = await self._read(surface, board, rules, accept=lambda after: family.model.assess(
                before, action, after, family.rules)["effect"] == "confirmed")
        trace.looks.append(Look(png, state, board, frames=self.frames))
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
        return Look(png, state, board, frames=self.frames)

    # ── generate and adopt ────────────────────────────────────────────

    def _context(self, trace: Trace, previous: tuple[str, list[str]] | None) -> dict[str, Any]:
        family = catalog.family(self.family_id)
        examples = [{"state": look.state, "board": look.board, "cells": cells(look), "action": look.action,
                     "sent": look.sent} for look in trace.train]
        return {"family": self.descriptor, "rules": trace.rules, "controls": self.controls,
                "rule_model": Path(family.model.__file__).read_text(encoding="utf-8"),
                "helpers": (Path(__file__).parent / "sandbox" / "helpers.py").read_text(encoding="utf-8"),
                "examples": examples, "probes": probes(trace.train[0]),
                **({"previous": {"module": previous[0], "problems": previous[1]}} if previous else {})}

    async def write(self, trace: Trace, previous: tuple[str, list[str]] | None = None) -> str:
        content = [{"type": "text", "text": json.dumps(self._context(trace, previous))},
                   *(_image(look.png) for look in trace.train)]
        self.questions += 1
        result = await self.model.complete(TurnRequest(system_stable=_prompt("generate_adapter.md"),
                                                       max_tokens=GENERATE_TOKENS, timeout_s=GENERATE_TIMEOUT_S,
                                                       messages=[Msg("user", content)]))
        return result.text or ""

    async def make(self, trace: Trace, previous: tuple[str, list[str]] | None = None) -> Made:
        """An adapter that passes every check on the trace, written in at most MAX_GENERATIONS attempts;
        `previous` is an earlier adapter and what is wrong with it now, to write from."""
        started, before = time.monotonic(), self.questions
        attempts = []
        for generation in range(1, MAX_GENERATIONS + 1):
            source = await self.write(trace, previous)
            report = adopt(source, trace)
            attempts.append({key: report[key] for key in ("passed", "failed_check", "transitions")})
            if report["passed"]:
                return Made(report["source"], generation, False, {**report, "attempts": attempts},
                            time.monotonic() - started, self.questions - before)
            previous = (report["source"], report["problems"])
        raise AdaptationFailed("no_adapter_passed", json.dumps(attempts))


def _size(png: bytes) -> list[int]:
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        return list(image.size)


def cells(look: Look) -> list[dict[str, Any]]:
    """For each cell of the look's board (at its approximate box): the value read there and the colours most
    common around its middle and its upper part, where a tile's face shows beside its numeral."""
    from PIL import Image

    grid = next(value for value in look.state.values() if isinstance(value, list) and value and isinstance(value[0], list))
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
        states: list[dict[str, Any] | None] = []
        for index, look in enumerate(trace.looks):
            measured = adapter.measure(look.png)
            if isinstance(measured, NeedObservation):
                fail("measure", index, adapter.last_error or "measure() returned no board box that lies on the screen")
                states.append(None)
                continue
            read = adapter.read(Observation(f"look-{index}", index, "adopt", 0, 0.0, "adopt"), look.png)
            if isinstance(read, NeedObservation):
                detail = (f"read() left cells unread: {json.dumps(_board(adapter.partial))}" if read.reason == CELLS_UNREAD
                          else adapter.last_error or f"read() gave a state the rules refuse ({read.reason})")
                adapter.last_error = None
                fail("read", index, f"{detail}; expected {json.dumps(_board(look.state))}")
                states.append(None)
                continue
            states.append(read.payload)
            if _board(read.payload) != _board(look.state):
                fail("state", index, f"read() gave {json.dumps(_board(read.payload))}, expected {json.dumps(_board(look.state))}")
            for frame in look.frames:
                again = adapter.read(Observation(f"look-{index}-frame", index, "adopt", 0, 0.0, "adopt"), frame) \
                    if not isinstance(adapter.measure(frame), NeedObservation) else None
                if again is None or isinstance(again, NeedObservation) or _board(again.payload) != _board(look.state):
                    got = "no board" if again is None else (
                        f"cells unread {json.dumps(_board(adapter.partial))}" if isinstance(again, NeedObservation)
                        else json.dumps(_board(again.payload)))
                    adapter.last_error = None
                    fail("frames", index, f"on another capture of the same screen {FRAME_GAP_S}s later (nothing played "
                                          f"between) the code gave {got}: read what stays the same while the screen animates")
                    break
            if look.action is not None:
                try:
                    asked = adapter.sandbox.call("target", action=look.action, state=read.payload, layout=adapter.layout)
                    adapter.checked_input(asked)
                except (SandboxError, ValueError) as error:
                    asked = f"{type(error).__name__}: {error}"
                if not _same_input(asked, look, adapter.layout["board"]):
                    fail("target", index, f"target() gave {json.dumps(asked)} for {json.dumps(look.action)}, expected {json.dumps(look.sent)}"
                                          + (" (a tap anywhere inside that cell)" if look.sent["kind"] == "tap" else ""))
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
    if look.sent["kind"] != "tap" or not isinstance(asked, dict) or asked.get("kind") != "tap":
        return asked == look.sent
    left, top, width, height = cell_box(board, _shape(None, look.state), look.action["cell"])
    return left <= asked["x"] < left + width and top <= asked["y"] < top + height


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
        path = self._path(site, family_id)
        if not (path / "adapter.py").is_file():
            return None
        meta = json.loads((path / "adapter.json").read_text(encoding="utf-8"))
        looks = [Look((path / f"look-{index}.png").read_bytes(), **{**look, "frames": [
                     (path / f"look-{index}-frame-{number}.png").read_bytes() for number in range(look.get("frames", 0))]})
                 for index, look in enumerate(meta["trace"])]
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
                       "held": look.held, "frames": len(look.frames)} for look in trace.looks]}, indent=2) + "\n",
            encoding="utf-8")
        for index, look in enumerate(trace.looks):
            (path / f"look-{index}.png").write_bytes(look.png)
            for number, frame in enumerate(look.frames):
                (path / f"look-{index}-frame-{number}.png").write_bytes(frame)
        return path

    def recoveries(self, site: str, family_id: str) -> list[dict[str, Any]]:
        """The inputs that got past this site's screens before (`Recovery.known`)."""
        path = self._path(site, family_id) / "recoveries.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []

    def save_recoveries(self, site: str, family_id: str, known: list[dict[str, Any]]) -> None:
        path = self._path(site, family_id)
        path.mkdir(parents=True, exist_ok=True)
        (path / "recoveries.json").write_text(json.dumps(known) + "\n", encoding="utf-8")


class Adaptive:
    """One site's game read by generated code for as long as it is played: the adapter kept for the site or
    made now, and made again from one more look whenever play stops at a screen it cannot read (a value the
    game had not shown yet)."""

    def __init__(self, surface: Surface, model: Model, family_id: str, *, store: Store | None = None,
                 policy: str | None = None, compute: int = 200, fresh: bool = False,
                 accept_consent: bool = False) -> None:
        self.surface, self.family_id, self.store, self.fresh = surface, family_id, store, fresh
        # one way past the site's screens for the whole game: while exploring, playing and learning again
        self.recovery = Recovery(model, family_id, accept_consent=accept_consent)
        self.adaptation = Adaptation(model, family_id, policy=policy, compute=compute, recovery=self.recovery)
        self.trace: Trace | None = None
        self.made: Made | None = None
        self.adapter: GeneratedAdapter | None = None
        self.relearned = 0
        self.seconds = 0.0

    def _adapter(self) -> GeneratedAdapter:
        if self.adapter is not None:
            self.adapter.close()
        self.adapter = GeneratedAdapter(self.family_id, self.made.source, rules=self.trace.rules, site=self.trace.site)
        return self.adapter

    async def start(self) -> GeneratedAdapter:
        """The adapter to play by. Making one plays the game's first moves on the surface."""
        started = time.monotonic()
        site = await self.surface.address()
        if self.store is not None:
            self.recovery.known = self.store.recoveries(site, self.family_id)
        kept = None if self.store is None or self.fresh else self.store.load(site, self.family_id)
        if kept is not None:
            source, meta, self.trace = kept
            self.made = Made(source, meta["generations"], True, meta["report"])
        else:
            self.trace = await self.adaptation.explore(self.surface)
            self.made = await self.adaptation.make(self.trace)
            if self.store is not None:
                self.store.save(self.trace, self.made)
        self.seconds += time.monotonic() - started
        return self._adapter()

    async def relearn(self, misread: bool = False) -> GeneratedAdapter | None:
        """After play stopped at a screen the adapter left unread: the model reads that screen, and when the
        adapter cannot read it as recorded a new one is written from every look so far. None when the screen
        shows no board the rules accept (nothing to learn: the game is not readable here)."""
        started = time.monotonic()
        try:
            # `misread`: the adapter read the screen whole and wrongly, so nothing of its reading is held to
            look = await self.adaptation.relabel(self.surface, self.trace, None if misread else self.adapter.partial)
            if look is None:
                return None
            self.trace.looks.append(look)
            self.relearned += 1
            report = adopt(self.made.source, self.trace)
            if not report["passed"]:
                made = await self.adaptation.make(self.trace, (self.made.source, report["problems"]))
                self.made = Made(made.source, self.made.generations + made.generations, False, made.report)
            if self.store is not None:
                self.store.save(self.trace, self.made)
            return self._adapter()
        finally:
            self.seconds += time.monotonic() - started

    def summary(self) -> dict[str, Any]:
        return {"reused": self.made.reused if self.made else None, "generations": self.made.generations if self.made else 0,
                "relearned": self.relearned, "looks": len(self.trace.looks) if self.trace else 0,
                "questions": self.adaptation.questions, "seconds": round(self.seconds, 1),
                "recovery": self.recovery.summary(),
                "digest": self.adapter.digest if self.adapter else None,
                "calls": self.adapter.calls if self.adapter else None}

    def close(self) -> None:
        if self.adapter is not None:
            self.adapter.close()
        if self.store is not None and self.trace is not None and self.recovery.known:
            self.store.save_recoveries(self.trace.site, self.family_id, self.recovery.known)


# questions asked and inputs sent to reach a board before a game is given up as not shown
IDENTIFY_TOKENS, IDENTIFY_TIMEOUT_S = 1000, 60.0
MOST_CANDIDATES = 3


def explorable() -> list[str]:
    """The families a game can be recognised as: those whose first moves can be played before an adapter exists."""
    return [family_id for family_id in sorted(catalog.MODELS)
            if catalog.descriptor(family_id).get("discovery", {}).get("controls", {}).get("kind") in EXPLORED_WITH]


async def identify(surface: Surface, model: Model, recovery: Recovery, *, hint: str | None = None) -> dict[str, Any]:
    """Which families the game on the screen may be, as a model sees it (PLAN §10, game identification):
    `{"families": [ids, likeliest first], "questions", "situation"}`. A screen that shows no game in play is
    not the end of it: the model gets past it with inputs of its own, and the screen is asked about again.
    The answer is a guess; the caller checks it by reading the board and playing moves by the family's rules."""
    listed = [{key: descriptor[key] for key in ("id", "summary", "board", "actions")}
              for descriptor in map(catalog.descriptor, explorable())]
    known = {family["id"] for family in listed}
    found: dict[str, Any] = {"families": [], "questions": 0, "situation": None}
    for tries in range(MAX_TRIES + 1):
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
        if tries == MAX_TRIES or not await recovery.attempt(surface, "game_not_shown", {"state": None, "recent": []}):
            break
    if found["families"]:
        recovery.succeeded()
    found["inputs"] = recovery.summary()["inputs"]
    return found


async def adapter_for(surface: Surface, model: Model, family_id: str, *, store: Store | None = None,
                      policy: str | None = None, compute: int = 200, fresh: bool = False
                      ) -> tuple[GeneratedAdapter, Made]:
    """The adapter this site's game is played by: the one kept for it, else one made now from its first moves
    (which are played on the surface) and kept. Either way its first moves in play are trial moves."""
    adaptive = Adaptive(surface, model, family_id, store=store, policy=policy, compute=compute, fresh=fresh)
    adapter = await adaptive.start()
    adaptive.made.questions = adaptive.adaptation.questions
    return adapter, adaptive.made


# a game's endings a new look at the screen may get past: the adapter left the screen unread, or its trial
# moves did not do what the rules predict
RELEARN_REASONS = ("unreadable", "cells_unread", "profile_unconfirmed", "board_not_found", "surface_unsettled")
# moves that did not do what the rules predict on a board read whole: the reading itself may be wrong (a value
# the adapter takes for another), which one more look read by the model shows
MISREAD_REASONS = ("inconsistent", "unknown", "absent")
# adapters made again in one game before giving up: a game shows a handful of new looks, not dozens
MAX_RELEARNS = 16


async def _begin(surface: Surface, model: Model, family_id: str | None, make: Any, store: Store | None,
                 fresh: bool, hint: str | None) -> tuple[Adaptive | None, GeneratedAdapter | None, dict[str, Any]]:
    """The game's `Adaptive` and its adapter, for the family given or, with none given, the one the game is
    found to be: the family a kept adapter of this site names, else each family a model names for the screen,
    likeliest first, taken when its board is read from the screen and its first moves do what its rules say.
    Also what was found on the way (`identified`, `failed`); the adapter is None when none could be made."""
    found: dict[str, Any] = {}
    candidates = [family_id]
    if family_id is None:
        site = await surface.address()
        kept = [] if store is None or fresh else [name for name in explorable() if store.load(site, name) is not None]
        if kept:
            candidates, found["identified"] = kept[:1], {"family": kept[0], "source": "kept", "questions": 0}
        else:
            asked = await identify(surface, model, Recovery(model, None, known=[]), hint=hint)
            candidates = asked["families"]
            found["identified"] = {"family": None, "source": "model", "candidates": candidates, "tried": [],
                                   "questions": asked["questions"], "inputs": asked["inputs"],
                                   "situation": asked["situation"]}
            if not candidates:
                return None, None, {**found, "failed": "game_not_identified", "detail": asked["situation"] or ""}
    adaptive, failure = None, AdaptationFailed("game_not_identified")
    for candidate in candidates:
        if adaptive is not None:
            adaptive.close()
        adaptive = make(candidate)
        asked = found.get("identified", {}).get("source") == "model"
        try:
            if asked:
                found["identified"]["tried"].append(candidate)
                # a family the screen's board cannot be read as is not this game: the next one is tried
                if not await adaptive.adaptation.probe(surface):
                    failure = AdaptationFailed("game_not_identified", f"no {candidate} board was read from the screen")
                    continue
            adapter = await adaptive.start()
        except Exception as error:
            # a model that could not be reached while the adapter was made ends the game too, with its record
            if not isinstance(error, AdaptationFailed):
                log.exception("adaptation failed")
                error = AdaptationFailed("model_failed", f"{type(error).__name__}: {error}")
            failure = error
            # first moves that did not do what this family's rules say: the game may be another's
            if asked and error.reason == "label_failed":
                continue
            break
        if "identified" in found:
            found["identified"]["family"] = candidate
        return adaptive, adapter, found
    return adaptive, None, {**found, "failed": failure.reason, "detail": failure.detail[:2000]}


async def play_adaptive(surface: Surface, model: Model, family_id: str | None, *, goal: Any, budget: Any,
                        store: Store | None = None, policy: str | None = None, fresh: bool = False,
                        hint: str | None = None, **options: Any) -> tuple[Any, dict[str, Any]]:
    """One game read by generated code from its first screen to its end: `play` by the site's adapter, and
    each time it stops at a screen the adapter cannot read, one more look learned and play taken up again
    with what is left of the budget. Returns the whole game as one `Run` and how the adapter was made.
    `family_id` None: nobody says which game this is, and it is found out from the screen (`_begin`); `hint`
    is what the person said of it, if anything."""
    from .contracts import Budget, GoalSpec, Outcome
    from .play import Run, play
    from .session import ended

    def make(family: str) -> Adaptive:
        return Adaptive(surface, model, family, store=store, policy=policy, compute=budget.compute, fresh=fresh,
                        accept_consent=options.get("accept_consent", False))

    started = time.monotonic()
    whole = Run(record={})
    used = confirmed = 0
    adaptive = None
    try:
        try:
            adaptive, adapter, found = await _begin(surface, model, family_id, make, store, fresh, hint)
        except Exception as error:
            log.exception("identification failed")
            adapter, found = None, {"failed": "model_failed", "detail": f"{type(error).__name__}: {error}"[:2000]}
        if adapter is None:
            reason = found["failed"] if found["failed"] == "game_not_identified" else f"adaptation_{found['failed']}"
            whole.record = ended(Outcome.UNSUPPORTED, reason, family_id=adaptive.family_id if adaptive else family_id)
            whole.recoveries = list(adaptive.recovery.log) if adaptive else []
            return whole, {**(adaptive.summary() if adaptive else {}), **found}
        family_id = adaptive.family_id
        stalled = 0
        while True:
            left = None if budget.max_seconds is None else budget.max_seconds - (time.monotonic() - started)
            target = goal.target_value - confirmed if goal.objective == "actions" else goal.target_value
            run = await play(surface, adapter=adapter, policy=policy, model=model, recovery=adaptive.recovery,
                             goal=GoalSpec(goal.objective, target, goal.scope, goal.stop_conditions, goal.allowed_meta_actions),
                             budget=Budget(budget.max_actions - used, budget.compute, left), **options)
            record = run.record
            used += record.get("actions_used", 0)
            confirmed += record.get("confirmed_actions", 0)
            whole.moves += [{**move, "round": adaptive.relearned} for move in run.moves]
            whole.looks += run.looks
            whole.recoveries = list(adaptive.recovery.log)
            whole.geometry, whole.vision, whole.last_screen = run.geometry, run.vision, run.last_screen
            whole.record = {**record, "actions_used": used, "confirmed_actions": confirmed}
            stalled = 0 if record.get("confirmed_actions") else stalled + 1
            if (record.get("outcome") not in ("needs_recovery", "unsupported") or record.get("reason") not in (*RELEARN_REASONS, *MISREAD_REASONS)
                    or adaptive.relearned >= MAX_RELEARNS or stalled >= 2 or used >= budget.max_actions
                    or (left is not None and left <= 0)):
                break
            try:
                adapter = await adaptive.relearn(misread=record.get("reason") in MISREAD_REASONS)
            except AdaptationFailed as error:
                return whole, {**adaptive.summary(), **found, "failed": error.reason, "detail": error.detail[:2000]}
            if adapter is None:
                break
        return whole, {**adaptive.summary(), **found}
    finally:
        if adaptive is not None:
            adaptive.close()
