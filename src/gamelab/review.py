"""Looking back over a work played by the model's own inputs (PLAN §27 M11, §36 E8).

What a model playing a game keeps for itself (`direct.MEMORY`) is written move by move: `failed` holds an input
that did nothing, `run` where a long game stands. Nothing looked at a work as a whole: why health went where it
went, which fight or choice cost the most, what to do otherwise next time. So the same loss could come again in
every work.

Once a work ends (its goal reached, the game over, its budget spent), when the model asked enough questions in it,
it is asked once more with the work's own record: how it ended, what the screen said as play went on, the inputs
with what each was to do, the fights the program read and searched (health at each read, what it foresaw), the
memory, the lessons given before and how often each was followed, and the last screen. It answers what happened,
what it cost and why, and the game's lessons anew: at most `LESSONS_MOST` short rules, the earlier ones kept where
the record bears them out. They are kept by the game (`memory.json`, `lessons`, beside the last reviews) and given
with every question of direct play in later works (`direct.Direct.lessons`), where an input that follows one
names it (`lesson`), so that whether they are used is counted.

The question is not of the board's budget (it comes after it is spent), at most `TOKENS_MOST` input tokens.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from . import recovery, resources
from .model import Model, Msg, TurnRequest

log = logging.getLogger(__name__)

# a work whose model asked fewer questions than this is not looked back on: it played too little to tell
QUESTIONS_LEAST = 4
# lessons kept by a game, each this long; the reviews kept beside them, newest last
LESSONS_MOST, LESSON_CHARS, REVIEWS_KEPT, SAID_CHARS = 8, 200, 6, 300
# what of the work is told: the last inputs, the situations as they changed, the fight's reads
TRAIL_MOST, SITUATIONS_MOST, READS_MOST = 60, 30, 30
TOKENS_MOST, ANSWER_TOKENS, TIMEOUT_S, VIEW_SIDE = 20_000, 1500, 120.0, 768


def lessons(said: Any) -> list[str]:
    """The lessons as they are kept: short texts, at most `LESSONS_MOST`."""
    if not isinstance(said, list):
        return []
    return [text.strip()[:LESSON_CHARS] for text in said if isinstance(text, str) and text.strip()][:LESSONS_MOST]


def _entry(entry: dict[str, Any]) -> dict[str, Any]:
    """One input of the work as the review is told it."""
    action = entry.get("action") or {}
    told = {"input": action.get("kind") if isinstance(action, dict) else action,
            **({"scene": entry["scene"]} if entry.get("scene") else {}),
            **({"expected": entry["expected"]} if entry.get("expected") else {}),
            "changed": bool(entry.get("screen_changed"))}
    for key in ("sent_by", "fight", "refused", "lesson", "choice"):
        if entry.get(key) not in (None, "", [], {}):
            told[key] = entry[key]
    return told


def told(player: Any, goal: dict[str, Any], outcome: str, reason: str, detail: str) -> dict[str, Any]:
    """The work's record as the review question is given it."""
    fight = player.fight.account
    return {"review": True, "goal": goal, "outcome": outcome, "reason": reason, **({"detail": detail[:SAID_CHARS]} if detail else {}),
            "questions": player.questions,
            **({"score_first": player.first_score, "score_last": player.score} if player.score is not None else {}),
            "situations": player.situations[-SITUATIONS_MOST:],
            "inputs": [_entry(entry) for entry in player.history[-TRAIL_MOST:]],
            **({"fight": {"reads": fight["reads"], "turns_by_program": fight["turns"], "cards_by_program": fight["cards"],
                          "health_at_reads": fight["hp"][-READS_MOST:], "left_to_you": fight["fell_back"],
                          "rules_dropped": fight["dropped"][-8:]}} if fight["reads"] else {}),
            "memory": {part: text for part, text in player.memory.items()},
            "lessons_before": [{"number": number, "lesson": text, "followed": player.followed.get(number, 0)}
                               for number, text in enumerate(player.lessons, 1)]}


async def look_back(model: Model, record: dict[str, Any], png: bytes | None) -> dict[str, Any] | None:
    """One question over the work's record (`told`) and its last screen: `{"went", "cost", "lessons"}`, None when
    the model gave no lessons."""
    prompt = (resources.FRAGMENT / "prompts" / "review_work.md").read_text(encoding="utf-8")
    shown = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                          "data": base64.b64encode(recovery.view(png, VIEW_SIDE)[0]).decode("ascii")}}] if png else []
    result = await model.complete(TurnRequest(
        system_stable=prompt, name="review_work", max_tokens=ANSWER_TOKENS, timeout_s=TIMEOUT_S,
        messages=[Msg("user", [{"type": "text", "text": json.dumps(record, ensure_ascii=False)}, *shown])]))
    try:
        answer = json.loads(result.text or "")
    except ValueError:
        return None
    if not isinstance(answer, dict) or not lessons(answer.get("lessons")):
        return None
    return {"went": str(answer.get("went") or "")[:SAID_CHARS], "cost": str(answer.get("cost") or "")[:SAID_CHARS],
            "lessons": lessons(answer.get("lessons"))}


def kept(reviews: Any, answer: dict[str, Any], outcome: str, reason: str) -> list[dict[str, Any]]:
    """The reviews kept by the game with this one last (`REVIEWS_KEPT`)."""
    old = [one for one in reviews if isinstance(one, dict)] if isinstance(reviews, list) else []
    return (old + [{"outcome": outcome, "reason": reason, "went": answer["went"], "cost": answer["cost"]}])[-REVIEWS_KEPT:]
