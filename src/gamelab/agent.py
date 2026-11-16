"""One game played by one loop, from the first screen to its end (PLAN §10; docs/design/2026-11-14-agent-loop.md).

    look      the screen once it has come to rest
    read      by the site's generated adapter (L3), else by a model checked by the rule model (L2);
              a screen that shows no board is put to a model for one input of its own (L0) and looked at again
    judge     the rule model says whether the goal is reached or the game over
    choose    the registered solver, leaving out the moves this board did not take
    send      where the adapter's `target` says (L3), else placed on the board box the model gave (L2)
    confirm   the screen read again and the rule model's `assess` of the move
    record    the move, who read it and what came of it; a screen read by the model is a look to write the
              adapter from
    move up or down between the levels by what the moves showed

Who reads is the only thing that changes between the levels: the loop is the same. The model reads until
enough moves are confirmed to write an adapter from; the adapter then reads until it leaves a screen unread or
its moves stop making sense, where the model reads that screen and the adapter is checked against it and
written again if it must be. When no adapter passes, the game goes on read by the model. Nothing here waits
for a person: a game ends at its goal, its own end, a budget (inputs, time, the tokens of its model
questions), or when a model sees nothing more to do (`gave_up`, with what it said of the screen).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
import logging
import time
from typing import Any, Protocol

from . import adaptation, carry, catalog, direct, made, samples
from .adaptation import (EXPLORE_MOVES, HELD_BACK, MAX_GENERATIONS, MAX_RELEARNS, AdaptationFailed, Adaptation, Look, Made,
                         Store, Trace, _agrees, _board, _shape, adopt, explorable, identify)
from .adapters.generated import GeneratedAdapter
from .carry import Carrier
from .contracts import Budget, GoalSpec, NeedObservation, Observation, Outcome
from .execution import TAP_MS
from .families import state_id
from .model import Model, TurnRequest, TurnResult
from . import motion
from .carry import _boxes as carry_boxes
from .motion import Steady
from .run import Run, check
from .recovery import GIVE_UP, MAX_TRIES, NONE, Recovery

log = logging.getLogger("gamelab.agent")

RECORD_VERSION = 2
L0, L2, L3 = "L0", "L2", "L3"
# the first moves read by an adapter (made now, or kept from another game) that must each do what the rules
# predict: one that does not sends the screen to the model
TRIAL_MOVES = 3
# moves in a row that did not do what the rules predict before the reading (L3) or the screen (L2) is doubted
MAX_STALLED = 3
# confirmed moves read by the model after an adapter could not be written, before it is tried once more, and
# how often in one game
RETRY_LOOKS, RETRIES = 12, 2
# a game that must be still but whose pixels never rest is settled once its state has read the same this long;
# a screen the adapter leaves unread the same way this long is not waited on further
RESTLESS_S = 1.0
# after its trial moves a still game waits the longest of them plus this margin, then looks once
ANIMATION_MARGIN_S, ANIMATION_MAX_S = 0.1, 2.0
# a screen the model is to read is waited for until its game shows the same twice, this far apart, compared
# as small grey pictures that may differ this much on average (of 255), and for no longer than this
STILL_GAP_S, STILL_SIDE, STILL_DISTANCE, STILL_TIMEOUT_S = 0.3, 32, 0.6, 5.0
# an adapter earns being written again by reading moves: one that read fewer than EARNED confirmed moves since
# it was last written is churning, and after MAX_CHURN such rewrites in a row the game goes on read by the
# model (writing costs about what EXPLORE_MOVES readings do)
EARNED, MAX_CHURN = EXPLORE_MOVES, 2
# between a tap that picks something and the input that completes the move
THEN_S = 0.3
# a game played by the model's own inputs (L0) is given up when this many of them in a row changed nothing on
# the screen, or when the model gave this many answers in a row that are no input
MAX_IDLE, MAX_SILENT = 6, 3
# a game the model plays itself after a family's play broke off is tried by that family again when a move of its
# changes this share of the screen (a tutorial gives way to the game), at most RESUMES_MOST times a game
RESUME_SHARE, RESUMES_MOST = 0.3, 3
# and when this many of its moves were confirmed since it was last tried: a scene that changes a little at a time
# (a tutorial's last step giving way to the game's board) shows no such change
RESUME_EVERY = 4
# moves in a row the game did not take, with none taken between them, after which play by the family's rules is
# left to the model's own inputs: the rules allow what the game does not (a tutorial that takes one move only, a
# game taken for another), and leaving out each refused move alone never ends where many moves are allowed
FRUITLESS_MOST = 6
# the question of which game this is, asked again when the model could not be reached
IDENTIFY_RETRIES, IDENTIFY_RETRY_S = 2, 2.0
# a state reached this many times by confirmed moves ends the game as going nowhere
MAX_VISITS = 3
# moves kept in the record and told to a model asked about a screen
HISTORY_KEPT = 8
# a direction key on a surface without keys is a swipe across the board
SWIPES = {"ArrowLeft": (-1, 0), "ArrowRight": (1, 0), "ArrowUp": (0, -1), "ArrowDown": (0, 1)}
SWIPE_SPAN, SWIPE_MS = 0.6, 120
WON = "won"


class Watch(Protocol):
    """What a run may hang on the loop to measure it; the game is not told."""

    async def seen(self, png: bytes) -> None: ...
    async def sending(self, number: int, action: dict[str, Any], state: dict[str, Any], sent: dict[str, Any],
                      observed_s: float) -> None: ...
    async def sent(self) -> None: ...


@dataclass(frozen=True)
class Settling:
    """How long a move's result is waited for (the adapter's `settling`, as the family starts it)."""
    timeout_s: float = 4.0
    interval_s: float = 0.15
    stable_s: float = 0.25
    animation_s: float = 0.0
    still: bool = False


@dataclass
class Reading:
    png: bytes
    state: dict[str, Any]
    at: float
    by: str
    # the look it was kept as, when the model read it
    look: Look | None = None

    @property
    def id(self) -> str:
        return state_id(self.state)


class Metered:
    """A model with what its questions cost counted."""

    def __init__(self, model: Model) -> None:
        self.model, self.input, self.output, self.questions = model, 0, 0, 0

    async def complete(self, request: TurnRequest) -> TurnResult:
        return await self.ask(self.model, request)

    async def ask(self, model: Model, request: TurnRequest) -> TurnResult:
        self.questions += 1
        result = await model.complete(request)
        self.input += result.usage.input_tokens
        self.output += result.usage.output_tokens
        return result

    def also(self, model: Model) -> Model:
        """Another model whose questions are counted here too (one that writes adapters)."""
        return _Also(self, model)


class _Also:
    def __init__(self, meter: Metered, model: Model) -> None:
        self.meter, self.model = meter, model

    async def complete(self, request: TurnRequest) -> TurnResult:
        return await self.meter.ask(self.model, request)


class _End(Exception):
    def __init__(self, outcome: Outcome, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.outcome, self.reason, self.detail = outcome, reason, detail


class Agent:
    def __init__(self, surface: Any, model: Model, family_id: str | None, *, goal: GoalSpec, budget: Budget,
                 store: Store | None = None, policy: str | None = None, fresh: bool = False, hint: str | None = None,
                 watch: Watch | None = None, cancel: asyncio.Event | None = None, accept_consent: bool = False,
                 writer: Model | None = None, direct: bool = True) -> None:
        self.surface, self.model, self.family_id = surface, Metered(model), family_id
        # the screen as a model is shown it: what keeps moving over it (a tutorial's hand) left out (`motion`)
        self.seen = Steady(surface)
        # the frames every wait on the screen takes, each with the time it was taken (`frames`): captured one at a
        # time from the surface, unless it gives frames of its own as they are drawn
        self.frames = self.seen.frame_source
        # the model that writes adapters, when it is another: its questions count in the game's as the reader's do
        self.writer = self.model.also(writer) if writer is not None else None
        self.goal, self.budget, self.store, self.fresh, self.hint = goal, budget, store, fresh, hint
        # whether a game of no registered family is played by the model's own inputs, not left unplayed
        self.direct = direct
        # the family made for this game when none is registered for it (`made`), the moves played by its
        # written rules that the screen bore out, and a move whose real result those rules refused
        self.game: made.Game | None = None
        self.transitions: list[dict[str, Any]] = []
        self.refused: dict[str, Any] | None = None
        self.asked_policy, self.watch, self.cancel, self.accept_consent = policy, watch, cancel, accept_consent
        self.started = time.monotonic()
        # drags are carried: what a held thing is drawn like is measured on the way (`carry`)
        self.carrier = Carrier(self.seen)
        self.carried_as: dict[str, Any] | None = None
        # what was found of the game nobody named, and the families still to try for it
        self.found: dict[str, Any] = {}
        self.candidates: list[str] = []
        self.named = False
        self.adaptation: Adaptation | None = None
        self.recovery: Recovery | None = None
        self.family: Any = None
        self.policy: str | None = None
        self.site = ""
        # the looks the model read (what an adapter is written from), the adapter and how it was made
        self.trace: Trace | None = None
        self.made: Made | None = None
        self.adapter: GeneratedAdapter | None = None
        self.settling = Settling()
        # where the model was told the board is, and the rules it is read by
        self.board: list[int] | None = None
        # the adapter's reading is doubted: the model reads until one of its moves is confirmed. `blind`: only
        # because it found no board, which an input that clears the screen puts right
        self.distrusted = self.blind = False
        # a look no move vouches for yet: dropped when the move played from it is not confirmed
        self.unproven: Look | None = None
        # the last result confirmed: the next move's board, with no second look
        self.carried: Reading | None = None
        self.state: dict[str, Any] | None = None
        self.last_png: bytes | None = None
        # why the last read gave no board, what the adapter read of it, and whether the rules alone end the game
        self.why = "board_not_found"
        self.unread: dict[str, Any] | None = None
        self.over = False
        self.moves: list[dict[str, Any]] = []
        self.used = self.confirmed = self.stalled = self.tries = self.shots = self.sequence = 0
        self.shrugged = False
        self.ineffective: dict[str, Any] | None = None
        # confirmed moves the adapter read since it was last written, and rewrites in a row it had not earned
        self.earned = self.churn = 0
        # a move whose result nobody read yet, with the board it was played from: the next reading is held to it
        self.unread_move: tuple[Reading, dict[str, Any]] | None = None
        self.visits: dict[str, int] = {}
        self.trial = 0
        self.rested: list[float] = []
        self.rested_at = 0.0
        # screens the model read for an adapter that could not (`relearned`), and how often the adapter then had to
        # be written again: only that is bounded, since a screen that only adds samples costs one question
        self.relearned = self.rewrites = self.generations = self.attempts = 0
        # values the adapter's code left unread that the rules told instead of a model (`_learn_unread`)
        self.told_by_rules = 0
        # each screen the model read while there was an adapter, and why (`_reread`)
        self.rereads: list[dict[str, Any]] = []
        self.need = adaptation.EXPLORE_MOVES
        self.failures: list[dict[str, Any]] = []
        self.model_seconds = 0.0
        self.levels = {L2: 0, L3: 0}
        # the registered family whose board the screen was read as, to go back to from the model's own play
        # (`_resume`), the times that was tried, and whether play is by a family gone back to
        self.resumable: str | None = None
        self.resumes = 0
        self.resumed = False
        # the model's own moves confirmed since the family was last tried
        self.since_tried = 0
        # moves in a row the game did not take (`FRUITLESS_MOST`)
        self.fruitless = 0
        # the model's own play, kept when play goes back to a family and comes back to it
        self.player: direct.Direct | None = None

    # ── the game ──────────────────────────────────────────────────────

    async def run(self) -> tuple[Run, dict[str, Any]]:
        try:
            try:
                await self._begin()
                while True:
                    await self._turn()
            except _End as end:
                outcome, reason, detail = end.outcome, end.reason, end.detail
            except Exception as error:
                log.exception("the game's loop failed")
                outcome, reason, detail = Outcome.GAVE_UP, "error", f"{type(error).__name__}: {error}"[:2000]
            if outcome in (Outcome.GOAL_REACHED, Outcome.GAME_OVER):
                await self._keep_for_next()
            if self.game is not None:
                self.found["made"] = self.game.summary()
            self._keep_looks()
            record = {"version": RECORD_VERSION, "family": self.family_id, "phase": "ended", "outcome": str(outcome),
                      "reason": reason, "detail": detail, "goal": self.goal.to_json(), "budget": self.budget.to_json(),
                      "policy": self.policy, "actions_used": self.used, "confirmed_actions": self.confirmed,
                      "levels": dict(self.levels), "history": self.moves[-HISTORY_KEPT:]}
            run = Run(record, list(self.moves), looks=self.shots, last_screen=self.last_png,
                      recoveries=list(self.recovery.log) if self.recovery else [])
            failed = {"failed": reason, "detail": detail} if outcome is Outcome.UNSUPPORTED else {}
            return run, {**self.summary(), **self.found, **failed}
        finally:
            self.close()

    def summary(self) -> dict[str, Any]:
        return {"reused": self.made.reused if self.made else None, "generations": self.generations,
                "relearned": self.relearned, "told_by_rules": self.told_by_rules,
                "quick_looks": self.seen.quick, "looks": len(self.trace.looks) if self.trace else 0,
                "frames": self.frames.summary() if hasattr(self.frames, "summary") else None,
                "rereads": self._rereads(),
                "questions": self.model.questions, "seconds": round(self.model_seconds, 1),
                "tokens": {"input": self.model.input, "output": self.model.output},
                "recovery": self.recovery.summary() if self.recovery else None,
                "digest": self.adapter.digest if self.adapter else None,
                "calls": self.adapter.calls if self.adapter else None,
                **({"generation_failures": self.failures} if self.failures else {})}

    def close(self) -> None:
        if self.adapter is not None:
            self.adapter.close()
            self.adapter = None
        if self.game is not None:
            self.game.close()
        if self.store is not None and self.recovery is not None and self.family_id and self.site and self.recovery.known:
            self.store.save_recoveries(self.site, self.family_id, self.recovery.known)

    async def _begin(self) -> None:
        """The family the game is played as: the one given, the one a kept adapter of this site names, else
        each a model names for the screen, likeliest first (`_take`)."""
        self.site = await self.surface.address()
        self.candidates = [self.family_id] if self.family_id else []
        if self.family_id is None:
            kept = [] if self.store is None or self.fresh else \
                [name for name in explorable() if self.store.load(self.site, name) is not None]
            if kept:
                self.candidates, self.found["identified"] = kept[:1], {"family": kept[0], "source": "kept", "questions": 0}
            else:
                asked = await self._identify()
                self.candidates, self.named = list(asked["families"]), True
                self.found["identified"] = {"family": None, "source": "model", "candidates": list(self.candidates),
                                            "tried": [], "questions": asked["questions"], "inputs": asked["inputs"],
                                            "situation": asked["situation"]}
        if not await self._next_family() and not await self._make_family():
            await self._play_direct(self.found.get("identified", {}).get("situation") or "")

    async def _identify(self) -> dict[str, Any]:
        """Which families the game may be, as a model sees it; asked again when the model could not be reached
        (a gateway's timeout is not the game's end)."""
        for attempt in range(1 + IDENTIFY_RETRIES):
            try:
                recovery = Recovery(self.model, None, known=[], accept_consent=self.accept_consent)
                return await self._asking(identify(self.seen, self.model, recovery, hint=self.hint))
            except Exception as error:
                log.info("the game could not be asked about: %s", error)
                self.failures.append({"reason": "model_failed", "detail": f"{type(error).__name__}: {error}"[:500]})
                if attempt == IDENTIFY_RETRIES:
                    raise _End(Outcome.GAVE_UP, "model_failed", f"{type(error).__name__}: {error}"[:2000]) from None
                await asyncio.sleep(IDENTIFY_RETRY_S)

    async def _next_family(self) -> bool:
        """The next family the game may be, taken when its board reads from the screen."""
        while self.candidates:
            if await self._take(self.candidates.pop(0)):
                return True
        return False

    async def _take(self, family_id: str, probe: bool = False) -> bool:
        if self.adapter is not None:
            self.adapter.close()
        self.adapter = self.trace = self.made = self.board = self.carried = self.unproven = self.unread_move = None
        try:
            self.policy = check(self.goal, self.budget, family_id, self.asked_policy)
            self.recovery = Recovery(self.model, family_id, accept_consent=self.accept_consent,
                                     known=self.store.recoveries(self.site, family_id) if self.store else None)
            self.adaptation = Adaptation(self.model, family_id, policy=self.policy, compute=self.budget.compute,
                                         writer=self.writer)
        except AdaptationFailed as error:
            raise _End(Outcome.UNSUPPORTED, error.reason, error.detail) from None
        except ValueError as error:
            raise _End(Outcome.UNSUPPORTED, str(error)) from None
        self.family_id = family_id
        kept = None if self.store is None or self.fresh else self.store.load(self.site, family_id)
        if kept is not None:
            source, meta, self.trace = kept
            self.made, self.generations = Made(source, meta["generations"], True, meta["report"]), meta["generations"]
            self.board = self.trace.looks[0].board
            self._adopted()
        elif self.named or probe:
            if self.named and not probe:
                self.found["identified"]["tried"].append(family_id)
            # the first registered family named for the game is the one to go back to from the model's own
            # play, even when its board is not read now: a tutorial or a notice may be in the way
            if self.resumable is None and (self.game is None or family_id != self.game.id):
                self.resumable = family_id
            # a family the screen's board cannot be read as is not this game
            if not await self._asking(self.adaptation.probe(self.seen)):
                return False
        self.family = catalog.family(family_id).bind({"rules": self.trace.rules} if self.trace else None)
        if "identified" in self.found:
            self.found["identified"]["family"] = family_id
        return True

    # ── one move ──────────────────────────────────────────────────────

    async def _turn(self) -> None:
        self._check_stops()
        before, self.carried = self.carried, None
        if before is None:
            # a move whose result nobody read is still what this screen must be the result of: no move is
            # chosen from a reading that takes the last one back
            held, self.unread_move = self.unread_move, None
            before = await self._read(held)
            if before is None:
                self.unread_move = held
                await self._unstick(self.why)
                return
            if held is not None and self._effect(held[0].state, held[1], before.state) == "absent":
                self._spent(held[0].id, held[1])
        self.state = before.state
        self._judge(before.state)
        excluded = self.ineffective["actions"] if self.ineffective and self.ineffective["state"] == before.id else []
        action = self.family.choose(before.state, self.policy, self.budget.compute, tuple(excluded))
        if action is None or action["kind"] in self.family.meta_actions:
            # every move this board offers was tried without effect, or the game waits at a message of its own
            await self._unstick("absent" if action is None else action["kind"])
            return
        sent = self._aim(action, before)
        if sent is None:
            self._doubt()
            return
        if self.adapter is None or self.distrusted:
            self._afford()               # the move's result is the model's to read: none is sent that it cannot be asked about
        if self.watch is not None:
            await self.watch.sending(self.used + 1, action, before.state, sent, before.at)
        await self._send(sent, before, action)
        self.used += 1
        if self.watch is not None:
            await self.watch.sent()
        sent_at = time.monotonic()
        try:
            after = await self._read((before, action))
        except _End:
            self._remember(before, action, "unknown", None)
            raise
        effect = "unknown" if after is None else self._effect(before.state, action, after.state)
        self._remember(before, action, effect, after)
        if self.over:
            raise _End(Outcome.GAME_OVER, "terminal")
        if after is None:
            self.unread_move = (before, action)
            if self.why == "inconsistent" and await self._rules_again():
                # the screen was read, and the made family's rules refused it: with rules written again from
                # it, the same screen is read once more as that move's result
                return
        if effect == "confirmed":
            await self._confirmed(before, action, sent, after, sent_at)
            return
        self.stalled += 1
        if self.carried_as and self.carried_as.get("picked") and effect in ("absent", "inconsistent"):
            # a piece picked up and let go that did not do what it should: how it follows is measured again
            self.carrier.doubt()
        if effect == "absent":
            self._spent(before.id, action)
            self.fruitless += 1
            if self.fruitless >= FRUITLESS_MOST and self.direct:
                await self._leave("fruitless")
                return
        if before.look is not None and before.look is self.unproven:
            self._drop_unproven()
        if after is None and self.why == "label_failed":
            # the model saw no board where the move's result should be: what is in the way is dealt with now
            await self._unstick(self.why)
            return
        # the adapter is doubted for a move it read either side of: a board it read wrongly is as often the
        # one after the move (a place it takes for empty) as the one before
        # not for a move whose carry is not known to have put the piece where it was meant to go (it picked
        # nothing up, or could not tell where the piece hung): that is the carry's, until moves keep failing
        carried_unsure = self.carried_as is not None and not carry.trusted(self.carried_as)
        if L3 in (before.by, after.by if after else None) and (
                self.stalled >= MAX_STALLED or (self.trial > 0 and not carried_unsure)):
            self._doubt()

    def _spent(self, state: str, action: dict[str, Any]) -> None:
        """A move this board did not take: left out when the next is chosen from the same board."""
        kept = self.ineffective if self.ineffective and self.ineffective["state"] == state else {"state": state, "actions": []}
        self.ineffective = {**kept, "actions": [*kept["actions"], action]}

    async def _leave(self, reason: str) -> None:
        """Play by the family's rules gets nowhere: the game goes on by the model's own inputs, and comes back to
        the family once some of those are confirmed (`_resume`)."""
        log.info("the family is left: %s", reason)
        self.failures.append({"reason": "family_left", "detail": reason})
        self.fruitless = self.since_tried = 0
        if self.resumable is None and (self.game is None or self.family_id != self.game.id):
            self.resumable = self.family_id
        await self._play_direct(self._situation())

    async def _confirmed(self, before: Reading, action: dict[str, Any], sent: dict[str, Any], after: Reading,
                         sent_at: float) -> None:
        self.confirmed += 1
        self.stalled = self.tries = self.fruitless = 0
        if self._made_plays():
            self.transitions = [*self.transitions, {"state": before.state, "action": action, "after": after.state}][-made.EXAMPLES_KEPT:]
        self.shrugged, self.ineffective, self.carried, self.state = False, None, after, after.state
        self.recovery.succeeded()
        # a game that keeps coming back to the same state by its own moves is going nowhere (a stock turned
        # round and round with nothing to play): it is over, whatever moves it still offers
        self.visits[after.id] = self.visits.get(after.id, 0) + 1
        if self.visits[after.id] >= MAX_VISITS:
            raise _End(Outcome.GAME_OVER, "no_progress")
        if before.by == L3 and after.by == L3:
            self.earned += 1
        if before.by == L3 and after.by == L3 and self.trial > 0:
            self.trial -= 1
            self._time_trial(self.rested_at - sent_at)
        if before.look is None or after.look is None:
            return
        # two screens the model read with a confirmed move between them: what an adapter is written from
        before.look.action, before.look.sent = action, sent
        if before.look is self.unproven:
            self.unproven = None
        if self.adapter is not None:
            if self.distrusted:
                # the adapter was doubted: it is checked against what the model read, and written again if it must be
                self.relearned += 1
                await self._recheck()
        elif sum(look.action is not None for look in self.trace.looks) >= self.need and self.attempts <= RETRIES:
            await self._generate(None)

    def _doubt(self) -> None:
        """The adapter's reading may be what is wrong: the model reads the next screens."""
        if self.adapter is not None:
            if self.rewrites >= MAX_RELEARNS:
                self._demote("relearn_limit")
            else:
                self.distrusted = True
            self.stalled = 0

    def _effect(self, before: dict[str, Any], action: dict[str, Any], after: dict[str, Any]) -> str:
        return self.family.model.assess(before, action, after, self.family.rules)["effect"]

    def _judge(self, state: dict[str, Any]) -> None:
        objective = str(self.goal.objective)
        if objective == "actions" and self.confirmed >= (self.goal.target_value or 1):
            raise _End(Outcome.GOAL_REACHED, "actions")
        seen = self.family.model.status(state, self.family.rules, {"objective": objective, "target_value": self.goal.target_value})
        if seen["goal"] == "reached":
            raise _End(Outcome.GOAL_REACHED, WON if state.get("scene") == WON else objective)
        if seen["terminal"]:
            raise _End(Outcome.GAME_OVER, "terminal")

    def _remember(self, before: Reading, action: dict[str, Any], effect: str, after: Reading | None) -> None:
        self.levels[before.by] += 1
        self.moves.append({"action_id": f"a{self.used}", "kind": action.get("kind"), "action": action, "level": before.by,
                           "read_after_by": after.by if after else None, "delivery": "delivered", "effect": effect,
                           **({"carried": self.carried_as} if action is not None and self.carried_as else {}),
                           "round": self.relearned, "at_s": round(time.monotonic() - self.started, 2)})

    def _check_stops(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise _End(Outcome.BUDGET_EXHAUSTED, "cancelled")
        if self.used >= self.budget.max_actions:
            raise _End(Outcome.BUDGET_EXHAUSTED, "actions")
        self._check_time()

    def _check_time(self) -> None:
        if self._left() is not None and self._left() <= 0:
            raise _End(Outcome.BUDGET_EXHAUSTED, "time")

    def _left(self) -> float | None:
        return None if self.budget.max_seconds is None else self.budget.max_seconds - (time.monotonic() - self.started)

    def _affordable(self, cost: int = 0) -> bool:
        """Whether a question of about `cost` input tokens still fits the game's budget of them."""
        return self.budget.max_input_tokens is None or self.model.input + cost <= self.budget.max_input_tokens \
            and self.model.input < self.budget.max_input_tokens

    def _afford(self) -> None:
        """Before a model is asked anything: a game whose questions used up their tokens ends there."""
        if not self._affordable():
            raise _End(Outcome.BUDGET_EXHAUSTED, "tokens")

    async def _asking(self, question: Any) -> Any:
        started = time.monotonic()
        try:
            return await question
        finally:
            self.model_seconds += time.monotonic() - started

    # ── reading ───────────────────────────────────────────────────────

    async def _read(self, expect: tuple[Reading, dict[str, Any]] | None) -> Reading | None:
        """The screen as a board to play on, read by the adapter when there is one that is trusted, else by
        the model; None when it shows none (`why`). `expect`: the move just played and the board it was
        played from, whose result this is."""
        self.over = False
        if self.adapter is not None and not self.distrusted:
            reading = await self._read_code(expect)
            if reading is not None:
                return reading
            if expect is not None and self.unread is not None and self.family.model.ended(
                    expect[0].state, expect[1], self.unread, self.family.rules):
                # what the screen leaves unread (an end notice over part of it) cannot undo an end the rules see
                self.over = True
                return None
            if self.rewrites >= MAX_RELEARNS:
                self._demote("relearn_limit")
            elif self.unread is None and expect is None:
                # nothing vouches for what the model will read here until a move from it is confirmed
                self.distrusted = self.blind = True
            else:
                why = self.why
                reading = await self._read_model(expect, self.unread)
                self._reread(why)
                if reading is not None:
                    self.relearned += 1
                    await self._recheck()
                elif expect is None and self.why == "inconsistent":
                    # the model's reading and what the adapter made of the screen do not go together: the
                    # adapter is doubted, and the screen read without it
                    self.distrusted = True
                return reading
        reading = await self._read_model(expect, None)
        if self.adapter is not None:
            self._reread("distrusted")
        return reading

    def _rereads(self) -> dict[str, Any]:
        by: dict[str, int] = {}
        for entry in self.rereads:
            by[entry["why"]] = by.get(entry["why"], 0) + 1
        return {"by_why": by, "moving": sum(entry["moving"] for entry in self.rereads), "each": self.rereads[-60:]}

    def _reread(self, why: str) -> None:
        """A screen the adapter was there for and the model read: why, and whether what kept moving on it was
        left out of the picture the model was shown (`motion`)."""
        self.rereads.append({"at_action": self.used, "why": why, "moving": bool(self.seen.moving)})

    def _observe(self, png: bytes) -> tuple[Any, str | None]:
        """One capture read by the adapter: its state, or why there is none."""
        adapter = self.adapter
        self.sequence += 1
        observation = Observation(f"o{self.sequence}", self.sequence, self.surface.name, 0, time.monotonic(), "agent")
        measured = adapter.layout is not None
        if not measured and isinstance(found := adapter.measure(png), NeedObservation):
            return None, found.reason
        state = adapter.read(observation, png)
        if isinstance(state, NeedObservation) and measured:
            # the board may lie elsewhere now (another window size): measured again, and read again if it does
            found = adapter.measure(png)
            if isinstance(found, NeedObservation):
                return None, found.reason
            if found:
                state = adapter.read(observation, png)
        if isinstance(state, NeedObservation):
            return None, state.reason
        return state, None

    async def _read_code(self, expect: tuple[Reading, dict[str, Any]] | None) -> Reading | None:
        """The adapter's reading once the screen has come to rest: a state seen unchanged for the settling's
        stable time, or sooner the predicted result of the move just played."""
        policy, clock = self.settling, time.monotonic
        # the first frame looked at: one taken once the move's animation is over
        after = clock() + policy.animation_s if expect is not None and policy.animation_s else None
        deadline = (after or clock()) + policy.timeout_s
        previous = pictured = read = unread = None
        stable_since = rested = read_since = unread_since = clock()
        while True:
            self._check_time()
            # a screen something kept moving over is read as it is under that, for as long as it keeps moving
            frame = await (self.seen.steady(after) if self.seen.animating else self.frames.frame(after))
            png, looked = frame.png, frame.at
            self.shots += 1
            self.last_png = png
            if self.watch is not None:
                await self.watch.seen(png)
            state, why = self._observe(png)
            if state is None and why == adaptation.CELLS_UNREAD and expect is not None and self._learn_unread(png, expect):
                # the rules told what the code could not read: it reads it now
                state, why = self._observe(png)
            usable = state is not None
            if not usable:
                # a screen read through small moving things that could not be read is drawn through next time
                self.seen.forget()
                self.why = why
                self.unread = self.adapter.partial if why == adaptation.CELLS_UNREAD else None
                seen = (why, self.unread)
                if seen != unread:
                    unread, unread_since = seen, looked
                if clock() - unread_since >= max(policy.stable_s, RESTLESS_S) or clock() >= deadline:
                    return None
            image = self.adapter.picture(png) if usable and policy.still else None
            key = (state.id, state.geometry_ref, image) if usable else None
            if key is None or key != previous:
                stable_since = clock()
            # the same state under pixels that keep moving: an animation that never ends is not waited out
            if key is None or key[:-1] != read:
                read_since = looked
            read = key[:-1] if key is not None else None
            restless = policy.still and usable and clock() - read_since >= RESTLESS_S
            settled = not policy.still or image == pictured
            if image is None or image != pictured:
                rested = looked
            previous, pictured = key, image
            self.rested_at = read_since if restless else rested
            if usable:
                reading = Reading(png, state.payload, looked, L3)
                if expect is not None and (settled or restless) \
                        and self._effect(expect[0].state, expect[1], state.payload) == "confirmed":
                    return reading
                stable = clock() - stable_since >= policy.stable_s or restless
                if stable and (expect is None or clock() >= deadline):
                    return reading
                if clock() >= deadline:
                    self.why, self.unread = "surface_unsettled", None
                    return None
            # the next frame: one taken this long after this one, and no later than the deadline
            after = min(looked + policy.interval_s, deadline)

    def _learn_unread(self, png: bytes, expect: tuple[Reading, dict[str, Any]]) -> bool:
        """What the adapter's code left unread on the result of a move (a look of a value it has no sample of
        yet), told by the rules instead of a model, when every result the move can have that agrees with what
        was read has the same there (two tiles joined into one of a value not seen before). Its samples are kept,
        so that the code reads it from now on (PLAN §17, F5). False when the rules leave it open."""
        outcomes = getattr(self.family.model, "outcomes", None)
        partial = self.adapter.partial if self.adapter is not None else None
        if outcomes is None or partial is None:
            return False
        try:
            possible = [state for _, state in outcomes(expect[0].state, expect[1], self.family.rules)]
        except (ValueError, KeyError, TypeError):
            return False
        agreeing = [state for state in possible if _agrees(_board(partial), _board(state))]
        if not agreeing or any(_board(state) != _board(agreeing[0]) for state in agreeing[1:]):
            return False
        raw = self.adapter.raw(png)
        if not isinstance(raw, dict):
            return False
        image, kept = samples._open(png), []
        try:
            for path, box in samples.marks(raw):
                if samples._at(partial, path) is not None:
                    continue            # read already: only what was left unread is told
                value, sample = samples._at(agreeing[0], path), samples.cut(image, box)
                if value is None or sample is None:
                    return False
                kept.append((value, sample))
        except (KeyError, IndexError, TypeError):
            return False
        for value, sample in kept:
            self.adapter.bank.add(value, sample)
        self.told_by_rules += len(kept)
        return bool(kept)

    async def _read_model(self, expect: tuple[Reading, dict[str, Any]] | None, partial: dict[str, Any] | None) -> Reading | None:
        """The screen read by the model into a state the rule model accepts, and kept as a look. With `expect`
        it must be a result the rules allow of that move (or the board unchanged: a move the game did not
        take); with `partial` and no move to hold it to, what the adapter did read of the screen must agree
        with it."""
        self._afford()
        if expect is None:
            self._drop_unproven()
        tool, first = self.adaptation, self.trace is None
        if expect is not None:
            await asyncio.sleep(adaptation.SETTLE_S)
            await self._rest()

        def accept(state: dict[str, Any]) -> bool:
            # a result the rules allow of the move just played needs no leave from the adapter: what the adapter
            # made of the screen is held to only where nothing else vouches for the reading
            if expect is not None:
                return self._effect(expect[0].state, expect[1], state) in ("confirmed", "absent")
            return partial is None or _agrees(_board(partial), _board(state))
        started = time.monotonic()
        try:
            if first and tool.first is not None:
                (png, state, board, rules), tool.first = tool.first, None
            else:
                png, state, board, rules = await self._asking(tool._label(
                    self.seen, self.board, self.trace.rules if self.trace else None,
                    accept if expect is not None or partial is not None else None, patient=False, prior=self._prior(expect)))
                tool.last_state, tool.frames = state, await tool._frames(self.seen, png)
        except AdaptationFailed as error:
            # no board on the screen, or one the model could not read as the rules have it
            if expect is not None and error.detail == "transition_refused" and tool.refused is not None:
                self.refused = {"state": expect[0].state, "action": expect[1], "after": tool.refused}
            self.why = "label_failed" if error.detail == "no_board" else "inconsistent"
            return None
        except Exception as error:
            log.exception("the model could not be asked")
            self.why = "model_failed"
            self.failures.append({"reason": "model_failed", "detail": f"{type(error).__name__}: {error}"[:500]})
            return None
        self.last_png = png
        if first:
            self.trace = Trace(self.family_id, rules, self.site)
            self.family = catalog.family(self.family_id).bind({"rules": rules})
        # where the board is now: the first screen's box, or another when the game has moved on the screen
        self.board = board
        look = Look(png, state, self.board, frames=tool.frames, sources=tool.sources)
        self.trace.looks.append(look)
        if expect is None and partial is None:
            self.unproven = look
        return Reading(png, state, started, L2, look)

    async def _rest(self) -> None:
        """Wait until the game's part of the screen shows the same twice in a row: a move's result is read by
        the model, and kept as a look to write an adapter from, once what the move set off (blocks clearing, a
        banner flashing over the board) is over; not waited for longer than STILL_TIMEOUT_S."""
        from io import BytesIO
        from PIL import Image

        if self.seen.animating:
            # it never rests: the reading leaves out what keeps moving instead
            return
        boxes = [self.board, *(box for look in self.trace.looks[-1:] if isinstance(look.sources, list)
                               for box in look.sources if box)]
        left, top = min(box[0] for box in boxes), min(box[1] for box in boxes)
        right, bottom = max(box[0] + box[2] for box in boxes), max(box[1] + box[3] for box in boxes)
        previous, after, deadline = None, None, time.monotonic() + STILL_TIMEOUT_S
        while True:
            frame = await self.frames.frame(after)
            with Image.open(BytesIO(frame.png)) as image:
                seen = list(image.convert("L").crop((left, top, right, bottom)).resize((STILL_SIDE, STILL_SIDE)).tobytes())
            if previous is not None and sum(abs(a - b) for a, b in zip(seen, previous)) / len(seen) <= STILL_DISTANCE:
                return
            if time.monotonic() >= deadline:
                return
            previous, after = seen, frame.at + STILL_GAP_S

    def _prior(self, expect: tuple[Reading, dict[str, Any]] | None) -> dict[str, Any] | None:
        """What is known of the screen about to be read: what the rules say the move just played leaves (where
        the rule model says), else the last state read."""
        predict = getattr(self.family.model, "apply", None) if expect is not None else None
        if predict is None:
            return self.state
        try:
            return predict(expect[0].state, self.family.rules, expect[1])["state"]
        except (ValueError, KeyError, TypeError):
            return self.state

    def _drop_unproven(self) -> None:
        """A look nothing bore out is not one to write an adapter from."""
        if self.unproven is not None and self.trace is not None:
            self.trace.looks = [look for look in self.trace.looks if look is not self.unproven]
        self.unproven = None

    # ── the adapter ───────────────────────────────────────────────────

    def _adopted(self) -> None:
        """Play by the adapter as made: its first moves are trial moves."""
        if self.adapter is not None:
            self.adapter.close()
        self.adapter = adaptation.GeneratedAdapter(self.family_id, self.made.source, rules=self.trace.rules, site=self.site)
        self.adapter.keep(self.trace.looks)
        self.settling = Settling(**self.adapter.played.get("settling", {}))
        self.distrusted, self.blind, self.trial, self.rested, self.carried = False, False, TRIAL_MOVES, [], None
        self.earned = 0

    def _demote(self, reason: str) -> None:
        """No adapter reads this game: the model reads it from here on."""
        log.info("the game goes on read by the model: %s", reason)
        if self.adapter is not None:
            self.adapter.close()
        self.adapter, self.distrusted, self.blind = None, False, False
        if self.carried is not None and self.carried.by == L3:
            self.carried = None
        self.need = sum(look.action is not None for look in self.trace.looks) + RETRY_LOOKS

    async def _recheck(self) -> None:
        """The adapter against every look so far, the one just read included: kept when it reads them all,
        else written again from them."""
        report = adopt(self.made.source, self.trace)
        if report["passed"]:
            # the look just read gave its samples: what the code leaves to the host is now known there too
            self.adapter.keep(self.trace.looks)
            self.distrusted, self.blind, self.trial = False, False, TRIAL_MOVES
            if self.store is not None:
                self.store.save(self.trace, self.made)
        else:
            # an adapter that read few moves since it was written and must be written again is churning: the
            # screens it cannot keep up with are the model's to read
            if self.earned < EARNED:
                if self.churn >= MAX_CHURN:
                    self._demote("rewrite_churn")
                    return
                self.churn += 1
            else:
                self.churn = 0
            self.rewrites += 1
            await self._generate((self.made.source, report["problems"]))

    async def _generate(self, previous: tuple[str, list[str]] | None) -> bool:
        """An adapter written from the looks so far and adopted when it passes on all of them. When none
        passes the game goes on read by the model, and one is tried again after RETRY_LOOKS more moves."""
        tool, trace = self.adaptation, self.trace
        if not any(look.held for look in trace.looks):
            for look in trace.looks[-HELD_BACK:]:
                look.held = True
        self._settle_rules()
        self.attempts += 1
        try:
            made = await self._asking(tool.make(trace, previous, allowed=self._affordable))
        except Exception as error:
            if not isinstance(error, AdaptationFailed):
                log.exception("no adapter could be written")
                error = AdaptationFailed("model_failed", f"{type(error).__name__}: {error}")
            self.generations += MAX_GENERATIONS if error.reason == "no_adapter_passed" else 0
            self.failures.append({"reason": error.reason, "detail": error.detail[:8000]})
            self._demote(error.reason)
            return False
        self.generations += made.generations
        self.made = Made(made.source, self.generations, False, made.report, made.seconds, made.questions)
        self._adopted()
        if self.store is not None:
            self.store.save(trace, self.made)
        return True

    def _settle_rules(self) -> None:
        """A rule value the game may be played without knowing is taken from the screen only when every screen
        read said the same (a count beside the board, not a clock): a wrong value is worse than none."""
        tool, changed = self.adaptation, False
        for name in tool._unknowable():
            said = {told.get(name) for told in tool.told}
            if len(said) == 1 and None not in said and self.trace.rules.get(name) is None:
                self.trace.rules[name], changed = said.pop(), True
        if changed:
            self.family = catalog.family(self.family_id).bind({"rules": self.trace.rules})

    async def _keep_for_next(self) -> None:
        """A game that ended before an adapter was tried still showed its screens: enough of them are written
        from now, for the next game on this site."""
        if self.store is None or self.adapter is not None or self.attempts or self.trace is None \
                or len(self.trace.looks) <= HELD_BACK + 2 or not self._affordable():
            return
        try:
            await self._generate(None)
        except Exception:
            log.exception("no adapter was kept for the next game")

    def _keep_looks(self) -> None:
        """A game no adapter was adopted for leaves its looks behind, for whoever looks into why."""
        if self.store is not None and self.adapter is None and self.trace is not None and self.trace.looks and self.failures:
            self.store.save_looks(self.trace)

    def _time_trial(self, rested_s: float) -> None:
        """A trial move's time to come to rest; after the last, a game that had to be still waits the longest
        of them and looks once."""
        if not self.settling.still:
            return
        self.rested.append(round(rested_s, 3))
        if self.trial == 0:
            animation = min(ANIMATION_MAX_S, max(self.settling.animation_s, *self.rested) + ANIMATION_MARGIN_S)
            self.settling = replace(self.settling, animation_s=round(animation, 3), still=False)

    # ── input ─────────────────────────────────────────────────────────

    def _aim(self, action: dict[str, Any], before: Reading) -> dict[str, Any] | None:
        """The input a move is played with: where the adapter's `target` says for a board it read, else placed
        on the board box the model gave. None when the adapter's input is one no game is given."""
        if before.by == L2:
            return self.adaptation.input_for(action, self.board, _shape(self.family, before.state), before.state,
                                             before.look.sources)
        asked = self.adapter._call("target", action=action, state=before.state, layout=self.adapter.layout)
        try:
            self.adapter.checked_input(asked)
        except ValueError:
            return None
        return asked

    async def _send(self, sent: dict[str, Any], before: Reading, action: dict[str, Any]) -> None:
        surface, self.carried_as = self.surface, None
        if sent["kind"] == "key":
            if "keys" in surface.kinds:
                await surface.key(sent["key"])
            else:
                left, top, width, height = self.adapter.layout["board"] if self.adapter and self.adapter.layout else self.board
                across, down = SWIPES[sent["key"]]
                x, y = left + width / 2, top + height / 2
                reach_x, reach_y = across * width * SWIPE_SPAN / 2, down * height * SWIPE_SPAN / 2
                await surface.drag(x - reach_x, y - reach_y, x + reach_x, y + reach_y, SWIPE_MS)
        elif sent["kind"] == "tap":
            await surface.tap(sent["x"], sent["y"], TAP_MS)
            await self._then(sent, before, action)
        elif "source" not in self.adaptation.controls:
            await surface.drag(sent["x0"], sent["y0"], sent["x1"], sent["y1"], sent["ms"])      # between two cells: nothing is carried
        else:
            self.carried_as = await self.carrier.carry((sent["x0"], sent["y0"]), (sent["x1"], sent["y1"]), sent["ms"],
                                                       before=before.png, source=self._source(before, action))

    def _source(self, before: Reading, action: dict[str, Any]) -> list[int] | None:
        """Where the screen a move is played from was read to show the thing the move drags, when it was."""
        sources = before.look.sources if before.look is not None else None
        index = action.get(self.adaptation.controls.get("index", "")) if self.adaptation else None
        if isinstance(sources, list) and type(index) is int and 0 <= index < len(sources) \
                and isinstance(sources[index], list) and len(sources[index]) == 4:
            return [round(part) for part in sources[index]]
        return None

    async def _then(self, sent: dict[str, Any], before: Reading, action: dict[str, Any]) -> None:
        """What completes a move whose first tap only picks something (the controls' `then`): the same tap
        again, a key the move names, or a tap on a second cell it names."""
        controls = self.adaptation.controls
        if "then" not in controls:
            return
        await asyncio.sleep(THEN_S)
        if controls["then"] == "tap":
            await self.surface.tap(sent["x"], sent["y"], TAP_MS)
        elif controls["then"] == "key":
            await self.surface.key(str(action[controls["key"]]))
        else:
            board = self.adapter.layout["board"] if before.by == L3 and self.adapter and self.adapter.layout else self.board
            left, top, width, height = adaptation.cell_box(board, _shape(self.family, before.state), action[controls["to"]])
            await self.surface.tap(left + width / 2, top + height / 2, TAP_MS)

    # ── a screen that stopped play ────────────────────────────────────

    async def _unstick(self, reason: str) -> None:
        """Play cannot go on from this screen: a model is shown it and sends one input of its own (L0), and
        the screen is looked at again. The game ends when the model sees its end on the screen, sees nothing
        to do, or MAX_TRIES inputs in a row brought no confirmed move."""
        if reason == "inconsistent" and (await self._rules_again() or await self._other_family()):
            return
        if self.tries >= MAX_TRIES:
            if await self._other_family():
                return
            await self._give_up(reason)
            return
        self._afford()
        self.tries += 1
        self.carried = None
        recent = [{key: move[key] for key in ("action_id", "kind", "effect")} for move in self.moves[-HISTORY_KEPT:]]
        if await self._asking(self.recovery.attempt(self.seen, reason, {"state": self.state, "recent": recent},
                                                    timeout_s=self._left())):
            if self.blind:
                self.distrusted = self.blind = False
            # an input of the model's stands between the last move and this screen: it is read for itself
            self.unread_move = None
            return
        action = self.recovery.log[-1]["action"] or {}
        if action.get("kind") == GIVE_UP and "end" in action:
            won = action["end"] == WON and self._won_meets_goal()
            raise _End(Outcome.GOAL_REACHED if won else Outcome.GAME_OVER, WON if won else "end_screen", self._situation())
        if action.get("kind") == NONE and not self.shrugged:
            # the model sees a board in play: it is read once more before the game is given up
            self.shrugged = True
            return
        if await self._other_family():
            return
        await self._give_up(reason)

    # ── a game of no registered family ────────────────────────────────

    async def _make_family(self) -> bool:
        """No registered family is this game's: a model describes it, and when it is one that can be played by
        written rules (a grid, one kind of move) a family is made for it (`made`): its board is read by the
        description and its rules written as code. True when the game goes on as that family."""
        if self.game is not None or not self.direct:
            return False
        self._afford()
        try:
            game = await self._asking(made.describe(self.seen, self.model, self.site, hint=self.hint))
        except Exception as error:
            log.info("the game could not be described: %s", error)
            self.failures.append({"reason": "model_failed", "detail": f"{type(error).__name__}: {error}"[:500]})
            return False
        if game is None:
            return False
        self.game, self.named = game, True
        try:
            # its board must read by the description before anything is written for it
            if not await self._take(game.id):
                return False
        except _End:
            return False
        state = self.adaptation.first[1]
        if not await self._asking(game.write(self.writer or self.model, [state], allowed=self._affordable)):
            self.failures.append({"reason": "no_rules_passed", "detail": json.dumps(game.problems)[:2000]})
            return False
        return True

    def _made_plays(self) -> bool:
        return self.game is not None and self.family_id == self.game.id and self.game.code.source is not None

    async def _rules_again(self) -> bool:
        """A made family's rules refused what the screen really showed after a move: they are written again
        from it. True when rules that take it (and every move borne out before) are now in use."""
        refused, self.refused = self.refused, None
        if not self._made_plays() or refused is None or not self._affordable():
            return False
        written = await self._asking(self.game.rewrite(self.writer or self.model, [refused["state"]], self.transitions,
                                                       refused, allowed=self._affordable))
        if written:
            self.tries, self.stalled = 0, 0
        return written

    async def _give_up(self, reason: str) -> None:
        """Play cannot go on by the family's rules. A made family's rules may simply be wrong: the game is
        then the model's to play with its own inputs. Otherwise the game is given up."""
        if self._made_plays() or self.resumed:
            # a family gone back to from the model's own play is left again the same way
            log.info("the family is dropped: %s", reason)
            self.failures.append({"reason": "made_family_dropped" if self._made_plays() else "family_dropped",
                                  "detail": reason})
            await self._play_direct(self._situation())
            return
        raise _End(Outcome.GAVE_UP, reason, self._situation())

    async def _play_direct(self, situation: str) -> None:
        """The game is none the harness has rules for: a model plays it with inputs of its own (L0), told after
        each what the code saw change on the screen. Ends the game (`_End`) at the game's own end as the model
        reads it, when nothing more can be done, or at a budget; returns only when a family's play broke off
        before and a move of the model's changed the scene so that the family reads the board again (`_resume`).
        A move counts as confirmed when the screen changed after it, which is all the code can say without
        rules."""
        if not self.direct:
            raise _End(Outcome.UNSUPPORTED, "game_not_identified", situation)
        if self.adapter is not None:
            self.adapter.close()
        self.adapter, self.family_id, self.policy = None, None, "model"
        if self.player is None:
            self.player = direct.Direct(self.model, hint=self.hint, accept_consent=self.accept_consent)
        player = self.player
        self.found["direct"] = told = player.summary()
        self.levels.setdefault(L0, 0)
        objective, idle, silent = str(self.goal.objective), 0, 0
        while True:
            told.update(player.summary())
            if objective == "actions" and self.confirmed >= (self.goal.target_value or 1):
                raise _End(Outcome.GOAL_REACHED, "actions")
            if objective == "reach_value" and player.score is not None and self.goal.target_value is not None \
                    and player.score >= self.goal.target_value:
                raise _End(Outcome.GOAL_REACHED, objective)
            self._check_stops()
            self._afford()
            before = await self.seen.capture()
            moving = list(self.seen.moving)
            looked = time.monotonic()
            self.shots += 1
            self.last_png = before
            if self.watch is not None:
                await self.watch.seen(before)
            left = {"inputs": self.budget.max_actions - self.used,
                    "seconds": None if self._left() is None else round(self._left())}
            try:
                answer = await self._asking(player.ask(before, self.surface.kinds, self.goal.to_json(), left, moving))
            except Exception as error:
                log.info("the model could not be asked for a move: %s", error)
                self.failures.append({"reason": "model_failed", "detail": f"{type(error).__name__}: {error}"[:500]})
                answer = None
            told.update(player.summary())
            if answer is None:
                silent += 1
                if silent >= MAX_SILENT:
                    raise _End(Outcome.GAVE_UP, "model_no_answer", player.situation)
                continue
            silent = 0
            action = answer["action"]
            if action["kind"] == direct.DONE:
                won = action["end"] == WON
                raise _End(Outcome.GOAL_REACHED if won and objective == "play_until_end" else Outcome.GAME_OVER,
                           WON if won else "end_screen", player.situation)
            if action["kind"] == direct.GIVE_UP:
                raise _End(Outcome.GAVE_UP, "model_gave_up", player.situation)
            if self.watch is not None:
                await self.watch.sending(self.used + 1, action, None, action, looked)
            carried = await direct.send(self.seen, action, self.carrier, before)
            self.used += 1
            if self.watch is not None:
                await self.watch.sent()
            after = await direct.settled(self.seen)
            moving = motion.joined(moving + self.seen.moving)
            changed = player.saw(answer, before, after, moving, carried)
            if carried and carried.get("picked") and not changed:
                self.carrier.doubt()
            self.levels[L0] += 1
            self.moves.append({"action_id": f"a{self.used}", "kind": action["kind"], "action": answer["told"], "level": L0,
                               "read_after_by": L0, "delivery": "delivered", "effect": "confirmed" if changed else "absent",
                               "expected": answer["expect"], "at_s": round(time.monotonic() - self.started, 2),
                               **({"carried": carried} if carried else {})})
            if changed or action["kind"] == direct.WAIT:
                self.confirmed, idle = self.confirmed + (action["kind"] != direct.WAIT), 0
            else:
                idle += 1
                if idle >= MAX_IDLE:
                    raise _End(Outcome.GAVE_UP, "no_effect", player.situation)
            self.since_tried += bool(changed)
            if changed and (self._new_scene(before, after, moving) or self._due()) and await self._resume():
                return

    def _due(self) -> bool:
        """Whether enough of the model's moves were confirmed since the family was last tried to try it again."""
        return self.resumable is not None and self.resumes < RESUMES_MOST and self.since_tried >= RESUME_EVERY

    def _new_scene(self, before: bytes, after: bytes, moving: list[list[int]]) -> bool:
        """Whether a move of the model's changed so much of the screen (left out what kept moving) that a family
        whose play broke off may read the board now: a tutorial gave way to the game, a level to the next."""
        if self.resumable is None or self.resumes >= RESUMES_MOST:
            return False
        from io import BytesIO
        from PIL import Image

        with Image.open(BytesIO(after)) as image:
            area = image.width * image.height
        changed = motion.outside(carry_boxes(before, after), moving)
        return sum(box[2] * box[3] for box in changed) >= RESUME_SHARE * area

    async def _resume(self) -> bool:
        """The family whose play broke off, tried again on the screen as it is now: taken when its board reads."""
        family_id, self.resumes, self.since_tried = self.resumable, self.resumes + 1, 0
        tried = {"family": family_id, "at_action": self.used, "taken": False}
        self.found.setdefault("resumes", []).append(tried)
        try:
            taken = await self._take(family_id, probe=True)
        except _End:
            raise
        except Exception as error:
            log.info("the family could not be tried again: %s", error)
            taken = False
        if not taken:
            # the model's own play goes on
            if self.adapter is not None:
                self.adapter.close()
            self.adapter, self.family_id, self.policy = None, None, "model"
            return False
        tried["taken"] = self.resumed = True
        self.tries = self.stalled = self.fruitless = 0
        self.shrugged = False
        self.carried = self.unread_move = self.ineffective = self.state = None
        return True

    def _won_meets_goal(self) -> bool:
        """Whether the game's own win message, with nothing under it read, is the goal reached: the rule
        model's to say (a win is the family's own target, which a run may have set higher)."""
        try:
            seen = self.family.model.status(self.family.unread_state(WON), self.family.rules,
                                            {"objective": str(self.goal.objective), "target_value": self.goal.target_value})
        except Exception:
            return False
        return seen["goal"] == "reached"

    def _situation(self) -> str:
        return (self.recovery.log[-1]["situation"] or "") if self.recovery and self.recovery.log else ""

    async def _other_family(self) -> bool:
        """A game a model named whose moves never did what the family's rules say may be another's: the next
        family named for it, else one made for it, else the model's own play."""
        if not self.named or self.confirmed or self._made_plays() or (not self.candidates and not self.direct):
            return False
        self.tries, self.stalled, self.shrugged = 0, 0, False
        if not await self._next_family() and not await self._make_family():
            await self._play_direct(self._situation())
        return True


async def play(surface: Any, model: Model, family_id: str | None, *, goal: GoalSpec, budget: Budget,
               store: Store | None = None, policy: str | None = None, fresh: bool = False, hint: str | None = None,
               watch: Watch | None = None, cancel: asyncio.Event | None = None, accept_consent: bool = False,
               writer: Model | None = None, direct: bool = True, **ignored: Any) -> tuple[Run, dict[str, Any]]:
    """One game from its first screen to its end: the whole game as one `Run`, and how it was read (the
    adapter, the looks, the model's questions and what the game was found to be). `writer`, when given, is
    asked to write the adapters and `model` everything else."""
    return await Agent(surface, model, family_id, goal=goal, budget=budget, store=store, policy=policy, fresh=fresh,
                       hint=hint, watch=watch, cancel=cancel, accept_consent=accept_consent, writer=writer,
                       direct=direct).run()
