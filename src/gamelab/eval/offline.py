"""Measure a model on screens kept from earlier games, with no site (PLAN §11, H1).

The four things the harness asks a model are asked again on what adapter stores kept (`Store.save`: each
site's adapter, its looks and what was read on them), so models can be compared by name, at the cost of their
questions only. No page or device is opened and no input reaches a game.

  .venv/bin/python -m gamelab.eval offline --vision-model gpt-6.1-sol --out out/offline/sol
  .venv/bin/python -m gamelab.eval offline --vision-model gpt-6.1-sol --writer-model gpt-6.1-terra \\
      --tasks write --site blockblast --out out/offline/terra-writes

  read      each site's first screen read with the board not known (as a game starts) and known, and the
            middle and last of its first `--looks` screens with it known (`Adaptation.label`). Compared with
            the state kept: whether it answered, whether it read the same, how many values (leaves) and how
            many of them differ, whether the rule model takes the reading (`estimate`), and on the first
            screen how well its board box overlaps the kept one (IoU)
  write     an adapter written from the site's first `--looks` screens, the last HELD_BACK kept back, as a
            game's first generation is (`Adaptation.make`): whether one was adopted, at which attempt, the
            check each attempt failed, whether it leaves cells to the host's samples ("look"), whether the
            adopted code also passes on every screen kept, its time and tokens. Every attempt is kept as
            `write/SITE/attempt-N.py` under `--out`
  identify  each site's first screen (its family expected) and the `--screens` ones, asked once which game
            this is (`adaptation.identify`, no input sent)
  recover   each `--screens` recovery screen put to `Recovery.attempt` once, with the reason play stopped
            there. What the model answered is recorded with what a person expected, not scored

The kept states are what the model of that game read and the rule model accepted, not a person's answer key:
a reading that differs from them may be the right one. The rows say so (`truth`).

`--screens FILE` is JSON; its pictures are named relative to the file:

  {"identify": [{"name": "...", "png": "a.png", "family": "match3"},       family null: no known game
                ...],
   "recover":  [{"name": "...", "png": "b.png", "family": "slide_merge", "reason": "board_not_found",
                 "expected": "a tap on the dialog's close button"}, ...]}

A recovery entry may give `recent`, the moves told to the model as played before (none unless given).

Questions go out AT_ONCE at a time. A question that fails (a timeout, a gateway's 504) is that row's error
and the rest go on. Each task's table is printed; everything, with every model answer and the tokens, is
written to `--out/offline.json`. The writing's time is the wall clock: the checks of one site's attempt run on
the loop the other questions wait on.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any, Awaitable

from .. import catalog
from ..adaptation import HELD_BACK, Adaptation, AdaptationFailed, Store, Trace, _board, _shape, adopt, identify
from ..contracts import NeedObservation, Observation
from ..model import Model, Recorded
from ..recovery import Recovery
from ..surface import DRAG, KEYS, TAP
from . import measure

# the stores looked in unless --store is given, under the repository (each where a stage's games kept theirs)
STORES = ("out/web/g5-adapters", "out/web/g7-adapters", "out/web/g8-adapters", "out/web/g9-adapters-2",
          "out/web/step4-adapters")
TASKS = ("read", "write", "identify", "recover")
# a game's first generation is written from this many screens (EXPLORE_MOVES moves and the screen after)
FIRST_LOOKS = 7
# questions out at one time
AT_ONCE = 4
# what a row's state is compared with
TRUTH = "kept: what that game's model read and the rule model accepted, not an answer key"
# characters of a model's text kept in a row (a situation, a problem)
SAID = 300


@dataclass
class Kept:
    """One site's kept adapter, as a store holds it."""
    name: str
    path: Path
    source: str
    meta: dict[str, Any]
    trace: Trace


def kept(stores: list[Path], sites: list[str] | None = None) -> list[Kept]:
    """Every adapter the stores keep, named by its site's folder (with the store's and the family's where two
    share it); with `sites`, those whose name or address holds one of them."""
    found, names = [], set()
    for store in stores:
        for path in Store(store).kept():
            read = Store.read(path)
            if read is None:
                continue
            name = path.parent.name
            if name in names:
                name = f"{store.name}/{name}/{path.name}"
            names.add(name)
            source, meta, trace = read
            if sites and not any(part in name or part in trace.site for part in sites):
                continue
            found.append(Kept(name, path, source, meta, trace))
    return found


def first(trace: Trace, looks: int = FIRST_LOOKS) -> Trace:
    """The trace as a game's first generation sees it: its first `looks` screens, the last HELD_BACK kept back
    and no move played from the last."""
    cut = [copy.deepcopy(look) for look in trace.looks[:looks]]
    for number, look in enumerate(cut):
        look.held = number >= len(cut) - HELD_BACK
    if cut:
        cut[-1].action = cut[-1].sent = None
    return Trace(trace.family_id, dict(trace.rules), trace.site, cut)


def leaves(value: Any, path: tuple[Any, ...] = ()) -> dict[tuple[Any, ...], Any]:
    """Every value of a state by where it is (an empty list counts as one)."""
    if isinstance(value, dict):
        return {found: leaf for key, part in value.items() for found, leaf in leaves(part, (*path, key)).items()}
    if isinstance(value, list):
        if not value:
            return {path: "[]"}
        return {found: leaf for index, part in enumerate(value) for found, leaf in leaves(part, (*path, index)).items()}
    return {path: value}


def iou(one: Any, other: list[int]) -> float:
    """How much two boxes [left, top, width, height] overlap: their common area over their joint one."""
    if not (isinstance(one, list) and len(one) == 4 and all(type(value) in (int, float) for value in one)):
        return 0.0
    width = min(one[0] + one[2], other[0] + other[2]) - max(one[0], other[0])
    height = min(one[1] + one[3], other[1] + other[3]) - max(one[1], other[1])
    common = max(0, width) * max(0, height)
    joint = one[2] * one[3] + other[2] * other[3] - common
    return round(common / joint, 3) if common and joint > 0 else 0.0


class Still:
    """A surface that shows one picture and changes for nothing: inputs sent to it are only kept."""
    name, reads_dom = "still", False
    kinds = frozenset({KEYS, TAP, DRAG})

    def __init__(self, png: bytes) -> None:
        self.png, self.sent = png, []

    async def capture(self, region: list[int] | None = None) -> bytes:
        return self.png

    async def address(self) -> str:
        return "still://"

    async def tap(self, x: float, y: float, duration_ms: int) -> None:
        self.sent.append({"kind": "tap", "x": x, "y": y})

    async def key(self, key: str) -> None:
        self.sent.append({"kind": "key", "key": key})

    async def drag(self, x0: float, y0: float, x1: float, y1: float, duration_ms: int) -> None:
        self.sent.append({"kind": "drag", "x0": x0, "y0": y0, "x1": x1, "y1": y1})

    async def release(self) -> None:
        pass


class Keeping(Adaptation):
    """An adaptation that keeps each adapter written, as `attempt-N.py` in `folder`."""

    def __init__(self, *args: Any, folder: Path, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.folder, self.written = folder, 0

    async def write(self, trace: Trace, previous: tuple[str, list[str]] | None = None) -> str:
        text = await super().write(trace, previous)
        self.written += 1
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"attempt-{self.written}.py").write_text(text, encoding="utf-8")
        return text


def _error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:SAID]


def _asked(recorded: Recorded) -> dict[str, Any]:
    """What one row's questions cost, and the ones that failed."""
    failed = [answer["error"][:SAID] for answer in recorded.answers if "error" in answer]
    return {"questions": len(recorded.answers), "tokens": recorded.tokens(), **({"model_errors": failed} if failed else {})}


# ── the tasks ─────────────────────────────────────────────────────────

async def read_one(model: Model, site: Kept, index: int, known: bool) -> dict[str, Any]:
    """One kept screen read by the model, compared with the state kept for it."""
    look, recorded = site.trace.looks[index], Recorded(model)
    row: dict[str, Any] = {"site": site.name, "family": site.trace.family_id, "look": index, "board_known": known,
                           "truth": "kept"}
    started = time.monotonic()
    try:
        tool = Adaptation(recorded, site.trace.family_id)
        family = catalog.family(site.trace.family_id).bind({"rules": site.trace.rules})
        shape = _shape(family, family.unread_state(tool.descriptor["scenes"][0]["id"])) if known else None
        answer = await tool.label(look.png, look.board if known else None, shape)
    except Exception as error:
        return {**row, "error": _error(error), **_asked(recorded)}
    row.update(seconds=round(time.monotonic() - started, 1), **_asked(recorded))
    if answer is None:
        return {**row, "answered": False}
    want, have = leaves(_board(look.state)), leaves(_board(answer["state"]))
    right = sum(path in have and have[path] == value for path, value in want.items())
    row.update(answered=True, exact=_board(answer["state"]) == _board(look.state), leaves=len(want),
               wrong=len(want) - right, unread=sum(path in have and have[path] is None for path in want))
    # as the loop takes a reading: a score read off the page is not the board's
    read = {**answer["state"], **({"score": None} if "score" in answer["state"] else {})}
    try:
        state = family.estimate(Observation("label", 0, "offline", 0, 0.0, "offline", public_facts={"state": read}))
        row["rule_valid"] = not (isinstance(state, NeedObservation) or bool(state.unknown_fields))
    except Exception:
        row["rule_valid"] = False
    if not known:
        row["board_iou"] = iou(answer.get("board"), look.board)
        chosen = tool._rules(answer.get("rules"))
        row["rules_agree"] = chosen is not None and all(
            site.trace.rules.get(name) == value for name, value in chosen.items() if value is not None)
    return row


async def write_one(writer: Model, site: Kept, looks: int, out: Path) -> dict[str, Any]:
    """An adapter written from the site's first screens, and checked again on all of them."""
    part, recorded = first(site.trace, looks), Recorded(writer)
    row: dict[str, Any] = {"site": site.name, "family": site.trace.family_id, "looks": len(part.looks)}
    if len(part.looks) <= HELD_BACK + 2:
        return {**row, "error": f"{len(part.looks)} looks kept: too few to write from"}
    folder = out / "write" / site.name.replace("/", "_")
    started = time.monotonic()
    try:
        tool = Keeping(recorded, site.trace.family_id, folder=folder)
        made = await tool.make(part)
    except AdaptationFailed as error:
        try:
            attempts = json.loads(error.detail)
        except ValueError:
            attempts = error.detail
        row.update(adopted=False, reason=error.reason, attempts=attempts)
    except Exception as error:
        row.update(adopted=False, error=_error(error))
    else:
        (folder / "adopted.py").write_text(made.source, encoding="utf-8")
        whole = adopt(made.source, copy.deepcopy(site.trace))
        row.update(adopted=True, generation=made.generations, attempts=made.report["attempts"],
                   uses_marks='"look"' in made.source, lines=len(made.source.splitlines()),
                   whole={"looks": len(site.trace.looks), "passed": whole["passed"], "failed_check": whole["failed_check"],
                          "transitions": whole["transitions"], "problems": [p[:SAID] for p in whole["problems"][:2]]})
    if isinstance(row.get("attempts"), list):
        row["checks"] = [attempt.get("failed_check") for attempt in row["attempts"]]
    return {**row, "seconds": round(time.monotonic() - started, 1), **_asked(recorded)}


async def identify_one(model: Model, name: str, png: bytes, expected: str | None) -> dict[str, Any]:
    """Which game one screen shows, asked once with nothing sent to it."""
    recorded = Recorded(model)
    row: dict[str, Any] = {"screen": name, "expected": expected}
    try:
        found = await identify(Still(png), recorded, Recovery(recorded, None, known=[]), tries=0)
    except Exception as error:
        return {**row, "error": _error(error), **_asked(recorded)}
    families = found["families"]
    return {**row, "families": families, "situation": found["situation"],
            "ok": families[:1] == [expected] if expected else families == [], **_asked(recorded)}


async def recover_one(model: Model, entry: dict[str, Any], png: bytes) -> dict[str, Any]:
    """What the model does about one screen play stopped on, as it answered."""
    recorded, surface = Recorded(model), Still(png)
    row: dict[str, Any] = {key: entry.get(key) for key in ("name", "family", "reason", "expected")}
    try:
        recovery = Recovery(recorded, entry.get("family"), known=[])
        await recovery.attempt(surface, entry["reason"], {"state": None, "recent": entry.get("recent", [])})
    except Exception as error:
        return {**row, "error": _error(error), **_asked(recorded)}
    said = recovery.log[-1]
    return {**row, "action": said["action"], "situation": said["situation"], "sent": surface.sent, **_asked(recorded)}


# ── the run ───────────────────────────────────────────────────────────

def read_looks(trace: Trace, looks: int) -> list[int]:
    """The screens read: the first, middle and last of the first `looks`."""
    count = min(looks, len(trace.looks))
    return sorted({0, (count - 1) // 2, count - 1}) if count else []


def screens(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    """The `--screens` file's entries, each with its picture read (`picture`)."""
    if path is None:
        return {"identify": [], "recover": []}
    listed = json.loads(path.read_text(encoding="utf-8"))
    return {task: [{**entry, "picture": (path.parent / entry["png"]).read_bytes()} for entry in listed.get(task, [])]
            for task in ("identify", "recover")}


async def evaluate(sites: list[Kept], shown: dict[str, list[dict[str, Any]]], tasks: list[str], model: Model,
                   writer: Model | None, *, looks: int, out: Path) -> dict[str, list[dict[str, Any]]]:
    """Every row of the tasks asked for, the questions sent AT_ONCE at a time."""
    jobs: dict[str, list[Awaitable[dict[str, Any]]]] = {task: [] for task in tasks}
    for site in sites:
        if "read" in tasks:
            for index in read_looks(site.trace, looks):
                jobs["read"] += [read_one(model, site, index, known) for known in ((False, True) if index == 0 else (True,))]
        if "write" in tasks:
            jobs["write"].append(write_one(writer or model, site, looks, out))
        if "identify" in tasks and site.trace.looks:
            jobs["identify"].append(identify_one(model, site.name, site.trace.looks[0].png, site.trace.family_id))
    if "identify" in tasks:
        jobs["identify"] += [identify_one(model, entry["name"], entry["picture"], entry.get("family"))
                             for entry in shown["identify"]]
    if "recover" in tasks:
        jobs["recover"] += [recover_one(model, entry, entry["picture"]) for entry in shown["recover"]]
    gate = asyncio.Semaphore(AT_ONCE)

    async def bounded(job: Awaitable[dict[str, Any]]) -> dict[str, Any]:
        async with gate:
            return await job
    rows = await asyncio.gather(*(bounded(job) for task in tasks for job in jobs[task]))
    found, at = {}, 0
    for task in tasks:
        found[task], at = list(rows[at:at + len(jobs[task])]), at + len(jobs[task])
    return found


# what each task's table shows: a heading and how the cell is made of a row
COLUMNS = {
    "read": [("site", "site"), ("look", "look"), ("known", "board_known"), ("answered", "answered"), ("exact", "exact"),
             ("leaves", "leaves"), ("wrong", "wrong"), ("unread", "unread"), ("rules ok", "rule_valid"),
             ("iou", "board_iou"), ("s", "seconds"), ("error", "error")],
    "write": [("site", "site"), ("adopted", "adopted"), ("gen", "generation"),
              ("checks", lambda row: ",".join(str(check) for check in row.get("checks", [])) or None),
              ("marks", "uses_marks"), ("all looks", lambda row: (row.get("whole") or {}).get("passed")),
              ("s", "seconds"), ("in", lambda row: row["tokens"]["input"]), ("out", lambda row: row["tokens"]["output"]),
              ("error", lambda row: row.get("error") or row.get("reason"))],
    "identify": [("screen", "screen"), ("expected", "expected"),
                 ("named", lambda row: ",".join(row.get("families") or []) or None), ("ok", "ok"), ("error", "error")],
    "recover": [("screen", "name"), ("reason", "reason"), ("action", lambda row: json.dumps(row.get("action"))),
                ("situation", lambda row: (row.get("situation") or "")[:60]), ("expected", "expected")],
}
# a cell no wider than this in the printed table
CELL = 48


def table(task: str, rows: list[dict[str, Any]]) -> str:
    columns = COLUMNS[task]

    def cell(row: dict[str, Any], how: Any) -> str:
        value = how(row) if callable(how) else row.get(how)
        return ("-" if value is None else str(value))[:CELL]
    lines = [[heading for heading, _ in columns], *([cell(row, how) for _, how in columns] for row in rows)]
    widths = [max(len(line[number]) for line in lines) for number in range(len(columns))]
    return "\n".join("  ".join(text.ljust(width) for text, width in zip(line, widths)).rstrip() for line in lines)


def _sum(*recorded: Recorded | None) -> dict[str, int]:
    counted = [one.tokens() for one in recorded if one is not None]
    return {side: sum(tokens[side] for tokens in counted) for side in ("input", "output")}


async def main(args: argparse.Namespace) -> int:
    if args.looks < 1:
        raise SystemExit("--looks must be positive")
    tasks = [task for task in TASKS if task in (args.tasks or TASKS)]
    stores = args.store or [measure.ROOT / store for store in STORES if (measure.ROOT / store).is_dir()]
    sites = kept(stores, args.site)
    shown = screens(args.screens)
    if not sites and not any(shown[task] for task in tasks if task in shown):
        raise SystemExit("nothing to ask about: no kept adapter in the stores (--store) and no --screens")
    client, vision = measure.vision_model(args)
    if vision is None:
        raise SystemExit("the questions go to a model: name one with --vision-model")
    writer_client, writer = measure.writer_model(args, vision, apart=True)
    try:
        found = await evaluate(sites, shown, tasks, vision, writer, looks=args.looks, out=args.out)
    finally:
        for opened in (client, writer_client):
            if opened is not None:
                await opened.close()
    tokens = {"vision": vision.tokens(), **({"writer": writer.tokens()} if writer else {}), "total": _sum(vision, writer)}
    report = {"revision": measure.revision(), "vision_model": args.vision_model,
              "writer_model": args.writer_model or args.vision_model, "truth": TRUTH,
              "stores": [str(store) for store in stores], "sites": [site.name for site in sites], "looks": args.looks,
              "args": {key: str(value) for key, value in vars(args).items()}, **found, "tokens": tokens,
              "model_answers": vision.answers + (writer.answers if writer else [])}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "offline.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
                                           encoding="utf-8")
    for task in tasks:
        print(f"\n{task}" + (f" (compared with the {TRUTH})" if task == "read" else ""))
        print(table(task, found[task]) if found[task] else "  nothing to ask")
    print(f"\ntokens {json.dumps(tokens)}\n{args.out / 'offline.json'}")
    return 0


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--store", type=Path, action="append",
                        help="an adapter store to ask about (repeatable; default: those of STORES that exist)")
    parser.add_argument("--screens", type=Path, help="a JSON list of screens to identify and recover from (see above)")
    measure.model_arguments(parser)
    parser.add_argument("--tasks", nargs="+", choices=TASKS, help="what to ask (default: all)")
    parser.add_argument("--site", action="append", help="only the kept sites whose name holds this (repeatable)")
    parser.add_argument("--looks", type=int, default=FIRST_LOOKS,
                        help="the screens a first generation is written from, the last two kept back")
    parser.add_argument("--out", type=Path, required=True, help="offline.json and every adapter written (out/ is not tracked)")
