"""Waiting for the player's turn to come back, judged by a light model (2026-11-24, the person).

A game played in turns hands the turn to the other side after an input, and the model's next question is to come
when the turn is the player's again. Waiting for the screen to come to rest does not tell that: a turn timer, an
opponent's animation or a pulsing glow keeps a screen changing while the other side is at it and after, so the wait
ran to its limit every time and a game with a turn timer played the turn for the player (UNO!, 2026-11-24: four
turns taken over, each turn's question came 24 seconds after the card was played). No share of the screen that
"counts as changed" holds across games either.

So what the turn coming back looks like is the model's word: with the input that hands the turn over it says, in a
few words, what the screen shows once it can act again (`my_turn`: "your hand is lit and a timer runs by your
seat"). The program then looks at the screen and, each time it changed, asks a light judge yes or no: does the
screen show that now? The fast judge first (`checker.Jev`, half a second); when it is not sure either way, a more
careful one (`checker.Chat` on the light model). A sure yes ends the wait; at its limit the model is asked as
before. Nothing here knows a game.

Whether a yes was right is seen at the next question: a model that answers `wait` there was not at its turn
(`account["false_yes"]`, counted by `agent`).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Awaitable, Callable

from . import carry, checker, motion, recovery

# how often the screen is looked at while waiting, how soon after handing over the judge is first asked, and how
# often it is asked at most (once a screen it said no to changed)
LOOK_S, FIRST_S, ASK_EVERY_S = 0.4, 0.8, 1.0
# a judge's probability of yes at or above this is a yes, at or below the other a no; between, the next judge is
# asked
SURE_YES, SURE_NO = 0.7, 0.3
# the picture a judge is shown, its longer side at most; the words kept of the model's cue
VIEW_SIDE, CUE_MOST = 768, 200
# a wait judged so goes on no longer than the model's own (`then_wait_ms`, else `direct.REST_MS_RANGE`'s most): a
# judge that did not see the turn come back must not outlast the game's turn timer (2026-11-24: waits stretched to
# 30 s ran out twice and the game played those turns for the player)
CONTEXT = ("A game played in turns. The player handed the turn to the other side and is waiting for it to come back. "
           "Judge the picture of the game's screen as it is now.")
QUESTION = ("{image} is the game's screen now. Is it the player's own turn now, so that the player can act? It is the "
            "player's turn when the screen shows this: {cue}. Answer yes only when the screen shows it now, not while "
            "the other side is still acting.")


def cue(said: Any) -> str | None:
    """The model's words for what the screen shows at its turn, as they are kept; None when it gave none."""
    return said.strip()[:CUE_MOST] if isinstance(said, str) and said.strip() else None


def fresh_account() -> dict[str, Any]:
    return {"waits": 0, "asked": 0, "yes": 0, "timed_out": 0, "false_yes": 0, "seconds": 0.0, "by": {}}


async def until_turn(capture: Callable[[], Awaitable[bytes]], judges: list[Any], said: str, ms: int,
                     moving: Callable[[], list[list[int]]], account: dict[str, Any]) -> bytes:
    """The screen once a judge said it is the player's turn as `said` describes it, or as it is when the wait
    (`ms`) ran out. A screen is put to the judges only when it changed since the last they were shown (what keeps
    moving left out), at most every `ASK_EVERY_S`. With GAMELAB_TURN_DUMP set, each picture judged is saved there
    with what the judges said, for looking into a measured run."""
    started = time.monotonic()
    deadline = started + ms / 1000
    account["waits"] += 1
    asked_on: bytes | None = None
    last_ask = started - ASK_EVERY_S + FIRST_S
    png = await capture()
    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                account["timed_out"] += 1
                return png
            changed = asked_on is None or bool(motion.outside(carry._boxes(asked_on, png), moving()))
            if changed and now - last_ask >= ASK_EVERY_S:
                last_ask, asked_on = now, png
                if await _judged(judges, png, said, account):
                    account["yes"] += 1
                    return png
            await asyncio.sleep(LOOK_S)
            png = await capture()
    finally:
        account["seconds"] = round(account["seconds"] + time.monotonic() - started, 1)


async def _judged(judges: list[Any], png: bytes, said: str, account: dict[str, Any]) -> bool:
    """Whether the judges say the screen shows the player's turn: the first sure answer stands; one no judge was
    sure of is a no."""
    shown = recovery.view(png, VIEW_SIDE)[0]
    check = checker.Check("turn", checker.NOUL, QUESTION.format(image=checker.IMAGE, cue=said), shown)
    said_by: dict[str, Any] = {}
    try:
        return await _asked(judges, check, account, said_by)
    finally:
        if os.environ.get("GAMELAB_TURN_DUMP"):
            folder = os.environ["GAMELAB_TURN_DUMP"]
            os.makedirs(folder, exist_ok=True)
            stem = os.path.join(folder, f"turn-{os.getpid()}-{account['asked']:03d}")
            with open(stem + ".png", "wb") as out:
                out.write(shown)
            with open(stem + ".json", "w", encoding="utf-8") as out:
                json.dump({"cue": said, "said": said_by}, out, ensure_ascii=False)


async def _asked(judges: list[Any], check: Any, account: dict[str, Any], said_by: dict[str, Any]) -> bool:
    for judge in judges:
        account["asked"] += 1
        name = getattr(judge, "name", "judge")
        account["by"][name] = account["by"].get(name, 0) + 1
        try:
            answer = (await judge.ask([check], CONTEXT)).get("turn")
        except Exception:
            continue
        p = answer.get("p") if answer else None
        said_by[name] = p
        if p is None:
            continue
        if p >= SURE_YES:
            return True
        if p <= SURE_NO:
            return False
    return False
