"""What every screen evaluation shares: the run's arguments, the watcher that records each input, the
figures made of a game, and the files written."""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .. import catalog, model
from ..contracts import Budget, GoalSpec
from ..run import Run

ROOT = Path(__file__).resolve().parents[3]
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
    parser.add_argument("--objective", choices=("play_until_end", "reach_value", "actions"), default="play_until_end")
    parser.add_argument("--target", type=int)
    parser.add_argument("--policy")
    parser.add_argument("--compute", type=int, required=True, help="evaluated boards per decision")
    parser.add_argument("--max-actions", type=int, default=3000)
    parser.add_argument("--max-seconds", type=float, default=7200)
    parser.add_argument("--max-input-tokens", type=int, default=300_000,
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
    parser.add_argument("--model-base", default=model.api_base(),
                        help=f"an OpenAI-compatible API's base URL ({model.BASE_VARIABLE}, else {model.DEFAULT_BASE})")


def goal_and_budget(args: argparse.Namespace) -> tuple[GoalSpec, Budget]:
    return (GoalSpec(args.objective, args.target),
            Budget(max_actions=args.max_actions, compute=args.compute, max_seconds=args.max_seconds,
                   max_input_tokens=getattr(args, "max_input_tokens", None) or None))


def vision_model(args: argparse.Namespace) -> tuple[model.ChatClient | None, model.Recorded | None]:
    """The model a game is played with and the recorder around it; neither when no model is named."""
    if not args.vision_model:
        return None, None
    key = model.api_key()
    if not key:
        raise SystemExit(f"asking {args.vision_model} needs a key: set {model.KEY_VARIABLE}")
    client = model.ChatClient(args.vision_model, key, base=args.model_base)
    return client, model.Recorded(client, model.prompts(catalog.FRAGMENT / "prompts"))


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


def write_game(out: Path, name: str, summary: dict[str, Any], request: dict[str, Any], run: Run, recorder: Recorder,
               answers: list[dict[str, Any]], screen: bytes | None) -> None:
    """One game's files: `NAME.json` (its figures, request, record, every move and input, the model's answers)
    and `NAME-final.png`, the screen it ended on."""
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps({
        "result": summary, "request": request, "record": run.record, "geometry": run.geometry, "vision": run.vision,
        "recoveries": run.recoveries, "model_answers": answers, "moves": run.moves, "inputs": recorder.inputs}, indent=2, default=str) + "\n", encoding="utf-8")
    if screen is not None:
        (out / f"{name}-final.png").write_bytes(screen)


def write_summary(out: Path, args: argparse.Namespace, results: list[dict[str, Any]], **more: Any) -> None:
    summary = {"revision": revision(), **more, "args": {key: str(value) for key, value in vars(args).items()},
               "games": results}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def cancel_soon(cancel: asyncio.Event) -> None:
    asyncio.get_running_loop().call_soon(cancel.set)
