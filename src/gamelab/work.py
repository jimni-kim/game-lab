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
    until_level  levels as `levels` does, counted by the number the game shows rather than by the parts: the
                 screen is named (`scenes.name`) before the first part and after each level won, and the last level
                 cleared (`through`) read from it; nothing is played when the screen already stands past the level
                 asked for, and the work gives up when no number can be read at all
    budget       one for the whole work: each part is given what the parts before it left of the inputs, the
                 seconds and the tokens
    measures     how well play went, as the game shows it (PLAN §27, M9; §36, E5): the screen is named once after
                 each level played and once where the work stops, and the values the model read off it (steps
                 taken, health left, stars, a score: `scenes.measures`) are kept with the part and with the work.
                 The model says which values the screen shows, by the game's own labels, and whether more is
                 better; nothing here knows a game's
    counting     levels up to one in a game whose levels do not end on a screen of their own (a floor, a day):
                 the part played by a model's own inputs is told the number the screen stood at, and ends once
                 the model reads a greater one (`agent.Agent.counting`); the screen is then named as after a level
    hand back    the work's own account (`handback`): why it stopped, the levels won and lost, what it spent
                 (inputs, moves, what was used beyond the board, tokens), where it stopped (the kind of screen,
                 `scenes`, and what it shows, asked of a model once), and what it measured, for the next request
                 to go on from (`Scope.CURRENT_GAME`)

Nothing here reads the screen or sends an input: the parts do.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import time
from typing import Any

import logging

from . import agent, catalog, routine, scenes
from .contracts import LOST, Budget, GoalSpec, Outcome, Scope
from .run import Run

log = logging.getLogger("gamelab.work")

LEVELS, UNTIL = "levels", "until_level"
# why the levels up to one stopped: none of the screens named showed a level number to count by
LEVEL_UNREAD = "level_unread"
# a part that ended without a move of its own, a level counted for nothing: the work does not go round again on it
EMPTY_MOST = 1
# the times a family was tried again, or let go, told of each part
RESUMES_TOLD = 8


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

    def progress(self, event: dict[str, Any]) -> None:
        agent.progress(self.watch, **event)


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


async def _named(model: Any, surface: Any) -> dict[str, Any] | None:
    """The screen as it stands named by a model (`scenes.name`); None when it could not be asked."""
    try:
        return await scenes.name(model, await surface.capture())
    except Exception as error:
        log.info("the screen could not be named: %s", error)
        return None


def _through(named: dict[str, Any]) -> int | None:
    """The last level cleared as a named screen shows it: the level it is about when it shows that level won, the
    one before it otherwise (a level about to be opened, in play or lost); None when it shows no number."""
    level = named.get("level")
    if level is None:
        return None
    return level if named.get("level_won") else level - 1


async def play(surface: Any, model: Any, family_id: str | None, *, goal: GoalSpec, budget: Budget,
               watch: Any = None, cancel: asyncio.Event | None = None, fresh: bool = False, **options: Any
               ) -> tuple[Run, dict[str, Any]]:
    """The work `goal` asks for on the surface as it stands: one `Run` holding every part's moves, and how the
    last part was read with the work's account (`work`) beside it. `options` go to each part as `agent.play`
    takes them."""
    started = time.monotonic()
    until = str(goal.objective) == UNTIL
    levels = str(goal.objective) == LEVELS or until
    if levels and (type(goal.target_value) is not int or goal.target_value < 1):
        raise ValueError("goal_target_missing")
    parts: list[tuple[Run, dict[str, Any]]] = []
    # each part's own seconds, from its start to its end (between-level screens before its level included)
    lasted: list[float] = []
    used = confirmed = tokens_in = tokens_out = questions = 0
    won = lost = empty = 0
    ended: tuple[str, str, str] | None = None
    # levels up to one: the last level cleared as the screen numbers it (None: not read yet), the levels this work
    # is to clear (known once `through` is read before the first part), and the screen's naming last asked for with
    # the parts played before it
    through: int | None = None
    counted: str | None = None
    wanted: int | None = goal.target_value if levels and not until else None
    read: dict[str, Any] | None = None
    read_after = -1
    # the screen as named after each part that played a level, by the part's number (its measures are the part's)
    after: dict[int, dict[str, Any]] = {}

    def tokens_left() -> bool:
        return budget.max_input_tokens is None or tokens_in < budget.max_input_tokens

    def wanted_now() -> int | None:
        if wanted is not None or not until or through is None:
            return wanted
        return won + max(goal.target_value - through, 0)

    if until:
        # where the levels stand before any is played: nothing is played when the screen is past the one asked for
        read, read_after = await _named(model, surface), 0
        if read is not None:
            tokens_in, tokens_out = tokens_in + read["usage"].input_tokens, tokens_out + read["usage"].output_tokens
            questions += 1
            through = _through(read)
            counted = read.get("counted")
        if through is not None:
            wanted = max(goal.target_value - through, 0)
            if through >= goal.target_value:
                ended = (str(Outcome.GOAL_REACHED), UNTIL, read["situation"] or read["scene"] or "")
    # what the model playing between the levels kept for itself, for the part after (`direct.MEMORY`)
    notes = options.pop("notes", "") or ""
    # the game's routines, one book for every part: the steps one part's model taught are sent by code in the next
    # (PLAN §28)
    book = options.pop("book", None) or routine.Book()
    # whether the part before ended in a level lost: the next plays that level again
    again = False
    while ended is None:
        # (the screen named before the first part spent tokens of the budget too)
        left, spent = _left(budget, used, started, tokens_in) if parts or tokens_in else (budget, None)
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
            allowed_meta_actions=goal.allowed_meta_actions, choices=goal.choices)
        began = time.monotonic()
        run, adapted = await agent.play(surface, model, family_id, goal=part, budget=left, cancel=cancel,
                                        watch=_Numbered(watch, used) if watch is not None and parts else watch,
                                        fresh=fresh and not parts, onward=levels,
                                        anew=goal.scope == Scope.NEW_GAME and not parts,
                                        counting=through + 1 if until and through is not None else None,
                                        **{**options, "notes": notes, "book": book})
        parts.append((run, adapted))
        lasted.append(round(time.monotonic() - began, 1))
        record = run.record
        spent_here = adapted.setdefault("account", {"used": {}, "refused": {}}).setdefault("used", {})
        if again and record.get("level_moves") and not spent_here.get("retry"):
            # a lost level was played again: another try was used, whoever pressed the button and whatever was
            # said of it (a step of the game's routine says nothing, `routine`)
            spent_here["retry"] = 1
        again = again and not record.get("level_moves")
        used, confirmed = used + record["actions_used"], confirmed + record["confirmed_actions"]
        tokens_in, tokens_out = tokens_in + adapted["tokens"]["input"], tokens_out + adapted["tokens"]["output"]
        questions += adapted["questions"]
        notes = (adapted.get("direct") or {}).get("memory") or notes
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
        else:
            # a level played to its end: the screen it ended on is named once, for what it shows of how the level
            # went (`measures`) and, for levels up to one, for the number it shows
            empty, seen = 0, None
            if tokens_left():
                read, read_after = await _named(model, surface), len(parts)
                if read is not None:
                    tokens_in, tokens_out = tokens_in + read["usage"].input_tokens, tokens_out + read["usage"].output_tokens
                    questions += 1
                    after[len(parts)] = read
                    counted = read.get("counted") or counted
                    seen = _through(read)
            if outcome == Outcome.GOAL_REACHED:
                won += 1
                if until:
                    # the level won counted by the number the screen shows now; one more than the last when it
                    # shows none
                    if seen is not None:
                        through = seen
                    elif through is not None:
                        through += 1
                    elif tokens_left():
                        # (with the tokens spent the work ends on them instead, before the next part)
                        ended = (str(Outcome.GAVE_UP), LEVEL_UNREAD, "the level number could not be read from the screen")
                    if through is not None and through >= goal.target_value:
                        ended = (str(Outcome.GOAL_REACHED), UNTIL, "")
                elif won >= goal.target_value:
                    ended = (str(Outcome.GOAL_REACHED), LEVELS, "")
            else:
                lost, again = lost + 1, True
                if LOST in goal.stop_conditions or "retry" not in goal.allowed_meta_actions:
                    ended = (str(Outcome.GAME_OVER), "level_lost", detail)
        agent.progress(watch, "part", number=len(parts), outcome=outcome, reason=reason, family=record.get("family"),
                       inputs=used, **({"won": won, "lost": lost, "wanted": wanted_now()} if levels else {}),
                       **({"until": goal.target_value, "through": through} if until else {}))
    outcome, reason, detail = ended
    # (levels up to one the screen already stood past: no part was played)
    last_run, last = parts[-1] if parts else (Run({"family": None}), {})
    account = {"used": {}, "refused": {}}
    for _, adapted in parts:
        for kind in account:
            _added(account[kind], (adapted.get("account") or {}).get(kind))
    site = await surface.address()
    stopped = {"screen": last.get("screen"), "situation": last.get("situation") or ""}
    # where the work left the game: the screen it stopped on is named once, for the person and for what it shows of
    # how play went, unless it was named after the last part already. One game that sent nothing (an errand not due
    # yet, handed back with no question asked) played nothing to measure
    named = read if read is not None and read_after == len(parts) else None
    if named is None and parts and (levels or used) and tokens_left():
        named = await _named(model, surface)
        if named is not None:
            tokens_in, tokens_out = tokens_in + named["usage"].input_tokens, tokens_out + named["usage"].output_tokens
            questions += 1
    if named is not None and (named["scene"] or named["situation"]):
        stopped = {"screen": named["scene"], "situation": named["situation"]}
    profiles = [made for _, adapted in parts for made in adapted.get("profiles") or ()]
    handback = {
        "goal": goal.to_json(), "outcome": outcome, "reason": reason, "detail": detail,
        "levels": None if not levels else {"wanted": wanted_now(), "won": won, "lost": lost,
                                            **({"until": goal.target_value, "through": through} if until else {}),
                                            **({"counted": counted} if until and counted else {})},
        "parts": [{**_part(run, adapted, seconds), **_measured(after.get(number))}
                  for number, ((run, adapted), seconds) in enumerate(zip(parts, lasted), 1)],
        "spent": {"inputs": used, "moves": confirmed, "used": account["used"], "refused": account["refused"],
                  "tokens": {"input": tokens_in, "output": tokens_out}, "questions": questions,
                  "seconds": round(time.monotonic() - started, 1)},
        # where the work stopped: the next request may go on from this screen as it stands
        "stopped_at": {"where": site, "family": last_run.record.get("family"), "scene": last.get("scene"),
                       "screen": stopped["screen"], "situation": stopped["situation"],
                       # the level number the last screen showed, when it was named and showed one
                       **({"level": named["level"]} if named is not None and named.get("level") is not None else {}),
                       "in_play": last.get("scene") == "playing"
                       and outcome not in (Outcome.GOAL_REACHED, Outcome.GAME_OVER)},
        "resume": {"where": site, "scope": str(Scope.CURRENT_GAME)},
        # the game opens no level until time gives a life back: how long until the next (PLAN §26, C2-4)
        **({"out_of_lives": last["out_of_lives"]} if reason == agent.OUT_OF_LIVES and last.get("out_of_lives") else {}),
        # the errand asked for is not offered by the game yet: the time left, with nothing sent for it when the
        # game's world memory knew (`agent.NOT_YET`); and what that memory holds now (PLAN §30)
        **({"not_yet": last[agent.NOT_YET]} if last.get(agent.NOT_YET) else {}),
        **({"world": last["world"]} if last.get("world") else {}),
        # the errand was told done although everything the game holds read as before it: to be looked at before
        # it is believed (`world.World.unmoved`)
        **({"unmoved": last["unmoved"]} if last.get("unmoved") else {}),
        # how play went as the screen the work stopped on shows it (PLAN §27, M9)
        **({"measured": _measured(named)} if _measured(named) else {}),
        # names and profiles the game would not go on without, and what they were made as (C2-5)
        **({"profiles": profiles} if profiles else {}),
        # what the game's routines did in this work: the steps kept, sent by code, failed and learned (PLAN §28)
        **({"routines": book.summary()} if book.summary() else {}),
    }
    agent.progress(watch, "handback", work=handback)
    moves = [{**move, "part": number} for number, (run, _) in enumerate(parts, 1) for move in run.moves]
    record = {**last_run.record, "outcome": outcome, "reason": reason, "detail": detail, "goal": goal.to_json(),
              "budget": budget.to_json(), "actions_used": used, "confirmed_actions": confirmed,
              "levels": {name: sum(run.record["levels"].get(name, 0) for run, _ in parts)
                         for name in dict.fromkeys(name for run, _ in parts for name in run.record["levels"])},
              "history": moves[-agent.HISTORY_KEPT:], "parts": len(parts)}
    run = Run(record, moves, geometry=last_run.geometry, vision=last_run.vision, looks=sum(run.looks for run, _ in parts),
              last_screen=last_run.last_screen, recoveries=[entry for run, _ in parts for entry in run.recoveries],
              screens=[{**kept, "part": number} for number, (run, _) in enumerate(parts, 1) for kept in run.screens])
    return run, {**last, "tokens": {"input": tokens_in, "output": tokens_out}, "questions": questions,
                 "seconds": round(sum(adapted["seconds"] for _, adapted in parts), 1), "account": account, "work": handback}


def _measured(named: dict[str, Any] | None) -> dict[str, Any]:
    """What a named screen showed of how play went: the number the game counts its progress by, what it counts,
    and the values read off it (`scenes.measures`); empty when it showed none."""
    if named is None:
        return {}
    return {**({"level": named["level"]} if named.get("level") is not None else {}),
            **({"counted": named["counted"]} if named.get("counted") else {}),
            **({"measures": named["measures"]} if named.get("measures") else {})}


def _part(run: Run, adapted: dict[str, Any], seconds: float) -> dict[str, Any]:
    """One part of the work as handed back: how it ended and what it spent (PLAN §26, C2-6), what the family made
    of the level (its goals as read, the ways it learned, the moves a model picked among the code's: PLAN §31), and
    each time the family's play was handed to the model and why (C2-1)."""
    record, kit = run.record, adapted.get("kit") or {}
    # the inputs before the level's first move: those a model chose, and those code sent as steps of the game's
    # routine
    beside = []
    for move in run.moves:
        if not _beside(move):
            break
        beside.append(move)
    return {"outcome": record["outcome"], "reason": record["reason"], "family": record.get("family"),
            "actions": record["actions_used"], "confirmed": record["confirmed_actions"],
            "level_moves": record.get("level_moves"),
            # (`seconds`: from the part's start to the last of them done; `first_move_s`: to the level's first move)
            "between": {"by_model": sum(move["level"] == agent.L0 for move in beside),
                        "by_routine": sum(move["level"] == agent.ROUTINE for move in beside),
                        "seconds": max((move.get("at_s") or 0.0 for move in beside), default=None),
                        "first_move_s": _until_level(run.moves)},
            "seconds": seconds, "input_tokens": (adapted.get("tokens") or {}).get("input"),
            "questions": adapted.get("questions"),
            **({"moves_left": adapted["moves_left"]} if adapted.get("moves_left") is not None else {}),
            # how the level's end was told: what a model said, on which kind of screen, whether that was no result
            # screen (`inferred`), and what the family still counted of the goals (`agent.Agent._ended`)
            **({"end": adapted["end"]} if adapted.get("end") else {}),
            # the level counted on by the number the game showed rather than by a result screen (`agent.COUNTED`)
            **({"counted_on": adapted["counted"]} if adapted.get("counted") else {}),
            **{name: kit[name] for name in ("goals_seen", "ways", "picks") if kit.get(name)},
            **({"resumes": adapted["resumes"][-RESUMES_TOLD:]} if adapted.get("resumes") else {}),
            **({"let_go": adapted["let_go"][-RESUMES_TOLD:]} if adapted.get("let_go") else {}),
            # the times play went on from a board that was no result of the last move, and who read that board
            # (`agent.Agent._reseed`)
            **({"reseeds": adapted["reseeds"]} if adapted.get("reseeds") else {}),
            "used": (adapted.get("account") or {}).get("used") or {}}


def _until_level(moves: list[dict[str, Any]]) -> float | None:
    """How long the part took to its first move on a level's board done and read (the screens before the level,
    and the reading of the board): None when it made none."""
    return next((move.get("at_s") for move in moves if not _beside(move)), None)


def _beside(move: dict[str, Any]) -> bool:
    """Whether an input was sent outside a level: a step of the game's routine, or the model's on a screen it named
    something other than a board."""
    return move.get("level") == agent.ROUTINE or (move.get("level") == agent.L0
                                                  and move.get("scene") not in (None, scenes.BOARD))


def shown(measure: dict[str, Any]) -> str:
    """One value read off a screen in a few words: `steps 46`, `health 32/70`."""
    value = f"{measure['value']:g}" + (f"/{measure['most']:g}" if measure.get("most") is not None else "")
    return f"{measure['name']} {value}"


def measured_text(measured: dict[str, Any] | None) -> str:
    """What the last screen showed of how play went, in a few words (empty when nothing): the number counted and
    the values with which way is better."""
    if not measured:
        return ""
    said = [f"{measured.get('counted') or 'level'} {measured['level']}"] if measured.get("level") is not None else []
    said += [shown(measure) + {"higher": " (more is better)", "lower": " (fewer is better)"}.get(measure.get("better"), "")
             for measure in measured.get("measures") or ()]
    return ", ".join(said)


def told(handback: dict[str, Any]) -> str:
    """The handback in a few words for a person: why it stopped, what was done and spent, where it stands."""
    spent, levels, stopped = handback["spent"], handback["levels"], handback["stopped_at"]
    done = "" if not levels else (
        f"levels won {levels['won']} up to {levels.get('counted') or 'level'} {levels['until']} (through "
        f"{levels['through'] if levels.get('through') is not None else '?'}), lost {levels['lost']}; "
        if levels.get("until") is not None else f"levels won {levels['won']} of {levels['wanted']}, lost {levels['lost']}; ")
    used = ", ".join(f"{name} {count}" for name, count in sorted(spent["used"].items())) or "nothing beside the board"
    lives = handback.get("out_of_lives")
    waits = "" if lives is None else "; out of lives" + (
        f", next life in {lives['minutes']:g} min" if lives.get("minutes") is not None else ", time to the next life not shown")
    later = handback.get("not_yet")
    waits += "" if later is None else f"; {later['task']} is not offered yet, again in about {later['seconds']} s"
    if handback.get("unmoved"):
        waits += "; told done though nothing the game holds changed (" + ", ".join(
            f"{name} {count:g}" for name, count in handback["unmoved"].items()) + ")"
    names = "; ".join(f"a name or profile window came up and was passed as {made['made']!r}"
                      for made in handback.get("profiles") or ())
    if handback.get("measured"):
        waits += "; the screen shows " + measured_text(handback["measured"])
    return (f"{handback['outcome']} ({handback['reason']}): {done}{spent['inputs']} inputs, {spent['moves']} moves, "
            f"used {used}; stopped at {stopped['where']}" + (f" ({stopped['screen']})" if stopped.get("screen") else "")
            + (f": {stopped['situation']}" if stopped["situation"] else "") + waits + (f"; {names}" if names else ""))
