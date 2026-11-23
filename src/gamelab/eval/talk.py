"""What the demo page's agent says in its chat (PLAN §35): what a person typed sorted into a game asked for, a
question or a stop; the sentences the code makes of what it knows (what it will do, what it handed back, why a
request was not taken); and the answer a model gives a question from the game as it stands.

The facts of the sentences the code makes come from what the host checked (the `GoalSpec` a request was read as,
the work's handback), never from a model's account of them: what is said is what is done. Only a question is
answered by a model, which is given the code's counts beside the screen and told to put them first.

They are said in Korean when the person's words hold Hangul, else in English.
"""
from __future__ import annotations

import base64
import json
from typing import Any

from .. import resources
from ..model import Model, Msg, TurnRequest

GAME, QUESTION, STOP = "game", "question", "stop"
KINDS = (GAME, QUESTION, STOP)
SORT_TOKENS, SORT_TIMEOUT_S = 200, 30.0
REPLY_TOKENS, REPLY_TIMEOUT_S = 1200, 90.0
# a reply's letters at most
REPLY_MOST = 2000

META = {"boosters": ("부스터", "boosters"), "currency": ("게임 재화(코인 등)", "the game's currency (coins and such)"),
        "continue": ("이어하기", "continuing a lost game"), "retry": ("다시 하기", "playing a lost level again")}


def korean(words: str) -> bool:
    return any("가" <= letter <= "힣" or "ㄱ" <= letter <= "ㆎ" for letter in words)


def _plain(text: str) -> str:
    return "".join(letter for letter in text.casefold() if letter.isalnum())


def _prompt(name: str) -> str:
    return (resources.FRAGMENT / "prompts" / f"{name}.md").read_text(encoding="utf-8")


def _json(text: str | None) -> dict[str, Any]:
    try:
        said = json.loads(text or "")
    except ValueError:
        return {}
    return said if isinstance(said, dict) else {}


async def sort(model: Model, words: str, playing: bool, last: dict[str, Any] | None = None,
               chat: list[dict[str, Any]] | None = None, pending: str | None = None) -> str:
    """Whether the words ask for a game, a stop or are a question: one question of `model`, told the game played
    last (`last`, for words that go on with it and name no game), the chat's last lines (`chat`, each `{"who",
    "text"}`) and the question the agent asked back and is waiting on (`pending`). When it cannot say, words while
    a game is played are a question (nothing is started or stopped on a guess), and words with none played are a
    game asked for, which the request's own reading then takes or refuses (`no_game`)."""
    fallback = QUESTION if playing else GAME
    context = {"words": words, "playing": playing, **({"last": last} if last else {}),
               **({"chat": chat} if chat else {}), **({"pending": pending} if pending else {})}
    try:
        result = await model.complete(TurnRequest(
            system_stable=_prompt("chat_sort"), max_tokens=SORT_TOKENS, timeout_s=SORT_TIMEOUT_S, name="chat_sort",
            messages=[Msg("user", [{"type": "text", "text": json.dumps(context, ensure_ascii=False, default=str)}])]))
    except Exception:
        return fallback
    kind = _json(result.text).get("kind")
    return kind if kind in KINDS else fallback


async def reply(model: Model, context: dict[str, Any], frame: bytes | None) -> tuple[str, Any]:
    """A model's answer to the person's words (`context["words"]`) from the game as `context` holds it and its last
    screen (a JPEG), and what the question cost (`Usage`)."""
    content: list[dict[str, Any]] = [{"type": "text", "text": json.dumps(context, ensure_ascii=False, default=str)}]
    if frame is not None:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.b64encode(frame).decode("ascii")}})
    result = await model.complete(TurnRequest(system_stable=_prompt("chat_reply"), max_tokens=REPLY_TOKENS,
                                              timeout_s=REPLY_TIMEOUT_S, name="chat_reply",
                                              messages=[Msg("user", content)]))
    said = _json(result.text).get("reply")
    text = said if isinstance(said, str) and said.strip() else (result.text or "").strip()
    return text[:REPLY_MOST], result.usage


# ── the sentences the code makes ────────────────────────────────────


def _goal(goal: dict[str, Any], ko: bool) -> str:
    objective, target = goal.get("objective"), goal.get("target_value")
    if objective == "levels":
        return f"레벨 {target}개를 연달아 깨겠습니다" if ko else f"clear {target} levels in a row"
    if objective == "reach_value":
        return (f"게임이 세는 값(점수·타일·지운 줄)이 {target}에 이를 때까지 두겠습니다" if ko
                else f"play until the game's count (a score, a tile, lines cleared) reaches {target}")
    if objective == "actions":
        return f"{target}수를 두겠습니다" if ko else f"make {target} moves"
    if objective == "until_level":
        return (f"레벨 {target}까지 깨겠습니다(화면의 레벨 번호로 셉니다)" if ko
                else f"clear levels up to level {target} (counted by the level number on the screen)")
    return ("지금 판이 끝날 때까지 두겠습니다(레벨이 있는 게임이면 지금 레벨 하나)" if ko
            else "play the game on the screen to its end (in a game of levels, the level now on it)")


def promise(asked: dict[str, Any], limits: dict[str, Any], ko: bool) -> str:
    """What will be done, and how far, from the request as the host read and checked it (`request.understand`):
    the place, the goal and its number, where it starts, when it stops short, what beside the board it may use,
    the errand, how a story's choices are made, and the game's budget."""
    goal = asked["goal"].to_json() if hasattr(asked["goal"], "to_json") else dict(asked["goal"])
    where = asked.get("url") or asked.get("app") or "?"
    game = asked.get("within") or asked.get("hint")
    if game and _plain(game) in _plain(str(where)):
        # a name that only says again what the app or page is
        game = None
    task = asked.get("task")
    if ko:
        lines = [f"{where}" + (f"의 {game}" if game else "") + "에서 "
                 + (f"'{task}' 작업을 하겠습니다" + (" (아직 때가 아니면 게임을 연 채 기다렸다가)" if asked.get("wait") else "")
                    if task else _goal(goal, ko)) + "."]
        lines.append("지금 화면에서 이어서 합니다." if goal.get("scope") == "current_game" else "게임을 새로 띄워서 시작합니다.")
        stops = list(goal.get("stop_conditions") or ())
        if "lost" in stops:
            lines.append("레벨이나 판을 지면 거기서 멈춥니다.")
        worded = [f"'{stop}'" for stop in stops if stop != "lost"]
        if worded:
            # a condition in words is the models' to see: none looks at the screen while code plays
            lines.append(", ".join(worded) + "가 화면에 보이면 멈춥니다. 이런 조건은 모델이 화면을 볼 때만 확인되고, "
                                             "코드가 두는 동안에는 확인되지 않습니다.")
        allowed = [META[name][0] for name in goal.get("allowed_meta_actions") or () if name in META]
        lines.append(f"허락하신 것: {', '.join(allowed)}." if allowed else "부스터·재화·이어하기·다시 하기는 쓰지 않습니다.")
        lines.append(f"이야기 선택지는 '{goal['choices']}'대로 고릅니다." if goal.get("choices")
                     else "되돌릴 수 없는 이야기 선택지가 나오면 고르지 않고 멈춰서 알려 드립니다.")
        lines.append(f"한도는 입력 {limits['inputs']}번, {limits['seconds']:g}초입니다.")
    else:
        lines = [f"On {where}" + (f", {game}" if game else "") + ": I will "
                 + (f"do the errand '{task}'" + (" (waiting with the game open if it is not ready yet)" if asked.get("wait") else "")
                    if task else _goal(goal, ko)) + "."]
        lines.append("I go on from the screen as it is now." if goal.get("scope") == "current_game"
                     else "I start the game again first.")
        stops = list(goal.get("stop_conditions") or ())
        if "lost" in stops:
            lines.append("I stop short when a level or game is lost.")
        worded = [f"'{stop}'" for stop in stops if stop != "lost"]
        if worded:
            lines.append("I stop when " + " or ".join(worded) + " shows on the screen. Such a condition is checked only "
                         "when a model looks at the screen, not while code plays.")
        allowed = [META[name][1] for name in goal.get("allowed_meta_actions") or () if name in META]
        lines.append(f"You allowed: {', '.join(allowed)}." if allowed
                     else "I use no boosters, currency, continues or retries.")
        lines.append(f"A story's choices are made as you said: '{goal['choices']}'." if goal.get("choices")
                     else "A story's choice that cannot be taken back is not made: I stop there and tell you.")
        lines.append(f"The budget is {limits['inputs']} inputs and {limits['seconds']:g} seconds.")
    return " ".join(lines)


WHY = {
    ("goal_reached", None): ("목표를 이뤘습니다", "the goal was reached"),
    ("game_over", "level_lost"): ("레벨을 져서 멈췄습니다", "a level was lost and the work stopped there"),
    ("game_over", None): ("게임이 끝났습니다", "the game ended"),
    ("budget_exhausted", "cancelled"): ("중지 요청으로 멈췄습니다", "it was stopped as you asked"),
    ("budget_exhausted", "time"): ("시간 한도에 닿아 멈췄습니다", "the time limit was reached"),
    ("budget_exhausted", "actions"): ("입력 한도에 닿아 멈췄습니다", "the input limit was reached"),
    ("budget_exhausted", "tokens"): ("모델 토큰 한도에 닿아 멈췄습니다", "the model token limit was reached"),
    ("stopped", "out_of_lives"): ("하트(목숨)가 없어 멈췄습니다", "the game is out of lives"),
    ("stopped", "choice"): ("이야기 선택지에서 멈췄습니다", "it stopped at a story's choice"),
    ("stopped", None): ("말씀하신 멈춤 조건에서 멈췄습니다", "a condition you gave for stopping was met"),
    ("gave_up", "error"): ("오류로 멈췄습니다", "it stopped on an error"),
    ("gave_up", "level_unread"): ("화면에서 레벨 번호를 읽지 못해 더 세지 못하고 멈췄습니다",
                                  "the level number could not be read from the screen, so the levels could not be counted"),
    ("gave_up", None): ("더 할 수 있는 것을 찾지 못해 멈췄습니다", "it found nothing more it could do"),
}


def _why(outcome: str, reason: str, ko: bool, detail: str = "") -> str:
    said = WHY.get((outcome, reason)) or WHY.get((outcome, None))
    return (said[0 if ko else 1] if said else f"{outcome}") + f" ({outcome}/{reason}" \
        + (f": {detail[:200]}" if reason == "error" and detail else "") + ")"


def handed_back(summary: dict[str, Any], ko: bool) -> str:
    """The end of a game in a few sentences, from what the work handed back (`work.play`) and the game's result:
    why it stopped, the levels won and lost, what it spent and used, where it stopped, how long until lives or an
    errand come back, and the story's choice it stopped at with its options."""
    work = summary.get("work") or {}
    adapted = summary.get("adaptation") or {}
    outcome, reason = work.get("outcome") or summary.get("outcome"), work.get("reason") or summary.get("reason")
    spent = work.get("spent") or {}
    inputs, moves = spent.get("inputs", summary.get("actions_used")), spent.get("moves", summary.get("confirmed_actions"))
    seconds = spent.get("seconds", summary.get("seconds"))
    levels, stopped = work.get("levels"), work.get("stopped_at") or {}
    falling = adapted.get("realtime") or {}
    used = {name: count for name, count in (spent.get("used") or {}).items() if count}
    lives, later, choice = work.get("out_of_lives"), work.get("not_yet"), adapted.get("choice")
    family = stopped.get("family") or (adapted.get("identified") or {}).get("family")
    goal = work.get("goal") or {}
    target, objective = goal.get("target_value"), goal.get("objective")
    # how far the goal the request counted got (the levels are said below)
    counted = objective in ("levels", "until_level")
    reached = None if target is None or counted else \
        falling.get("lines") if objective == "reach_value" and falling.get("pieces") is not None else \
        falling.get("pieces") if objective == "actions" and falling.get("pieces") is not None else \
        moves if objective == "actions" else None
    if ko:
        lines = [f"끝났습니다: {_why(outcome, reason, ko, work.get('detail') or '')}."]
        if target is not None and not counted:
            lines.append(f"목표 {target} 가운데 {reached if reached is not None else '?'}에 닿았습니다."
                         if reached is not None or objective == "reach_value" else "")
        if levels and levels.get("until") is not None:
            lines.append(f"레벨 {levels['until']}까지: {levels['won']}개 깼고 {levels['lost']}개 졌습니다."
                         + (f" 화면으로 본 마지막 깬 레벨은 {levels['through']}입니다." if levels.get("through") is not None else ""))
        elif levels:
            lines.append(f"레벨 {levels['wanted']}개 가운데 {levels['won']}개를 깼고 {levels['lost']}개를 졌습니다.")
        if falling.get("pieces") is not None:
            lines.append(("마지막 레벨에서 " if levels else "") + f"조각 {falling['pieces']}개를 놓아 줄 {falling.get('lines', 0)}개를 지웠습니다.")
        lines.append(f"입력 {inputs}번(확인된 수 {moves})" + (f", {seconds:g}초" if isinstance(seconds, (int, float)) else "")
                     + (f", 쓴 것: {', '.join(f'{META.get(name, (name,))[0]} {count}' for name, count in used.items())}"
                        if used else ", 판 밖의 것은 쓰지 않았습니다") + ".")
        if family:
            lines.append(f"게임은 {family} 계열로 두었습니다.")
        if stopped.get("screen") or stopped.get("situation"):
            lines.append("멈춘 화면: " + " — ".join(part for part in (stopped.get("screen"), stopped.get("situation")) if part).rstrip(".") + ".")
        if lives:
            lines.append(f"다음 하트까지 약 {lives['minutes']:g}분입니다." if lives.get("minutes") is not None
                         else "다음 하트까지 남은 시간은 화면에 없었습니다.")
        if later:
            lines.append(f"'{later.get('task')}' 작업은 아직 할 수 없습니다: 약 {later.get('seconds')}초 뒤에 다시 됩니다.")
        if choice and not choice.get("sent", True):
            lines.append(f"이야기가 묻습니다: {choice.get('asks') or ''} 선택지: {' / '.join(choice.get('options') or ())}. "
                         "되돌릴 수 없는 선택이라 고르지 않았습니다.")
    else:
        lines = [f"Done: {_why(outcome, reason, ko, work.get('detail') or '')}."]
        if target is not None and not counted:
            lines.append(f"Reached {reached if reached is not None else '?'} of the goal's {target}.")
        if levels and levels.get("until") is not None:
            lines.append(f"Up to level {levels['until']}: won {levels['won']}, lost {levels['lost']}."
                         + (f" The last level the screen showed cleared is {levels['through']}."
                            if levels.get("through") is not None else ""))
        elif levels:
            lines.append(f"Levels won {levels['won']} of {levels['wanted']}, lost {levels['lost']}.")
        if falling.get("pieces") is not None:
            lines.append(("In the last level: " if levels else "") + f"{falling['pieces']} pieces placed, {falling.get('lines', 0)} lines cleared.")
        lines.append(f"{inputs} inputs ({moves} moves confirmed)" + (f", {seconds:g} s" if isinstance(seconds, (int, float)) else "")
                     + (f", used: {', '.join(f'{name} {count}' for name, count in used.items())}" if used
                        else ", nothing used beside the board") + ".")
        if family:
            lines.append(f"The game was played as {family}.")
        if stopped.get("screen") or stopped.get("situation"):
            lines.append("Stopped at: " + " — ".join(part for part in (stopped.get("screen"), stopped.get("situation")) if part).rstrip(".") + ".")
        if lives:
            lines.append(f"The next life comes in about {lives['minutes']:g} min." if lives.get("minutes") is not None
                         else "The screen did not show when the next life comes.")
        if later:
            lines.append(f"The errand '{later.get('task')}' is not offered yet: again in about {later.get('seconds')} s.")
        if choice and not choice.get("sent", True):
            lines.append(f"The story asks: {choice.get('asks') or ''} Options: {' / '.join(choice.get('options') or ())}. "
                         "It cannot be taken back, so nothing was chosen.")
    return " ".join(line for line in lines if line)


REFUSED = {
    "no_game": ("주소나 게임을 찾지 못해 시작하지 않았습니다. 웹 주소나 설치된 앱의 이름을 함께 말해 주세요.",
                "I found no address or game in that, so nothing was started. Name a page's address or an installed app."),
    "web_only": ("지금은 웹 주소가 있는 요청만 받습니다.", "Only requests with a page's address are taken now."),
    "not_public": ("공개 주소가 아니라 열지 않았습니다.", "That address is not a public one, so it was not opened."),
    "in_use": ("다른 실행이 기기를 쓰는 중이라 시작하지 않았습니다.", "Another run is using the device, so nothing was started."),
    "busy": ("지금 판이 도는 중이라 새 게임은 시작하지 않습니다. 이 판이 끝난 뒤에 다시 말해 주세요(멈추려면 중지).",
             "A game is being played, so no new one is started. Ask again once it is over (or stop it)."),
    "day_limit": ("오늘의 판 수 한도에 닿아 시작하지 않았습니다.", "Today's games are used up, so nothing was started."),
    "chat_limit": ("오늘의 대화 한도에 닿았습니다.", "Today's chat questions are used up."),
    "idle": ("지금 도는 판이 없습니다.", "No game is being played now."),
    "stopping": ("멈추는 중입니다: 다음 입력 앞에서 멈춥니다.", "Stopping: the game ends before its next input."),
    "error": ("오류로 끝났습니다", "It ended in an error"),
    "no_answer": ("지금은 답하지 못했습니다(모델 질문이 실패함).", "I could not answer just now (the model question failed)."),
}


def said(name: str, ko: bool, detail: str = "") -> str:
    text = REFUSED[name][0 if ko else 1]
    return f"{text}: {detail}" if detail else text
