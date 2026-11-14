"""A game on a real screen: a browser page or a phone, looked at and played through a `Surface`.

How the site's game is read and played is its site adapter's (`adapters.SiteAdapter`): the board is
measured on the first look and the layout kept as the session's geometry. A look the geometry no longer
reads (another window size, a moved board) is measured again and, when every check confirms the new
layout, play goes on with it: the change drops any plan made on the old one. A move's effect is only
ever checked on the geometry it was played on.

A whole screen the code reads no board on, shown the same by two looks in a row or for longer than
`UNREAD_ASK_S` however its pixels move, is put to the bounded model (`vision.CorrectionReader.classify`):
a board the code did not find is measured where the model saw it (`SiteAdapter.locate`) and becomes the
session's geometry only when every check confirms it; the game's own message for a scene that stops play
is that scene, with nothing under it read; something else over the board is a cover the session closes
(`session.CLOSE_COVER`) with a tap on the one control the model found.

This harness measures a game agent, so the surface is trusted: an input is sent as mapped, with no
approval, no re-check of the pixels right before it and no receipt from the surface. What stays is the
game's own check: every move's result is read again and compared with what the rules predict (the session).
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import logging
import time
from typing import Any, Protocol
import uuid

from .. import catalog, perception, pixels
from ..adapters import SiteAdapter
from ..contracts import (
    ActionPlan, Delivery, EnvironmentUnavailable, InputReceipt, NeedObservation, Neutral,
    Observation, PreparedInput, StateEstimate,
)
from ..errors import Denied
from ..execution import MappedAction, map_action, tap
from ..surface import DRAG, KEYS, TAP, Surface  # noqa: F401 (named here by what plays through this environment)
from ..session import (ACCEPT_CONSENT, CELLS_UNREAD, CLOSE_COVER, DECLINE_CONSENT, INPUT_VALID_S, STALLED, START_GAME,
                       UNCOVERING)

log = logging.getLogger("gamelab.screen")

INPUT_KIND_UNSUPPORTED = "input_kind_unsupported"
PAGE_READ_UNAVAILABLE = "page_read_unavailable"
# looks at a screen that shows none of the profile's boards before the game is refused: enough to
# outlast a frame caught mid-animation, not to wait for a page that is still loading
MEASURE_ATTEMPTS = 3
# how long the screen may show no readable board before a model is asked what it shows although its
# pixels keep changing (an animated advertisement over an end screen): longer than a move's animation
UNREAD_ASK_S = 3.0
# a direction key on a surface without keys (a phone) is a swipe across the board: its direction, how much
# of the board it crosses, and how long it takes
SWIPES = {"ArrowLeft": (-1, 0), "ArrowRight": (1, 0), "ArrowUp": (0, -1), "ArrowDown": (0, 1)}
SWIPE_SPAN = 0.6
SWIPE_MS = 120


class Watch(Protocol):
    """What a run may hang on the environment to measure it; the game is not told."""

    async def looked(self, observation: Observation, png: bytes) -> None: ...
    # state: what the move was planned on, None for the session's own press at a cover
    async def before_input(self, prepared: PreparedInput, state: dict[str, Any] | None, mapped: MappedAction) -> None: ...
    async def after_input(self, receipt: InputReceipt) -> None: ...


class ScreenEnvironment:
    def __init__(self, surface: Surface, adapter: SiteAdapter, *, reader: Any = None, watch: Watch | None = None,
                 accept_consent: bool = False) -> None:
        self.surface, self.adapter, self.reader, self.watch = surface, adapter, reader, watch
        self.family_id = adapter.family_id
        # whether the person agreed beforehand to a site's terms when it offers no refusal
        self.accept_consent = accept_consent
        self.refused: set[str] = set()
        # a game no profile registers (plan §4.5) gets the reading made on the first look the code confirms
        # (`_discover`), then handed to the run (`save_profile`); `save_geometry` gets each measured layout
        self.save_profile = None
        self.save_geometry = None
        self.unmeasured = 0
        self.unmeasured_reason = "board_not_found"
        # the next look captures the whole screen to measure the board again (a page read pictures the board alone)
        self.remeasure = False
        self.target = surface.name
        self.clock_id = f"screen:{uuid.uuid4().hex}"
        self.sequence = 0
        # the last look input may be planned on (its picture as read, and the page's elements), and the last
        # whole picture taken, kept for the run's record
        self.last: tuple[Observation, bytes, Any] | None = None
        self.evidence: bytes | None = None
        self.state: StateEstimate | None = None
        self.received_at = 0.0
        self.last_receipt: InputReceipt | None = None
        self.deadline: float | None = None
        # the last whole screen read no board on (by its pixels; what the model said such a screen shows
        # is kept by the reader), and the control a tap may press on this look
        self.unseen: str | None = None
        # when the looks since the last readable board began to show none (monotonic seconds), and the
        # scene that stops play a model saw in them: moving pixels over it are not asked about again
        self.unread_since: float | None = None
        self.stopped: dict[str, Any] | None = None
        # a model said the board shows in these looks and the code could make nothing of where: asking again
        # while no board reads would only say the same of moving pixels
        self.board_told = False
        self.control: list[int] | None = None
        self.looks = 0
        # the model asked what to do about a screen play stopped on (`recovery.Recovery`), when there is one
        self.recovery = None

    @property
    def still(self) -> bool:
        """Whether a move's result must also be still where input lands (`SettlingPolicy.still`)."""
        return self.adapter.played.get("settling", {}).get("still", False)

    def unsupported(self) -> str | None:
        """Why this surface cannot play the adapter's game, or None."""
        kind = self.adapter.played["controls"]["kind"]
        # a direction key may be swiped where there are no keys
        if kind not in self.surface.kinds and not (kind == KEYS and DRAG in self.surface.kinds):
            return INPUT_KIND_UNSUPPORTED
        if self.adapter.reads_dom and not self.surface.reads_dom:
            return PAGE_READ_UNAVAILABLE
        return None

    def geometry_record(self) -> dict[str, Any] | None:
        return self.adapter.record() if self.adapter.measured else None

    async def _adopted(self) -> None:
        """The layout the adapter just took is the session's geometry."""
        self.unmeasured = 0
        if self.save_geometry is not None:
            await self.save_geometry(self.geometry_record())

    async def _measure(self, png: bytes) -> bool | None:
        """Measure the board on this capture; a new layout every check confirms becomes the session's.
        Whether it changed, None when no board was found."""
        changed = self.adapter.measure(png)
        if isinstance(changed, NeedObservation):
            if not self.adapter.measured:
                # the last of the attempts ends the game unless a model accounts for the screen
                self.unmeasured += 1
                self.unmeasured_reason = changed.reason
            return None
        if changed:
            await self._adopted()
        return changed

    def offers(self, kind: str) -> bool:
        """Whether this look shows the button a meta action presses, found on these very pixels."""
        return kind in catalog.family(self.family_id).buttons and self.control is not None

    async def allow_meta(self, kind: str) -> None:
        """A meta action the goal did not allow at its start. A game's own button (keep playing after a win)
        is pressed; agreeing to a site's terms only when the run was started with that allowed."""
        if kind == ACCEPT_CONSENT and not self.accept_consent:
            self.refused.add(kind)

    async def ready(self, observation: Observation) -> bool:
        if self.refused:
            raise Denied(f"not allowed: {sorted(self.refused)}")
        if (reason := self.unsupported()) is not None:
            raise EnvironmentUnavailable(reason)
        covered = observation.public_facts.get("covered") is True and self.control is not None
        return (self.state is not None or covered) and bool(observation.input_ref) and await self.current(observation)

    async def current(self, observation: Observation) -> bool:
        """Whether input may still be planned on `observation`: the last look, within its time."""
        return await self.latest(observation) and time.monotonic() - self.received_at < INPUT_VALID_S

    async def latest(self, observation: Observation) -> bool:
        """Whether `observation` is still the last look at this screen, however old: a model's answer is
        about its pixels and is kept for them, while input waits for a look within its time."""
        return (self.last is not None and self.last[0].id == observation.id and observation.target == self.target
                and (self.deadline is None or time.monotonic() < self.deadline))

    async def observe(self, target: str, purpose: str) -> Observation:
        if target != self.target:
            raise ValueError("target_changed")
        if purpose not in ("decide", "input", "verify", STALLED):
            raise ValueError("observation_purpose_unknown")
        if (reason := self.unsupported()) is not None:
            raise EnvironmentUnavailable(reason)
        # a move's effect is judged on the geometry it was played on: only a look before a move measures again
        measuring = not self.adapter.measured or (self.remeasure and purpose != "verify")
        scope = self.adapter.scope(measuring)
        self.last, self.state, self.control = None, None, None
        self.received_at = time.monotonic()
        self.sequence += 1
        name = f"{self.clock_id}:{self.sequence}"
        observation = Observation(name, self.sequence, self.target, 0, self.received_at, self.clock_id, input_ref=name)
        region = scope.get("region")
        size = [region[2] - region[0], region[3] - region[1]] if region else None
        try:
            if region and await self.surface.size() != self.adapter.viewport:
                # the board's picture would come from a screen of another size: measure the whole screen next
                self.remeasure = purpose != "verify"
                return observation
            elements = await self.surface.elements(scope["elements"]) if scope else None
            png = await self.surface.capture(region)
            self.looks += 1
            with pixels.decode(png, size) as image:
                digest = hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest()
        except ValueError:
            return observation
        if scope and not isinstance(elements, dict):
            return observation
        if not region:
            self.evidence = png
        if self.watch is not None:
            await self.watch.looked(observation, png)
        self.last = observation, png, elements
        if self.adapter.reads_dom:
            if measuring:
                self.remeasure = False
                if await self._measure(png) is None:
                    return await self._unread_screen(observation, png, digest, elements, purpose)
                png = self.adapter.board_picture(png)
                self.last = observation, png, elements
            state = self.adapter.read(observation, png, elements)
            if (isinstance(state, NeedObservation) or state.unknown_fields) and purpose != "verify":
                self.remeasure = True        # the page and its pixels disagree: is the board where it was?
        else:
            if self.adapter.awaits_look:
                observation = await self._discover(observation, png, elements)
                if self.adapter.awaits_look:
                    return observation
            state = self.adapter.read(observation, png) if self.adapter.measured else None
            if (state is None or isinstance(state, NeedObservation)) and purpose != "verify":
                if await self._measure(png):
                    state = self.adapter.read(observation, png)
            if state is None:
                return await self._unread_screen(observation, png, digest, elements, purpose)
            if purpose == "verify" and isinstance(state, StateEstimate) and state.unknown_fields:
                state, taught = self.adapter.learn(observation, png, state)
                if taught and self.save_geometry is not None:
                    await self.save_geometry(self.geometry_record())
            state = await self.adapter.correct(observation, png, digest, state, self.reader, self._remaining())
            if purpose == STALLED and isinstance(state, StateEstimate) and not state.unknown_fields:
                observation, state = await self._judge_stalled(observation, png, digest, elements, state)
                if state is None:
                    return observation
        if isinstance(state, NeedObservation) and state.reason == CELLS_UNREAD:
            # the board was found and some of its cells are a look the code does not know (a value not seen
            # yet): nothing is in the way, so no model is asked what the screen shows; the reading is learned
            return replace(observation, public_facts={CELLS_UNREAD: True})
        if isinstance(state, NeedObservation) and not self.adapter.reads_dom:
            return await self._unread_screen(observation, png, digest, elements, purpose)
        if state is None or isinstance(state, NeedObservation):
            return observation
        observation = replace(observation, public_facts={"state": state.payload}, geometry_ref=self.adapter.geometry_ref,
                              image_ref=self.adapter.picture(png) if self.still else None)
        self.last, self.state, self.unread_since, self.stopped = (observation, png, elements), state, None, None
        self.board_told = False
        return observation

    async def _discover(self, observation: Observation, png: bytes, elements: Any) -> Observation:
        """The reading of a game no profile registers, from what the bounded model says this look shows and
        the code confirms on it (`SiteAdapter.propose`); handed to the run to keep, measured where the look
        was confirmed. A cover the model sees a control for is marked `covered`, closed as on any game; a look it
        cannot account for counts as a failed measurement, MEASURE_ATTEMPTS of which end the game."""
        answer = NeedObservation("model_unavailable")
        remaining = self._remaining()
        if self.reader is not None and (remaining is None or remaining > 0):
            try:
                async with asyncio.timeout(remaining):
                    answer = await self.reader.discover(observation, png, self.family_id)
            except TimeoutError:
                answer = NeedObservation("correction_timeout")
        if not isinstance(answer, NeedObservation):
            if (press := _press(answer)) is not None:
                self.unmeasured = 0
                return self._cover(observation, png, elements, press)
            if answer["screen"] == perception.BOARD:
                answer = self.adapter.propose(png, answer["look"])
            else:
                answer = NeedObservation("board_not_found")
        if isinstance(answer, NeedObservation):
            self.unmeasured += 1
            self.unmeasured_reason = answer.reason
            if self.unmeasured >= MEASURE_ATTEMPTS:
                raise EnvironmentUnavailable(answer.reason)
            return observation
        # the board where the look was confirmed is the session's first geometry
        made = self.adapter.adopt_profile(answer)
        if self.save_profile is not None:
            await self.save_profile(made)
        await self._adopted()
        return observation

    async def _unread_screen(self, observation: Observation, png: bytes, digest: str, elements: Any,
                             purpose: str) -> Observation:
        observation = await self._judge_screen(observation, png, digest, elements, purpose)
        # no board measured in MEASURE_ATTEMPTS looks and nothing the model accounts for: not this game
        if not self.adapter.measured and self.unmeasured >= MEASURE_ATTEMPTS:
            raise EnvironmentUnavailable(self.unmeasured_reason)
        return observation

    async def _judge_screen(self, observation: Observation, png: bytes, digest: str, elements: Any,
                            purpose: str) -> Observation:
        """A whole screen the code read no board on. When the look before showed the same pixels (not a
        frame caught mid-animation), or the screen has shown no readable board for UNREAD_ASK_S however
        its pixels move (an animated advertisement), or this look is the last before a board never found
        would end the game, the bounded model says what it shows: a board it
        saw is measured there for the next look to read; the game's message for a scene that stops play
        becomes that scene's state, nothing under it read, and stays so while no board reads again; a
        cover over the board with a control to close it is marked `covered` for the session, its control
        always the one found on these pixels. Anything else stays unread."""
        seen, self.unseen = self.unseen, digest
        if self.unread_since is None:
            self.unread_since = time.monotonic()
        if self.reader is None:
            return observation
        # a screen judged before is not asked about again; a button is pressed only where these very pixels
        # were judged to show it
        judged = self.reader.judged(digest)
        answer = judged or self.stopped
        if answer is None and self.board_told:
            return observation
        if answer is None:
            # the same pixels twice, or no board for longer than any animation however the pixels move,
            # or the last look before a board never found ends the game
            if seen != digest and time.monotonic() - self.unread_since < UNREAD_ASK_S \
                    and (self.adapter.measured or self.unmeasured < MEASURE_ATTEMPTS):
                return observation
            remaining = self._remaining()
            if remaining is not None and remaining <= 0:
                return observation
            try:
                async with asyncio.timeout(remaining):
                    answer = await self.reader.classify(observation, png, digest)
            except TimeoutError:
                return observation
            if isinstance(answer, NeedObservation):
                return observation
            judged = answer
        if answer["screen"] not in (perception.BOARD, perception.COVERED, perception.UNKNOWN_SCREEN):
            self.stopped = answer
            # the button the game's message shows to keep playing
            self.control = answer["control"] if judged is answer else None
        if answer["screen"] == perception.BOARD:
            # a move's effect is judged on the geometry it was played on: only a look before a move adopts one
            changed = self.adapter.locate(png, answer["board"]) if purpose != "verify" else None
            if changed is not None:
                if changed:
                    await self._adopted()
                self.remeasure = False
            # a board the code found no new place for: the next looks are not asked about again
            self.board_told = not changed
            return observation
        press = _press(answer)
        if answer["screen"] == perception.COVERED and press is None:
            # a cover with no button to close it (a card closed by a tap anywhere): nothing more to wait for,
            # the session has what else can be done about it tried at once
            return replace(observation, public_facts={"blocked": True})
        if answer["screen"] == perception.UNKNOWN_SCREEN:
            return observation
        # a screen the model accounts for is not a failed measurement of a board it hides
        self.unmeasured = 0
        if press is not None:
            return self._cover(observation, png, elements, press)
        family = self.adapter.family()
        observation = replace(observation, public_facts={"state": family.unread_state(answer["screen"])},
                              geometry_ref=self.adapter.geometry_ref)
        state = family.estimate(observation)
        if isinstance(state, NeedObservation):
            return observation
        self.last, self.state = (observation, png, elements), state
        return observation

    async def _judge_stalled(self, observation: Observation, png: bytes, digest: str, elements: Any,
                             state: StateEstimate) -> tuple[Observation, StateEstimate | None]:
        """A board read whole that took a delivered move without effect: what stops play, asked of the bounded
        model once for these pixels. Only an answer that names something to press counts: the game's message
        for a scene whose button the family presses (a win notice beside a board that stays readable), which
        becomes that scene over the board as read, or a cover and its control (None: the session closes it).
        Anything else leaves the board as read, and the move's lack of effect counts as it did."""
        if self.reader is None:
            return observation, state
        remaining = self._remaining()
        if remaining is not None and remaining <= 0:
            return observation, state
        try:
            async with asyncio.timeout(remaining):
                answer = await self.reader.classify(observation, png, digest, board_read=True)
        except TimeoutError:
            return observation, state
        pressed = set(catalog.family(self.family_id).buttons.values())
        if isinstance(answer, NeedObservation) or answer["screen"] not in (perception.COVERED, *pressed):
            return observation, state
        if answer["screen"] == perception.COVERED:
            press = _press(answer)
            return (observation, state) if press is None else (self._cover(observation, png, elements, press), None)
        if answer["control"] is None:
            return observation, state
        self.control = answer["control"]
        stopped = self.adapter.family().estimate(replace(
            observation, public_facts={"state": {**state.payload, "scene": answer["screen"]}},
            geometry_ref=self.adapter.geometry_ref))
        return observation, (state if isinstance(stopped, NeedObservation) else stopped)

    async def recover(self, reason: str, context: dict[str, Any]) -> bool:
        """Play stopped on this screen for `reason`: the model looks at it whole and sends one input of its own.
        Whether it did; the next look then starts afresh, as if no board had been missed before it."""
        if self.recovery is None or self.unsupported() is not None:
            return False
        remaining = self._remaining()
        if remaining is not None and remaining <= 0:
            return False
        if not await self.recovery.attempt(self.surface, reason, context, timeout_s=remaining):
            return False
        self.adapter.moved(None)
        self.last, self.state, self.control = None, None, None
        self.unmeasured, self.unseen, self.unread_since, self.stopped = 0, None, None, None
        self.board_told = False
        self.remeasure = self.adapter.measured
        return True

    def recovered(self) -> None:
        if self.recovery is not None:
            self.recovery.succeeded()

    def _cover(self, observation: Observation, png: bytes, elements: Any, press: tuple[str, list[int]]) -> Observation:
        """A look whose board a cover hides: the session's input there (`press`) and the button it presses."""
        self.control = press[1]
        observation = replace(observation, public_facts={"covered": True, "press": press[0]})
        self.last = observation, png, elements
        return observation

    def _remaining(self) -> float | None:
        """How long a model may take: the run's time left, not the look's. Its answer is kept for the
        pixels it was about (`CorrectionReader.judged`, `corrected`), so a slow one serves the next look of them."""
        return None if self.deadline is None else self.deadline - time.monotonic()

    def _mapping(self, plan: ActionPlan) -> MappedAction:
        if self.last is None or self.state is None:
            raise ValueError("capture_missing")
        observation, png, elements = self.last
        return map_action(plan, self.state, observation, png, self.adapter,
                          elements=elements if self.adapter.reads_dom else None)

    async def execute(self, prepared: PreparedInput, context: dict[str, Any]) -> InputReceipt:
        def receipt(delivery: Delivery, steps: int | None, neutral: Neutral = Neutral.CONFIRMED) -> InputReceipt:
            return InputReceipt(prepared.action_id, prepared.attempt_id, prepared.target, prepared.surface_epoch,
                                delivery, steps, neutral)

        step = prepared.steps[0]
        # the session's own press at a cover or a game's button: a tap at the control found on this very look
        family = catalog.family(self.family_id)
        clicking = step.get("kind") in (*UNCOVERING, *family.buttons) and self.control is not None
        if self.last is None or (self.state is None and not clicking):
            return receipt(Delivery.REJECTED, 0)
        observation = self.last[0]
        if (prepared.target != self.target or prepared.input_ref != observation.input_ref
                or not await self.current(observation)):
            return receipt(Delivery.REJECTED, 0)
        if clicking:
            mapped = tap(self.control)
            self.adapter.moved(None)
        else:
            plan = ActionPlan("input", self.state.id, self.state.rule_revision, "input", prepared.steps, 1)
            try:
                mapped = self._mapping(plan)
            except ValueError as error:
                # a move this screen cannot be given (a piece the tray no longer shows, a drop off the screen)
                raise EnvironmentUnavailable(str(error)) from error
            self.adapter.moved((self.state.payload, step))
        if self.watch is not None:
            await self.watch.before_input(prepared, None if clicking else self.state.payload, mapped)
        try:
            await self._send(mapped)
            self.last_receipt = receipt(Delivery.DELIVERED, 1)
        except EnvironmentUnavailable:
            raise
        except Exception:
            # how much of it reached the game is not known: the screen says what happened
            log.exception("input failed: %s", mapped.tool)
            self.last_receipt = receipt(Delivery.UNKNOWN, None, Neutral.UNKNOWN)
        if self.watch is not None:
            await self.watch.after_input(self.last_receipt)
        return self.last_receipt

    async def _send(self, mapped: MappedAction) -> None:
        """One mapped input on the surface: keys, or where there are none a direction key as a swipe across
        the board; a pointer pressed and released in place as a tap, else a drag."""
        arguments = mapped.arguments
        if mapped.tool == "browser_press":
            for key in arguments["keys"]:
                if KEYS in self.surface.kinds:
                    await self.surface.key(key)
                elif key in SWIPES and DRAG in self.surface.kinds:
                    await self.surface.drag(*self._swipe(key), SWIPE_MS)
                else:
                    raise EnvironmentUnavailable(INPUT_KIND_UNSUPPORTED)
        elif mapped.tool == "browser_drag":
            start, end = (arguments["start_x"], arguments["start_y"]), (arguments["end_x"], arguments["end_y"])
            if start == end:
                await self.surface.tap(*start, arguments["duration_ms"])
            else:
                await self.surface.drag(*start, *end, arguments["duration_ms"])
        else:
            raise EnvironmentUnavailable(INPUT_KIND_UNSUPPORTED)

    def _swipe(self, key: str) -> tuple[float, float, float, float]:
        left, top, right, bottom = self.adapter.regions()[0]
        x, y = (left + right) / 2, (top + bottom) / 2
        across, down = SWIPES[key]
        reach_x, reach_y = across * (right - left) * SWIPE_SPAN / 2, down * (bottom - top) * SWIPE_SPAN / 2
        return x - reach_x, y - reach_y, x + reach_x, y + reach_y

    async def neutralize(self, context: dict[str, Any]) -> Neutral:
        """Nothing held: the surface lets go of whatever an interrupted input left pressed."""
        await self.surface.release()
        return Neutral.CONFIRMED

    async def lookup(self, action_id: str) -> InputReceipt | None:
        receipt = self.last_receipt
        return receipt if receipt is not None and receipt.action_id == action_id else None


def _press(answer: dict[str, Any]) -> tuple[str, list[int]] | None:
    """The session's input at a cover the model saw, and its button: a notice's close, a consent request's
    refusal or, when it shows none, its agreeing button (pressed only when the run allows it), a start
    screen's start; None for a cover with nothing to press, or no cover."""
    if answer["screen"] != perception.COVERED:
        return None
    if answer["cover"] == perception.CONSENT:
        if answer["control"] is not None:
            return DECLINE_CONSENT, answer["control"]
        return None if answer["accept"] is None else (ACCEPT_CONSENT, answer["accept"])
    if answer["control"] is None:
        return None
    return START_GAME if answer["cover"] == perception.START else CLOSE_COVER, answer["control"]
