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

from . import checker, recovery

# the screen is put to the judges as it is, one frame after another with no wait between (PLAN §36, 강제 대기
# 검토: the judges were shown a screen every 3.6 to 3.9 s, and the turn of a game that allows 10 s was found
# late). The next frame is taken while the judges look at the one before
# a judge's probability of yes at or above this is a yes, at or below the other a no; between, the next judge is
# asked
SURE_YES, SURE_NO = 0.7, 0.3
# the picture a judge is shown, its longer side at most; the words kept of the model's cue
VIEW_SIDE, CUE_MOST = 768, 200
# the wait has no time of its own (2026-11-24, the person: a fixed limit is a number no game agrees on). It ends at
# the judges' yes, and after this many noes in a row the main model is asked as before: if the judges were wrong it
# plays its turn, if not it answers `wait` and the judges watch again. A judge that cannot see the turn costs a main
# question every few noes, not the whole turn timer (UNO!, 2026-11-24: two waits of 30 s ran past it). Each time
# the main model only waited after such noes, the noes it takes are twice as many, up to NOES_MOST_TOP: with the
# judges asked back to back, five noes are a few seconds of the other side's turn. A turn the judges missed (the
# main model played at once) puts it back to NOES_MOST
NOES_MOST, NOES_MOST_TOP = 5, 40
CONTEXT = ("A game played in turns. The player handed the turn to the other side and is waiting for it to come back. "
           "Judge the picture of the game's screen as it is now.")
QUESTION = ("{image} is the game's screen now. Is it the player's own turn now, so that the player can act? It is the "
            "player's turn when the screen shows this: {cue}. Answer yes only when the screen shows it now, not while "
            "the other side is still acting.")
# asked beside it: a window, banner or message over the game waiting for the player to act on it. The game may have
# taken the player's turns over after a timeout and keep playing them until told to stop (UNO!, 2026-11-24: "Played
# by computer" with a Cancel button stood 24 to 36 s each time, the judge rightly saying it was no turn of the
# player's); the main model is asked at once
NOTICE = ("Over the game in {image}, is there a window, banner or message addressed to the player that waits for them "
          "to act on it: something to confirm, cancel or choose, or a notice that the game now plays for them? The "
          "game's usual table, its usual buttons and the other side's moves do not count.")


def cue(said: Any) -> str | None:
    """The model's words for what the screen shows at its turn, as they are kept; None when it gave none."""
    return said.strip()[:CUE_MOST] if isinstance(said, str) and said.strip() else None


def fresh_account() -> dict[str, Any]:
    # `noes_out`: waits that ended on `noes_most` noes; of those, `false_no`: the main model played at once (the
    # turn was there); `false_yes`: a yes the main model only waited at; `notice`: waits that ended on a window
    # waiting for the player (`NOTICE`); `noes_most`: the noes a wait now takes before the main model is asked
    return {"waits": 0, "asked": 0, "yes": 0, "noes_out": 0, "false_yes": 0, "false_no": 0, "notice": 0,
            "seconds": 0.0, "by": {}, "noes_most": NOES_MOST}


def waited_for_nothing(account: dict[str, Any]) -> None:
    """The main model, asked after the judges' noes, only waited: they were right, and the next wait takes twice
    as many noes before it asks again."""
    account["noes_most"] = min(NOES_MOST_TOP, 2 * account.get("noes_most", NOES_MOST))


async def until_turn(capture: Callable[[], Awaitable[bytes]], judges: list[Any], said: str,
                     account: dict[str, Any]) -> bytes:
    """The screen once a judge said it is the player's turn as `said` describes it, or as it is after
    `noes_most` noes in a row (the main model then looks). Each frame is put to the judges as it was taken; the
    next is taken meanwhile. With GAMELAB_TURN_DUMP set, each picture judged is saved there with what the judges
    said."""
    started = time.monotonic()
    account["waits"] += 1
    noes = 0
    png = await capture()
    try:
        while True:
            # the next frame is taken while the judges look at this one: it is there when they say no
            following = asyncio.ensure_future(capture())
            try:
                judged = await _judged(judges, png, said, account)
            finally:
                # a capture is let finish, not cut off (an adb command cut off is left running)
                taken = await asyncio.gather(following, return_exceptions=True)
            if judged:
                account["notice" if judged == NOTICED else "yes"] += 1
                return png
            noes += 1
            if noes >= account.get("noes_most", NOES_MOST):
                account["noes_out"] += 1
                return png
            if isinstance(taken[0], BaseException):
                raise taken[0]
            png = taken[0]
    finally:
        account["seconds"] = round(account["seconds"] + time.monotonic() - started, 1)


NOTICED = "notice"


async def _judged(judges: list[Any], png: bytes, said: str, account: dict[str, Any]) -> bool | str:
    """Whether the judges say the screen shows the player's turn (True), or a window waiting for the player
    (`NOTICED`): the first sure answer stands; one no judge was sure of is a no."""
    shown = recovery.view(png, VIEW_SIDE)[0]
    checks = [checker.Check("turn", checker.NOUL, QUESTION.format(image=checker.IMAGE, cue=said), shown),
              checker.Check("notice", checker.NOUL, NOTICE.format(image=checker.IMAGE), shown)]
    said_by: dict[str, Any] = {}
    try:
        return await _asked(judges, checks, account, said_by)
    finally:
        if os.environ.get("GAMELAB_TURN_DUMP"):
            folder = os.environ["GAMELAB_TURN_DUMP"]
            os.makedirs(folder, exist_ok=True)
            stem = os.path.join(folder, f"turn-{os.getpid()}-{account['asked']:03d}")
            with open(stem + ".png", "wb") as out:
                out.write(shown)
            with open(stem + ".json", "w", encoding="utf-8") as out:
                json.dump({"cue": said, "said": said_by}, out, ensure_ascii=False)


async def _asked(judges: list[Any], checks: list[Any], account: dict[str, Any], said_by: dict[str, Any]) -> bool | str:
    for judge in judges:
        account["asked"] += 1
        name = getattr(judge, "name", "judge")
        account["by"][name] = account["by"].get(name, 0) + 1
        try:
            answers = await judge.ask(checks, CONTEXT)
        except Exception:
            continue
        p = (answers.get("turn") or {}).get("p")
        notice = (answers.get("notice") or {}).get("p")
        said_by[name] = {"turn": p, "notice": notice}
        if notice is not None and notice >= SURE_YES:
            return NOTICED
        if p is None:
            continue
        if p >= SURE_YES:
            return True
        # a sure no to the turn is a no only when the judge was sure of the window too: one it was unsure of goes to
        # the next judge (UNO!, 2026-11-24: "Played by computer" judged 0.45 to 0.62 four times in a row, about
        # 14 s, while the turn was a sure no each time)
        if p <= SURE_NO and (notice is None or notice <= SURE_NO):
            return False
    return False
