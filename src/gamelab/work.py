"""The work a person asked for, played as one game or as several in a row, and handed back (PLAN §25, R1 to R3).

A request is "what, and how far": the game on the screen to its end, a score, a number of moves, or a number of
levels cleared one after another. One game (`agent.Agent`) is the loop that plays a level; this is the layer over
it that says how many are played and what is handed back.

    one part     every objective but `levels` is one game, played as it always was
    levels       a game for each level, each on the same surface with nothing started again between them, the
                 next told that the level it first sees ended is the last one's (`onward`), so a result or a
                 map screen is passed for the next level rather than taken for its end, and what a model
                 playing between the levels noted for itself goes to the next part. A level won counts; a
                 level lost ends the work unless the person allowed another try (`retry`) and did not ask to
                 stop at a loss (`LOST`)
    budget       one for the whole work: each part is given what the parts before it left of the inputs, the
                 seconds and the tokens
    hand back    the work's own account (`handback`): why it stopped, the levels won and lost, what it spent
                 (inputs, moves, what was used beyond the board, tokens), and where it stopped (the kind of
                 screen, `scenes`, and what it shows, asked of a model once when levels were played), for the
                 next request to go on from (`Scope.CURRENT_GAME`)

Nothing here reads the screen or sends an input: the parts do.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import time
from typing import Any

import logging

from . import agent, catalog, scenes
from .contracts import LOST, Budget, GoalSpec, Outcome, Scope
from .run import Run

log = logging.getLogger("gamelab.work")

LEVELS = "levels"
# a part that ended without a move of its own, a level counted for nothing: the work does not go round again on it
EMPTY_MOST = 1


class _Numbered:
    """A watcher of the whole work: the inputs of each part numbered on from the parts before it."""

    def __init__(self, watch: Any, offset: int) -> None:
        self.watch, self.offset = watch, offset

    async def seen(self, png: bytes) -> None:
        await self.watch.seen(png)

    async def sending(self, number: int, action: dict[str, Any], state: Any, sent: dict[str, Any], observed_s: float) -> None:
        await self.watch.sending(self.offset + number, action, state, sent, observed_s)

    async def sent(self) -> None:
        await self.watch.sent()


def _left(budget: Budget, used: int, started: float, tokens: int) -> tuple[Budget, str | None]:
    """What the parts before left of the work's budget, and which part of it is used up (None: none is)."""
    seconds = None if budget.max_seconds is None else budget.max_seconds - (time.monotonic() - started)
    tokens_left = None if budget.max_input_tokens is None else budget.max_input_tokens - tokens
    spent = "actions" if budget.max_actions - used <= 0 else "time" if seconds is not None and seconds <= 0 else \
        "tokens" if tokens_left is not None and tokens_left <= 0 else None
    return replace(budget, max_actions=max(budget.max_actions - used, 0), max_seconds=seconds,
                   max_input_tokens=tokens_left), spent


def _added(total: dict[str, int], more: dict[str, int] | None) -> dict[str, int]:
    for name, count in (more or {}).items():
        total[name] = total.get(name, 0) + count
    return total


async def play(surface: Any, model: Any, family_id: str | None, *, goal: GoalSpec, budget: Budget,
               watch: Any = None, cancel: asyncio.Event | None = None, fresh: bool = False, **options: Any
               ) -> tuple[Run, dict[str, Any]]:
    """The work `goal` asks for on the surface as it stands: one `Run` holding every part's moves, and how the
    last part was read with the work's account (`work`) beside it. `options` go to each part as `agent.play`
    takes them."""
    started = time.monotonic()
    levels = str(goal.objective) == LEVELS
    if levels and (type(goal.target_value) is not int or goal.target_value < 1):
        raise ValueError("goal_target_missing")
    parts: list[tuple[Run, dict[str, Any]]] = []
    used = confirmed = tokens_in = tokens_out = questions = 0
    won = lost = empty = 0
    ended: tuple[str, str, str] | None = None
    # what the model playing between the levels kept for itself, for the part after
    notes = str(options.pop("notes", "") or "")
    while ended is None:
        left, spent = _left(budget, used, started, tokens_in) if parts else (budget, None)
        if spent is not None:
            ended = (str(Outcome.BUDGET_EXHAUSTED), spent, "")
            break
        if parts and cancel is not None and cancel.is_set():
            ended = (str(Outcome.BUDGET_EXHAUSTED), "cancelled", "")
            break
        # each level is one game to its end, played on from the screen as the host left it
        # whatever was to be started again the host started before the work: each part plays on from the screen
        part = replace(goal, scope=Scope.CURRENT_GAME) if not levels else GoalSpec(
            "play_until_end", scope=Scope.CURRENT_GAME, stop_conditions=goal.stop_conditions,
            allowed_meta_actions=goal.allowed_meta_actions)
        run, adapted = await agent.play(surface, model, family_id, goal=part, budget=left, cancel=cancel,
                                        watch=_Numbered(watch, used) if watch is not None and parts else watch,
                                        fresh=fresh and not parts, onward=levels, **{**options, "notes": notes})
        parts.append((run, adapted))
        record = run.record
        used, confirmed = used + record["actions_used"], confirmed + record["confirmed_actions"]
        tokens_in, tokens_out = tokens_in + adapted["tokens"]["input"], tokens_out + adapted["tokens"]["output"]
        questions += adapted["questions"]
        notes = (adapted.get("direct") or {}).get("notes") or notes
        outcome, reason, detail = record["outcome"], record["reason"], record.get("detail", "")
        if not levels:
            ended = (outcome, reason, detail)
            break
        # the next part goes by the family this one was found to be, when it is one the harness has (a family
        # made for a game lives only as long as that game)
        if record.get("family") in catalog.descriptor_ids():
            family_id = record["family"]
        if outcome not in (Outcome.GOAL_REACHED, Outcome.GAME_OVER):
            ended = (outcome, reason, detail)
        elif not record.get("level_moves", record["confirmed_actions"]):
            # it ended on what it first saw: that was no level played here
            empty += 1
            if empty > EMPTY_MOST:
                ended = (str(Outcome.GAVE_UP), "no_level_started", detail)
        elif outcome == Outcome.GOAL_REACHED:
            won, empty = won + 1, 0
            if won >= goal.target_value:
                ended = (str(Outcome.GOAL_REACHED), LEVELS, "")
        else:
            lost, empty = lost + 1, 0
            if LOST in goal.stop_conditions or "retry" not in goal.allowed_meta_actions:
                ended = (str(Outcome.GAME_OVER), "level_lost", detail)
    outcome, reason, detail = ended
    last_run, last = parts[-1]
    account = {"used": {}, "refused": {}}
    for _, adapted in parts:
        for kind in account:
            _added(account[kind], (adapted.get("account") or {}).get(kind))
    site = await surface.address()
    stopped = {"screen": last.get("screen"), "situation": last.get("situation") or ""}
    if levels and (budget.max_input_tokens is None or tokens_in < budget.max_input_tokens):
        # where the levels left the game: the screen after the last of them is named once, for the person
        try:
            named = await scenes.name(model, await surface.capture())
            tokens_in, tokens_out = tokens_in + named["usage"].input_tokens, tokens_out + named["usage"].output_tokens
            questions += 1
            if named["scene"] or named["situation"]:
                stopped = {"screen": named["scene"], "situation": named["situation"]}
        except Exception as error:
            log.info("the last screen could not be named: %s", error)
    handback = {
        "goal": goal.to_json(), "outcome": outcome, "reason": reason, "detail": detail,
        "levels": {"wanted": goal.target_value, "won": won, "lost": lost} if levels else None,
        "parts": [{"outcome": run.record["outcome"], "reason": run.record["reason"], "family": run.record.get("family"),
                   "actions": run.record["actions_used"], "confirmed": run.record["confirmed_actions"],
                   "level_moves": run.record.get("level_moves"),
                   "used": (adapted.get("account") or {}).get("used") or {}} for run, adapted in parts],
        "spent": {"inputs": used, "moves": confirmed, "used": account["used"], "refused": account["refused"],
                  "tokens": {"input": tokens_in, "output": tokens_out}, "questions": questions,
                  "seconds": round(time.monotonic() - started, 1)},
        # where the work stopped: the next request may go on from this screen as it stands
        "stopped_at": {"where": site, "family": last_run.record.get("family"), "scene": last.get("scene"),
                       "screen": stopped["screen"], "situation": stopped["situation"],
                       "in_play": last.get("scene") == "playing"
                       and outcome not in (Outcome.GOAL_REACHED, Outcome.GAME_OVER)},
        "resume": {"where": site, "scope": str(Scope.CURRENT_GAME)},
    }
    moves = [{**move, "part": number} for number, (run, _) in enumerate(parts, 1) for move in run.moves]
    record = {**last_run.record, "outcome": outcome, "reason": reason, "detail": detail, "goal": goal.to_json(),
              "budget": budget.to_json(), "actions_used": used, "confirmed_actions": confirmed,
              "levels": {name: sum(run.record["levels"].get(name, 0) for run, _ in parts)
                         for name in dict.fromkeys(name for run, _ in parts for name in run.record["levels"])},
              "history": moves[-agent.HISTORY_KEPT:], "parts": len(parts)}
    run = Run(record, moves, geometry=last_run.geometry, vision=last_run.vision, looks=sum(run.looks for run, _ in parts),
              last_screen=last_run.last_screen, recoveries=[entry for run, _ in parts for entry in run.recoveries])
    return run, {**last, "tokens": {"input": tokens_in, "output": tokens_out}, "questions": questions,
                 "seconds": round(sum(adapted["seconds"] for _, adapted in parts), 1), "account": account, "work": handback}


def told(handback: dict[str, Any]) -> str:
    """The handback in a few words for a person: why it stopped, what was done and spent, where it stands."""
    spent, levels, stopped = handback["spent"], handback["levels"], handback["stopped_at"]
    done = f"levels won {levels['won']} of {levels['wanted']}, lost {levels['lost']}; " if levels else ""
    used = ", ".join(f"{name} {count}" for name, count in sorted(spent["used"].items())) or "nothing beside the board"
    return (f"{handback['outcome']} ({handback['reason']}): {done}{spent['inputs']} inputs, {spent['moves']} moves, "
            f"used {used}; stopped at {stopped['where']}" + (f" ({stopped['screen']})" if stopped.get("screen") else "")
            + (f": {stopped['situation']}" if stopped["situation"] else ""))
