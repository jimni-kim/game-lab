"""A request in a person's words turned into what one game is asked for (PLAN §10, G12).

One question: the model reads the words and answers with the address they give, the objective and its
number, and what they say of the game. The host checks the answer; nothing the model says is played without
that. The words about the game go on to the loop as its `hint`.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from . import resources
from .contracts import GoalSpec
from .model import Model, Msg, TurnRequest

OBJECTIVES = ("play_until_end", "reach_value", "actions")
ASK_TOKENS, ASK_TIMEOUT_S = 1000, 60.0
MOST = 2000


async def understand(model: Model, words: str) -> dict[str, Any]:
    """`{"url", "goal", "hint"}` for a request: the page it names (None when it names none), the goal it asks
    for (the game to its end when it asks for nothing the host can count), and its words about the game."""
    prompt = (resources.FRAGMENT / "prompts" / "ask_goal.md").read_text(encoding="utf-8")
    context = {"request": words[:MOST], "objectives": list(OBJECTIVES)}
    result = await model.complete(TurnRequest(system_stable=prompt, max_tokens=ASK_TOKENS, timeout_s=ASK_TIMEOUT_S,
                                              messages=[Msg("user", [{"type": "text", "text": json.dumps(context)}])]))
    try:
        answer = json.loads(result.text or "")
    except ValueError:
        answer = None
    answer = answer if isinstance(answer, dict) else {}
    objective, target = answer.get("objective"), answer.get("target_value")
    if objective not in OBJECTIVES or (objective != "play_until_end" and (type(target) is not int or target < 1)):
        objective, target = "play_until_end", None
    url = answer.get("url")
    parts = urlsplit(url) if isinstance(url, str) else None
    # an address is taken only when the request itself carries its host
    named = parts is not None and parts.scheme in ("http", "https") and bool(parts.netloc) and parts.netloc.lower() in words.lower()
    hint = answer.get("game")
    return {"url": url if named else None, "goal": GoalSpec(objective, target if objective != "play_until_end" else None),
            "hint": hint[:200] if isinstance(hint, str) and hint.strip() else None}
