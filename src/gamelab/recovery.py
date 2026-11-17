"""What a model does about a screen the code cannot play on (PLAN §8): the way back to a board the code reads.

The code plays: it reads the board, picks the move and checks its result. When it is stuck on a screen (no
board found, a board left unread, a cover it has no button for, moves the game ignores), the game is not
ended there: the model is shown the screen and why play stopped, and answers with one input of its own
choosing (a tap, a key, a swipe, a wait), that nothing is in the way, or that it gives up. The input is sent,
the screen looked at again by the code, and play goes on by the code as soon as it reads a board.

The model never picks a move of the game. An input that brought play back is kept with a small picture of
the screen it was sent on, and sent again without asking when a screen like it comes back.

The model is told what the person allowed beyond the board and when they want to stop (PLAN §25, R1 and R2):
an input that uses up something of the player's says so (`uses`) and is sent only when each such thing is
allowed, and the model may answer that a stop condition is met (`stop`).
"""
from __future__ import annotations

import asyncio
import base64
from io import BytesIO
import json
import logging
from typing import Any

from . import catalog, resources
from .contracts import META_ACTIONS
from .execution import TAP_MS
from .model import Model, Msg, TurnRequest

log = logging.getLogger("gamelab.recovery")

TAP, KEY, SWIPE, WAIT, NONE, GIVE_UP, BACK, STOP = "tap", "key", "swipe", "wait", "none", "give_up", "back", "stop"
# why play stopped when the level on the screen ended before any move of this part of the work, and the work goes
# on to the next (`agent.Agent.onward`)
NEXT_LEVEL = "next_level"
# what a model giving up may say of the game itself: its own end screen, won or lost, stands where the board was
ENDS = ("won", "lost")
# what an input of the model's is carried out with (`Surface.kinds`)
NEEDS = {TAP: "tap", KEY: "keys", SWIPE: "drag", BACK: "back"}
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
# and still be the same screen. A guess, kept tight: on a dark site (2026-11-13) a notice and the board behind
# it differed by only 21 to 29. What keeps a replay from landing on a board is less this than REPLAYED_REASONS
# the longer side of a picture a model is shown: larger ones are made this size first (`view`)
VIEW_SIDE = 1536
SIGNATURE_SIDE = 16
REPLAY_DISTANCE = 8.0
# why play stopped when the screen showed no board the code could use (something in the way): only there is
# an input that got past a screen before sent again unasked. Where the board was read whole and the moves made
# no sense, a look that resembles an old notice may well be the board itself, and a tap on it a move
REPLAYED_REASONS = frozenset({"label_failed", "board_not_found", "board_covered", "unreadable", "surface_unsettled",
                              NEXT_LEVEL})
# what is told the model of the inputs it already tried on this screen, and kept of those that worked
TRIED_KEPT, KNOWN_KEPT = 8, 32


def signature(png: bytes) -> list[int]:
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        return list(image.convert("L").resize((SIGNATURE_SIDE, SIGNATURE_SIDE)).tobytes())


def view(png: bytes) -> tuple[bytes, float]:
    """The picture as a model is shown it, and how much smaller it is than the screen: a model's service
    shrinks a large picture before the model sees it, and the model then answers in the pixels of what it
    saw. A picture already no longer than VIEW_SIDE is shown as it is; points and boxes a model gives are
    divided by the factor to be the screen's."""
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        longer = max(image.size)
        if longer <= VIEW_SIDE:
            return png, 1.0
        scale = VIEW_SIDE / longer
        out = BytesIO()
        image.convert("RGB").resize((round(image.width * scale), round(image.height * scale))).save(out, format="PNG")
        return out.getvalue(), scale


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
    if kind == GIVE_UP and action.get("end") in ENDS:
        return {"kind": kind, "end": action["end"]}
    if kind in (NONE, GIVE_UP, BACK, STOP):
        return {"kind": kind}
    return None


def uses(action: Any) -> list[str] | None:
    """What an answer's input says it uses up beyond the board (`META_ACTIONS` names), or None when what it says
    is no such list."""
    said = action.get("uses", []) if isinstance(action, dict) else []
    if not isinstance(said, list) or not all(isinstance(name, str) and name in META_ACTIONS for name in said):
        return None
    return sorted(set(said))


async def send(surface: Any, action: dict[str, Any]) -> None:
    if action["kind"] == TAP:
        await surface.tap(*action["at"], TAP_MS)
    elif action["kind"] == KEY:
        await surface.key(action["key"])
    elif action["kind"] == SWIPE:
        await surface.drag(*action["from"], *action["to"], SWIPE_MS)
    elif action["kind"] == BACK:
        await surface.back()
    elif action["kind"] == WAIT:
        await asyncio.sleep(action["ms"] / 1000)


class Recovery:
    def __init__(self, model: Model, family_id: str | None, *, known: list[dict[str, Any]] | None = None,
                 accept_consent: bool = False, allowed: frozenset[str] = frozenset(), stops: tuple[str, ...] = (),
                 account: dict[str, dict[str, int]] | None = None) -> None:
        # `family_id` None: the game is not known yet (a screen before any board was seen)
        self.model, self.family_id, self.accept_consent = model, family_id, accept_consent
        # what the person allowed beyond the board (`META_ACTIONS` names) and the conditions they stop at
        self.allowed, self.stops = frozenset(allowed), tuple(stops)
        # what the inputs sent used up, and what inputs not sent would have: the game's own account when it
        # gives one, so that a recovery made anew for another family adds to the same
        account = account if account is not None else {"used": {}, "refused": {}}
        self.used, self.refused = account.setdefault("used", {}), account.setdefault("refused", {})
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
        self._forget()
        action = self._replay(seen, reason) if reason in REPLAYED_REASONS else None
        if action is not None:
            entry["source"] = "replay"
        else:
            action = await self._ask(png, reason, context, surface.kinds, entry, timeout_s)
        entry["action"] = action
        if action is None or action["kind"] in (NONE, GIVE_UP, STOP):
            return False
        refused = [name for name in action.get("uses", []) if name not in self.allowed]
        if refused:
            # not sent: the model is told so with its next question, which this stop still is
            entry["refused"] = refused
            for name in refused:
                self.refused[name] = self.refused.get(name, 0) + 1
            self.tried.append({"action": action, "screen_changed": False, "signature": seen, "replayed": entry["source"] == "replay",
                               "reason": reason, "refused": f"not sent: the person did not allow {', '.join(refused)}"})
            return True
        try:
            await send(surface, action)
            await asyncio.sleep(SETTLE_S)
            entry["changed"] = _distance(seen, signature(await surface.capture())) > 0
        except Exception:
            log.exception("recovery input failed: %s", action)
            return False
        for name in action.get("uses", []):
            self.used[name] = self.used.get(name, 0) + 1
        self.tried.append({"action": action, "screen_changed": entry["changed"], "signature": seen,
                           "replayed": entry["source"] == "replay", "reason": reason})
        return True

    def _forget(self) -> None:
        """A replayed input after which play is stuck again did not get past its screen: it is not kept."""
        if self.tried and self.tried[-1]["replayed"]:
            self.known = [known for known in self.known if known["action"] != self.tried[-1]["action"]
                          or _distance(known["signature"], self.tried[-1]["signature"]) > REPLAY_DISTANCE]

    def _replay(self, seen: list[int], reason: str) -> dict[str, Any] | None:
        """The input that got past a screen like this one before; never twice on one screen, so one that
        no longer works is followed by a question. An input that opened the next level is sent again only to
        open the next level: the same end screen after a level played here is that level's end, not in the way."""
        if self.tried:
            return None
        near = [known for known in self.known if _distance(known["signature"], seen) <= REPLAY_DISTANCE
                and bool(known.get("next")) == (reason == NEXT_LEVEL)]
        return min(near, key=lambda known: _distance(known["signature"], seen))["action"] if near else None

    async def _ask(self, png: bytes, reason: str, context: dict[str, Any], kinds: frozenset[str],
                   entry: dict[str, Any], timeout_s: float | None) -> dict[str, Any] | None:
        png, scale = view(png)
        size = _size(png)
        timeout = RECOVER_TIMEOUT_S if timeout_s is None else min(RECOVER_TIMEOUT_S, timeout_s)
        if timeout <= 0:
            return None
        asked = {"family": catalog.descriptor(self.family_id) if self.family_id else None, "reason": reason,
                 "screen": list(size),
                 "inputs": sorted(kind for kind, needs in NEEDS.items() if needs in kinds) + [WAIT],
                 "keys": sorted(KEYS), "accept_consent": self.accept_consent, "meta_actions": META_ACTIONS,
                 "allowed_meta_actions": sorted(self.allowed), **({"stop_conditions": list(self.stops)} if self.stops else {}),
                 **context,
                 "tried": [{"action": tried["action"], "screen_changed": tried["screen_changed"],
                            **({"refused": tried["refused"]} if "refused" in tried else {})}
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
        action, used = check(answer.get("action"), size, kinds), uses(answer.get("action"))
        if action is None or used is None:
            return None
        if used:
            action["uses"] = used
        if action["kind"] == STOP and isinstance(answer["action"].get("because"), str):
            entry["because"] = answer["action"]["because"][:200]
        # the model answered in the pixels of the picture it was shown: the input is sent in the screen's
        return action if action is None or scale == 1.0 else {
            key: [part / scale for part in value] if key in ("at", "from", "to") else value for key, value in action.items()}

    def succeeded(self) -> None:
        """Play goes on (a move was confirmed, a board read): the last input sent is what got it there."""
        if not self.tried:
            return
        last = self.tried[-1]
        for entry in reversed(self.log):
            if entry["action"] == last["action"]:
                entry["restored"] = True
                break
        if not last["replayed"] and last["action"]["kind"] != WAIT and last["reason"] in REPLAYED_REASONS:
            self.known = [*self.known, {"action": last["action"], "signature": last["signature"],
                                        **({"next": True} if last["reason"] == NEXT_LEVEL else {})}][-KNOWN_KEPT:]
        self.tried = []

    def summary(self, since: int = 0) -> dict[str, Any]:
        entries = self.log[since:]
        return {"stuck": len(entries), "inputs": sum(entry["changed"] is not None for entry in entries),
                "replayed": sum(entry["source"] == "replay" for entry in entries),
                "restored": sum(bool(entry.get("restored")) for entry in entries),
                "questions": sum(entry["source"] == "model" for entry in entries),
                **({"used": dict(self.used)} if self.used else {}), **({"refused": dict(self.refused)} if self.refused else {})}
