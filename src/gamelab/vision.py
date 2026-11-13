"""Budgeted, read-only model answers about a screen (cells the code left unknown, what a screen with no
readable board shows, where a board the code did not find is); the image reader remains the authority
and code checks every answer."""
from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import inspect
import json
import math
from typing import Any

from . import catalog, perception, profiles
from .contracts import NeedObservation, Observation, StateEstimate
from .model import Msg, TurnRequest

MAX_RESPONSE_BYTES = 65_536
# 2: the record keeps what each screen was judged to show (`screens`), so a restart does not ask again
# 3: a judged cover says what it is and may name a consent request's agreeing button (`cover`, `accept`)
# 4: the questions asked are counted (`asked`); with no cap only the latest attempts and screens are kept
RECORD_VERSION = 4
# what a record keeps of a game with no cap on its questions: enough to never ask one capture twice and to
# reuse what recent screens were judged to show, small enough for the task's state however long the game
ATTEMPTS_KEPT = 64
SCREENS_KEPT = 64
# each question's output and time when a run names no budget of its own; no cap on a game's count of them
# (user decision, 2026-11-13): a game is never stopped for the questions it needed, and one capture is never
# asked about twice
DEFAULT_VISION = {"max_calls": None, "max_output_tokens": None, "tokens_per_call": 2000, "timeout_s": 30}


@dataclass(frozen=True)
class VisionBudget:
    """Each question's output and time; `max_calls` and `max_output_tokens` cap a whole game's only when given
    (None: a game is never stopped for how many questions it needed, the task's moves and time bound it)."""
    max_calls: int | None
    max_output_tokens: int | None
    tokens_per_call: int
    timeout_s: float

    def __post_init__(self) -> None:
        if type(self.tokens_per_call) is not int or self.tokens_per_call <= 0 \
                or any(value is not None and (type(value) is not int or value <= 0)
                       for value in (self.max_calls, self.max_output_tokens)) \
                or isinstance(self.timeout_s, bool) or not isinstance(self.timeout_s, (int, float)) \
                or not math.isfinite(self.timeout_s) or self.timeout_s <= 0:
            raise ValueError("invalid_vision_budget")


class CorrectionReader:
    def __init__(self, profile: profiles.Profile | None, router: Any, *, run_id: str,
                 budget: VisionBudget, save: Callable[[dict], Awaitable[None]],
                 current: Callable[[Observation], bool | Awaitable[bool]], cancelled: Callable[[], bool],
                 record: dict | None = None, family_id: str | None = None) -> None:
        # no profile yet: a game no profile registers, whose one question is what the screen shows (`discover`)
        self.profile = profiles.validate(profile.data) if profile is not None else None
        if self.profile != profile:
            raise ValueError("profile_changed")
        # the game's family when no profile names it (a generated adapter's game)
        self._family_id = family_id
        self.router = router
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("invalid_vision_scope")
        self.run_id = run_id
        self.budget, self.save, self.current, self.cancelled = budget, save, current, cancelled
        self._lock = asyncio.Lock()
        self._record = self._restore(record)

    @property
    def family_id(self) -> str:
        return self.profile.family_id if self.profile is not None else self._family_id

    def _restore(self, record: dict | None) -> dict:
        scope = {"run_id": self.run_id}
        if record is None:
            return {"version": RECORD_VERSION, "scope": scope, "budget": asdict(self.budget), "asked": 0, "attempts": [],
                    "screens": {}}
        if not isinstance(record, dict) or set(record) != {"version", "scope", "budget", "asked", "attempts", "screens"} \
                or type(record["version"]) is not int or record["version"] != RECORD_VERSION \
                or record["scope"] != scope or record["budget"] != asdict(self.budget):
            raise ValueError("invalid_vision_record")
        attempts, asked = record["attempts"], record["asked"]
        if type(asked) is not int or not isinstance(attempts, list) or len(attempts) > min(asked, ATTEMPTS_KEPT) \
                or self._spent(asked) or any(not _digest(key) for key in attempts) \
                or len(set(attempts)) != len(attempts):
            raise ValueError("invalid_vision_record")
        # one judged screen per call at most, each as `perception.screen` checked it
        screens = record["screens"]
        if not isinstance(screens, dict) or len(screens) > min(asked, SCREENS_KEPT) \
                or any(not _digest(key) or not isinstance(answer, dict)
                       or set(answer) != {"screen", "board", "control", "cover", "accept"}
                       for key, answer in screens.items()):
            raise ValueError("invalid_vision_record")
        return deepcopy(record)

    def _spent(self, asked: int) -> bool:
        """Whether `asked` questions are more than the game's cap allows; never so when no cap was given."""
        calls, tokens = self.budget.max_calls, self.budget.max_output_tokens
        return (calls is not None and asked > calls) or (tokens is not None and asked * self.budget.tokens_per_call > tokens)

    @property
    def record(self) -> dict:
        return deepcopy(self._record)

    async def _stopped(self, observation: Observation) -> NeedObservation | None:
        if self.cancelled():
            return NeedObservation("cancelled")
        current = self.current(observation)
        if inspect.isawaitable(current):
            current = await current
        if not current:
            return NeedObservation("observation_stale")
        return None

    async def read(self, observation: Observation, png: bytes, profile: profiles.Profile
                   ) -> StateEstimate | NeedObservation:
        """Read `png` with `profile` (this reader's profile as measured on the screen) and, for cells
        the code leaves unknown, ask the model once per capture within the budget: what they hold, or
        which of the family's scenes that stop play covers them."""
        # the same registered profile, whatever colours its session has learned since
        if self.profile is None or (profile.id, profile.data["revision"]) != (self.profile.id, self.profile.data["revision"]):
            return NeedObservation("profile_changed")
        async with self._lock:
            if stopped := await self._stopped(observation):
                return stopped
            state = perception.estimate(observation, png, profile)
            if isinstance(state, NeedObservation) or not state.unknown_fields:
                return state
            proposal = await self._ask(observation, png, {"geometry_ref": profile.geometry_ref},
                                       self._request(observation, png, state, profile))
            if isinstance(proposal, NeedObservation):
                return proposal
            return perception.estimate(observation, png, profile, correction=proposal)

    def judged(self, pixels: str) -> dict[str, Any] | None:
        """What a screen of these pixels (the digest of its decoded image) was judged to show, in this
        task or an earlier run of it: an answer is about the pixels, not the look that showed them."""
        answer = self._record["screens"].get(pixels)
        return deepcopy(answer) if answer is not None else None

    async def classify(self, observation: Observation, png: bytes, pixels: str, *, board_read: bool = False
                       ) -> dict[str, Any] | NeedObservation:
        """What a whole screen the code found no readable board on shows, asked once per capture within
        the same budget: the board and about where it is, one of the family's scenes that stop play, a
        cover over the board with the one control that closes it if the model sees one, or nothing plain
        (`perception.screen`). With `board_read`, the code read the board whole but the game took no input
        on it, and the question is what stops play. The answer is saved for `pixels`, the digest of the
        decoded image."""
        if not _digest(pixels):
            raise ValueError("invalid_pixels")
        async with self._lock:
            if (known := self.judged(pixels)) is not None:
                return known
            if stopped := await self._stopped(observation):
                return stopped
            context = {"observation_id": observation.id, "image_sha256": hashlib.sha256(png).hexdigest(),
                       "family": catalog.descriptor(self.family_id),
                       "screens": perception.screens(self.family_id), "board_read": board_read,
                       "buttons": sorted(set(catalog.family(self.family_id).buttons.values()))}
            answer = await self._ask(observation, png, {"question": "stalled" if board_read else "screen"},
                                     self._turn("classify_screen.md", context, png))
            if isinstance(answer, NeedObservation):
                return answer
            checked = perception.screen(answer, observation, png, self.family_id)
            if checked is None:
                return NeedObservation("invalid_screen_answer")
            screens = self._record["screens"]
            screens[pixels] = checked
            for oldest in list(screens)[:-SCREENS_KEPT]:
                del screens[oldest]
            await self.save(self.record)
            return deepcopy(checked)

    def adopt(self, profile: profiles.Profile) -> None:
        """The profile made for a game no profile registers: from now on the reader's, as a registered one is."""
        if self.profile is not None:
            raise ValueError("profile_changed")
        self.profile = profiles.validate(profile.data)

    async def discover(self, observation: Observation, png: bytes, family_id: str) -> dict[str, Any] | NeedObservation:
        """What a screen of a game no profile registers shows, asked once per capture within the same budget:
        `{"screen", "control", "cover", "accept", "look"}`, where screen is "board" (the look: the board's box,
        grid and cells, for `discovery.propose` to check), "covered" (what covers it and its buttons, checked as
        `perception.screen` checks them) or "unknown". The family is the request's; another one is not this game."""
        async with self._lock:
            if stopped := await self._stopped(observation):
                return stopped
            context = {"observation_id": observation.id, "image_sha256": hashlib.sha256(png).hexdigest(),
                       "family": catalog.descriptor(family_id)}
            answer = await self._ask(observation, png, {"question": "discover"},
                                     self._turn("discover_screen.md", context, png))
            if isinstance(answer, NeedObservation):
                return answer
            look = ("family", "board", "grid", "cells")
            if (set(answer) != {"observation_id", "image_sha256", "screen", "control", "cover", "accept", *look}
                    or answer["screen"] not in (perception.BOARD, perception.COVERED, perception.UNKNOWN_SCREEN)):
                return NeedObservation("invalid_screen_answer")
            # the evidence and the control as any screen's answer is checked
            checked = perception.screen({"observation_id": answer["observation_id"],
                                         "image_sha256": answer["image_sha256"],
                                         "screen": answer["screen"] if answer["screen"] != perception.BOARD else perception.UNKNOWN_SCREEN,
                                         "board": None, "control": answer["control"], "cover": answer["cover"],
                                         "accept": answer["accept"]}, observation, png, family_id)
            if checked is None:
                return NeedObservation("invalid_screen_answer")
            if answer["screen"] == perception.BOARD and answer["family"] != family_id:
                return NeedObservation("family_unknown")
            return {"screen": answer["screen"], "control": checked["control"], "cover": checked["cover"],
                    "accept": checked["accept"], "look": {key: deepcopy(answer[key]) for key in look}}

    async def _ask(self, observation: Observation, png: bytes, scope: dict[str, Any], request: TurnRequest
                   ) -> dict | NeedObservation:
        """One model answer about this capture: an attempt saved before the call, never two for the same
        capture and question, and nothing once the budget is spent."""
        identity = {"observation_id": observation.id, "target": observation.target,
                    "surface_epoch": observation.surface_epoch, "clock_id": observation.clock_id,
                    "image_sha256": hashlib.sha256(png).hexdigest(), **scope}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        attempts = self._record["attempts"]
        if key in attempts:
            return NeedObservation("correction_already_attempted")
        if self._spent(self._record["asked"] + 1):
            return NeedObservation("vision_budget_exhausted")
        if self.router is None:
            return NeedObservation("model_unavailable")
        self._record["asked"] += 1
        self._record["attempts"] = [*attempts, key][-ATTEMPTS_KEPT:]
        await self.save(self.record)
        if stopped := await self._stopped(observation):
            return stopped
        try:
            async with asyncio.timeout(self.budget.timeout_s):
                result = await self.router.complete(request)
            if result.tool_calls or not isinstance(result.text, str) \
                    or len(result.text) > MAX_RESPONSE_BYTES \
                    or len(result.text.encode("utf-8")) > MAX_RESPONSE_BYTES:
                return NeedObservation("invalid_correction_response")
            answer = json.loads(result.text, object_pairs_hook=_unique_object)
            if not isinstance(answer, dict):
                return NeedObservation("invalid_correction_response")
        except TimeoutError:
            return NeedObservation("correction_timeout")
        except (ValueError, RecursionError):
            return NeedObservation("invalid_correction_response")
        except Exception:
            return NeedObservation("model_unavailable")
        if stopped := await self._stopped(observation):
            return stopped
        return answer

    def _request(self, observation: Observation, png: bytes, state: StateEstimate,
                 profile: profiles.Profile) -> TurnRequest:
        context = {"observation_id": observation.id, "image_sha256": hashlib.sha256(png).hexdigest(),
                   "geometry_ref": profile.geometry_ref, "family": catalog.descriptor(profile.family_id),
                   "profile": profile.layout, "state": state.payload, "unknown_fields": list(state.unknown_fields)}
        return self._turn("correct_screen.md", context, png)

    def _turn(self, prompt_file: str, context: dict[str, Any], png: bytes) -> TurnRequest:
        prompt = (catalog.FRAGMENT / "prompts" / prompt_file).read_text(encoding="utf-8")
        return TurnRequest(system_stable=prompt, timeout_s=self.budget.timeout_s, max_tokens=self.budget.tokens_per_call,
                           messages=[Msg(role="user", content=[{"type": "text", "text": json.dumps(context)},
                               {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                           "data": base64.b64encode(png).decode("ascii")}}])])


def _digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result
