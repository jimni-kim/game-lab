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

from . import catalog, research, resources
from .contracts import FORCED_FREE, META_ACTIONS
from .execution import TAP_MS
from .model import Model, Msg, TurnRequest

log = logging.getLogger("gamelab.recovery")

TAP, KEY, SWIPE, WAIT, NONE, GIVE_UP, BACK, STOP = "tap", "key", "swipe", "wait", "none", "give_up", "back", "stop"
# the game will not open a level until time gives back its lives (hearts, energy): the work is handed back with how
# long until the next one, nothing waited for or bought (PLAN §26, C2-4)
OUT_OF_LIVES = "out_of_lives"
# the longest wait for a life a model may tell, in minutes
LIFE_MINUTES_MOST = 24 * 60
# why play stopped when the level on the screen ended before any move of this part of the work, and the work goes
# on to the next (`agent.Agent.onward`)
NEXT_LEVEL = "next_level"
# why play stopped when a game from its start was asked for and the one on the screen ended before the work began:
# a new game of the same kind is to be started (`agent.Agent.anew`)
NEW_GAME = "new_game"
# the level on the screen cannot be won as it stands (the family's rules show it) and the game shows no end of its
# own: the level is started again from its beginning, when the person allowed another try
START_OVER = "start_over"
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
# what a question before the game is known is told of what was found of it, and of looking at its own help first
RESEARCH_TOLD = ("\n\nThe context has `research` because this game was looked up before it was found: `research.unconfirmed` "
                 "is what was found outside the game (its store page, what is generally known of it), not checked against "
                 "this game, so where it and the screen differ the screen is right; `research.read_in_game` is what was "
                 "read on the game's own help screens. Use them to tell what the screens are and the way to the game.")
LOOK_FIRST = ("\n\nThe context has `look_first` because the game was not yet looked at from inside. Before starting play from "
              "a start screen or a menu, when the screen offers a help, how-to-play, rules, guide, tutorial text or a codex "
              "(on the menu, in its settings, behind a \"?\" or an \"i\"), open it, read it page by page and close it again, "
              "marking each input you send for that with `\"research\": true` inside the action. Add `found` to the answer: "
              "an object with the parts of `look_first.parts`, each a short text, `terms` an object of the game's words "
              "with what each means; give only what the screen showed. Never start a level or a run to reach such a page, "
              "and never open one that costs anything or signs in. When you have read what there is, or this screen offers "
              "none, add `\"research_done\": true` and give the input that gets to the game as usual. Inputs that only get "
              "past a loading screen, a notice or a consent are sent as usual, without the mark.")


def signature(png: bytes) -> list[int]:
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        return list(image.convert("L").resize((SIGNATURE_SIDE, SIGNATURE_SIDE)).tobytes())


def view(png: bytes, side: int = VIEW_SIDE) -> tuple[bytes, float]:
    """The picture as a model is shown it, and how much smaller it is than the screen: a model's service
    shrinks a large picture before the model sees it, and the model then answers in the pixels of what it
    saw. A picture already no longer than `side` is shown as it is; points and boxes a model gives are
    divided by the factor to be the screen's."""
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        longer = max(image.size)
        if longer <= side:
            return png, 1.0
        scale = side / longer
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
        return {"kind": kind, "end": action["end"], **left_of(action)}
    if kind == OUT_OF_LIVES:
        return {"kind": kind, "minutes": minutes(action.get("minutes"))}
    if kind in (NONE, GIVE_UP, BACK, STOP):
        return {"kind": kind}
    return None


def minutes(value: Any) -> float | None:
    """How long until the next life, as a model read it off the screen; None when it gave no such number."""
    return value if type(value) in (int, float) and 0 <= value <= LIFE_MINUTES_MOST else None


def left_of(action: dict[str, Any]) -> dict[str, int]:
    """The moves a level ended with, as the model saw them on the screen of its end (PLAN §26, C2-6)."""
    left = action.get("moves_left")
    return {"moves_left": left} if type(left) is int and 0 <= left <= 999 else {}


def profile(action: Any) -> str | None:
    """What an input made of a name or profile the game would not go on without (PLAN §26, C2-5), as the model
    marked it; None for an input that made none."""
    made = action.get("profile") if isinstance(action, dict) else None
    return made.strip()[:40] if isinstance(made, str) and made.strip() else None


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
                 account: dict[str, dict[str, int]] | None = None, profiles: list[dict[str, Any]] | None = None,
                 found: dict[str, Any] | None = None, looked: dict[str, Any] | None = None) -> None:
        # `family_id` None: the game is not known yet (a screen before any board was seen)
        self.model, self.family_id, self.accept_consent = model, family_id, accept_consent
        # what the person allowed beyond the board (`META_ACTIONS` names) and the conditions they stop at
        self.allowed, self.stops = frozenset(allowed), tuple(stops)
        # what the inputs sent used up, and what inputs not sent would have: the game's own account when it
        # gives one, so that a recovery made anew for another family adds to the same
        account = account if account is not None else {"used": {}, "refused": {}}
        self.used, self.refused = account.setdefault("used", {}), account.setdefault("refused", {})
        # the names and profiles the game made its player make, for the work to tell (shared with the game's own)
        self.profiles = profiles if profiles is not None else []
        # inputs that brought play back, each with the small picture of the screen it was sent on
        self.known: list[dict[str, Any]] = list(known or [])
        # every time the code was stuck and what was done about it, for whoever measures the run
        self.log: list[dict[str, Any]] = []
        # what was tried since play last went on: told to the model, and credited when play goes on again
        self.tried: list[dict[str, Any]] = []
        self.questions = 0
        # what was found out of the game before it was known (`research`, PLAN §27 M8, §36 E4), given with each
        # question; and, while `looked` is given and not done, the screens before the game (a start screen, its
        # menu) may first be used to read the game's own help (`research.look_first`), shared with its play
        self.found, self.looked = found, looked

    def _looking(self, reason: str) -> bool:
        return reason == "game_not_shown" and self.looked is not None and self.looked["done"] is None \
            and self.looked["questions"] < research.QUESTIONS_MOST

    async def attempt(self, surface: Any, reason: str, context: dict[str, Any], *, timeout_s: float | None = None) -> bool:
        """One thing done about the screen as it is now. True when an input was sent (or waited out) and the
        screen is worth another look; False when nothing is in the way, the model gives up or has no answer."""
        png = await surface.capture()
        seen = signature(png)
        entry: dict[str, Any] = {"reason": reason, "source": "model", "situation": None, "action": None, "changed": None}
        self.log.append(entry)
        self._forget()
        # an input that got past a game ended before a work asked for from its start (a new game button) is sent
        # again only there, never on the end of a game played since (`agent.Agent.anew`)
        anew = bool(context.get("anew"))
        action = self._replay(seen, reason, anew) if reason in REPLAYED_REASONS else None
        if action is not None:
            entry["source"] = "replay"
        else:
            action = await self._ask(png, reason, context, surface.kinds, entry, timeout_s)
        entry["action"] = action
        if action is None or action["kind"] in (NONE, GIVE_UP, STOP, OUT_OF_LIVES):
            return False
        refused = [name for name in action.get("uses", []) if name not in self.allowed
                   and not (action.get("forced_free") and name in FORCED_FREE)]
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
        if action.get("profile"):
            self.profiles.append({"made": action["profile"], "situation": entry["situation"] or ""})
        if action.get("forced_free"):
            self.used["forced_free"] = self.used.get("forced_free", 0) + 1
        self.tried.append({"action": action, "screen_changed": entry["changed"], "signature": seen,
                           "replayed": entry["source"] == "replay", "reason": reason, **({"anew": True} if anew else {})})
        return True

    async def replay(self, surface: Any, reason: str) -> bool:
        """The input kept for a screen like this one sent again, with no model asked: True when one was sent."""
        seen = signature(await surface.capture())
        self._forget()
        action = self._replay(seen, reason) if reason in REPLAYED_REASONS else None
        if action is None:
            return False
        entry: dict[str, Any] = {"reason": reason, "source": "replay", "situation": None, "action": action, "changed": None}
        self.log.append(entry)
        try:
            await send(surface, action)
            await asyncio.sleep(SETTLE_S)
            entry["changed"] = _distance(seen, signature(await surface.capture())) > 0
        except Exception:
            log.exception("recovery input failed: %s", action)
            return False
        self.tried.append({"action": action, "screen_changed": entry["changed"], "signature": seen, "replayed": True,
                           "reason": reason})
        return True

    def _forget(self) -> None:
        """A replayed input after which play is stuck again did not get past its screen: it is not kept."""
        if self.tried and self.tried[-1]["replayed"]:
            self.known = [known for known in self.known if known["action"] != self.tried[-1]["action"]
                          or _distance(known["signature"], self.tried[-1]["signature"]) > REPLAY_DISTANCE]

    def _replay(self, seen: list[int], reason: str, anew: bool = False) -> dict[str, Any] | None:
        """The input that got past a screen like this one before; never twice on one screen, so one that
        no longer works is followed by a question. An input that opened the next level is sent again only to
        open the next level: the same end screen after a level played here is that level's end, not in the way."""
        if self.tried:
            return None
        near = [known for known in self.known if _distance(known["signature"], seen) <= REPLAY_DISTANCE
                and bool(known.get("next")) == (reason == NEXT_LEVEL) and bool(known.get("anew")) == anew]
        return min(near, key=lambda known: _distance(known["signature"], seen))["action"] if near else None

    async def _ask(self, png: bytes, reason: str, context: dict[str, Any], kinds: frozenset[str],
                   entry: dict[str, Any], timeout_s: float | None) -> dict[str, Any] | None:
        from . import scenes

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
                 "scenes": scenes.KINDS,
                 **context,
                 **({"research": given} if (given := research.told(self.found)) else {}),
                 **({"look_first": research.look_first(self.looked)} if (looking := self._looking(reason)) else {}),
                 "tried": [{"action": tried["action"], "screen_changed": tried["screen_changed"],
                            **({"refused": tried["refused"]} if "refused" in tried else {})}
                           for tried in self.tried[-TRIED_KEPT:]]}
        prompt = (resources.FRAGMENT / "prompts" / "recover.md").read_text(encoding="utf-8") \
            + (RESEARCH_TOLD if given else "") + (LOOK_FIRST if looking else "")
        image = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                              "data": base64.b64encode(png).decode("ascii")}}
        self.questions += 1
        result = None
        try:
            async with asyncio.timeout(timeout):
                result = await self.model.complete(TurnRequest(
                    system_stable=prompt, max_tokens=RECOVER_TOKENS, timeout_s=timeout,
                    messages=[Msg("user", [{"type": "text", "text": json.dumps(asked)}, image])]))
            answer = json.loads(result.text or "")
        except Exception as error:
            entry["situation"] = f"no answer: {type(error).__name__}"
            return None
        finally:
            if looking:
                self.looked["questions"] += 1
                self.looked["tokens"] += result.usage.input_tokens if result is not None else 0
        if not isinstance(answer, dict):
            return None
        marked = looking and research.looked_at(answer, self.found, self.looked, scenes.kind(answer.get("scene")) == scenes.BOARD)
        if isinstance(answer.get("situation"), str):
            entry["situation"] = answer["situation"][:300]
        if scenes.kind(answer.get("scene")) is not None:
            entry["scene"] = answer["scene"]
        action, used = check(answer.get("action"), size, kinds), uses(answer.get("action"))
        if action is None or used is None:
            return None
        if used:
            action["uses"] = used
            if answer["action"].get("forced_free") is True and set(used) <= FORCED_FREE:
                action["forced_free"] = True
        if action["kind"] == STOP and isinstance(answer["action"].get("because"), str):
            entry["because"] = answer["action"]["because"][:200]
        if action["kind"] in (TAP, KEY) and (made := profile(answer["action"])) is not None:
            action["profile"] = made
        if marked:
            action["research"] = True
            self.looked["inputs"] += 1
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
                                        **({"next": True} if last["reason"] == NEXT_LEVEL else {}),
                                        **({"anew": True} if last.get("anew") else {})}][-KNOWN_KEPT:]
        self.tried = []

    def summary(self, since: int = 0) -> dict[str, Any]:
        entries = self.log[since:]
        return {"stuck": len(entries), "inputs": sum(entry["changed"] is not None for entry in entries),
                "replayed": sum(entry["source"] == "replay" for entry in entries),
                "restored": sum(bool(entry.get("restored")) for entry in entries),
                "questions": sum(entry["source"] == "model" for entry in entries),
                **({"used": dict(self.used)} if self.used else {}), **({"refused": dict(self.refused)} if self.refused else {})}
