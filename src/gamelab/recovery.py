"""What a model does about a screen the code cannot play on (PLAN §8): the way back to a board the code reads.

The code plays: it reads the board, picks the move and checks its result. When it is stuck on a screen (no
board found, a board left unread, a cover it has no button for, moves the game ignores), the game is not
ended there: the model is shown the screen and why play stopped, and answers with one input of its own
choosing (a tap, a key, a swipe, a wait), that nothing is in the way, or that it gives up. The input is sent,
the screen looked at again by the code, and play goes on by the code as soon as it reads a board.

The model never picks a move of the game. An input that brought play back is kept with a small picture of
the screen it was sent on, and sent again without asking when a screen like it comes back.
"""
from __future__ import annotations

import asyncio
import base64
from io import BytesIO
import json
import logging
from typing import Any

from . import catalog, resources
from .execution import TAP_MS
from .model import Model, Msg, TurnRequest

log = logging.getLogger("gamelab.recovery")

TAP, KEY, SWIPE, WAIT, NONE, GIVE_UP = "tap", "key", "swipe", "wait", "none", "give_up"
# what an input of the model's is carried out with (`Surface.kinds`)
NEEDS = {TAP: "tap", KEY: "keys", SWIPE: "drag"}
# the keys it may press: the ones a game or a notice is answered with, never a shortcut or text
KEYS = frozenset({"ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Enter", "Escape", " "})
# inputs of the model's in a row, with no move confirmed between them, before the game ends as it would have
MAX_TRIES = 6
RECOVER_TOKENS, RECOVER_TIMEOUT_S = 2000, 60.0
# how long an input of the model's is left to take effect before the screen is looked at again
SETTLE_S = 0.8
SWIPE_MS = 150
MAX_WAIT_MS = 5000
# a screen's small picture: this many grey levels a side, and how far two may differ on average (of 255)
# and still be the same kind of screen (a notice whose picture differs each time it is shown)
SIGNATURE_SIDE = 16
REPLAY_DISTANCE = 24.0
# what is told the model of the inputs it already tried on this screen, and kept of those that worked
TRIED_KEPT, KNOWN_KEPT = 8, 32


def signature(png: bytes) -> list[int]:
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        return list(image.convert("L").resize((SIGNATURE_SIDE, SIGNATURE_SIDE)).tobytes())


def _distance(one: list[int], other: list[int]) -> float:
    if len(one) != len(other):
        return 255.0
    return sum(abs(a - b) for a, b in zip(one, other)) / len(one)


def _size(png: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        return image.size


def check(action: Any, size: tuple[int, int], kinds: frozenset[str]) -> dict[str, Any] | None:
    """The model's action as one the surface can be given, or None: a point on the screen, a key of KEYS,
    a bounded wait."""
    def point(value: Any) -> list[float] | None:
        if (isinstance(value, list) and len(value) == 2
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
                and 0 <= value[0] < size[0] and 0 <= value[1] < size[1]):
            return [float(value[0]), float(value[1])]
        return None

    if not isinstance(action, dict):
        return None
    kind = action.get("kind")
    if kind in NEEDS and NEEDS[kind] not in kinds:
        return None
    if kind == TAP and (at := point(action.get("at"))) is not None:
        return {"kind": TAP, "at": at}
    if kind == KEY and action.get("key") in KEYS:
        return {"kind": KEY, "key": action["key"]}
    if kind == SWIPE and (start := point(action.get("from"))) is not None and (end := point(action.get("to"))) is not None:
        return {"kind": SWIPE, "from": start, "to": end}
    if kind == WAIT and type(action.get("ms")) is int and 0 < action["ms"] <= MAX_WAIT_MS:
        return {"kind": WAIT, "ms": action["ms"]}
    if kind in (NONE, GIVE_UP):
        return {"kind": kind}
    return None


async def send(surface: Any, action: dict[str, Any]) -> None:
    if action["kind"] == TAP:
        await surface.tap(*action["at"], TAP_MS)
    elif action["kind"] == KEY:
        await surface.key(action["key"])
    elif action["kind"] == SWIPE:
        await surface.drag(*action["from"], *action["to"], SWIPE_MS)
    elif action["kind"] == WAIT:
        await asyncio.sleep(action["ms"] / 1000)


class Recovery:
    def __init__(self, model: Model, family_id: str, *, known: list[dict[str, Any]] | None = None,
                 accept_consent: bool = False) -> None:
        self.model, self.family_id, self.accept_consent = model, family_id, accept_consent
        # inputs that brought play back, each with the small picture of the screen it was sent on
        self.known: list[dict[str, Any]] = list(known or [])
        # every time the code was stuck and what was done about it, for whoever measures the run
        self.log: list[dict[str, Any]] = []
        # what was tried since play last went on: told to the model, and credited when play goes on again
        self.tried: list[dict[str, Any]] = []
        self.questions = 0

    async def attempt(self, surface: Any, reason: str, context: dict[str, Any], *, timeout_s: float | None = None) -> bool:
        """One thing done about the screen as it is now. True when an input was sent (or waited out) and the
        screen is worth another look; False when nothing is in the way, the model gives up or has no answer."""
        png = await surface.capture()
        seen = signature(png)
        entry: dict[str, Any] = {"reason": reason, "source": "model", "situation": None, "action": None, "changed": None}
        self.log.append(entry)
        action = self._replay(seen)
        if action is not None:
            entry["source"] = "replay"
        else:
            action = await self._ask(png, reason, context, surface.kinds, entry, timeout_s)
        entry["action"] = action
        if action is None or action["kind"] in (NONE, GIVE_UP):
            return False
        try:
            await send(surface, action)
            await asyncio.sleep(SETTLE_S)
            entry["changed"] = _distance(seen, signature(await surface.capture())) > 0
        except Exception:
            log.exception("recovery input failed: %s", action)
            return False
        self.tried.append({"action": action, "screen_changed": entry["changed"], "signature": seen,
                           "replayed": entry["source"] == "replay"})
        return True

    def _replay(self, seen: list[int]) -> dict[str, Any] | None:
        """The input that got past a screen like this one before; never twice on one screen, so one that
        no longer works is followed by a question."""
        if self.tried:
            return None
        near = [known for known in self.known if _distance(known["signature"], seen) <= REPLAY_DISTANCE]
        return min(near, key=lambda known: _distance(known["signature"], seen))["action"] if near else None

    async def _ask(self, png: bytes, reason: str, context: dict[str, Any], kinds: frozenset[str],
                   entry: dict[str, Any], timeout_s: float | None) -> dict[str, Any] | None:
        size = _size(png)
        timeout = RECOVER_TIMEOUT_S if timeout_s is None else min(RECOVER_TIMEOUT_S, timeout_s)
        if timeout <= 0:
            return None
        asked = {"family": catalog.descriptor(self.family_id), "reason": reason, "screen": list(size),
                 "inputs": sorted(kind for kind, needs in NEEDS.items() if needs in kinds) + [WAIT],
                 "keys": sorted(KEYS), "accept_consent": self.accept_consent, **context,
                 "tried": [{"action": tried["action"], "screen_changed": tried["screen_changed"]}
                           for tried in self.tried[-TRIED_KEPT:]]}
        prompt = (resources.FRAGMENT / "prompts" / "recover.md").read_text(encoding="utf-8")
        image = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                              "data": base64.b64encode(png).decode("ascii")}}
        self.questions += 1
        try:
            async with asyncio.timeout(timeout):
                result = await self.model.complete(TurnRequest(
                    system_stable=prompt, max_tokens=RECOVER_TOKENS, timeout_s=timeout,
                    messages=[Msg("user", [{"type": "text", "text": json.dumps(asked)}, image])]))
            answer = json.loads(result.text or "")
        except Exception as error:
            entry["situation"] = f"no answer: {type(error).__name__}"
            return None
        if not isinstance(answer, dict):
            return None
        if isinstance(answer.get("situation"), str):
            entry["situation"] = answer["situation"][:300]
        return check(answer.get("action"), size, kinds)

    def succeeded(self) -> None:
        """Play goes on (a move was confirmed, a board read): the last input sent is what got it there."""
        if not self.tried:
            return
        last = self.tried[-1]
        for entry in reversed(self.log):
            if entry["action"] == last["action"]:
                entry["restored"] = True
                break
        if not last["replayed"] and last["action"]["kind"] != WAIT:
            self.known = [*self.known, {"action": last["action"], "signature": last["signature"]}][-KNOWN_KEPT:]
        self.tried = []

    def summary(self, since: int = 0) -> dict[str, Any]:
        entries = self.log[since:]
        return {"stuck": len(entries), "inputs": sum(entry["changed"] is not None for entry in entries),
                "replayed": sum(entry["source"] == "replay" for entry in entries),
                "restored": sum(bool(entry.get("restored")) for entry in entries),
                "questions": sum(entry["source"] == "model" for entry in entries)}
