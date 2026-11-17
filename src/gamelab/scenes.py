"""What a screen is, by names no family owns (PLAN §25, R5), and whether the play between two levels gets
anywhere (PLAN §27, M5).

A family's rules name the scenes of its own board (`playing`, `won`). The screens between two levels are no
family's: a level's result, the map or home the next level is opened from, a notice over it, a story told a
line at a time, a tutorial that takes one input only, a puzzle of another kind altogether. A model that looks
at a screen names it by one of the kinds here (`play_direct.md`, `recover.md`, `name_screen.md`), and the code
goes by the name: a family's own reader is tried only on a screen named a board, an input on such a screen is
a move of the level, and what the work hands back says what kind of screen it stopped on.

Nothing here reads a screen for what it is; the one thing the code measures itself is whether a screen looks
like one it was on before (`Trail`): play between two levels that keeps coming back to the same screen is told
so, with what it tried from there, and given up when it comes back too often.
"""
from __future__ import annotations

import base64
import json
from typing import Any

from . import recovery, resources
from .model import Model, Msg, TurnRequest

BOARD, RESULT, MAP, POPUP = "board", "result", "map", "popup"
TUTORIAL, STORY, OTHER_PUZZLE, LOADING = "tutorial", "story", "other_puzzle", "loading"
# the kinds, each with what a model is told it means
KINDS = {
    BOARD: "a level of the game's own play is open and waits for a move",
    RESULT: "a level just ended: its win or loss is shown, with what it gave",
    MAP: "the map, home or menu the next level is opened from, with nothing over it",
    POPUP: "a notice, reward, offer or dialog over whatever is under it",
    TUTORIAL: "the game points at one thing to press outside a level and takes nothing else",
    STORY: "a story scene or dialogue that goes on a line at a time",
    OTHER_PUZZLE: "a side puzzle or mini-game that is not one of the game's levels",
    LOADING: "nothing can be pressed yet: the game is loading or an animation plays",
}
# a stay on a screen like one left before is told to the model from the first time on; play that comes back to
# one screen this often gets nowhere
ROUND_MOST = 6
# the inputs tried from a screen that are told when play comes back to it
TRIED_TOLD = 4
NAME_TOKENS, NAME_TIMEOUT_S = 400, 45.0


def kind(value: Any) -> str | None:
    """A model's name for a screen when it is one of the kinds, else None."""
    return value if isinstance(value, str) and value in KINDS else None


class Trail:
    """The screens play passed through between two levels, as small pictures (`recovery.signature`), each stay
    with the inputs sent from it. Screens one after another that look alike are one stay (a story's lines, a
    button pressed in vain); a screen like one left earlier is a coming back."""

    def __init__(self) -> None:
        self.stays: list[dict[str, Any]] = []
        self.inputs = 0

    def met(self, png: bytes) -> dict[str, Any] | None:
        """The screen play is on now: None when it was not here before, else how many times it left a screen
        like this one and the inputs it sent from it then."""
        seen = recovery.signature(png)
        if self.stays and recovery._distance(self.stays[-1]["signature"], seen) <= recovery.REPLAY_DISTANCE:
            return self._told(self.stays[-1]["signature"], self.stays[:-1])
        self.stays.append({"signature": seen, "actions": []})
        return self._told(seen, self.stays[:-1])

    def _told(self, seen: list[int], earlier: list[dict[str, Any]]) -> dict[str, Any] | None:
        before = [stay for stay in earlier if recovery._distance(stay["signature"], seen) <= recovery.REPLAY_DISTANCE]
        if not before:
            return None
        tried = [action for stay in before for action in stay["actions"]]
        return {"times": len(before), "tried": tried[-TRIED_TOLD:]}

    def sent(self, action: dict[str, Any]) -> None:
        self.inputs += 1
        if self.stays:
            self.stays[-1]["actions"].append(action)

    def round(self) -> bool:
        """Whether play came back to the screen it is on ROUND_MOST times."""
        if not self.stays:
            return False
        told = self._told(self.stays[-1]["signature"], self.stays[:-1])
        return told is not None and told["times"] >= ROUND_MOST

    def clear(self) -> None:
        self.stays, self.inputs = [], 0


async def name(model: Model, png: bytes) -> dict[str, Any]:
    """What kind of screen this is and what it shows, as a model names it: `{"scene", "situation"}` (None and ""
    when it gave no such answer). Asked where the work is handed back and no model looked at its last screen."""
    shown, _ = recovery.view(png)
    prompt = (resources.FRAGMENT / "prompts" / "name_screen.md").read_text(encoding="utf-8")
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                          "data": base64.b64encode(shown).decode("ascii")}}
    result = await model.complete(TurnRequest(
        system_stable=prompt, max_tokens=NAME_TOKENS, timeout_s=NAME_TIMEOUT_S,
        messages=[Msg("user", [{"type": "text", "text": json.dumps({"name_screen": True, "scenes": KINDS})}, image])]))
    try:
        answer = json.loads(result.text or "")
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        return {"scene": None, "situation": "", "usage": result.usage}
    situation = answer["situation"][:300] if isinstance(answer.get("situation"), str) else ""
    return {"scene": kind(answer.get("scene")), "situation": situation, "usage": result.usage}
