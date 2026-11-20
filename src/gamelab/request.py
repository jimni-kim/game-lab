"""A request in a person's words turned into what one game is asked for (PLAN §10, G12; §14, D1).

One question: the model reads the words and answers with the address they give or the installed app they
mean, the objective and its number, and what they say of the game. The host checks the answer; nothing the
model says is played without that. A page is played in the browser, an app on the device; a request that
names neither is not taken. The words about the game go on to the loop as its `hint`.

The answer also gives the reach of the work (PLAN §25, R1 and R2): whether it goes on from the screen as it
stands or starts the game again, when to stop short of the objective, and which things beside the board that
cost something the person allowed. A permission is taken only by its name among `META_ACTIONS`; none is the
default, and stopping is the person's to say in their own words.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from . import resources, routine
from .contracts import LOST, META_ACTIONS, OBJECTIVES, GoalSpec, Scope
from .model import Model, Msg, TurnRequest

ASK_TOKENS, ASK_TIMEOUT_S = 1000, 60.0
WITHIN_MOST = 40
# an errand's name as the routine keeps its steps under it (`routine.Book`, a file of the game's store): a few plain
# words joined by underscores, none of the names the routine keeps for itself
TASK_NAME = re.compile(r"[a-z][a-z0-9_]{0,23}")
MOST = 2000
# the conditions to stop at a request may give, and how much of each one's words
STOPS_MOST, STOP_MOST = 4, 120
# the installed apps a model is shown at most, and how much of each one's name
APPS_MOST, LABEL_MOST = 200, 80


async def understand(model: Model, words: str, apps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """`{"url", "app", "within", "task", "goal", "hint"}` for a request: the page it names, else the app of `apps`
    (each `{"package", "label"}`, the device's) it means, None for whichever it does not; the one game it picks
    inside a page or app that holds several (None when the page or app is one game); the one errand it asks for
    beside the game's levels, by a plain name (None when it asks to play); the goal it asks for (the game to its
    end when it asks for nothing the host can count), and its words about the game."""
    prompt = (resources.FRAGMENT / "prompts" / "ask_goal.md").read_text(encoding="utf-8")
    shown = [{"package": app["package"], "label": (app.get("label") or app["package"])[:LABEL_MOST]}
             for app in (apps or [])[:APPS_MOST]]
    context = {"request": words[:MOST], "objectives": list(OBJECTIVES), "scopes": [str(scope) for scope in Scope],
               "meta_actions": META_ACTIONS, "lost": LOST, **({"apps": shown} if apps is not None else {})}
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
    hint, within = answer.get("game"), answer.get("within")
    within = within.strip()[:WITHIN_MOST] if isinstance(within, str) and within.strip() else None
    # a name that only says again what the app or the address already does picks no game inside it
    label = next((shown_app["label"] for shown_app in shown if shown_app["package"] == app), "")
    held = _plain(f"{app or ''}{label}{url if named else ''}")
    if within is not None and (not _plain(within) or _plain(within) in held):
        within = None
    return {"url": url if named else None, "app": app,
            # the one game of several the page or app holds, by a plain name: what is kept of it is kept apart from
            # the others' (`adaptation.keyed`)
            "within": within,
            # an errand beside the levels (a daily reward): its steps are kept as a routine of their own (`routine`)
            "task": _task(answer.get("task")),
            "goal": GoalSpec(objective, target if objective != "play_until_end" else None, scope=_scope(answer.get("scope")),
                             stop_conditions=_stops(answer.get("stop")), allowed_meta_actions=_allowed(answer.get("allowed"))),
            "hint": hint[:200] if isinstance(hint, str) and hint.strip() else None}


def _plain(text: str) -> str:
    """A name by its letters and digits alone, whatever its case."""
    return "".join(letter for letter in text.casefold() if letter.isalnum())


def _task(value: Any) -> str | None:
    """An errand's name, the same however it was spaced or cased; anything else, or a name the routine keeps for
    itself, is none."""
    if not isinstance(value, str):
        return None
    name = re.sub(r"[\s-]+", "_", value.strip().lower())
    return name if TASK_NAME.fullmatch(name) and name not in (routine.NEXT_LEVEL, routine.NEW_GAME) else None


def _scope(value: Any) -> Scope:
    """From the screen as it stands only when the request says so; else the game is started again, as a run is."""
    return Scope.CURRENT_GAME if value == Scope.CURRENT_GAME else Scope.NEW_GAME


def _stops(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    said = [one.strip()[:STOP_MOST] for one in value if isinstance(one, str) and one.strip()]
    return tuple(dict.fromkeys(said))[:STOPS_MOST]


def _allowed(value: Any) -> frozenset[str]:
    """The permissions named, those the host knows; a name it does not know is no permission."""
    return frozenset(name for name in value if isinstance(name, str) and name in META_ACTIONS) \
        if isinstance(value, list) else frozenset()
