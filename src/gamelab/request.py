"""A request in a person's words turned into what one game is asked for (PLAN §10, G12; §14, D1).

One question: the model reads the words and answers with the address they give or the installed app they
mean, the objective and its number, and what they say of the game. The host checks the answer; nothing the
model says is played without that. A page is played in the browser, an app on the device; a request that
names neither is not taken. The words about the game go on to the loop as its `hint`.
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
# the installed apps a model is shown at most, and how much of each one's name
APPS_MOST, LABEL_MOST = 200, 80


async def understand(model: Model, words: str, apps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """`{"url", "app", "goal", "hint"}` for a request: the page it names, else the app of `apps` (each
    `{"package", "label"}`, the device's) it means, None for whichever it does not; the goal it asks for
    (the game to its end when it asks for nothing the host can count), and its words about the game."""
    prompt = (resources.FRAGMENT / "prompts" / "ask_goal.md").read_text(encoding="utf-8")
    shown = [{"package": app["package"], "label": (app.get("label") or app["package"])[:LABEL_MOST]}
             for app in (apps or [])[:APPS_MOST]]
    context = {"request": words[:MOST], "objectives": list(OBJECTIVES), **({"apps": shown} if apps is not None else {})}
    result = await model.complete(TurnRequest(system_stable=prompt, max_tokens=ASK_TOKENS, timeout_s=ASK_TIMEOUT_S,
                                              messages=[Msg("user", [{"type": "text", "text": json.dumps(context, ensure_ascii=False)}])]))
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
    # an app only when it is one of those shown: the host's list, never a package the model thought of
    app = answer.get("app")
    app = app if not named and isinstance(app, str) and app in {shown_app["package"] for shown_app in shown} else None
    hint = answer.get("game")
    return {"url": url if named else None, "app": app,
            "goal": GoalSpec(objective, target if objective != "play_until_end" else None),
            "hint": hint[:200] if isinstance(hint, str) and hint.strip() else None}
