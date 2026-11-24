"""What every screen evaluation shares: the run's arguments, the watcher that records each input, the
figures made of a game, the files written, and the lock that keeps two runs off one device."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import replace
import fcntl
import json
import os
import re
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .. import catalog, checker, model, work
from ..contracts import META_ACTIONS, OBJECTIVES, Budget, GoalSpec, Scope
from ..run import Run

ROOT = Path(__file__).resolve().parents[3]
# the light model asked about single cells unless a run names another (PLAN §20, X1)
LIGHT_MODEL = "gpt-6-luna"


def locks_in(environ: Any) -> Path:
    """Where the devices' locks are kept, one file a device, held by the run that moves it (`lock`): GAMELAB_LOCKS,
    else a folder of the person's own. Not a checkout's: a session measuring from another worktree and the demo
    served from this one play on the same emulator (PLAN §35), and a lock kept under each checkout's own out/ let
    both move it at once."""
    return Path(environ.get("GAMELAB_LOCKS") or Path.home() / ".gamelab" / "lock")


LOCKS = locks_in(os.environ)
# a progress line every this many inputs, so a long game can be followed while it runs
PROGRESS_EVERY = 25


def revision() -> str:
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True)
        return head.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def game_arguments(parser: argparse.ArgumentParser) -> None:
    """The arguments every screen evaluation takes: what to play for, with what budget and which model."""
    parser.add_argument("--games", type=int, default=1)
    parser.add_argument("--objective", choices=OBJECTIVES, default="play_until_end",
                        help="levels: --target levels cleared one after another, with nothing started again between them; "
                             "until_level: levels cleared up to the one the game numbers --target on its screen")
    parser.add_argument("--target", type=int)
    parser.add_argument("--stop", action="append", default=[], metavar="CONDITION",
                        help="stop and hand the work back when this is met (repeatable): 'lost' at a level or game lost, "
                             "anything else in words, told to the models that look at the screen")
    parser.add_argument("--choices", metavar="WORDS",
                        help="how a story's choices are to be made, in words (PLAN §27; it allows nothing by itself: "
                             "spending coins is `--allow currency`); without it a choice that "
                             "cannot be taken back is not made and the work is handed back there")
    parser.add_argument("--allow", action="append", default=[], choices=sorted(META_ACTIONS),
                        help="a thing beside the board the work may use (repeatable); none unless given")
    parser.add_argument("--resume", action="store_true",
                        help="go on from the screen as it stands: nothing is started again before the first game, "
                             "and later games go on from where the last one stopped")
    parser.add_argument("--game", metavar="NAME",
                        help="the one game of several the page or app holds: what is kept is kept by it too (a request "
                             "in words names it itself)")
    parser.add_argument("--task", metavar="NAME",
                        help="an errand beside the levels, by a plain name ('daily_reward'): its steps are kept as a "
                             "routine of their own and the game ends when it is done (a request in words names it itself)")
    parser.add_argument("--way", choices=("direct", "made"),
                        help="for measuring one way of playing against another on the same game (PLAN §26, session G): "
                             "direct: no family is looked for, the model plays with its own inputs; made: no registered "
                             "family, and a family is made from the model's description even when it says the game is "
                             "not one for written rules. The harness's own choice unless given")
    parser.add_argument("--no-research", action="store_true",
                        help="do not look a game no family is known for up before its first move (PLAN §27 M8): no "
                             "store page, encyclopedia or help screens; for measuring with and without it (E4)")
    parser.add_argument("--no-review", action="store_true",
                        help="do not look back over a work the model played by its own inputs once it ends (PLAN §27 "
                             "M11): no lessons are written for the next work")
    parser.add_argument("--policy")
    parser.add_argument("--compute", type=int, required=True, help="evaluated boards per decision")
    parser.add_argument("--max-actions", type=int, default=3000)
    parser.add_argument("--max-seconds", type=float, default=7200)
    parser.add_argument("--max-input-tokens", type=int, default=3_000_000,
                        help="the tokens one game's model questions may send together (0: no limit)")
    parser.add_argument("--out", type=Path, required=True, help="results and screenshots (out/ is not tracked)")
    model_arguments(parser)
    parser.add_argument("--accept-consent", action="store_true",
                        help="agree to a site's terms when it offers no refusal (else the game stops there)")


def model_arguments(parser: argparse.ArgumentParser) -> None:
    """The models asked and where: one that reads the game and gets play past screens, and one that writes adapters."""
    parser.add_argument("--vision-model", default=model.default_model(),
                        help=f"the model that reads the game, writes its adapter and gets play past screens ({model.MODEL_VARIABLE} unless given)")
    parser.add_argument("--writer-model",
                        help="another model to write the adapters (the --vision-model unless given)")
    parser.add_argument("--light-model", default=LIGHT_MODEL,
                        help="a light model that names single cells cut out of the screen when a family's reader and "
                             "the rules cannot (PLAN §20); 'none': such cells are the --vision-model's")
    parser.add_argument("--model-base", default=model.api_base(),
                        help=f"an OpenAI-compatible API's base URL ({model.BASE_VARIABLE}, else {model.DEFAULT_BASE})")


def goal_and_budget(args: argparse.Namespace) -> tuple[GoalSpec, Budget]:
    """The run's goal and budget: the goal a request in words gave (`args.asked`) when there is one, held to
    `--resume` when it is given."""
    asked = getattr(args, "asked", None)
    goal = asked["goal"] if asked else GoalSpec(
        args.objective, args.target, scope=Scope.NEW_GAME, stop_conditions=tuple(getattr(args, "stop", None) or ()),
        allowed_meta_actions=frozenset(getattr(args, "allow", None) or ()), choices=getattr(args, "choices", None) or None)
    if getattr(args, "resume", False):
        goal = replace(goal, scope=Scope.CURRENT_GAME)
    return (goal,
            Budget(max_actions=args.max_actions, compute=args.compute, max_seconds=args.max_seconds,
                   max_input_tokens=getattr(args, "max_input_tokens", None) or None))


def game_within(args: argparse.Namespace) -> str | None:
    """The one game of several a page or app holds, as the request in words named it, else as `--game` did."""
    return (getattr(args, "asked", None) or {}).get("within") or getattr(args, "game", None)


def task_named(args: argparse.Namespace) -> str | None:
    """The errand beside the levels the request in words named, else the one `--task` did."""
    return (getattr(args, "asked", None) or {}).get("task") or getattr(args, "task", None)


def way_named(args: argparse.Namespace) -> str | None:
    """The one way of playing a measurement holds the harness to (`--way`), else none: its own choice."""
    return getattr(args, "way", None)


def researched(args: argparse.Namespace) -> bool:
    """Whether a game no family is known for is looked up before its first move (`research`)."""
    return not getattr(args, "no_research", False)


def reviewed(args: argparse.Namespace) -> bool:
    """Whether a work the model played by its own inputs is looked back on once it ends (`review`)."""
    return not getattr(args, "no_review", False)


def task_waits(args: argparse.Namespace) -> bool:
    """Whether the request in words asked for an errand the game does not offer yet to be waited for."""
    return bool((getattr(args, "asked", None) or {}).get("wait"))


def vision_model(args: argparse.Namespace) -> tuple[model.ChatClient | None, model.Recorded | None]:
    """The model a game is played with and the recorder around it; neither when no model is named."""
    if not args.vision_model:
        return None, None
    key = model.api_key()
    if not key:
        raise SystemExit(f"asking {args.vision_model} needs a key: set {model.KEY_VARIABLE}")
    client = model.ChatClient(args.vision_model, key, base=args.model_base)
    return client, model.Recorded(client, model.prompts(catalog.FRAGMENT / "prompts"))


def light_model(args: argparse.Namespace) -> Any:
    """The light model of a run (`--light-model`), as what names single cells (`checker.Chat`): made once a run
    and kept on its arguments. None when the run names none or has no key."""
    if not hasattr(args, "_light"):
        name, key = getattr(args, "light_model", None), model.api_key()
        args._light = checker.Chat(name, key, base=args.model_base) if name and name != "none" and key else None
    return args._light


async def close_light(args: argparse.Namespace) -> None:
    if getattr(args, "_light", None) is not None:
        await args._light.close()
        args._light = None


def writer_model(args: argparse.Namespace, vision: model.Recorded | None, *,
                 apart: bool = False) -> tuple[model.ChatClient | None, model.Recorded | None]:
    """The model that writes adapters, when `--writer-model` names another than `--vision-model`, and the
    recorder around it: its questions are kept with the reader's (`vision.answers`), or in a list of their own
    with `apart`. Neither when it is the same model (the reader writes too)."""
    if not getattr(args, "writer_model", None) or args.writer_model == args.vision_model:
        return None, None
    key = model.api_key()
    if not key:
        raise SystemExit(f"asking {args.writer_model} needs a key: set {model.KEY_VARIABLE}")
    client = model.ChatClient(args.writer_model, key, base=args.model_base)
    return client, model.Recorded(client, model.prompts(catalog.FRAGMENT / "prompts"),
                                  answers=None if apart or vision is None else vision.answers)


class Recorder:
    """Watches one game: each look and each input with when it was sent, and what `truth` (the site's
    answer key, when it has one) showed right before it."""

    def __init__(self, name: str, *, truth: Callable[[], Awaitable[dict[str, Any] | None]] | None = None,
                 after: Callable[[int], Awaitable[None]] | None = None,
                 during: Callable[[int], None] | None = None) -> None:
        self.name, self.truth, self.after, self.during = name, truth, after, during
        self.inputs: list[dict[str, Any]] = []
        self.shots = self.shot_bytes = 0
        self._open: dict[str, Any] | None = None

    # what the agent's loop tells (`agent.Watch`)
    async def seen(self, png: bytes) -> None:
        self.shots += 1
        self.shot_bytes += len(png)

    async def sending(self, number: int, action: dict[str, Any], state: dict[str, Any], sent: dict[str, Any],
                      observed_s: float) -> None:
        self._open = {"tool": sent["kind"], "arguments": sent, "action_id": f"a{number}", "kind": action.get("kind"),
                      "planned_on": state, "truth": await self.truth() if self.truth else None,
                      "observed_s": observed_s, "sent_s": time.monotonic()}
        if self.during is not None:
            self.during(len(self.inputs) + 1)

    async def sent(self) -> None:
        record, self._open = self._open, None
        self.inputs.append({**record, "delivery": "delivered", "done_s": time.monotonic()})
        if len(self.inputs) % PROGRESS_EVERY == 0:
            truth = record["truth"] or {}
            print(json.dumps({"game": self.name, "inputs": len(self.inputs), "shots": self.shots,
                              "seconds": round(record["sent_s"] - self.inputs[0]["sent_s"], 1),
                              "max_tile": max(map(max, truth["grid"])) if truth.get("grid") else None,
                              "score": truth.get("score")}), flush=True)
        if self.after is not None:
            await self.after(len(self.inputs))


def measure(run: Run, recorder: Recorder, final: dict[str, Any] | None) -> dict[str, Any]:
    moves = run.moves
    # effect and delivery share the name "unknown": each is counted under its own field
    count = {name: {value: sum(m[name] == value for m in moves) for value in values} for name, values in
             (("effect", ("confirmed", "absent", "inconsistent", "unknown")), ("delivery", ("delivered", "rejected", "unknown")))}
    # a decision is misread when the state the plan was made on differs from the page right before the input
    judged = [i for i in recorder.inputs if i["truth"] is not None and i["planned_on"] is not None]
    misread = sum(i["planned_on"].get("grid") != i["truth"]["grid"] for i in judged)
    # an intent carried out more than once
    carried = [i["action_id"] for i in recorder.inputs if i["delivery"] in ("delivered", "unknown")]
    # (in a row: a game played in rounds, each by a newer adapter, numbers every round's moves from one)
    resent = sum(one == other for one, other in zip(carried, carried[1:]))
    cycles = [b["done_s"] - a["done_s"] for a, b in zip(recorder.inputs, recorder.inputs[1:])]
    # from the capture the input was decided on to the input completed; the cycle above also holds the
    # wait for the previous move to settle
    latency = [i["done_s"] - i["observed_s"] for i in recorder.inputs if i["observed_s"] is not None]
    return {"moves": {"recorded": len(moves), **count}, "resent": resent,
            "decisions_checked": len(judged), "decisions_misread": misread,
            "recovery": {"stuck": len(run.recoveries), "inputs": sum(r["changed"] is not None for r in run.recoveries),
                         "replayed": sum(r["source"] == "replay" for r in run.recoveries),
                         "restored": sum(bool(r.get("restored")) for r in run.recoveries)},
            "shots": recorder.shots, "shots_per_move": round(recorder.shots / max(len(recorder.inputs), 1), 2),
            "png_kib_per_shot": round(recorder.shot_bytes / max(recorder.shots, 1) / 1024, 1),
            "seconds_per_move": _spread(cycles), "seconds_observed_to_input": _spread(latency), "final": final}


def _spread(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {"mean": round(statistics.fmean(values), 3), "p95": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 3)}


def result(name: str, run: Run, recorder: Recorder, recorded: model.Recorded | None, *, seconds: float,
           final: dict[str, Any] | None = None, **more: Any) -> dict[str, Any]:
    record = run.record
    return {"name": name, "outcome": record.get("outcome"), "reason": record.get("reason"),
            "actions_used": record.get("actions_used"), "confirmed_actions": record.get("confirmed_actions"),
            "seconds": round(seconds, 1), "model_questions": (run.vision or {}).get("asked", 0),
            **({"model_tokens": recorded.tokens()} if recorded else {}), **more, **measure(run, recorder, final)}


def handed(adapted: dict[str, Any]) -> dict[str, Any]:
    """A game's `result` fields from what its work handed back: how it was read, and the work's own account
    (`work.play`) beside it, in a line for a person too."""
    account = adapted.get("work")
    return {"adaptation": {key: value for key, value in adapted.items() if key != "work"},
            **({"work": account, "told": work.told(account)} if account else {})}


def write_game(out: Path, name: str, summary: dict[str, Any], request: dict[str, Any], run: Run, recorder: Recorder,
               answers: list[dict[str, Any]], screen: bytes | None) -> None:
    """One game's files: `NAME.json` (its figures, request, record, every move and input, the model's answers),
    `NAME-final.png`, the screen it ended on, and each screen the family's reader gave up on as
    `NAME-unread-pP-aN-WHY.png` (the part of the work, the input it was at, why), listed in the JSON (`unread`)."""
    out.mkdir(parents=True, exist_ok=True)
    unread = []
    for kept in run.screens:
        file = f"{name}-unread-p{kept.get('part', 1)}-a{kept['at_action']}-{kept['why']}.png"
        (out / file).write_bytes(kept["png"])
        unread.append({**{key: value for key, value in kept.items() if key != "png"}, "file": file})
    (out / f"{name}.json").write_text(json.dumps({
        "result": summary, "request": request, "record": run.record, "geometry": run.geometry, "vision": run.vision,
        "recoveries": run.recoveries, "unread": unread, "model_answers": answers, "moves": run.moves,
        "inputs": recorder.inputs}, indent=2, default=str) + "\n", encoding="utf-8")
    if screen is not None:
        (out / f"{name}-final.png").write_bytes(screen)


def write_summary(out: Path, args: argparse.Namespace, results: list[dict[str, Any]], **more: Any) -> None:
    summary = {"revision": revision(), **more, "args": {key: str(value) for key, value in vars(args).items()},
               "games": results}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def cancel_soon(cancel: asyncio.Event) -> None:
    asyncio.get_running_loop().call_soon(cancel.set)


class Busy(Exception):
    """Another run holds the device."""


@contextmanager
def lock(name: str):
    """Hold `name` (`android-SERIAL`) while a run moves it, or raise Busy at once when another run
    (`eval android`, `eval serve`) holds it. The lock is the system's on an open file, so a run
    that dies lets go of it."""
    LOCKS.mkdir(parents=True, exist_ok=True)
    handle = open(LOCKS / re.sub(r"[^\w.-]", "_", name), "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise Busy(name) from None
    try:
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def held(name: str) -> bool:
    """Whether a run holds `name` now (this process's own game among them): asked by what only looks at the
    device and so takes no lock of its own, to keep out of that run's way."""
    try:
        with lock(name):
            return False
    except Busy:
        return True
