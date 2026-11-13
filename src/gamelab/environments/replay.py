"""A simulated game the session plays as if it were a screen: tests and offline evaluation (plan §13).

It plays the family's own game (`next_state`) from a seed, so a session can run a whole game to its
end, and it behaves like a real surface where safety depends on it: an observation taken for input
allows one input, bound to the target and surface epoch, and expires on the surface's clock. Faults
a real surface has are scripted by input number (`faults`) or by the attributes below, so the
session's recovery can be tried without a browser or a phone. What a fault does to the board (a hidden
cell, a stray change) is the family core's (`hidden_state`, `stray_state`): the replay knows no board. What
the game hides (where mines lie) stays in its own state and never in an observation (`shown`).
"""
from __future__ import annotations

import copy
import random
from collections.abc import Callable, Mapping
from typing import Any

from ..contracts import Delivery, InputReceipt, Neutral, Observation, PreparedInput
from ..families import GridFamily
from ..errors import Denied
from ..session import CLOSE_COVER, STALLED, UNCOVERING

# how long an observation may back an input, on the surface's clock (a browser's is minutes)
INPUT_VALID_S = 5.0

CLOCK_ID = "replay"
DRAW_BITS = 32
# scripted faults, by the number of the input they hit (1 = the first input sent)
REJECTED = "rejected"              # the surface refused it: nothing happened, and it says so
NOT_DELIVERED = "not_delivered"    # nothing happened, and no answer came back
LOST_RECEIPT = "lost_receipt"      # the move happened, but no answer came back
WRONG_EFFECT = "wrong_effect"      # something other than the move happened (a stray tap, a lag)
IGNORED = "ignored"                # it arrived, and the game did not take it as a move
FAULTS = (REJECTED, NOT_DELIVERED, LOST_RECEIPT, WRONG_EFFECT, IGNORED)


class ReplayEnvironment:
    def __init__(self, family: GridFamily, *, seed: int, clock: Callable[[], float], target: str,
                 faults: Mapping[int, str] | None = None, state: dict[str, Any] | None = None) -> None:
        if unknown := set((faults or {}).values()) - set(FAULTS):
            raise ValueError(f"unknown replay faults: {sorted(unknown)}")
        self.family = family
        self.rng = random.Random(seed)
        self.clock = clock
        self.target = target
        self.faults = dict(faults or {})
        self.truth = copy.deepcopy(state) if state is not None else family.initial_state(self._draw())
        self.epoch = 1
        self.sequence = 0
        self.open_inputs: set[str] = set()
        self.receipts: dict[str, InputReceipt] = {}
        # what a test reads afterwards
        self.executed: list[str] = []
        self.neutralized = 0
        self.stale_inputs = 0
        # what a test may set: a release nobody confirms, a surface that forgets its receipts,
        # observations with a cell hidden, the surface changing right before an input observation,
        # observations it refuses to send on (as a browser whose guard saw the page move), and a cover
        # over the board that takes this many closes to go away
        self.neutralize_result = Neutral.CONFIRMED
        self.remembers = True
        self.hidden_observations = 0
        self.epoch_shifts_before_input = 0
        # the meta actions whose button the screen shows (`offers`), and those the person was asked for
        self.buttons: frozenset[str] = frozenset()
        self.asked: list[str] = []
        self.refused_ready = 0
        self.covered = 0
        # the input the cover offers (a notice's close, a consent request's refusal or agreement, a start
        # screen's start), and the meta actions the person refuses when asked
        self.cover_press = CLOSE_COVER
        self.refuses: frozenset[str] = frozenset()
        # a scene the game shows beside a board that stays readable (a win notice): the game takes no move
        # until its button is pressed, and only a look at the whole screen (`STALLED`) shows it
        self.beside: str | None = None
        self.stalled_looks = 0

    def _draw(self) -> int:
        return self.rng.getrandbits(DRAW_BITS)

    async def observe(self, target: str, purpose: str) -> Observation:
        self.sequence += 1
        if purpose == "input" and self.epoch_shifts_before_input > 0:
            self.epoch_shifts_before_input -= 1
            self.epoch += 1
        # the screen shows the game, never what it hides: a policy reads only what a player could see
        state = self.family.shown(copy.deepcopy(self.truth))
        if purpose == STALLED:
            self.stalled_looks += 1
            if self.beside is not None:
                state["scene"] = self.beside
        if self.hidden_observations > 0:
            self.hidden_observations -= 1
            state = self.family.hidden_state(state)
        facts = {"covered": True, "press": self.cover_press} if self.covered > 0 else {"state": state}
        # every observation may back one input, and only the latest does (a browser's for_input shot)
        input_ref = f"in{self.sequence}"
        self.open_inputs = {input_ref}
        return Observation(id=f"o{self.sequence}", sequence=self.sequence, target=target, surface_epoch=self.epoch,
                           captured_at=self.clock(), clock_id=CLOCK_ID, public_facts=facts,
                           input_ref=input_ref)

    def offers(self, kind: str) -> bool:
        return kind in self.buttons

    async def allow_meta(self, kind: str) -> None:
        self.asked.append(kind)

    async def ready(self, observation: Observation) -> bool:
        if refused := self.refuses & set(self.asked):
            raise Denied(f"the person refused {sorted(refused)}")
        if self.refused_ready > 0:
            self.refused_ready -= 1
            return False
        return (observation.input_ref in self.open_inputs and observation.surface_epoch == self.epoch
                and self.clock() <= observation.captured_at + INPUT_VALID_S)

    async def execute(self, prepared: PreparedInput, context: dict[str, Any]) -> InputReceipt:
        self.executed.append(prepared.action_id)
        fault = self.faults.get(len(self.executed))
        fresh = (prepared.target == self.target and prepared.surface_epoch == self.epoch
                 and prepared.clock_id == CLOCK_ID and prepared.input_ref in self.open_inputs
                 and (prepared.expires_at is None or self.clock() <= prepared.expires_at))
        self.open_inputs.discard(prepared.input_ref)
        if not fresh:
            self.stale_inputs += 1
            return self._receipt(prepared, Delivery.REJECTED, 0)
        if fault in (REJECTED, NOT_DELIVERED):
            self._remember(prepared, Delivery.REJECTED, 0)
            if fault == REJECTED:
                return self._receipt(prepared, Delivery.REJECTED, 0)
            return InputReceipt.unknown(prepared.action_id, prepared.attempt_id, prepared.target, self.epoch)
        for step in prepared.steps if fault != IGNORED else ():
            if self.beside is not None:
                # the notice takes every input; only its own button closes it
                if step.get("kind") in self.buttons:
                    self.beside = None
                continue
            if step.get("kind") in UNCOVERING:
                self.covered = max(0, self.covered - 1)
            else:
                self.truth = self.family.next_state(self.truth, step, self._draw())
        if fault == WRONG_EFFECT:
            self.truth = self.family.stray_state(self.truth)
        self._remember(prepared, Delivery.DELIVERED, len(prepared.steps))
        if fault == LOST_RECEIPT:
            return InputReceipt.unknown(prepared.action_id, prepared.attempt_id, prepared.target, self.epoch)
        return self._receipt(prepared, Delivery.DELIVERED, len(prepared.steps))

    async def neutralize(self, context: dict[str, Any]) -> Neutral:
        self.neutralized += 1
        return Neutral(self.neutralize_result)

    async def lookup(self, action_id: str) -> InputReceipt | None:
        return self.receipts.get(action_id) if self.remembers else None

    def _receipt(self, prepared: PreparedInput, delivery: Delivery, steps: int) -> InputReceipt:
        return InputReceipt(action_id=prepared.action_id, attempt_id=prepared.attempt_id, target=prepared.target,
                            surface_epoch=self.epoch, delivery=delivery, delivered_steps=steps,
                            input_neutral=Neutral.CONFIRMED, timing={"at": self.clock()})

    def _remember(self, prepared: PreparedInput, delivery: Delivery, steps: int) -> None:
        self.receipts[prepared.action_id] = self._receipt(prepared, delivery, steps)

