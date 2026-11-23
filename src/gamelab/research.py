"""What is found out of a game before it is played, when no family is known for it (PLAN §27 M8, §36 E6).

A game the harness has no rules for was played from the first screen on, the model making out the controls, the
rules and what wins by trying them on the board, and a game is never set back, so every such try was play. Before
the first move the game is now looked up once, in two places, apart from the board's budget:

- outside the game (`outside`): its store page (a phone app's Play Store description, fetched by the app's id
  alone) and an encyclopedia's summary of it (Wikipedia, searched by the game's title alone), given to the model
  with what it knows of the game itself; it answers the parts `PARTS` in short. Nothing of the person goes out.
- inside the game, off its boards (`direct.Direct`, while `looking`): the model is asked to open what the game
  itself tells of how it is played (a help, how-to-play, rules, a codex) on the screens before the first level,
  read it, put it in the same parts and close it again, and never to try a move on a board to see what it does.

What is found is kept by the game (`adaptation.Store.research`) and given with every question of direct play, in
this work and the later ones: what came from outside as not confirmed (the screen is right where they differ),
what was read in the game as read there. The tokens spent on it are kept apart from the board's (`spent`), up to
`TOKENS_MOST`."""
from __future__ import annotations

import html
import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import resources
from .model import Model, Msg, TurnRequest

log = logging.getLogger(__name__)

# what is found of a game, each part with what it is to hold
PARTS = {
    "controls": "how the game is played with the screen or keys: what a tap, a drag or a key does where",
    "rules": "the game's rules in short: what a move or turn is and what follows from it",
    "win": "what wins a level, a run or the game, and what loses it",
    "avoid": "what to stay away from: moves or choices that lose, waste or cannot be taken back",
}
TERMS = "terms"
PART_MOST, TERMS_MOST, TERM_NAME, TERM_TEXT = 400, 16, 40, 120
# what is given of the store page and of the encyclopedia, at most
STORE_CHARS, WIKI_CHARS = 3000, 1500
OUTSIDE_TOKENS, OUTSIDE_TIMEOUT_S, FETCH_TIMEOUT_S = 1500, 90.0, 15.0
# the questions of direct play that may go to looking in the game before its first level, and the tokens of all
# the research of one game (outside and in the game) that are not counted against the board's budget
QUESTIONS_MOST, TOKENS_MOST = 6, 60_000
STORE_URL = "https://play.google.com/store/apps/details"
WIKI_URL = "https://en.wikipedia.org/w/api.php"
AGENT = "game-lab research (personal research harness)"


def package(address: str) -> str | None:
    """The app's id where the game is a phone app (`android://<id>`)."""
    parts = urlsplit(address)
    return parts.netloc or None if parts.scheme == "android" else None


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<br\s*/?>", "\n", re.sub(r"<[^>]+>", " ", fragment)))).strip()


def store_page(page: str) -> dict[str, str] | None:
    """The title and description a Play Store page shows."""
    title = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    described = re.search(r'data-g-id="description"[^>]*>(.*?)</div>', page, re.S) \
        or re.search(r'<meta property="og:description" content="([^"]*)"', page)
    if not title and not described:
        return None
    name = html.unescape(title.group(1)).removesuffix(" - Apps on Google Play").strip() if title else ""
    return {"title": name, "description": _text(described.group(1))[:STORE_CHARS] if described else ""}


async def fetch_store(client: httpx.AsyncClient, app: str) -> dict[str, str] | None:
    response = await client.get(STORE_URL, params={"id": app, "hl": "en", "gl": "US"})
    return store_page(response.text) if response.status_code == 200 else None


async def fetch_wiki(client: httpx.AsyncClient, title: str) -> dict[str, str] | None:
    """The opening of the encyclopedia's article the title finds first as a game, if any."""
    found = (await client.get(WIKI_URL, params={"action": "query", "list": "search", "srsearch": f"{title} video game",
                                                "srlimit": 1, "format": "json"})).json()
    hits = found.get("query", {}).get("search", [])
    if not hits:
        return None
    pages = (await client.get(WIKI_URL, params={"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
                                                "titles": hits[0]["title"], "format": "json"})).json()
    for page in pages.get("query", {}).get("pages", {}).values():
        if page.get("extract"):
            return {"title": page.get("title", ""), "extract": page["extract"][:WIKI_CHARS]}
    return None


async def gather(address: str, hint: str | None, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """What can be read of the game outside it: `{"store", "encyclopedia"}`, either missing when none was found.
    Only the app's id and the game's title are sent anywhere."""
    found: dict[str, Any] = {}
    own = client is None
    client = client or httpx.AsyncClient(timeout=FETCH_TIMEOUT_S, follow_redirects=True,
                                         headers={"User-Agent": AGENT})
    try:
        app = package(address)
        if app:
            try:
                found["store"] = await fetch_store(client, app)
            except (httpx.HTTPError, ValueError) as error:
                log.info("the store page could not be read: %s", error)
        title = (found.get("store") or {}).get("title") or hint
        if title:
            try:
                found["encyclopedia"] = await fetch_wiki(client, title)
            except (httpx.HTTPError, ValueError, KeyError) as error:
                log.info("the encyclopedia could not be read: %s", error)
    finally:
        if own:
            await client.aclose()
    return {name: value for name, value in found.items() if value}


def parts(said: Any) -> dict[str, Any]:
    """What a model said of a game, as it is kept: the parts of `PARTS` in short, and the game's own words
    (`terms`) each with what it means."""
    if not isinstance(said, dict):
        return {}
    kept: dict[str, Any] = {part: said[part].strip()[:PART_MOST] for part in PARTS
                            if isinstance(said.get(part), str) and said[part].strip()}
    terms = said.get(TERMS)
    if isinstance(terms, dict):
        named = {name.strip()[:TERM_NAME]: text.strip()[:TERM_TEXT] for name, text in terms.items()
                 if isinstance(name, str) and isinstance(text, str) and name.strip() and text.strip()}
        if named:
            kept[TERMS] = dict(list(named.items())[:TERMS_MOST])
    return kept


def merged(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """`new`'s parts take the place of `old`'s, its terms are added to them."""
    terms = {**(old.get(TERMS) or {}), **(new.get(TERMS) or {})}
    return {**old, **new, **({TERMS: dict(list(terms.items())[-TERMS_MOST:])} if terms else {})}


async def outside(model: Model, address: str, hint: str | None, found: dict[str, Any]) -> dict[str, Any]:
    """One question: what the store page, the encyclopedia and the model's own knowledge say of the game, in
    the parts of `PARTS` (`{}` when it answered none). `sources`: what it went by."""
    prompt = (resources.FRAGMENT / "prompts" / "research_game.md").read_text(encoding="utf-8")
    asked = {"research": True, "address": address, **({"hint": hint} if hint else {}), **found,
             "parts": {**PARTS, TERMS: "the game's own words for its things (a resource, a status, a piece) with what each means"}}
    result = await model.complete(TurnRequest(
        system_stable=prompt, name="research_game", max_tokens=OUTSIDE_TOKENS, timeout_s=OUTSIDE_TIMEOUT_S,
        messages=[Msg("user", [{"type": "text", "text": json.dumps(asked, ensure_ascii=False)}])]))
    try:
        answer = json.loads(result.text or "")
    except ValueError:
        return {}
    if not isinstance(answer, dict) or answer.get("known") is False:
        return {}
    kept = parts(answer)
    if not kept:
        return {}
    sources = [name for name in ("store", "encyclopedia", "own_knowledge")
               if name in (answer.get("sources") or []) and (name == "own_knowledge" or name in found)]
    return {**kept, "sources": sources, **({"game": answer["game"][:80]} if isinstance(answer.get("game"), str) else {})}


def told(kept: dict[str, Any] | None) -> dict[str, Any] | None:
    """What of the research is given to the model that plays: from outside as not confirmed, from the game's own
    screens as read there."""
    if not kept:
        return None
    given = {**({"unconfirmed": {name: value for name, value in kept["outside"].items() if name in PARTS or name == TERMS}}
                if kept.get("outside") else {}),
             **({"read_in_game": kept["inside"]} if kept.get("inside") else {})}
    return given or None
