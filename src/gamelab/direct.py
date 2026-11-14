"""A game no family is registered for, played by a model (PLAN §11, H2; the base of the levels, L0).

The harness has rules and a solver for a handful of families. Any other game is not left unplayed: a model is
shown the screen and answers with one input of its own, what it expects that input to change, and a few notes
it wants to keep (the rules as far as it has made them out, what it is trying). The input is sent, the screen
looked at again, and the model is told what the code saw of it: whether anything changed and where. Nothing
here knows the game, so nothing checks a move by its rules; the one check is the code's own, that the screen
changed.

The game ends when the model says its own end screen stands there, when it sees nothing more to do, when its
inputs keep changing nothing, or at a budget.
"""
from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from . import carry, recovery, resources
from .execution import TAP_MS
from .model import Model, Msg, TurnRequest

TAP, KEY, SWIPE, TEXT, WAIT, DONE, GIVE_UP = "tap", "key", "swipe", "text", "wait", "done", "give_up"
# how a game the model says is over ended
ENDS = ("won", "lost")
# what an input is carried out with (`Surface.kinds`)
NEEDS = {TAP: "tap", KEY: "keys", SWIPE: "drag", TEXT: "keys"}
# the keys a game may be played with beyond those a notice is answered with, and the letters and digits typed
KEYS = recovery.KEYS | {"Backspace", "Tab"}
TYPED = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ")
TEXT_MOST = 32
# moves told to the model of what it did before, the notes it may keep, and the changed places it is told of
HISTORY_KEPT, NOTES_MOST, PLACES_TOLD = 8, 1200, 6
PLAY_TOKENS, PLAY_TIMEOUT_S = 3000, 90.0
# how long an input is left to take effect, then until the screen shows the same twice this far apart, at most
SETTLE_S, STILL_GAP_S, STILL_TIMEOUT_S = 0.6, 0.3, 3.0
SWIPE_MS = 150


def check(action: Any, size: tuple[int, int], kinds: frozenset[str]) -> dict[str, Any] | None:
    """The model's action as one the surface can be given, or None."""
    if not isinstance(action, dict):
        return None
    kind = action.get("kind")
    if kind in NEEDS and NEEDS[kind] not in kinds:
        return None
    if kind in (TAP, SWIPE, WAIT):
        return recovery.check(action, size, kinds)
    if kind == KEY and (action.get("key") in KEYS or (isinstance(action.get("key"), str) and len(action["key"]) == 1
                                                      and action["key"] in TYPED)):
        return {"kind": KEY, "key": action["key"]}
    if kind == TEXT and isinstance(action.get("text"), str) and 0 < len(action["text"]) <= TEXT_MOST \
            and set(action["text"]) <= TYPED:
        return {"kind": TEXT, "text": action["text"]}
    if kind == DONE and action.get("end") in ENDS:
        return {"kind": DONE, "end": action["end"]}
    if kind == GIVE_UP:
        return {"kind": GIVE_UP}
    return None


async def send(surface: Any, action: dict[str, Any]) -> None:
    if action["kind"] == TAP:
        await surface.tap(*action["at"], TAP_MS)
    elif action["kind"] == KEY:
        await surface.key(action["key"])
    elif action["kind"] == TEXT:
        for letter in action["text"]:
            await surface.key(letter)
    elif action["kind"] == SWIPE:
        await surface.drag(*action["from"], *action["to"], SWIPE_MS)
    elif action["kind"] == WAIT:
        await asyncio.sleep(action["ms"] / 1000)


async def settled(surface: Any) -> bytes:
    """The screen once an input's effect is over: the same twice in a row, or as it is at the time limit."""
    await asyncio.sleep(SETTLE_S)
    previous = await surface.capture()
    waited = 0.0
    while waited < STILL_TIMEOUT_S:
        await asyncio.sleep(STILL_GAP_S)
        waited += STILL_GAP_S
        seen = await surface.capture()
        if not carry._boxes(previous, seen):
            return seen
        previous = seen
    return previous


class Direct:
    """One game played by a model's own inputs."""

    def __init__(self, model: Model, *, hint: str | None = None, accept_consent: bool = False) -> None:
        self.model, self.hint, self.accept_consent = model, hint, accept_consent
        # what the model wants kept from one question to the next, and what it last said the screen shows
        self.notes, self.situation = "", ""
        # the score it read on the first screen that showed one, and on the last
        self.first_score: float | None = None
        self.score: float | None = None
        self.history: list[dict[str, Any]] = []
        self.questions = 0

    async def ask(self, png: bytes, kinds: frozenset[str], goal: dict[str, Any], left: dict[str, Any]) -> dict[str, Any] | None:
        """The model's answer for the screen as it is: `{"action", "expect"}` with the action in the screen's
        pixels, or None when it gave none the surface can be given."""
        shown, scale = recovery.view(png)
        size = recovery._size(shown)
        asked = {"play": True, "goal": goal, "left": left, "screen": list(size),
                 "inputs": sorted(kind for kind, needs in NEEDS.items() if needs in kinds) + [WAIT],
                 "keys": sorted(KEYS), "accept_consent": self.accept_consent, "notes": self.notes,
                 "history": self.history[-HISTORY_KEPT:], **({"hint": self.hint} if self.hint else {})}
        image = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                              "data": base64.b64encode(shown).decode("ascii")}}
        prompt = (resources.FRAGMENT / "prompts" / "play_direct.md").read_text(encoding="utf-8")
        self.questions += 1
        result = await self.model.complete(TurnRequest(
            system_stable=prompt, max_tokens=PLAY_TOKENS, timeout_s=PLAY_TIMEOUT_S,
            messages=[Msg("user", [{"type": "text", "text": json.dumps(asked)}, image])]))
        try:
            answer = json.loads(result.text or "")
        except ValueError:
            return None
        if not isinstance(answer, dict):
            return None
        if isinstance(answer.get("situation"), str):
            self.situation = answer["situation"][:300]
        if isinstance(answer.get("notes"), str):
            self.notes = answer["notes"][:NOTES_MOST]
        if type(answer.get("score")) in (int, float):
            self.score = answer["score"]
            self.first_score = self.score if self.first_score is None else self.first_score
        action = check(answer.get("action"), size, kinds)
        if action is None:
            return None
        told = dict(action)
        if scale != 1.0:
            action = {key: [part / scale for part in value] if key in ("at", "from", "to") else value
                      for key, value in action.items()}
        expect = answer["expect"][:200] if isinstance(answer.get("expect"), str) else ""
        return {"action": action, "told": told, "expect": expect, "scale": scale}

    def saw(self, answer: dict[str, Any], before: bytes, after: bytes) -> bool:
        """What the code saw of an input, kept for the next question: whether the screen changed and where."""
        scale = answer["scale"]
        places = sorted(carry._boxes(before, after), key=lambda box: -box[2] * box[3])[:PLACES_TOLD]
        self.history.append({"action": answer["told"], "expected": answer["expect"], "screen_changed": bool(places),
                             "changed_at": [[round(part * scale) for part in box] for box in places]})
        return bool(places)

    def summary(self) -> dict[str, Any]:
        return {"questions": self.questions, "score_first": self.first_score, "score_last": self.score,
                "notes": self.notes, "situation": self.situation}
