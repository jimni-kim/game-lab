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
questions), when a model sees nothing more to do (`gave_up`, with what it said of the screen), or when a model
says a condition the person set for stopping is met (`stopped`).

One game is one part of the work a person asked for (`work`): when more levels follow (`onward`), a level the
screen shows ended before any move here is passed for the next one, not taken for this game's end. Whatever
stands between two levels (a result, a map, a notice, a story, a puzzle of another kind) is no family's to
read: until a move of a level is confirmed in this part, a screen the family reads no board on is the model's
to play by its own inputs (`_between`), and the family plays again from the first screen the model names a
board that the family's reader reads (`scenes`, `_resume`; PLAN §25, R5 and R6).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
import logging
import time
from typing import Any, Protocol

from . import adaptation, carry, catalog, direct, kits, made, recovery as recovering, routine, samples, scenes
from .adaptation import (EXPLORE_MOVES, HELD_BACK, MAX_GENERATIONS, MAX_RELEARNS, AdaptationFailed, Adaptation, Look, Made,
                         Store, Trace, _agrees, _board, _shape, adopt, explorable, identify, keyed)
from .adapters.generated import GeneratedAdapter
from .adapters.kit import KitAdapter
from .carry import Carrier
from .checker import CHOICE, Check
from .contracts import Budget, GoalSpec, NeedObservation, Observation, Outcome
from .execution import TAP_MS
from .families import state_id
from .model import Model, Msg, TurnRequest, TurnResult
from . import motion
from .carry import _boxes as carry_boxes
from .motion import Steady
from .run import Run, check
from .recovery import GIVE_UP, MAX_TRIES, NEW_GAME, NEXT_LEVEL, NONE, STOP, Recovery

log = logging.getLogger("gamelab.agent")

RECORD_VERSION = 2
L0, L2, L3 = "L0", "L2", "L3"
# an input no reading chose: a step of the game's routine, sent again by code on a screen like the one a model chose
# it on (`routine`, PLAN §28)
ROUTINE = "R"
# after a step sent by code no kept step may fit the screen yet (a scene on its way to the next): it is looked at
# again this often, until it has looked the same this long (small pictures this near, of 255) and for no longer
# than that, before a model is asked or, after a step that opened a level before, the family is tried
LOOK_AGAIN_S, STILL_S, STILL_NEAR, TRANSIT_S = 0.5, 1.5, 1.0, 8.0
# the first screen of a stretch the game has steps for is looked at again for this long when none fits it yet (the
# end of the level before may still be drawing)
FIRST_S = 3.0
# what `_by_routine` did about a screen: nothing (the model is asked), something (the screen is looked at again), or
# the family took the board
ASK, AGAIN, RESUMED = "ask", "again", "resumed"
# the first moves read by an adapter (made now, or kept from another game) that must each do what the rules
# predict: one that does not sends the screen to the model
TRIAL_MOVES = 3
# moves that must be confirmed by rule values taken anew from a screen before what the game had is let go
RESIZE_TRIAL = 2
# a family's own reader is first shown the screen this long after a move was sent
KIT_FIRST_S = 0.15
# a game no family was found for is asked about again from the model's own play at most this many times
REASK_MOST = 2
# moves in a row that did not do what the rules predict before the reading (L3) or the screen (L2) is doubted
MAX_STALLED = 3
# confirmed moves read by the model after an adapter could not be written, before it is tried once more, and
# how often in one game
RETRY_LOOKS, RETRIES = 12, 2
# a game that must be still but whose pixels never rest is settled once its state has read the same this long;
# a screen the adapter leaves unread the same way this long is not waited on further
RESTLESS_S = 1.0
# a family's own reader takes a board at rest when it read the same this many frames in a row (a piece still moving
# reads as none, so a board read whole and alike that often is not one caught between two falls). A move whose
# result reads as the board it was played from is one the game did not take once the count of moves left the game
# shows has not changed this long after it was sent; when no such count is known, once this longer time is over
SAME_READS, REFUSED_S, ABSENT_S = 3, 1.5, 3.0
# a board at rest that shows looks not learned is looked at again this long after: a look that changed meanwhile
# is still moving, and is not asked about
UNSTABLE_GAP_S = 0.4
# moves whose lines hit things fixed, none of which looked otherwise after, before such things are taken not to
# break beside a line
BESIDE_UNMOVED = 3
# the readings of a level's goals kept for the game's summary
GOALS_KEPT = 8
# after its trial moves a still game waits the longest of them plus this margin, then looks once
ANIMATION_MARGIN_S, ANIMATION_MAX_S = 0.1, 2.0
# a screen the model is to read is waited for until its game shows the same twice, this far apart, compared
# as small grey pictures that may differ this much on average (of 255), and for no longer than this
STILL_GAP_S, STILL_SIDE, STILL_DISTANCE, STILL_TIMEOUT_S = 0.3, 32, 0.6, 5.0
# a reader that takes a board at rest (`KitAdapter.foresees` off) takes one that read the same SAME_READS frames in a
# row while its pixels still change somewhere not known to keep changing only once it read the same this long (a
# glint on a piece made by the move just played: PLAN §26, C3)
UNQUIET_S = 2.0
# the screens a family's reader gave up on, kept of one game for whoever looks into why (C3)
SCREENS_KEPT = 8
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
# the times play by a family was handed to the model kept for the record
LET_GO_KEPT = 12
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
# moves in a row, chosen by the code for a level whose goals it weighs, that gained nothing towards them: from
# then on a model picks among the code's best moves, until one gains (PLAN §31, L3)
AIMLESS_MOST = 3
PICK_TOKENS, PICK_TIMEOUT_S = 1500, 60.0
# the names a model is told for the powers a move leaves (`rules.match3.KINDS`)
POWERS = {1: "line", 2: "seeker", 3: "area", 4: "colour", 5: "power"}
# a direction key on a surface without keys is a swipe across the board
SWIPES = {"ArrowLeft": (-1, 0), "ArrowRight": (1, 0), "ArrowUp": (0, -1), "ArrowDown": (0, 1)}
SWIPE_SPAN, SWIPE_MS = 0.6, 120
WON = "won"
# why a game asked for from its start stopped before any move: the one on the screen, still in play, already met
# the goal (`Agent.anew`)
MET_BEFORE = "met_before"
OUT_OF_LIVES = recovering.OUT_OF_LIVES
# why a game played for an errand the request named beside the levels (a daily reward) ended: the model read the
# errand done (`Agent.errand`)
TASK_DONE = "task_done"
# what a light model is asked of one cell cut out of a board, and what it is told of the game
LIGHT_QUESTION = ("What does the cell in the middle of {image} hold? The picture is one cell of the game's board "
                  "with a little of its neighbours around it.")
LIGHT_CONTEXT = "A cell of the board of this game: {game}"


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
    # where the screen kept changing while this board stood on it at rest (a glint, a hint's wiggle), as boxes on
    # the screen; None when nothing told (`motion.Resting`)
    moving: list[list[int]] | None = None

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
                 writer: Model | None = None, direct: bool = True, light: Any = None, onward: bool = False,
                 notes: Any = "", anew: bool = False, game: str | None = None, book: routine.Book | None = None,
                 task: str | None = None) -> None:
        # the one game of several the site or app holds, as the person named it: what is kept is kept by it too
        # (`adaptation.keyed`)
        self.game_name = game
        # the errand the request names beside the game's levels (collecting a daily reward), by its plain name
        # (`request.understand`): its steps are a routine of their own, and the game ends when the model reads it done
        self.errand = task
        # the game's routines (`routine`): the steps kept of its screens between levels, sent again by code. The
        # work's own book when it has parts, so that what one part learned the next can send
        self.book = book if book is not None else routine.Book()
        # the stretch of the model's own play now going on, as the routine sees it (`_stretch`)
        self.stretch: dict[str, Any] = {}
        # more levels follow this one (`work`): a level shown ended before any move here is passed for the next
        self.onward = onward
        # the person asked for a game from its start, not one to go on with (`Scope.NEW_GAME`, `work`): a game the
        # first screen shows already ended, or already at the goal, was played before the work and is not its own
        # (2026-11-19, session Q: a finished Mines game and a 2048 game holding 1024 were each handed back as the
        # goal reached with no move made)
        self.anew = anew
        # what a model playing the part before this one by its own inputs kept for itself (how this game gets
        # from one level to the next): the model of this part starts from it
        self.notes = notes
        # moves of a level confirmed in this part: the family's, and the model's own on a screen it named a board
        self.level_moves = 0
        # the kind of the last screen a model named, or a board once a move on it was confirmed (`scenes`)
        self.screen: str | None = None
        # the screens the model's own play passed through since the last move of a level (`scenes.Trail`), the
        # one input it sent among them when it was one a kept recovery can send again, and the input count a
        # family tried again on a screen named a board did not read it at
        self.trail = scenes.Trail()
        self._stretch()
        # moves in a row that gained nothing towards the level's goals (`AIMLESS_MOST`), and what a model was
        # asked to pick among the code's moves: the questions, the picks that were not the code's own first, the
        # answers that picked none
        self.aimless = 0
        self.picks = {"questions": 0, "other": 0, "none": 0}
        self.picked: dict[str, Any] | None = None
        # whether the game's count of moves left changed with the last move, as the family's reader saw it
        self.ticked: bool | None = None
        self.unread_at: int | None = None
        # what the inputs sent used up beyond the board, and what inputs not sent would have (PLAN §25, R2)
        self.account: dict[str, dict[str, int]] = {"used": {}, "refused": {}}
        # the names and profiles the game would not go on without, as made by a model's inputs (PLAN §26, C2-5)
        self.profiles: list[dict[str, Any]] = []
        # what names a single cell cut out of the screen when the family's reader and the rules cannot (`checker`:
        # a light model); None: such a cell is the reading model's
        self.light = light
        # the questions put to it, the cells its answers named, and the answers the rules did not take
        self.light_told = {"questions": 0, "named": 0, "refused": 0}
        # the ways of moving the game was seen to take or not (`_learn_ways`)
        self.ways: dict[str, Any] = {}
        # the move just refused taught something (a way the game does not take, a thing that is no piece): it
        # says nothing against the reading
        self.taught = False
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
        # a carry that goes straight to where the piece is to lie looks at the screen once, with the piece held
        # over the board: whoever watches the game is shown that frame too
        self.carrier.on_frame = watch.seen if watch is not None else None
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
        self.adapter: GeneratedAdapter | KitAdapter | None = None
        # a family's own reader (`kits`): how often it was adopted from a look, how many screens it read, and
        # whether it began from what was kept of the site
        self.kit_adopted = self.kit_reads = 0
        # how often a screen the model read gave the level's goals anew (`_take_goals`), and the moves in a row
        # whose lines hit things fixed that did not change (`_learn_beside`)
        self.goals_read = self.unbroken = 0
        # whether a model was asked to look at a level the kept reader read first, for its goals (`_turn`)
        self.goals_asked = False
        self.goals_seen: list[dict[str, Any]] = []
        self.kit_demoted = False
        # the game's rule values were taken anew from a screen (a board of another size, a tray grown): what they
        # were and what the reader had learned, until RESIZE_TRIAL moves by the new ones are confirmed (`_resize`);
        # and whether the reading just made was such a one (`_turn` does not hold it to the move before it)
        # times the game was asked about again from the model's own play (`_play_direct`)
        self.reasked = 0
        # where the last move's time went (`_remember`), and what its result's reading by code took (`_read_code`)
        self.timing: dict[str, Any] | None = None
        self.timing_from = 0.0
        self.read_told: dict[str, Any] | None = None
        self.resized: dict[str, Any] | None = None
        self.fresh_scene = False
        self.resizes = 0
        self.kit_restored = False
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
        # how the last wait for a screen the model was to read went (`_rest`), and the screens the family's
        # reader gave up on, each with why (`_keep_screen`)
        self.rest_told: dict[str, Any] | None = None
        self.screens: list[dict[str, Any]] = []
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
        # moves the family sent since it was last taken, and whether the screen it was last taken on was one the
        # model named a level's board (it is then no screen between two levels, whatever the family's reader
        # makes of it)
        self.since_taken = 0
        self.on_board = False
        # each time play by a family was handed to the model's own inputs, and why (`_let_go`)
        self.let_go: list[dict[str, Any]] = []
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
            if outcome in (Outcome.GOAL_REACHED, Outcome.GAME_OVER, Outcome.STOPPED):
                await self._keep_for_next()
            if self.game is not None:
                self.found["made"] = self.game.summary()
                self._keep_made()
            self._keep_looks()
            record = {"version": RECORD_VERSION, "family": self.family_id, "phase": "ended", "outcome": str(outcome),
                      "reason": reason, "detail": detail, "goal": self.goal.to_json(), "budget": self.budget.to_json(),
                      "policy": self.policy, "actions_used": self.used, "confirmed_actions": self.confirmed,
                      "level_moves": self.level_moves,
                      "levels": dict(self.levels), "history": self.moves[-HISTORY_KEPT:]}
            run = Run(record, list(self.moves), looks=self.shots, last_screen=self.last_png,
                      recoveries=list(self.recovery.log) if self.recovery else [], screens=list(self.screens))
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
                "kit": self._kit_summary(),
                # what was used up beyond the board, and where the game stood when it ended: what the work hands back
                "account": {kind: dict(counts) for kind, counts in self.account.items()},
                "scene": self.state.get("scene") if isinstance(self.state, dict) else None,
                "screen": self.screen,
                "situation": self.player.situation if self.player is not None and self.policy == "model" else self._situation(),
                **({"profiles": list(self.profiles)} if self.profiles else {}),
                **({"routines": self.book.summary()} if self.book.summary() else {}),
                **({"generation_failures": self.failures} if self.failures else {})}

    def _kit_summary(self) -> dict[str, Any] | None:
        if not kits.has(self.family_id) and not self.kit_reads:
            return None
        told = self.adapter.summary() if isinstance(self.adapter, KitAdapter) else {}
        return {"adopted": self.kit_adopted, **told, "reads": self.kit_reads, **({"ways": self.ways} if self.ways else {}),
                **({"light": {**self.light_told, **self.light.spent.summary()}} if self.light is not None else {}),
                **({"goals": self.family.rules.get("goals"), "goals_read": self.goals_read}
                   if self.family is not None and self.family.rules.get("goals") else {}),
                **({"goals_seen": self.goals_seen} if self.goals_seen else {}),
                **({"picks": dict(self.picks)} if self.picks["questions"] else {}),
                "hang": self.carrier.hang.to_json()[-6:], "restored": self.kit_restored, "resizes": self.resizes}

    def _keep_profile(self) -> None:
        """What the family's reader learned of this site and how the game hangs held things, for its next game."""
        if self.store is None or not isinstance(self.adapter, KitAdapter) or self.adapter.layout is None or not self.site:
            return
        # a level's goals are that level's: the next game reads its own
        # and whether its layer spreads is read with them (`meant`)
        rules = {name: value for name, value in (self.trace.rules if self.trace else {}).items()
                 if name not in ("goals", "spread")}
        self.store.save_profile(self.site, self.family_id, {
            "family": self.family_id, "rules": rules,
            "kit": self.adapter.record(), "hang": self.carrier.hang.to_json(), "hold_s": self.carrier.hold_s})

    def close(self) -> None:
        self._keep_profile()
        if self.adapter is not None:
            self.adapter.close()
            self.adapter = None
        if self.game is not None:
            self.game.close()
        if self.store is not None and self.recovery is not None and self.family_id and self.site and self.recovery.known:
            self.store.save_recoveries(self.site, self.family_id, self.recovery.known)
        # (a stretch that reached no board leaves no step behind, kept or not: `routine.Book.keep`)
        self.book.keep(self.store if self.site else None)
        if self.site and self.store is not None and self.player is not None and self.player.lasting():
            self.store.save_memory(self.site, self.player.lasting())

    async def _begin(self) -> None:
        """The family the game is played as: the one given, the one a kept adapter of this site names, else
        each a model names for the screen, likeliest first (`_take`)."""
        self.site = keyed(await self.surface.address(), self.game_name)
        self.book.open(self.store, self.site, self.fresh)
        if not self.notes and self.store is not None and not self.fresh:
            # what a model playing this game before noted of it (PLAN §27, M4)
            self.notes = self.store.memory(self.site) or ""
        self.candidates = [self.family_id] if self.family_id else []
        if self._errand():
            # an errand is done on the game's screens beside its boards: no family is looked for, the model finds
            # the way once and the routine kept of it goes it again (PLAN §24, U2)
            await self._play_direct("")
            return
        if self.family_id is None and await self._made_again():
            return
        if self.family_id is None:
            kept = [] if self.store is None or self.fresh else \
                [name for name in explorable() if (self.store.profile(self.site, name) if kits.has(name)
                                                   else self.store.load(self.site, name)) is not None]
            if kept:
                self.candidates, self.found["identified"] = kept[:1], {"family": kept[0], "source": "kept", "questions": 0}
            else:
                asked = await self._identify()
                self.candidates, self.named = list(asked["families"]), True
                self.found["identified"] = {"family": None, "source": "model", "candidates": list(self.candidates),
                                            "tried": [], "questions": asked["questions"], "inputs": asked["inputs"],
                                            "situation": asked["situation"]}
        if not await self._next_family() and not await self._make_family():
            if self._before_own() and self.family_id in catalog.descriptor_ids() and self.resumable is None:
                # the model's own inputs open the game asked for (an ended one on the screen is not it), and the
                # family a model named takes the board once it is one (`_resume`)
                self.resumable = self.family_id
            await self._play_direct(self.found.get("identified", {}).get("situation") or "")

    async def _made_again(self) -> bool:
        """The family made for this game before (`made`), taken up with nothing described or written anew (plan
        §12, A3): its board must read from the first screen by its description, and its rules as written must
        hold on what is read. Else nothing of it is used, and the game is found as any other is."""
        kept = None if self.store is None or self.fresh or not self.direct else self.store.made(self.site)
        game = made.Game.restore(self.site, kept) if kept is not None else None
        if game is None:
            return False
        self.game, self.named = game, True
        self.found["identified"] = {"family": None, "source": "made_kept", "candidates": [game.id], "tried": [],
                                    "questions": 0, "inputs": 0, "situation": None}
        holds = False
        try:
            if await self._take(game.id):
                first = self.adaptation.first[1] if self.adaptation.first is not None else None
                if first is None and self.adapter is not None:
                    # read by the adapter kept with it: that reading is the first screen
                    self.carried = await self._read_code(None)
                    first = self.carried.state if self.carried is not None else None
                holds = first is not None and not game.code.check([first], [])
        except _End:
            holds = False
        except Exception as error:
            log.info("the family kept for this game could not be taken up: %s", error)
        if holds:
            return True
        if self.adapter is not None:
            self.adapter.close()
        game.close()
        self.game, self.named, self.adapter, self.adaptation, self.recovery = None, False, None, None, None
        self.family_id = self.family = self.policy = self.trace = self.made = self.board = self.carried = None
        self.found.pop("identified", None)
        self.found["made_kept"] = "not_taken"
        return False

    def _keep_made(self) -> None:
        """The family made for this game kept for its next game, when its rules played it: moves by them were
        borne out by the screen and they were not dropped. One taken up from what was kept that did not hold is
        kept no longer."""
        if self.store is None or self.game is None or not self.site:
            return
        dropped = any(failure.get("reason") == "made_family_dropped" for failure in self.failures)
        if self.game.code.source and self.transitions and not dropped:
            self.store.save_made(self.site, self.game.record())
        elif self.game.restored:
            self.store.save_made(self.site, None)

    async def _identify(self) -> dict[str, Any]:
        """Which families the game may be, as a model sees it; asked again when the model could not be reached
        (a gateway's timeout is not the game's end)."""
        for attempt in range(1 + IDENTIFY_RETRIES):
            try:
                recovery = self._recovery(None, [])
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

    async def _take(self, family_id: str, probe: bool = False, kept: bool = True) -> bool:
        """`kept` off: nothing kept of the site is taken up (it did not read the screen): the family is tried by
        a model's reading alone."""
        if self.adapter is not None:
            self.adapter.close()
        self.adapter = self.trace = self.made = self.board = self.carried = self.unproven = self.unread_move = None
        try:
            self.policy = check(self.goal, self.budget, family_id, self.asked_policy)
            self.recovery = self._recovery(family_id, self.store.recoveries(self.site, family_id) if self.store else None)
            self.adaptation = Adaptation(self.model, family_id, policy=self.policy, compute=self.budget.compute,
                                         writer=self.writer)
            if kits.has(family_id):
                # the reader's own knowledge, lent to the reading of a screen by a model
                self.adaptation.arrange = lambda things: self.adapter.arrange(things) \
                    if isinstance(self.adapter, KitAdapter) else None
                self.adaptation.gauge = lambda png, box: kits.count(self.family_id, png, box)
                self.adaptation.survey = lambda png, box, state: kits.survey(self.family_id, {}, png, box, state)
        except AdaptationFailed as error:
            raise _End(Outcome.UNSUPPORTED, error.reason, error.detail) from None
        except ValueError as error:
            raise _End(Outcome.UNSUPPORTED, str(error)) from None
        self.family_id = family_id
        old = None if self.store is None or self.fresh or not kept or kits.has(family_id) else self.store.load(self.site, family_id)
        profile = self.store.profile(self.site, family_id) if kits.has(family_id) and self.store and not self.fresh \
            and kept else None
        if profile is not None and self._restore(family_id, profile):
            pass
        elif old is not None:
            source, meta, self.trace = old
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

    def _recovery(self, family_id: str | None, known: list[dict[str, Any]] | None) -> Recovery:
        return Recovery(self.model, family_id, known=known, accept_consent=self.accept_consent, account=self.account,
                        allowed=self.goal.allowed_meta_actions, stops=self.goal.stop_conditions, profiles=self.profiles)

    def _restore(self, family_id: str, profile: dict[str, Any]) -> bool:
        """The family's reader as it was kept for this site: the game is read by it from the first screen, and
        its drags go by how the game hung held things before."""
        try:
            adapter = KitAdapter(family_id, rules=profile.get("rules") or {}, site=self.site)
            if not adapter.restore(profile.get("kit") or {}):
                return False
        except (ValueError, KeyError, TypeError):
            return False
        self.trace = Trace(family_id, dict(profile.get("rules") or {}), self.site)
        self.made = Made("", 0, True, {})
        self.adapter, self.board = adapter, list(adapter.layout["board"])
        self.settling = Settling(**adapter.played.get("settling", {}))
        self.distrusted, self.blind, self.trial, self.rested, self.carried = False, False, TRIAL_MOVES, [], None
        self.carrier.hang = carry.Hang.from_json(profile.get("hang"))
        self.carrier.hold_s = float(profile.get("hold_s") or 0.0)
        self.kit_restored = True
        return True

    def _resize(self, rules: dict[str, Any]) -> None:
        """The screen shows a board of another size (or more places for pieces) than the game had: the game goes
        on by the rule values read from it, and the reader learns the screen anew. What was is kept until
        RESIZE_TRIAL moves are confirmed, and comes back when one is not (the model miscounted)."""
        log.info("the game's rule values change: %s -> %s", self.trace.rules, rules)
        kept = self.adapter.record() if isinstance(self.adapter, KitAdapter) and self.adapter.layout else None
        self.resized = {"rules": dict(self.trace.rules), "kit": kept, "board": self.board, "left": RESIZE_TRIAL}
        self.resizes += 1
        self._rebind(rules, None)
        self.fresh_scene = True

    def _rebind(self, rules: dict[str, Any], kit: dict[str, Any] | None) -> None:
        self.trace.rules = dict(rules)
        self.family = catalog.family(self.family_id).bind({"rules": self.trace.rules})
        if self.adapter is not None:
            self.adapter.close()
        self.adapter = None
        if kit is not None:
            adapter = KitAdapter(self.family_id, rules=self.trace.rules, site=self.site)
            if adapter.restore(kit):
                self.adapter = adapter
                self.settling = Settling(**adapter.played.get("settling", {}))
        self.distrusted, self.blind, self.trial = False, False, TRIAL_MOVES
        self.carried = self.unread_move = self.ineffective = self.state = self.unproven = None
        self.visits = {}

    def _unresize(self) -> None:
        """A move by the rule values just taken was not confirmed: the game goes on by the ones it had."""
        was, self.resized = self.resized, None
        log.info("the game's rule values are taken back: %s", was["rules"])
        self._rebind(was["rules"], was["kit"])
        self.board = was["board"] or self.board
        self.failures.append({"reason": "resize_taken_back", "detail": json.dumps(was["rules"])})

    def _adopt_kit(self) -> bool:
        """The family's own reader, taught this screen by the looks the model read: it reads from here on. No
        code is written for the game."""
        adapter = self.adapter if isinstance(self.adapter, KitAdapter) else \
            KitAdapter(self.family_id, rules=self.trace.rules, site=self.site)
        if not adapter.keep(self.trace.looks):
            if self.adapter is not adapter:
                adapter.close()
            return False
        if self.adapter is not adapter:
            if self.adapter is not None:
                self.adapter.close()
            self.adapter = adapter
            self.settling = Settling(**adapter.played.get("settling", {}))
            self.rested = []
        self.made = self.made or Made("", 0, False, {})
        self.distrusted, self.blind, self.trial = False, False, TRIAL_MOVES
        self.kit_adopted += 1
        self._take_goals()
        return True

    def _learn_beside(self, before: Reading, action: dict[str, Any], after: Reading) -> None:
        """Whether things fixed break when a line is cleared beside them (`beside`), as the game showed it: they
        do once one the move's lines hit looks otherwise after it, and do not once BESIDE_UNMOVED moves hit some
        and none ever changed. Each new layer one shows is learned as the same thing (`KitAdapter.struck`)."""
        if "beside" not in self.family.rules or not isinstance(self.adapter, KitAdapter) or after.by != L3:
            return
        try:
            hit = self.family.model.apply(before.state, {**self.family.rules, "beside": True}, action).get("hit")
        except (ValueError, KeyError, IndexError, TypeError):
            return
        changed = self.adapter.struck(before.png, after.png, hit or [])
        if changed is None or self.family.rules.get("beside") is not None:
            return
        self.unbroken = 0 if changed else self.unbroken + 1
        if changed or self.unbroken >= BESIDE_UNMOVED:
            log.info("things fixed break beside a line: %s", changed)
            self.trace.rules = {**self.trace.rules, "beside": bool(changed)}
            self.family = catalog.family(self.family_id).bind({"rules": self.trace.rules})
            self.ways = {**self.ways, "beside": bool(changed)}

    def _advance_goals(self, before: Reading, action: dict[str, Any]) -> None:
        """A move the game took counts towards the level's goals by what the rules say it took off the board:
        only what the known pieces decide (what new pieces set off is not counted until a screen read shows it)."""
        goals, model = self.family.rules.get("goals"), self.family.model
        if not goals or not hasattr(model, "advance"):
            return
        try:
            taken = model.apply(before.state, self.family.rules, action).get("taken", {})
        except (ValueError, KeyError, IndexError, TypeError):
            return
        advanced = model.advance(goals, taken)
        # whether the move got on with a goal the code weighs: moves that keep gaining nothing are aimless
        weighed = any(goal.get("target") is not None and goal.get("count") for goal in goals)
        self.aimless = 0 if advanced != goals or not weighed else self.aimless + 1
        if advanced != goals:
            self.trace.rules = {**self.trace.rules, "goals": advanced}
            self.family = catalog.family(self.family_id).bind({"rules": self.trace.rules})

    def _take_goals(self) -> None:
        """The level's goals as the screen the model just read shows them (PLAN §23, Y2), for the moves to be
        chosen by: what each is about is the value of the cells its picture looks like, and how many are left is
        what the screen showed. A game whose rules have no goals is left as it is."""
        if "goals" not in self.family.rules or not isinstance(self.adapter, KitAdapter):
            return
        goals = self.adapter.goals(self.trace.looks)
        goals = _same_level(self.trace.rules.get("goals"), goals)
        if goals is None:
            return
        # what the goals say of the game's ways (a layer asked to be laid under more cells than have one spreads)
        meant = getattr(self.family.model, "meant", None)
        changed = {"goals": goals}
        if meant is not None and self.trace.looks:
            changed.update(meant(goals, self.trace.looks[-1].state, self.trace.rules))
        if all(self.trace.rules.get(name) == value for name, value in changed.items()):
            return
        goals = changed["goals"]
        log.info("the level's goals: %s", changed)
        self.trace.rules = {**self.trace.rules, **changed}
        self.family = catalog.family(self.family_id).bind({"rules": self.trace.rules})
        self.ways = {**self.ways, **{name: value for name, value in changed.items() if name != "goals"}}
        self.goals_read += 1
        # what each reading of them gave, the move it came at: a level's end clears them, and what they were is
        # what a game is looked back on by
        self.goals_seen = [*self.goals_seen, {"at_action": self.used, "goals": goals}][-GOALS_KEPT:]

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
                await self._unstick(self.why, "read")
                return
            if self.fresh_scene:
                self.fresh_scene, held = False, None
            if held is not None and self._effect(held[0].state, held[1], before.state) == "absent":
                self._spent(held[0].id, held[1])
        self.state = before.state
        if self._passed(before.state):
            # the level on the screen ended before any move here, and the work goes on to the next; or the game
            # asked for from its start is to be started again from an ended one
            await self._unstick(NEXT_LEVEL if self.onward else NEW_GAME, "ended")
            return
        self._judge(before.state)
        if before.by == L3 and not self.goals_asked and not self.trace.looks and self._goals_unread():
            # the reader kept of the site read the level's first board, and no model has looked at this level: what
            # the level asks for only a model's reading tells. It reads the screen once, the reader learns it again
            # from that, and the reader's own reading is still what is played from (PLAN §26, C3: 2026-11-20,
            # levels taken up by the kept reader were played with no goals, one for all its 31 moves)
            self.goals_asked = True
            said = await self._read_model(None, None)
            self._reread("goals")
            if said is not None:
                await self._recheck(vouched=False)
                before = self._own(said)
                self.state = before.state
        excluded = self.ineffective["actions"] if self.ineffective and self.ineffective["state"] == before.id else []
        action = self.family.choose(before.state, self.policy, self.budget.compute, tuple(excluded))
        if action is None or action["kind"] in self.family.meta_actions:
            # every move this board offers was tried without effect, or the game waits at a message of its own
            await self._unstick("absent" if action is None else action["kind"], "no_move" if action is None else "message")
            return
        why, self.picked = self._blind_to_goals(), None
        if why is not None:
            action = await self._pick(before, action, tuple(excluded), why) or action
        sent = self._aim(action, before)
        if sent is None:
            self._doubt()
            return
        if self.adapter is None or self.distrusted:
            self._afford()               # the move's result is the model's to read: none is sent that it cannot be asked about
        if self.watch is not None:
            await self.watch.sending(self.used + 1, action, before.state, sent, before.at)
        sending_at = time.monotonic()
        await self._send(sent, before, action)
        self.used += 1
        self.since_taken += 1
        if self.watch is not None:
            await self.watch.sent()
        sent_at = time.monotonic()
        self.timing = {"send_s": round(sent_at - sending_at, 2)}
        self.timing_from = sent_at - self.started
        self.read_told = None
        try:
            after = await self._read((before, action))
        except _End:
            self._remember(before, action, "unknown", None)
            raise
        if self.fresh_scene and after is not None:
            # the screen was read as another board altogether: it says nothing of the move, and play goes on from it
            self.fresh_scene = False
            self._remember(before, action, "unknown", after)
            self.carried, self.state = after, after.state
            return
        effect = "unknown" if after is None else self._effect(before.state, action, after.state)
        self.ticked = self.adapter.ticked(before.png, after.png) if isinstance(self.adapter, KitAdapter) \
            and after is not None and after.by == L3 else None
        if effect == "confirmed" and self._taken_back(before, action, after):
            # the board reads otherwise than before, yet the two pieces stand where they stood and the game's count
            # of moves left did not change: the game did not take the move, and what reads otherwise is the
            # reading's (PLAN §31, L4)
            effect = "absent"
        if isinstance(self.adapter, KitAdapter) and effect == "confirmed" and after.by == L3:
            # a move the game took: whether the count of moves left it shows changed with it
            self.adapter.tally(self.ticked)
        if effect == "confirmed":
            self._advance_goals(before, action)
            self._learn_beside(before, action, after)
            self._learn_seen(before, action, after)
        self._learn_ways(before, action, effect)
        if after is not None and after.by == L3 and effect == "confirmed":
            await self._settle(after, (before, action))
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
            if self.resized is not None:
                self.resized["left"] -= 1
                if self.resized["left"] <= 0:
                    self.resized = None
            await self._confirmed(before, action, sent, after, sent_at)
            return
        if self.resized is not None and effect in ("absent", "inconsistent") and carry.trusted(self.carried_as):
            self._unresize()
            return
        self.stalled += 0 if self.taught else 1
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
            await self._unstick(self.why, "read")
            return
        # the adapter is doubted for a move it read either side of: a board it read wrongly is as often the
        # one after the move (a place it takes for empty) as the one before
        # not for a move whose carry is not known to have put the piece where it was meant to go (it picked
        # nothing up, or could not tell where the piece hung): that is the carry's, until moves keep failing
        carried_unsure = self.carried_as is not None and not carry.trusted(self.carried_as)
        if L3 in (before.by, after.by if after else None) and not self.taught and (
                self.stalled >= MAX_STALLED or (self.trial > 0 and not carried_unsure)):
            self._doubt()

    def _taken_back(self, before: Reading, action: dict[str, Any], after: Reading) -> bool:
        """Whether a move read as taken was not: the family's reader believes the game's count of moves left,
        that count did not change with the move, and every cell the move names reads as it did before."""
        if not isinstance(self.adapter, KitAdapter) or after.by != L3 or before.by != L3 \
                or not self.adapter.counts or self.ticked is not False:
            return False
        field = getattr(self.adapter.kit, "field", "grid")
        try:
            cells = [action[name] for name in ("cell", "to") if name in action]
            return bool(cells) and all(after.state[field][r][c] == before.state[field][r][c] for r, c in cells)
        except (KeyError, IndexError, TypeError):
            return False

    def _learn_seen(self, before: Reading, action: dict[str, Any], after: Reading) -> None:
        """What a move the game took shows of its ways by the screens either side of it, where the family's
        rules leave that open (`observed`: whether a layer under the pieces spreads)."""
        observed = getattr(self.family.model, "observed", None)
        if observed is None or self.trace is None:
            return
        try:
            rules = observed(before.state, action, after.state, self.family.rules)
        except (ValueError, KeyError, IndexError, TypeError):
            return
        changed = {name: value for name, value in rules.items() if self.family.rules.get(name) != value}
        if not changed:
            return
        log.info("the game's ways as the screen showed them: %s", changed)
        meant = getattr(self.family.model, "meant", None)
        if meant is not None and self.family.rules.get("goals"):
            changed.update(meant(self.family.rules["goals"], after.state, {**self.family.rules, **changed}))
        self.trace.rules = {**self.trace.rules, **changed}
        self.family = catalog.family(self.family_id).bind({"rules": self.trace.rules})
        self.ways = {**self.ways, **{name: value for name, value in changed.items() if name != "goals"}}

    def _learn_ways(self, before: Reading, action: dict[str, Any], effect: str) -> None:
        """What a move's outcome says of how the game is played, where the family's rules leave that open until
        the game shows it (`learned`: a way of moving it took, or did not): the game goes on by the rule values so
        settled, and they are kept with what is kept of the site. A move the game did not take because a cell
        was not what it was taken for teaches the reader that instead."""
        learned = getattr(self.family.model, "learned", None)
        self.taught = False
        if learned is None or self.trace is None:
            return
        reader = isinstance(self.adapter, KitAdapter) and before.by == L3
        if reader and effect == "confirmed":
            self.adapter.took(before.png, action)
        try:
            rules = learned(before.state, action, effect, self.family.rules)
        except (ValueError, KeyError, IndexError, TypeError):
            rules = self.family.rules
        changed = {name: value for name, value in rules.items() if self.family.rules.get(name) != value}
        if changed:
            self.taught = effect == "absent"
            log.info("the game's ways of moving: %s", changed)
            self.trace.rules = {**self.trace.rules, **changed}
            self.family = catalog.family(self.family_id).bind({"rules": self.trace.rules})
            self.ways = {**self.ways, **changed}
        elif reader and effect == "absent" and self.adapter.refused(before.png, action):
            # (only a move no way of moving still open made: one refused that tried a way the game was not yet
            # seen to take says the game does not take it, not that its pieces are none. 2026-11-20: four in a
            # square refused made a colour of piece something fixed)
            self.taught = True

    async def _settle(self, after: Reading, expect: tuple[Reading, dict[str, Any]]) -> None:
        """A board at rest that shows looks the family's reader has not learned (`KitAdapter.settle`): what the
        rules say of them it has learned by now; each kind left is shown to the light model, a cell cut out
        alone, and is what it calls it, or, when nobody can say, a piece with a power of a kind not known. The
        reading is made again with what was learned."""
        if not isinstance(self.adapter, KitAdapter):
            return
        asked = self.adapter.settle(after.png, self._prior(expect))
        if asked is None:
            return
        if asked:
            # a look caught changing (an effect still playing, a piece that sparkles or turns) is no look to learn:
            # its kind is neither asked about nor named, and reads as a piece with a power of a kind not known on
            # this board only (PLAN §23: 2026-11-17, a light model asked twelve times in a game, most of the time
            # the game took, much of it of what was still moving)
            frame = await self.frames.frame(time.monotonic() + UNSTABLE_GAP_S)
            moving = self.adapter.unsteady(after.png, frame.png)
            steady = [(cells, pictures) for cells, pictures in asked if not any(tuple(cell) in moving for cell in cells)]
            self.light_told["unstable"] = self.light_told.get("unstable", 0) + sum(
                len(cells) for cells, _ in asked) - sum(len(cells) for cells, _ in steady)
            asked = steady
        kit, answers = self.adapter.kit, {}
        if asked and self.light is not None and len(asked) <= kit.KINDS_MOST:
            checks = [Check(f"k{index}-{shown}", CHOICE, LIGHT_QUESTION, picture, kit.asked())
                      for index, (_, pictures) in enumerate(asked) for shown, picture in enumerate(pictures)]
            self.light_told["questions"] += 1
            started = time.monotonic()
            try:
                answers = await self.light.ask(checks, LIGHT_CONTEXT.format(game=self.family.descriptor["summary"]))
            except Exception as error:
                log.info("the light model could not be asked: %s", error)
            finally:
                self.model_seconds += time.monotonic() - started
        for index, (cells, pictures) in enumerate(asked):
            said = {(answers.get(f"k{index}-{shown}") or {}).get("choice") for shown in range(len(pictures))}
            key = next(iter(said)) if len(said) == 1 else None      # two pictures of one kind called differently: nobody knows
            kit.name(after.png, cells, key)
            self.light_told["named" if key else "refused"] += len(cells) if key else 1
        state, _ = self._observe(after.png)
        if state is not None:
            after.state = state.payload

    def _blind_to_goals(self) -> str | None:
        """Why the code's own choice of move is not to be trusted with this level (PLAN §31, L3), or None: the
        level has goals and the code weighs none of them (`goals_not_weighed`), or the moves it chose gained
        nothing towards them AIMLESS_MOST times in a row (`no_progress`)."""
        goals = self.family.rules.get("goals") if self.family is not None else None
        if not goals or not self.direct or self.policy in (None, "model"):
            return None
        if all(goal.get("target") is None for goal in goals):
            return "goals_not_weighed"
        return "no_progress" if self.aimless >= AIMLESS_MOST else None

    async def _pick(self, before: Reading, chosen: dict[str, Any], excluded: tuple[dict[str, Any], ...], why: str
                    ) -> dict[str, Any] | None:
        """One of the code's best moves as a model picks it, shown the screen and what the rules say each does;
        None when there is nothing to pick among, the model cannot be asked or it picked none (the code's own
        choice stands). The model never gives a move of its own: only a move the rules allow is sent."""
        import base64

        from . import recovery as told, resources

        board = self.adapter.layout["board"] if before.by == L3 and self.adapter is not None and self.adapter.layout \
            else self.board
        if board is None or not self._affordable():
            return None
        try:
            ranked = self.family.rank(before.state, self.budget.compute, excluded)
        except (ValueError, KeyError, IndexError, TypeError):
            return None
        if len(ranked) < 2:
            return None
        shown, scale = told.view(before.png)
        shape = _shape(self.family, before.state)

        def middle(cell: list[int]) -> list[int]:
            left, top, width, height = adaptation.cell_box(board, shape, cell)
            return [round((left + width / 2) * scale), round((top + height / 2) * scale)]
        moves = [{"id": number, "from": middle(entry["action"]["cell"]),
                  **({"to": middle(entry["action"]["to"])} if "to" in entry["action"] else {}),
                  "clears": entry["cleared"], "makes": POWERS.get(entry["made"]),
                  "frees": entry["taken"].get(20, 0), "towards_goals": round(entry["towards_goals"], 2)}
                 for number, entry in enumerate(ranked, 1)]
        goals = [{"kind": goal.get("kind"), "count": goal.get("count"), "weighed": goal.get("target") is not None}
                 for goal in self.family.rules.get("goals") or []]
        asked = {"pick": True, "family": self.family.descriptor["summary"], "screen": list(told._size(shown)),
                 "goals": goals, "why": why, "moves": moves, **({"hint": self.hint} if self.hint else {})}
        image = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                              "data": base64.b64encode(shown).decode("ascii")}}
        prompt = (resources.FRAGMENT / "prompts" / "pick_move.md").read_text(encoding="utf-8")
        self.picks["questions"] += 1
        try:
            result = await self._asking(self.model.complete(TurnRequest(
                system_stable=prompt, max_tokens=PICK_TOKENS, timeout_s=PICK_TIMEOUT_S,
                messages=[Msg("user", [{"type": "text", "text": json.dumps(asked)}, image])])))
            answer = json.loads(result.text or "")
        except Exception as error:
            log.info("no move was picked: %s", error)
            return None
        pick = answer.get("pick") if isinstance(answer, dict) else None
        if type(pick) is not int or not 1 <= pick <= len(ranked):
            self.picks["none"] += 1
            return None
        self.picks["other"] += pick != 1
        self.picked = {"pick": pick, "of": len(ranked), "why": why,
                       **({"because": answer["because"][:200]} if isinstance(answer.get("because"), str) else {})}
        return ranked[pick - 1]["action"]

    def _spent(self, state: str, action: dict[str, Any]) -> None:
        """A move this board did not take: left out when the next is chosen from the same board."""
        kept = self.ineffective if self.ineffective and self.ineffective["state"] == state else {"state": state, "actions": []}
        self.ineffective = {**kept, "actions": [*kept["actions"], action]}

    async def _leave(self, reason: str) -> None:
        """Play by the family's rules gets nowhere: the game goes on by the model's own inputs, and comes back to
        the family once some of those are confirmed (`_resume`)."""
        log.info("the family is left: %s", reason)
        self.failures.append({"reason": "family_left", "detail": reason})
        self._let_go(reason, "fruitless")
        self.fruitless = self.since_tried = 0
        if self.resumable is None and (self.game is None or self.family_id != self.game.id):
            self.resumable = self.family_id
        await self._play_direct(self._situation())

    async def _confirmed(self, before: Reading, action: dict[str, Any], sent: dict[str, Any], after: Reading,
                         sent_at: float) -> None:
        self.confirmed += 1
        self.level_moves += 1
        self.screen = scenes.BOARD
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

    def _before_any(self) -> bool:
        """Whether more levels follow and no move of a level was confirmed here yet: what the screen shows ended
        is the last's, and whatever else it shows stands between that level and the next."""
        return self.onward and not self.level_moves

    def _before_own(self) -> bool:
        """Whether a game from its start was asked for and no move of it was confirmed here yet: what the screen
        shows played was played before the work."""
        return self.anew and not self.onward and not self.level_moves

    def _passed(self, state: dict[str, Any]) -> bool:
        if not self._before_any() and not self._before_own():
            return False
        seen = self.family.model.status(state, self.family.rules, {"objective": str(self.goal.objective),
                                                                   "target_value": self.goal.target_value})
        if self._before_own() and seen["goal"] == "reached" and not seen["terminal"]:
            # a game in play that met the goal before the work: a new game would throw it away, which is never
            # pressed unasked. Nothing is sent; the person is told (2026-11-19, the person's choice)
            raise _End(Outcome.STOPPED, MET_BEFORE, "the game on the screen, still in play, already met the goal "
                       "before any move of this work; no new game was started, as that would throw it away")
        return seen["terminal"] or seen["goal"] == "reached"

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
                           **({"ticked": self.ticked} if self.ticked is not None else {}),
                           **({"picked": self.picked} if self.picked else {}),
                           # a result the rules did not expect is kept as it was read, with what it was played
                           # from: why it was none is looked for afterwards
                           **({"before_state": before.state, "after_state": after.state}
                              if effect in ("inconsistent", "absent") and after is not None else {}),
                           # where the move's time went: sending it, and reading its result (frames taken, how many
                           # reads drew through frames for what keeps moving, seconds)
                           **({"timing": {**self.timing, "read_s": round(time.monotonic() - self.started - self.timing_from, 2),
                                          **({"read": self.read_told} if self.read_told else {})}} if self.timing else {}),
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
            elif expect is None and self._before_any() and self.direct and not self.on_board:
                # no level is open as far as the reader sees, and none was played here yet: the screen is one
                # between two levels, the model's to look at as that (`_between`), not to read a board from.
                # Not when the family was just taken on a screen the model named a level's board: the model's
                # reading of it, which the rules check, is played from then (PLAN §26, C2-3)
                return None
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
                    reading = self._own(reading)
                elif expect is None and self.why == "inconsistent":
                    # the model's reading and what the adapter made of the screen do not go together: the
                    # adapter is doubted, and the screen read without it
                    self.distrusted = True
                return reading
        doubted = self.adapter is not None
        reading = await self._read_model(expect, None)
        if doubted and self.adapter is not None:
            self._reread("distrusted")
            if reading is not None and isinstance(self.adapter, KitAdapter):
                # a doubted reader of the family's own is held against every screen the model reads whole and the
                # rules take, not only against one a confirmed move vouches for: a game whose results the model
                # keeps reading with cells left out confirms none, and the reader never came back (PLAN §26, C3:
                # 2026-11-18, seven moves of a level read by the model after the reader lost the board once).
                # Learning the screen again costs no question; when it then reads otherwise than the model, it
                # stays doubted and nothing counts against it
                await self._recheck(vouched=False)
        return self._own(reading) if reading is not None else None

    def _own(self, reading: Reading) -> Reading:
        """A screen the model just read, as the family's reader reads it once taught by that reading: the move
        is played from the reader's own reading (its kinds are the screen's, whatever a model called each cell, and
        its board is fitted to the picture), so the first move of a level is read by the same reader on both sides.
        The model's reading stands when the reader is doubted, does not read the screen whole, or reads it
        otherwise than the model in more than a few cells."""
        if not isinstance(self.adapter, KitAdapter) or self.distrusted or reading.by != L2 or self.adapter.layout is None:
            return reading
        self.adapter.expecting(None, None)
        state, _ = self._observe(reading.png)
        if state is None or not self.adapter.alike(state.payload, reading.state):
            return reading
        return replace(reading, state=state.payload, by=L3)

    def _rereads(self) -> dict[str, Any]:
        by: dict[str, int] = {}
        for entry in self.rereads:
            by[entry["why"]] = by.get(entry["why"], 0) + 1
        return {"by_why": by, "moving": sum(entry["moving"] for entry in self.rereads), "each": self.rereads[-60:]}

    def _reread(self, why: str) -> None:
        """A screen the adapter was there for and the model read: why, and whether what kept moving on it was
        left out of the picture the model was shown (`motion`)."""
        self.rereads.append({"at_action": self.used, "why": why, "moving": bool(self.seen.moving),
                             **({"rest": self.rest_told} if self.rest_told else {})})
        self.rest_told = None

    def _keep_screen(self, png: bytes, why: str) -> None:
        """A screen the family's reader gave up on is kept as it was, with why: whether something lay over the
        board or the reader failed on a board in plain sight is told from the picture (PLAN §26, C3)."""
        if len(self.screens) < SCREENS_KEPT:
            self.screens.append({"at_action": self.used, "why": why, "png": png})

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
        kit = isinstance(self.adapter, KitAdapter)
        # a family's own reader knows what the move leaves and is asked as soon as the screen may show it: no time
        # measured on other moves is waited out first (a move that clears nothing is done at once)
        fast = kit and self.adapter.foresees
        # one whose rules leave the result open (what comes in from above is the game's) takes it once the board has
        # come to rest: each frame is read as it is, and the result is the reading that stays (PLAN §26, C3). What
        # keeps changing in the same places while a board stands (`Reading.moving` of the one the move was played
        # from) says nothing of whether this one came to rest
        rests = kit and not fast and expect is not None
        watched = motion.Resting(self._region(self.adapter.regions()), self._recurring(expect[0]), STILL_SIDE,
                                 STILL_DISTANCE) if rests else None
        recent: list[bytes] = []
        wait = KIT_FIRST_S if fast else policy.animation_s
        after = clock() + wait if expect is not None and wait else None
        deadline = (after or clock()) + policy.timeout_s
        previous = pictured = read = unread = None
        stable_since = rested = read_since = unread_since = clock()
        unread_frames = 0
        self.read_told = told = {"frames": 0, "drawn_through": 0, "by": "kit" if kit else "adapter"}
        began = clock()
        dealt = False
        # frames in a row that read the same, and whether the count of moves left changed since the move was sent
        same_reads, ticked = 0, False
        if kit:
            # what the rules say the move left: it fills what the screen leaves unclear
            prior = self._prior(expect) if expect is not None else None
            self.adapter.expecting(prior, expect[0].state if expect is not None else None)
            # what the rules cannot say of the screen (a tray dealt anew) is taken once two frames in a row read
            # it the same (pieces still flying in read as others)
            dealt = self.adapter.awaits(prior)
        while True:
            self._check_time()
            # a screen something kept moving over is read as it is under that, for as long as it keeps moving
            # (not while a board is waited on to come to rest: frames drawn through one another show a board in
            # between, and what moved in them is the move's own doing, not something that keeps moving)
            through = not rests and self.seen.animating and not (kit and self._moving_beside())
            taken = self.frames.taken if hasattr(self.frames, "taken") else 0
            frame = await (self.seen.steady(after) if through else self.frames.frame(after))
            told["frames"] += (self.frames.taken - taken) if hasattr(self.frames, "taken") else 1
            told["drawn_through"] += through
            told["s"] = round(clock() - began, 2)
            png, looked = frame.png, frame.at
            # whether the game's part of the screen shows the same as on the frame before
            quiet = watched.seen(png) if watched is not None else True
            if rests:
                recent = [*recent, png][-motion.FRAMES:]
            self.shots += 1
            self.last_png = png
            if self.watch is not None:
                await self.watch.seen(png)
            state, why = self._observe(png)
            if state is None and why == adaptation.CELLS_UNREAD and expect is not None:
                # still: the same cells unread, looking the same, as on the frame before (asked once a frame)
                still = not kit or self.adapter.steady(png)
                if still and (self._learn_unread(png, expect) or await self._ask_light(png, expect)):
                    # the rules, or a light model shown the cells alone, told what the code could not read: it
                    # reads it now
                    state, why = self._observe(png)
            usable = state is not None
            if not usable:
                # a screen read through small moving things that could not be read is drawn through next time
                self.seen.forget()
                self.why = why
                self.unread = self.adapter.partial if why == adaptation.CELLS_UNREAD else None
                seen = (why, self.unread)
                if seen != unread:
                    unread, unread_since, unread_frames = seen, looked, 0
                unread_frames += 1
                # left unread the same way for long enough, on two frames at least (a device whose captures take
                # longer than that wait would else be given up on at its first: 2026-11-17, a capture took 1.3 s).
                # A board still changing is not given up on: what lies over it may be the move's own effect
                if (unread_frames >= 2 and quiet and clock() - unread_since >= max(policy.stable_s, RESTLESS_S)) \
                        or clock() >= deadline:
                    if kit and expect is not None:
                        self._keep_screen(png, why)
                    return None
            image = self.adapter.picture(png) if usable and policy.still and not rests else None
            key = (state.id, state.geometry_ref, image) if usable else None
            if key is None or key != previous:
                stable_since = clock()
            # the same state under pixels that keep moving: an animation that never ends is not waited out
            if key is None or key[:-1] != read:
                read_since = looked
            same_reads = same_reads + 1 if key is not None and key[:-1] == read else (1 if key is not None else 0)
            read = key[:-1] if key is not None else None
            restless = policy.still and usable and not rests and clock() - read_since >= RESTLESS_S
            settled = (not dealt or key == previous) if fast else (
                not policy.still or image == pictured or (kit and same_reads >= SAME_READS))
            if rests:
                # at rest: read the same that often with nothing changing between the last two frames; or, where
                # something not known to recur keeps changing, read the same for longer
                settled = same_reads >= SAME_READS and (quiet or clock() - read_since >= UNQUIET_S)
            if kit and expect is not None and not ticked:
                ticked = bool(self.adapter.ticked(expect[0].png, png))
            if image is None or image != pictured:
                rested = looked
            previous, pictured = key, image
            self.rested_at = read_since if restless else rested
            if usable:
                self.kit_reads += isinstance(self.adapter, KitAdapter)
                reading = Reading(png, state.payload, looked, L3, moving=self._kept_moving(watched, expect))
                effect = self._effect(expect[0].state, expect[1], state.payload) if expect is not None else None
                if expect is not None and (settled or restless) and effect == "confirmed":
                    return reading
                if kit and effect == "absent" and same_reads >= SAME_READS and not ticked \
                        and clock() - began >= (REFUSED_S if self.adapter.counts else ABSENT_S):
                    # the board as it was, and the game's count of moves left as it was: the move was not taken
                    told["refused_s"] = round(clock() - began, 2)
                    return reading
                stable = clock() - stable_since >= policy.stable_s or restless
                if stable and (expect is None or clock() >= deadline):
                    return reading
                if clock() >= deadline:
                    if rests:
                        # (kept as it was: what kept the board from reading the same is told from the picture)
                        self._keep_screen(png, "unsettled")
                    if rests and len(recent) >= 3:
                        # it never read the same for long: where something keeps passing over a cell, the frames
                        # drawn through one another show the board under it (`motion.middle`)
                        drawn = motion.middle(recent)
                        told["drawn_through"] += 1
                        state, _ = self._observe(drawn)
                        if state is not None:
                            return Reading(drawn, state.payload, looked, L3, moving=expect[0].moving)
                    self.why, self.unread = "surface_unsettled", None
                    return None
            # the next frame: one taken this long after this one, and no later than the deadline
            after = min(looked + policy.interval_s, deadline)

    def _moving_beside(self) -> bool:
        """Whether everything that keeps moving on the screen lies beside what the family's reader reads (the
        board and where the pieces wait): a pulsing score or a banner is then no reason to draw through frames."""
        regions = self.adapter.regions()
        for left, top, width, height in self.seen.moving:
            for x0, y0, x1, y1 in regions:
                if left < x1 and x0 < left + width and top < y1 and y0 < top + height:
                    return False
        return True

    @staticmethod
    def _region(regions: list[list[int]]) -> list[int]:
        """The part of the screen that holds all of these ([left, top, right, bottom] each)."""
        return [min(box[0] for box in regions), min(box[1] for box in regions),
                max(box[2] for box in regions), max(box[3] for box in regions)]

    def _recurring(self, before: Reading) -> list[list[int]]:
        """The small places that kept changing while the board a move was played from stood at rest: a wait for
        the move's result to come to rest leaves them out. Where nothing told of that board, the places the screen
        was last seen to keep moving at (`motion.Steady`)."""
        from io import BytesIO
        from PIL import Image

        with Image.open(BytesIO(before.png)) as image:
            area = image.width * image.height
        return motion.small(before.moving if before.moving is not None else self.seen.moving, area)

    @staticmethod
    def _kept_moving(watched: Any, expect: tuple[Reading, dict[str, Any]] | None) -> list[list[int]] | None:
        """Where a board just read at rest kept changing between the last two frames of it; what was known of the
        board before it when these tell nothing."""
        found = watched.changing() if watched is not None else None
        return found if found is not None else (expect[0].moving if expect is not None else None)

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
        if isinstance(self.adapter, KitAdapter):
            if agreeing and any(_board(state) != _board(agreeing[0]) for state in agreeing[1:]):
                # the rules leave it open: a result that has, where the screen is unread, a value the cell there
                # does not look like is not this screen's (a new tile unlike every 2 seen is the 4 it may also be)
                agreeing = [state for state in agreeing if not self.adapter.excludes(png, state)]
            if not agreeing or any(_board(state) != _board(agreeing[0]) for state in agreeing[1:]):
                return False
            learned = self.adapter.teach(png, agreeing[0])
            self.told_by_rules += learned
            return bool(learned)
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

    async def _ask_light(self, png: bytes, expect: tuple[Reading, dict[str, Any]]) -> bool:
        """What the family's reader left unread on the result of a move and the rules leave open, put to a light
        model: each kind of unread cell cut out and shown alone, to be named one of the values the family's cells
        hold (PLAN §20, X1). Its answers are kept only when the screen then reads as a result the rules allow of
        the move; else the screen is the reading model's, as before. True when the reader learned from them."""
        if self.light is None or not isinstance(self.adapter, KitAdapter) or self.adapter.partial is None:
            return False
        asked = self.adapter.questions(png)
        if not asked:
            return False
        kit = self.adapter.kit
        checks = [Check(f"k{index}-{shown}", CHOICE, LIGHT_QUESTION, picture, kit.options())
                  for index, (_, pictures) in enumerate(asked) for shown, picture in enumerate(pictures)]
        self.light_told["questions"] += 1
        started = time.monotonic()
        try:
            answers = await self.light.ask(checks, LIGHT_CONTEXT.format(game=self.family.descriptor["summary"]))
        except Exception as error:
            log.info("the light model could not be asked: %s", error)
            return False
        finally:
            self.model_seconds += time.monotonic() - started
        state = json.loads(json.dumps(self.adapter.partial))
        grid = state[kit.field]
        for index, (cells, pictures) in enumerate(asked):
            said = {(answers.get(f"k{index}-{shown}") or {}).get("choice") for shown in range(len(pictures))}
            if len(said) != 1 or None in said:
                self.light_told["refused"] += 1
                return False            # no answer, or two pictures of one kind named differently
            value = kit.value(next(iter(said)))
            for row, col in cells:
                grid[row][col] = value
        try:
            fits = self._effect(expect[0].state, expect[1], state) == "confirmed"
        except (ValueError, KeyError, TypeError):
            fits = False
        if not fits:
            self.light_told["refused"] += 1
            return False
        learned = self.adapter.teach(png, state, "light")
        self.light_told["named"] += learned
        return bool(learned)

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
            await self._rest(expect[0])

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
                    accept if expect is not None or partial is not None else None, patient=False, prior=self._prior(expect),
                    anew=self.why == "scene_changed"))
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
        elif rules != self.trace.rules:
            self._resize(rules)
        # where the board is now: the first screen's box, or another when the game has moved on the screen
        self.board = board
        look = Look(png, state, self.board, frames=tool.frames, sources=tool.sources, panel=tool.panel)
        self.trace.looks.append(look)
        if expect is None and partial is None:
            self.unproven = look
        reading = Reading(png, state, started, L2, look, moving=list(self.seen.moving))
        if self.adapter is None and kits.has(self.family_id) and not self.kit_demoted:
            # a family with a reader of its own is read by it from the first look on
            if self._adopt_kit():
                return self._own(reading)
        return reading

    async def _rest(self, before: Reading | None = None) -> None:
        """Wait until the game's part of the screen shows the same twice in a row: a move's result is read by
        the model, and kept as a look to write an adapter from, once what the move set off (blocks clearing, a
        banner flashing over the board) is over. What keeps changing in the same places while a board stands (a
        glint on a piece, a hint's wiggle, a button pulsing: `_recurring` of the board the move was played from)
        never is over and is left out; a thing that changes other places from frame to frame is waited on until it
        ends. Not waited for longer than STILL_TIMEOUT_S, whatever still moves: what covers a cell then is left
        out of the picture the model is shown by the frames drawn through one another (`motion.middle`)."""
        boxes = [self.board, *(box for look in self.trace.looks[-1:] if isinstance(look.sources, list)
                               for box in look.sources if box)]
        watched = motion.Resting(self._region([[box[0], box[1], box[0] + box[2], box[1] + box[3]] for box in boxes]),
                                 self._recurring(before) if before is not None else None, STILL_SIDE, STILL_DISTANCE)
        began, after, deadline, frames = time.monotonic(), None, time.monotonic() + STILL_TIMEOUT_S, 0
        while True:
            frame = await self.frames.frame(after)
            frames += 1
            rested = watched.seen(frame.png)
            if rested or time.monotonic() >= deadline:
                self.rest_told = {"s": round(time.monotonic() - began, 2), "frames": frames, "rested": rested,
                                  "left_out": len(watched.masked)}
                return
            after = frame.at + STILL_GAP_S

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
        self.kit_demoted = self.kit_demoted or isinstance(self.adapter, KitAdapter)
        self.adapter, self.distrusted, self.blind = None, False, False
        if self.carried is not None and self.carried.by == L3:
            self.carried = None
        self.need = sum(look.action is not None for look in self.trace.looks) + RETRY_LOOKS

    async def _recheck(self, vouched: bool = True) -> None:
        """The adapter against every look so far, the one just read included: kept when it reads them all,
        else written again from them. `vouched` off: no confirmed move vouches for the look just read, so a
        reader that reads it otherwise is doubted on, and that is not counted against it."""
        if isinstance(self.adapter, KitAdapter):
            # the family's reader learns the screen again from what the model just read: nothing is written
            if self._adopt_kit() and self.adapter.agrees(self.trace.looks[-1]):
                if self.store is not None:
                    self._keep_profile()
                return
            self.distrusted = True
            if not vouched:
                return
            self.rewrites += 1
            if self.rewrites >= MAX_RELEARNS:
                self.kit_demoted = True
                self._demote("relearn_limit")
            return
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
        if kits.has(self.family_id):
            return not self.kit_demoted and self._adopt_kit()
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
        if not self.settling.still or (isinstance(self.adapter, KitAdapter) and not self.adapter.foresees):
            # (a reader whose rules leave a move's result open goes on taking it at rest: what a move sets off
            # lasts as long as it lasts, whatever the moves before it took)
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
            check = self.adapter.hover(before.state, action) if isinstance(self.adapter, KitAdapter) else None
            self.carried_as = await self.carrier.carry((sent["x0"], sent["y0"]), (sent["x1"], sent["y1"]), sent["ms"],
                                                       before=before.png, source=self._source(before, action), check=check,
                                                       shape=self._piece(before, action))

    def _piece(self, before: Reading, action: dict[str, Any]) -> list[list[int]] | None:
        """The blocks of the thing a move drags, as rows and columns, when the state lists them so."""
        controls = self.adaptation.controls if self.adaptation else {}
        try:
            piece = before.state[controls["source"]][action[controls["index"]]]
        except (KeyError, IndexError, TypeError):
            return None
        return piece if isinstance(piece, list) and piece and all(
            isinstance(cell, list) and len(cell) == 2 and all(type(part) is int for part in cell) for cell in piece) else None

    def _source(self, before: Reading, action: dict[str, Any]) -> list[int] | None:
        """Where the screen a move is played from was read to show the thing the move drags, when it was."""
        index = action.get(self.adaptation.controls.get("index", "")) if self.adaptation else None
        if isinstance(self.adapter, KitAdapter) and type(index) is int:
            # where the family's reader sees the thing on that screen: the same box whoever read the screen, so
            # that what carries learn of one piece holds for the next
            if (seen := self.adapter.source_on(before.png, index)) is not None:
                return seen
        sources = before.look.sources if before.look is not None else None
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

    async def _unstick(self, reason: str, stage: str = "") -> None:
        """Play cannot go on from this screen: a model is shown it and sends one input of its own (L0), and
        the screen is looked at again. The game ends when the model sees its end on the screen, sees nothing
        to do, or MAX_TRIES inputs in a row brought no confirmed move. `stage`: where the family's play stopped
        (the screen not read as its board, the level read as ended, no move on it, a message of the game's)."""
        if reason == "inconsistent" and (await self._rules_again() or await self._other_family()):
            return
        if self._before_any():
            if self.direct:
                self._let_go(reason, stage)
            # whatever stands in the way, the next level is what is looked for
            reason = NEXT_LEVEL
            if self.direct:
                await self._between()
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
        context = {"state": self.state, "recent": recent, **({"onward": True} if self.onward else {}),
                   **({"anew": True} if self._before_own() else {})}
        if await self._asking(self.recovery.attempt(self.seen, reason, context, timeout_s=self._left())):
            if self.blind:
                self.distrusted = self.blind = False
            # an input of the model's stands between the last move and this screen: it is read for itself
            self.unread_move = None
            self.screen = self.recovery.log[-1].get("scene") or self.screen
            return
        action = self.recovery.log[-1]["action"] or {}
        self.screen = self.recovery.log[-1].get("scene") or self.screen
        if action.get("kind") == STOP:
            raise _End(Outcome.STOPPED, "stop_condition", self.recovery.log[-1].get("because") or self._situation())
        if action.get("kind") == recovering.OUT_OF_LIVES:
            self._out_of_lives(action, self._situation())
        if "moves_left" in action:
            self.found["moves_left"] = action["moves_left"]
        if action.get("kind") == GIVE_UP and "end" in action and self._before_any():
            # the end of the level before this part, which was to be passed for the next: no next level was found
            raise _End(Outcome.GAVE_UP, "next_level_not_found", self._situation())
        if action.get("kind") == GIVE_UP and "end" in action and self._before_own():
            # the end of a game played before the work: none was started for it
            raise _End(Outcome.GAVE_UP, "new_game_not_found", self._situation())
        if action.get("kind") == GIVE_UP and "end" in action:
            self._ended(action["end"], self.recovery.log[-1].get("scene"))
            won = action["end"] == WON and self._won_meets_goal()
            raise _End(Outcome.GOAL_REACHED if won else Outcome.GAME_OVER, WON if won else "end_screen", self._situation())
        if action.get("kind") == NONE and not self.shrugged:
            # the model sees a board in play: it is read once more before the game is given up
            self.shrugged = True
            return
        if await self._other_family():
            return
        await self._give_up(reason)

    def _out_of_lives(self, action: dict[str, Any], situation: str) -> None:
        """The game opens no level until time gives a life back, and buying one or watching an advertisement for
        it is not done: nothing is waited for, the work is handed back with how long until the next life (PLAN
        §26, C2-4)."""
        self.found["out_of_lives"] = {"minutes": action.get("minutes"), "situation": situation}
        raise _End(Outcome.STOPPED, OUT_OF_LIVES, situation)

    def _let_go(self, why: str, stage: str) -> None:
        """Play by the family is handed to the model's own inputs: why, kept for the record (PLAN §26, C2-1). A
        family taken again on a screen the model named a board and let go before a move of its own is tried again
        only some inputs on, as one that did not read the screen is (C2-2): else the tries a game has are all used
        up on the level's first screen."""
        if self.family_id is None or self.policy == "model":
            return
        entry = {"at_action": self.used, "family": self.family_id, "stage": stage or "other", "why": why,
                 "moves": self.since_taken, "resumed": self.resumed, "on_board": self.on_board}
        if stage == "no_move" and isinstance(self.state, dict) and self.family is not None:
            # what the rules made of the board that offered no move: the ways of moving still unknown say why
            rules = self.family.rules
            entry["ways"] = {name: rules.get(name) for name in ("square", "swap_power", "pair_power", "tap_power")
                             if name in rules}
        if stage == "read":
            entry["read_by"] = "kit" if isinstance(self.adapter, KitAdapter) else "adapter" if self.adapter else "model"
        self.let_go = [*self.let_go, entry][-LET_GO_KEPT:]
        self.found["let_go"] = self.let_go
        if self.found.get("resumes") and self.resumed:
            self.found["resumes"][-1].setdefault("let_go", entry)
        if self.resumed and not self.since_taken:
            self.unread_at = self.used
        self.on_board = False

    async def _between(self) -> None:
        """The screen stands between two levels (PLAN §25, R6): it is played on by the steps kept of the game's
        screens where one fits and by the model's own inputs where none does (`_play_direct`, `routine`), through
        whatever the game puts there, until the family reads a board (`_resume`)."""
        self.carried = None
        self.fruitless = self.since_tried = 0
        if self.resumable is None and (self.game is None or self.family_id != self.game.id):
            self.resumable = self.family_id
        await self._play_direct(self._situation())

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
            self._let_go(reason, "given_up")
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
            self.player = direct.Direct(self.model, hint=self.hint, accept_consent=self.accept_consent,
                                        refused=self.account["refused"], notes=self.notes)
        player = self.player
        self.found["direct"] = told = player.summary()
        self.levels.setdefault(L0, 0)
        objective, idle, silent = str(self.goal.objective), 0, 0
        # the screen an errand's work found: shown to the model beside each later one (`direct.Direct.ask`)
        found: bytes | None = None
        while True:
            told.update(player.summary())
            if objective == "actions" and self.confirmed >= (self.goal.target_value or 1):
                raise _End(Outcome.GOAL_REACHED, "actions")
            if objective == "reach_value" and player.score is not None and self.goal.target_value is not None \
                    and player.score >= self.goal.target_value:
                raise _End(Outcome.GOAL_REACHED, objective)
            self._check_stops()
            before = await self.seen.capture()
            moving = list(self.seen.moving)
            looked = time.monotonic()
            self.shots += 1
            self.last_png = before
            found = found or before
            if self.watch is not None:
                await self.watch.seen(before)
            left = {"inputs": self.budget.max_actions - self.used,
                    "seconds": None if self._left() is None else round(self._left())}
            # a screen that is no board, when play left one like it before: the model is told, with what it tried
            # from there, and play that keeps coming back to it is given up (PLAN §27, M5)
            been = self.trail.met(before) if player.scene != scenes.BOARD else None
            if been is not None and self.trail.round():
                raise _End(Outcome.GAVE_UP, "going_round", player.situation)
            between = self._before_any()
            # a screen the game's routine has a step for is passed by that step, and no model is asked (PLAN §28)
            did = await self._by_routine(before, moving, looked)
            if did == RESUMED:
                return
            if did == AGAIN:
                continue
            self._afford()
            try:
                answer = await self._asking(player.ask(before, self.surface.kinds, self.goal.to_json(), left, moving,
                                                       onward=between, been_here=been,
                                                       anew=self._before_own() and not self._errand(),
                                                       errand=self.errand if self._errand() else None,
                                                       began=found if self._errand() and found is not before else None))
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
            action, scene = answer["action"], answer["scene"]
            if self._errand() and scene in (None, scenes.BOARD):
                # an errand has no board of its own: the screen it is done on is the game's own ground, whatever
                # the model called it, and an input on it is a step of the errand, not a move of a level
                scene = answer["scene"] = scenes.MAP
            self.screen = scene or self.screen
            # a move of a level: an input on a screen the model names a board (or names nothing)
            level = scene in (None, scenes.BOARD)
            if action["kind"] == direct.STOP:
                raise _End(Outcome.STOPPED, "stop_condition", answer.get("because") or player.situation)
            if action["kind"] == direct.OUT_OF_LIVES:
                self._out_of_lives(action, player.situation)
            if "moves_left" in action:
                self.found["moves_left"] = action["moves_left"]
            if action["kind"] == direct.DONE and action["end"] == WON and self._errand():
                # the errand is done: the stretch got where it was going, and the steps learned on the way are kept
                self.book.of(self.errand).proved()
                raise _End(Outcome.GOAL_REACHED, TASK_DONE, player.situation)
            if action["kind"] == direct.DONE and self._before_any():
                raise _End(Outcome.GAVE_UP, "next_level_not_found", player.situation)
            if action["kind"] == direct.DONE and self._before_own():
                # the end of a game played before the work, which was asked for from its start: not its own end
                raise _End(Outcome.GAVE_UP, "new_game_not_found", player.situation)
            if action["kind"] == direct.DONE:
                self._ended(action["end"], scene)
                won = action["end"] == WON
                raise _End(Outcome.GOAL_REACHED if won and objective == "play_until_end" else Outcome.GAME_OVER,
                           WON if won else "end_screen", player.situation)
            if action["kind"] == direct.GIVE_UP:
                raise _End(Outcome.GAVE_UP, "model_gave_up", player.situation)
            if scene == scenes.BOARD and not answer["guided"] and self.resumable is not None \
                    and self.resumes < RESUMES_MOST and action["kind"] not in (direct.WAIT, direct.RELAUNCH) \
                    and (self.unread_at is None or self.used - self.unread_at >= RESUME_EVERY):
                # a level is open and takes any move: the family whose board it is plays it, and the model's
                # move is not sent. One the family does not read is the model's, and the family is tried again
                # some moves on
                if await self._resume(board=True):
                    return
                self.unread_at = self.used
            if self.watch is not None:
                await self.watch.sending(self.used + 1, action, None, action, looked)
            if not level:
                self.trail.sent(answer["told"])
            # inputs sent one after another at one answer, each only once the one before changed a little of the
            # screen: a tap to be sent again (a story's lines), or the inputs of a sequence on a screen that is no
            # board (PLAN §27, M1). On a board a sequence is one move, sent whole
            queue: list[dict[str, Any]] = []
            if action["kind"] == direct.SEQUENCE and not level:
                queue = list(action["inputs"][1:])
            elif action.get("repeat") and not level and not answer["uses"]:
                queue = [{"kind": direct.TAP, "at": action["at"]}] * (action["repeat"] - 1)
            carried = await direct.send(self.seen, action["inputs"][0] if action["kind"] == direct.SEQUENCE and not level
                                        else action, self.carrier, before)
            self.used += 1
            for name in answer["uses"]:
                # `forced_free`: of those, a booster the game forced in a tutorial for nothing
                self.account["used"][name] = self.account["used"].get(name, 0) + 1
            if answer.get("profile"):
                self.profiles.append({"made": answer["profile"], "situation": player.situation})
            if self.watch is not None:
                await self.watch.sent()
            after = await direct.settled(self.seen)
            moving = motion.joined(moving + self.seen.moving)
            repeated, sent_on = None, before
            # the screen each input of this answer was sent on
            screens = [before]
            if queue:
                # the next is not sent once one changed nothing, or so much that the scene is another: the model
                # looks at that first
                sends, why = 1, "count"
                for following in queue:
                    share = self._share(sent_on, after, moving)
                    if not share or share >= direct.REPEAT_SHARE or self.used >= self.budget.max_actions:
                        why = "no_change" if not share else "scene_changed" if share >= direct.REPEAT_SHARE else "budget"
                        break
                    self._check_stops()
                    sent_on = after
                    screens.append(after)
                    await direct.send(self.seen, following, self.carrier, after)
                    self.used, sends = self.used + 1, sends + 1
                    after = await direct.settled(self.seen)
                    moving = motion.joined(moving + self.seen.moving)
                repeated = {"sent": sends, **({"of": len(queue) + 1} if action["kind"] == direct.SEQUENCE else {}),
                            "stopped": why}
            changed = player.saw(answer, sent_on, after, moving, carried, repeated) or bool(repeated and repeated["sent"] > 1)
            if not level:
                self._learn(answer, screens, changed)
            if carried and carried.get("picked") and not changed:
                self.carrier.doubt()
            self.levels[L0] += 1
            self.moves.append({"action_id": f"a{self.used}", "kind": action["kind"], "action": answer["told"], "level": L0,
                               "read_after_by": L0, "delivery": "delivered", "effect": "confirmed" if changed else "absent",
                               "expected": answer["expect"], "at_s": round(time.monotonic() - self.started, 2),
                               **({"scene": scene} if scene else {}), **({"repeated": repeated} if repeated else {}),
                               **({"carried": carried} if carried else {})})
            if changed or action["kind"] == direct.WAIT:
                self.confirmed, idle = self.confirmed + (action["kind"] != direct.WAIT), 0
                if level and changed and action["kind"] != direct.WAIT:
                    if self._task() is not None:
                        # the model's own move on a level's board: the stretch that led here got there
                        self.book.of(self._task()).proved()
                    self.level_moves += 1
                    self.trail.clear()
            else:
                idle += 1
                if idle >= MAX_IDLE:
                    raise _End(Outcome.GAVE_UP, "no_effect", player.situation)
            self.since_tried += bool(changed)
            if changed and self.resumable is None and self.named and self.game is None \
                    and self.reasked < REASK_MOST and self.since_tried >= RESUME_EVERY:
                # the game was asked about on a screen that showed none yet (a splash, a notice) and so has no
                # family: asked again now that the model's own moves are taken, it may be one there is play for
                self.reasked, self.since_tried = self.reasked + 1, 0
                try:
                    asked = await self._identify()
                except _End:
                    asked = {"families": []}
                if asked["families"]:
                    self.resumable = asked["families"][0]
                    self.found.setdefault("identified", {}).update(candidates=list(asked["families"]), reasked=self.reasked)
                    if await self._resume():
                        return
                    continue
            # a model that names no kind of screen: the family is tried again when the scene changed, or some
            # moves on
            if scene is None and changed and (self._new_scene(before, after, moving) or self._due()) and await self._resume():
                return

    def _due(self) -> bool:
        """Whether enough of the model's moves were confirmed since the family was last tried to try it again."""
        return self.resumable is not None and self.resumes < RESUMES_MOST and self.since_tried >= RESUME_EVERY

    def _new_scene(self, before: bytes, after: bytes, moving: list[list[int]]) -> bool:
        """Whether a move of the model's changed so much of the screen (left out what kept moving) that a family
        whose play broke off may read the board now: a tutorial gave way to the game, a level to the next."""
        if self.resumable is None or self.resumes >= RESUMES_MOST:
            return False
        return self._share(before, after, moving) >= RESUME_SHARE

    def _share(self, before: bytes, after: bytes, moving: list[list[int]]) -> float:
        """The share of the screen that changed between two pictures of it, left out what kept moving."""
        from io import BytesIO
        from PIL import Image

        with Image.open(BytesIO(after)) as image:
            area = image.width * image.height
        changed = motion.outside(carry_boxes(before, after), moving)
        return sum(box[2] * box[3] for box in changed) / area

    async def _resume(self, board: bool = False) -> bool:
        """The family whose play broke off, tried again on the screen as it is now: taken when its board reads.
        A reader kept of the site vouches for nothing here: it must read this screen, else the family is tried
        by a model's reading of it. `board`: the model named the screen a level's board: a reader that cannot
        read it does not make it a screen between two levels (`_read`), the model reads it instead (C2-3)."""
        family_id, self.resumes, self.since_tried = self.resumable, self.resumes + 1, 0
        tried = {"family": family_id, "at_action": self.used, "taken": False, **({"on_board": True} if board else {})}
        self.found.setdefault("resumes", []).append(tried)
        reading = None
        try:
            taken = await self._take(family_id, probe=True)
            if taken and self.adapter is not None:
                reading = await self._read_code(None)
                if reading is None:
                    tried["kept_unread"] = self.why
                    taken = await self._take(family_id, probe=True, kept=False)
        except _End:
            raise
        except Exception as error:
            log.info("the family could not be tried again: %s", error)
            tried["error"] = f"{type(error).__name__}: {error}"[:300]
            taken = False
        if not taken:
            # the model's own play goes on: the model's reading of the screen was no board the rules take
            tried.setdefault("why", "error" if "error" in tried else "not_read_as_board")
            if self.adapter is not None:
                self.adapter.close()
            self.adapter, self.family_id, self.policy = None, None, "model"
            return False
        tried["taken"] = self.resumed = True
        tried["read_by"] = "kit" if reading is not None else "model"
        self.since_taken, self.on_board = 0, board
        self.tries = self.stalled = self.fruitless = 0
        self.shrugged = False
        self.unread_move = self.ineffective = self.state = None
        self.carried = reading
        task = self._task()
        if task is not None:
            # the stretch got to a level's board: what was learned on the way is kept, and the step the board was
            # read after is the one after which the family is tried next time, no model asked
            self.book.of(task).proved()
            self.book.of(task).opened(self.stretch["last"])
        self._stretch()
        self.trail.clear()
        return True

    def _goals_unread(self) -> bool:
        """Whether the family plays levels by goals its reader takes from a model's look (`_take_goals`) and none
        are known of this one."""
        return isinstance(self.adapter, KitAdapter) and getattr(self.adapter.kit, "goals", None) is not None \
            and self.family is not None and "goals" in self.family.rules and not self.family.rules.get("goals")

    # ── the game's routine (PLAN §28) ─────────────────────────────────

    def _task(self) -> str | None:
        """What the model's own play is for now, when it is a stretch the game has again and again: getting to
        the next level's board, the errand the request named (`errand`) while no move of a level was made, or
        getting to a new game's board. None on a level's board, where an input depends on the position."""
        if self._before_any():
            return routine.NEXT_LEVEL
        if self.errand and not self.level_moves:
            return self.errand
        return routine.NEW_GAME if self._before_own() else None

    def _errand(self) -> bool:
        """Whether this game is played for the errand the request named, and for nothing else."""
        return bool(self.errand) and not self.onward

    def _stretch(self) -> None:
        """A stretch of the model's own play begins: nothing of the routine was sent in it yet. `sends`: how often
        each step was sent (by code or by the model), `avoid`: steps that changed nothing here or were waited on
        long enough, `waits`: since when each screen to wait on has been, `last`: the last
        input sent that is a step (by code or by the model), `by_code`: whether code sent the last input, `until`:
        the time up to which a screen no step fits is looked at again before anyone is asked, `seen` and `since`:
        the last such screen and since when it has looked the same, `via`: by step id, the steps by way of which
        play came back to that step's screen when code sent it again (`routine.Routine.spent`)."""
        self.stretch = {"sends": {}, "avoid": set(), "total": 0, "waits": {}, "last": None,
                        "by_code": False, "until": 0.0, "first": True, "seen": None, "since": 0.0, "via": {}}

    async def _by_routine(self, before: bytes, moving: list[list[int]], looked: float) -> str:
        """The screen passed by a step kept of this game's screens, when one fits it (`routine.Routine.find`):
        the input the model chose on a screen like it is sent again and its effect looked at; a screen kept as one
        to wait on is looked at again. Where none fits right after a step, the screen may be on its way to the
        next and is looked at again for a while; after a step that opened a level before, the family is then tried
        on it. ASK: the model is to be asked about this screen."""
        task = self._task()
        if task is None:
            return ASK
        kept, stretch = self.book.of(task), self.stretch
        first, stretch["first"] = stretch["first"], False
        if first and kept.steps:
            stretch["until"] = time.monotonic() + FIRST_S
        step = None
        if stretch["total"] < routine.STRETCH_MOST:
            spent = {step["id"] for step in kept.steps
                     if kept.spent(step, stretch["sends"].get(step["id"], 0), stretch["last"], stretch["via"])}
            step = kept.find(before, avoid=stretch["avoid"] | spent, at=self.used)
        if step is None:
            now, seen = time.monotonic(), recovering.signature(before)
            if stretch["seen"] is None or recovering._distance(stretch["seen"], seen) > STILL_NEAR:
                stretch["since"] = now
            stretch["seen"] = seen
            if now < stretch["until"] and now - stretch["since"] < STILL_S:
                # a scene on its way to the next: looked at again until it stands still
                await asyncio.sleep(LOOK_AGAIN_S)
                return AGAIN
            stretch["until"] = 0.0
            last, by_code = stretch["last"], stretch["by_code"]
            stretch["by_code"] = False
            if by_code and last is not None and last.get("opens") and self.resumable is not None \
                    and self.resumes < RESUMES_MOST:
                if await self._resume(board=True):
                    return RESUMED
                kept.unopened(last)
            return ASK
        if step["action"]["kind"] == direct.WAIT:
            # a screen to wait on: for as long as the step has patience (`routine.Routine.patience`), then it is
            # left out and whoever is next looks at the screen
            if time.monotonic() - stretch["waits"].setdefault(step["id"], time.monotonic()) >= kept.patience(step):
                stretch["avoid"].add(step["id"])
                stretch["sends"][step["id"]] = 1
                return AGAIN
            kept.used(step, True)
            await asyncio.sleep(LOOK_AGAIN_S)
            stretch.update(by_code=True, until=time.monotonic() + TRANSIT_S)
            return AGAIN
        if stretch["sends"].get(step["id"], 0) >= kept.most(step):
            # sent again because play came back by way of another step: that one lets it be sent again only once
            stretch["via"].setdefault(step["id"], set()).add(stretch["last"]["id"])
        action = {key: value for key, value in step["action"].items() if key != "repeat"}
        scale = min(1.0, recovering.VIEW_SIDE / max(recovering._size(before)))
        told = direct.scaled(action, 1 / scale)
        if self.watch is not None:
            await self.watch.sending(self.used + 1, action, None, action, looked)
        self.trail.sent(told)
        await direct.send(self.seen, action, self.carrier, before)
        self.used += 1
        if self.watch is not None:
            await self.watch.sent()
        after = await direct.settled(self.seen)
        changed = bool(motion.outside(carry_boxes(before, after), motion.joined(moving + self.seen.moving)))
        kept.used(step, changed)
        stretch["sends"][step["id"]] = stretch["sends"].get(step["id"], 0) + 1
        stretch["total"] += 1
        self.player.replayed(told, step.get("scene"), changed)
        self.levels[ROUTINE] = self.levels.get(ROUTINE, 0) + 1
        self.moves.append({"action_id": f"a{self.used}", "kind": action["kind"], "action": told, "level": ROUTINE,
                           "read_after_by": ROUTINE, "delivery": "delivered", "effect": "confirmed" if changed else "absent",
                           "step": step["id"], "at_s": round(time.monotonic() - self.started, 2),
                           **({"scene": step["scene"]} if step.get("scene") else {})})
        self.confirmed += changed
        self.screen = step.get("scene") or self.screen
        if changed:
            stretch.update(last=step, by_code=True, until=time.monotonic() + TRANSIT_S)
        else:
            stretch["avoid"].add(step["id"])
        return AGAIN

    def _learn(self, answer: dict[str, Any], screens: list[bytes], changed: bool) -> None:
        """The inputs the model just sent on a screen that is no board, kept as steps of the game's routine: each
        with the screen it was sent on. The last of them only when the screen changed after it."""
        self.stretch.update(by_code=False, until=0.0)
        task = self._task()
        if task is None:
            return
        action, scene = answer["action"], answer["scene"]
        parts = action["inputs"] if action["kind"] == direct.SEQUENCE else [action]
        for index, (part, png) in enumerate(zip(parts, screens)):
            waits = part["kind"] == direct.WAIT and scene == scenes.LOADING
            if (index < len(screens) - 1 or changed or waits) and (waits or part["kind"] != direct.WAIT) \
                    and routine.kept(part, scene, answer):
                step = self.book.of(task).learn(png, part, scene=scene, sends=self.stretch["sends"])
                self.stretch["sends"][step["id"]] = self.stretch["sends"].get(step["id"], 0) + 1
                if not waits:
                    self.stretch["last"] = step

    def _ended(self, said: str, scene: str | None) -> None:
        """How a level's end was told, kept for the record: what a model said of it, on which kind of screen, and
        what the family still counted of the level's goals. Nothing but the model's word says how a level ended, and
        on a screen that is no result screen (a game that goes from a lost level straight to its map) that word is
        a guess from what the screen shows after (`inferred`): such an end is to be looked at before it is believed."""
        goals = self.trace.rules.get("goals") if self.trace is not None else None
        left = [goal.get("count") for goal in goals or [] if isinstance(goal, dict)]
        self.found["end"] = {"said": said, "screen": scene, "inferred": scene != scenes.RESULT,
                             **({"goals_left": left} if left else {})}

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


def _same_level(was: Any, now: Any) -> Any:
    """A level's goals as read again: a reading of the same level (as many goals, of the same kinds, none needing
    more than it did) tells how many are left, not what they are about, which was told already (2026-11-17, a
    reading taken as a goal's picture played its collecting took a pink button for a yellow lamp)."""
    if not isinstance(was, list) or not isinstance(now, list) or len(was) != len(now) or any(
            old.get("kind") != new.get("kind") or (type(old.get("count")) is int and type(new.get("count")) is int
                                                   and new["count"] > old["count"]) for old, new in zip(was, now)):
        return now
    return [{**new, "target": old["target"]} if old.get("target") is not None else new for old, new in zip(was, now)]


async def play(surface: Any, model: Model, family_id: str | None, *, goal: GoalSpec, budget: Budget,
               store: Store | None = None, policy: str | None = None, fresh: bool = False, hint: str | None = None,
               watch: Watch | None = None, cancel: asyncio.Event | None = None, accept_consent: bool = False,
               writer: Model | None = None, direct: bool = True, light: Any = None, onward: bool = False,
               notes: Any = "", anew: bool = False, game: str | None = None, book: routine.Book | None = None,
               task: str | None = None, **ignored: Any) -> tuple[Run, dict[str, Any]]:
    """One game from its first screen to its end: the whole game as one `Run`, and how it was read (the
    adapter, the looks, the model's questions and what the game was found to be). `writer`, when given, is
    asked to write the adapters and `model` everything else; `light` (a `checker`), to name single cells a
    family's reader could not."""
    return await Agent(surface, model, family_id, goal=goal, budget=budget, store=store, policy=policy, fresh=fresh,
                       hint=hint, watch=watch, cancel=cancel, accept_consent=accept_consent, writer=writer,
                       direct=direct, light=light, onward=onward, notes=notes, anew=anew, game=game, book=book,
                       task=task).run()
