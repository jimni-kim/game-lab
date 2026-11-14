"""Play game sites end to end in Chromium and measure the harness ("real screen → input").

Each game runs the real path: the session, the site's adapter, the solver, and a Playwright page for
looks and inputs. Setup steps (reloading the page for a new game) stand in for the person starting a
game and are not counted as the harness's moves.

  .venv/bin/python -m gamelab.eval web --profile ovolve_2048 --games 3 \\
      --objective reach_value --target 2048 --compute 500 --out out/ovolve

The bounded model reader asks `--vision-model` (AGENTIC_OPENAI_MODEL unless given; a name the LiteLLM proxy
serves, or another OpenAI-compatible API's with `--model-base`; the key is AGENTIC_OPENAI_KEY) about screens
the code cannot read; with `--vision-model ''` no model is asked and such a screen stays unread.

`--perturb KIND@N[:ARG]` (repeatable) disturbs a game after the harness's Nth input, as a person or a site
would; the harness is not told:

  resize@N:WxH          the window resized
  cover@N               a dialog over the page, closed by its own Close button
  cover_bare@N          the same dialog with no button
  consent@N             a request to agree to privacy terms, with Reject all and Agree
  consent_agree@N       the same request with only Agree (pressed only with --accept-consent)
  tab@N[:URL]           the person opens a new tab (about:blank unless given) and brings it to the front
  navigate@N[:URL]      the game's tab goes to another address (about:blank unless given)
  user@N:KEY            the person presses a key, or drags with user@N:X0,Y0,X1,Y1
  cancel@N[:during]     the run is cancelled after the Nth input, or while it is being carried out
  filter@N:SECONDS      the page's colours turned for this long, a screen no reader knows

N counts every input the harness sent, never the setup's.

The site's answer key (`sites.json`, read from the page's DOM) only scores the run; the harness never
sees it. A site whose board the page does not expose (a canvas) may name instead where the page states
the game's end (`ending`), read once the game is over and compared with the end the harness judged
(`page_end`, `end_agrees`). The registered sites are development sites: their results are not evidence
that an unregistered site would play.

A site no profile registers is evaluated with `--site NAME` alone: its entry names the page (`url`) and
the game's family (`family`), and the harness is given only the family, as with `--unregistered`.

  .venv/bin/python -m gamelab.eval web --site march2048_4x4 --games 3 \\
      --objective reach_value --target 2048 --compute 500 --out out/march
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from pathlib import Path

from .. import agent, baseline, profiles
from ..adaptation import Store
from ..environments.web import WebSurface, browser
from ..play import play
from . import measure

SITES = files(__package__) / "sites.json"
# setup waits for the page's document, not every ad frame: some sites never fire "load"; the
# setup's own wait_ms steps give the game its time to start
SETUP_LOADED = "domcontentloaded"
# a setup drag (the person's hand in a tutorial) moves in this many steps, this far apart
SETUP_DRAG_STEPS = 20
SETUP_DRAG_STEP_MS = 25
# a resized page redraws its game: wait this long before the harness looks
RESIZE_WAIT_MS = 2500
# what --perturb can do, and the argument each takes: required, optional, or none
PERTURBATIONS = {"resize": "required", "cover": None, "cover_bare": None, "consent": None, "consent_agree": None,
                 "tab": "optional", "navigate": "optional", "user": "required", "cancel": "optional", "filter": "required"}
# a request to agree to privacy terms over the page, with a refusal or (consent_agree) only an agreeing button
CONSENT = """(refuse) => {
  const cover = document.createElement('div');
  cover.id = 'game-eval-consent';
  cover.style.cssText = 'position:fixed;left:15%;top:20%;width:70%;height:60%;z-index:2147483647;background:#fff;'
    + 'border:2px solid #333;display:flex;flex-direction:column;align-items:center;justify-content:center;'
    + 'color:#222;visibility:visible!important;opacity:1!important;pointer-events:auto!important';
  cover.innerHTML = '<h2 style="font:26px sans-serif">We value your privacy</h2><p style="font:17px sans-serif;max-width:80%">'
    + 'We and our partners store and access information on your device, such as cookies, for personalised advertising '
    + 'and measurement. You may agree to this processing' + (refuse ? ' or refuse it.' : '.') + '</p>';
  const row = document.createElement('div');
  row.style.cssText = 'display:flex;gap:24px;margin-top:20px';
  const add = (label, answer) => {
    const button = document.createElement('button');
    button.textContent = label;
    button.style.cssText = 'font:20px sans-serif;padding:12px 32px';
    button.onclick = () => { window.gameEvalConsent = answer; cover.remove(); };
    row.appendChild(button);
  };
  if (refuse) add('Reject all', 'refused');
  add('Agree', 'agreed');
  cover.appendChild(row);
  document.body.appendChild(cover);
}"""
# where tab and navigate go unless told: a page with no game on it
ELSEWHERE = "about:blank"
# a dialog over the middle of the page, above everything the site draws; with a button, the button removes it
COVER = """(button) => {
  const cover = document.createElement('div');
  cover.id = 'game-eval-cover';
  // important: a site may hide what it did not put on the page (play2048.co sets visibility, opacity and pointer events)
  cover.style.cssText = 'position:fixed;left:15%;top:20%;width:70%;height:60%;z-index:2147483647;background:#fff;'
    + 'border:2px solid #333;display:flex;flex-direction:column;align-items:center;justify-content:center;'
    + 'font:28px sans-serif;color:#222;visibility:visible!important;opacity:1!important;pointer-events:auto!important';
  cover.textContent = 'Special offer';
  if (button) {
    const close = document.createElement('button');
    close.textContent = 'Close';
    close.style.cssText = 'margin-top:32px;font:22px sans-serif;padding:12px 40px';
    close.onclick = () => cover.remove();
    cover.appendChild(close);
  }
  document.body.appendChild(cover);
}"""
# colours no reader has learned: the whole page, iframes too, turned half way round the colour wheel
UNREADABLE = "(filter) => { document.documentElement.style.filter = filter; }"
UNREADABLE_FILTER = "hue-rotate(180deg)"


@dataclass(frozen=True)
class Perturbation:
    kind: str
    after: int
    arg: str | None = None

    @property
    def on_send(self) -> bool:
        """It happens on the Nth input itself (cancelled while it is carried out)."""
        return (self.kind, self.arg) == ("cancel", "during")

    def drag(self) -> tuple[float, ...] | None:
        """A person's drag (x0, y0, x1, y1), when `user` was given four numbers rather than a key."""
        parts = (self.arg or "").split(",")
        return tuple(float(part) for part in parts) if self.kind == "user" and len(parts) == 4 else None


def perturbation(text: str) -> Perturbation:
    """`KIND@N[:ARG]`, checked here so a misspelt scenario fails before any game is set up."""
    head, _, arg = text.partition(":")
    kind, _, after = head.partition("@")
    if kind not in PERTURBATIONS or not after.isdigit() or int(after) < 1:
        raise argparse.ArgumentTypeError(f"{text}: KIND@N[:ARG] with KIND one of {', '.join(PERTURBATIONS)}, N >= 1")
    takes = PERTURBATIONS[kind]
    if (takes == "required" and not arg) or (takes is None and arg):
        raise argparse.ArgumentTypeError(f"{text}: {kind} {'needs' if takes else 'takes no'} argument")
    try:
        if kind == "resize":
            width, height = (int(part) for part in arg.split("x"))
            if width < 1 or height < 1:
                raise ValueError
        elif kind == "filter" and not float(arg) > 0:
            raise ValueError
        elif kind == "cancel" and arg not in ("", "during"):
            raise ValueError
        elif kind == "user" and "," in arg and Perturbation(kind, 1, arg).drag() is None:
            raise ValueError
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError(f"{text}: not an argument {kind} takes") from None
    return Perturbation(kind, int(after), arg or None)


class Game:
    """One game's page as the evaluation sees it: its answer key, and what disturbs it."""

    def __init__(self, page: Any, site: dict[str, Any], perturb: list[Perturbation], cancel: asyncio.Event) -> None:
        self.page, self.site, self.perturb, self.cancel = page, site, list(perturb), cancel
        self.perturbed: list[dict[str, Any]] = []
        self.opened: list[Any] = []
        self.later: list[asyncio.Task] = []

    async def oracle(self) -> dict[str, Any] | None:
        if not self.site.get("oracle"):
            return None
        try:
            return await self.page.evaluate(self.site["oracle"])
        except Exception:
            return None                  # a page gone elsewhere has no answer

    async def page_end(self) -> str | None:
        """The end the page itself states (`won`, `over`), or None while it states neither."""
        ending = self.site["ending"]
        found = self.page.locator(ending["selector"]).first
        # the page says it in the element's text, or in one of its attributes (`attribute`: a face's class)
        text = (await found.get_attribute(ending["attribute"]) if "attribute" in ending else await found.text_content()) or ""
        return next((end for end in ("won", "over") if ending[end] in text), None)

    def during(self, number: int) -> None:
        for due in (p for p in self.perturb if p.after == number and p.on_send):
            self._happened(due)
            measure.cancel_soon(self.cancel)

    async def after(self, number: int) -> None:
        for due in (p for p in self.perturb if p.after == number and not p.on_send):
            await self.disturb(due)

    def _happened(self, perturbation: Perturbation) -> None:
        self.perturbed.append({"kind": perturbation.kind, "after": perturbation.after, "arg": perturbation.arg,
                               "at_s": time.monotonic()})

    async def disturb(self, perturbation: Perturbation) -> None:
        """What a person or the site does to the game between two of the harness's inputs."""
        self._happened(perturbation)
        kind, arg, page = perturbation.kind, perturbation.arg, self.page
        if kind == "cancel":
            self.cancel.set()
        elif kind == "resize":
            await page.set_viewport_size(dict(zip(("width", "height"), map(int, arg.split("x")))))
        elif kind in ("consent", "consent_agree"):
            await page.evaluate(CONSENT, kind == "consent")
        elif kind in ("cover", "cover_bare"):
            await page.evaluate(COVER, kind == "cover")
        elif kind == "navigate":
            await page.goto(arg or ELSEWHERE, wait_until=SETUP_LOADED)
        elif kind == "user":
            drag = perturbation.drag()
            await (_drag(page, *drag) if drag else page.keyboard.press(arg))
        elif kind == "filter":
            await page.evaluate(UNREADABLE, UNREADABLE_FILTER)
            self.later.append(asyncio.create_task(self._unfilter(float(arg))))
        elif kind == "tab":
            opened = await page.context.new_page()
            self.opened.append(opened)
            if (arg or ELSEWHERE) != ELSEWHERE:
                await opened.goto(arg, wait_until=SETUP_LOADED)
            await opened.bring_to_front()

    async def _unfilter(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        await self.page.evaluate(UNREADABLE, "")

    async def tidy(self) -> None:
        """Undo what perturbations left behind that the next game's setup would not: tabs and timers."""
        for task in self.later:
            task.cancel()
        await asyncio.gather(*self.later, return_exceptions=True)
        for page in self.opened:
            await page.close()


async def setup(page: Any, url: str, site: dict[str, Any]) -> None:
    """Open the site in the site's window size and run its setup steps, then resize the window to
    `resize` when given (the harness measures the board wherever it is).
    `clear_storage` forgets what the site saved (a game it would resume), as a first visit;
    `local_storage` saves items as the site would have (a game it then opens)."""
    if page.url != url:
        await page.goto(url, wait_until=SETUP_LOADED)
    await page.set_viewport_size(dict(zip(("width", "height"), site["viewport"])))
    for step in site.get("setup", []):
        if step.get("clear_storage"):
            session = await page.context.new_cdp_session(page)
            await session.send("Storage.clearDataForOrigin", {"origin": step["clear_storage"], "storageTypes": "all"})
            await session.detach()
        if "local_storage" in step:
            await page.evaluate("(items) => { for (const [key, value] of Object.entries(items)) "
                                "localStorage.setItem(key, value); }", step["local_storage"])
        if step.get("reload"):
            await page.reload(wait_until=SETUP_LOADED)
        if "click" in step:
            await page.mouse.click(*step["click"])
        if "drag" in step:
            await _drag(page, *step["drag"])
        if "wait_ms" in step:
            await page.wait_for_timeout(step["wait_ms"])
    if site.get("resize"):
        await page.set_viewport_size(dict(zip(("width", "height"), site["resize"])))
        await page.wait_for_timeout(RESIZE_WAIT_MS)


async def _drag(page: Any, x0: float, y0: float, x1: float, y1: float) -> None:
    await page.mouse.move(x0, y0)
    await page.mouse.down()
    for step in range(1, SETUP_DRAG_STEPS + 1):
        await page.mouse.move(x0 + (x1 - x0) * step / SETUP_DRAG_STEPS, y0 + (y1 - y0) * step / SETUP_DRAG_STEPS)
        await page.wait_for_timeout(SETUP_DRAG_STEP_MS)
    await page.mouse.up()


def end_agrees(outcome: str | None, stated: str | None) -> bool:
    """Whether the harness's outcome is the end the page states: a goal reached by a win, a game over by a
    loss, and anything else while the page states no end."""
    return {"won": "goal_reached", "over": "game_over"}.get(stated) == outcome if stated else \
        outcome not in ("goal_reached", "game_over")


def perturbed(game: Game, recorder: measure.Recorder) -> list[dict[str, Any]]:
    """Each perturbation with what followed it: the inputs sent after it and how many were carried out."""
    rows = []
    for event in game.perturbed:
        later = [i for i in recorder.inputs if i["sent_s"] >= event["at_s"]]
        rows.append({key: event[key] for key in ("kind", "after", "arg")} | {
            "inputs_after": len(later), "delivered_after": sum(i["delivery"] == "delivered" for i in later),
            "seconds_to_next_input": round(later[0]["sent_s"] - event["at_s"], 1) if later else None})
    return rows


async def play_one(args: argparse.Namespace, number: int, page: Any, name: str, url: str,
                   profile: profiles.Profile | None, family_id: str, site: dict[str, Any], model: Any) -> dict[str, Any]:
    goal, budget = measure.goal_and_budget(args)
    name = f"{name}-{number:02d}"
    answered = len(model.answers) if model else 0
    started = time.monotonic()
    adapted: dict[str, Any] | None = None
    await setup(page, url, site)
    cancel = asyncio.Event()
    game = Game(page, site, args.perturb, cancel)
    recorder = measure.Recorder(name, truth=game.oracle, after=game.after, during=game.during)
    options = {"goal": goal, "budget": budget, "policy": args.policy, "model": model, "cancel": cancel, "watch": recorder,
               "accept_consent": args.accept_consent, "run_id": name}
    try:
        if args.generate:
            # the game is read by an adapter a model wrote for this site: the one kept for it or one made now
            # from the game's first moves, and made again whenever play stops at a screen it cannot read
            reader = options.pop("model")
            # by the agent's own loop, or (--loop session) the session-driven one kept as the baseline
            played_by = baseline.play_adaptive if getattr(args, "loop", None) == "session" else agent.play
            run, adapted = await played_by(WebSurface(page), reader, family_id, store=Store(args.adapter_store),
                                           fresh=args.regenerate, **options)
        else:
            run = await play(WebSurface(page), **options,
                             **({"profile": profile} if profile is not None else {"family_id": family_id}))
    finally:
        await game.tidy()
    elapsed = time.monotonic() - started
    stated = await game.page_end() if site.get("ending") else None
    summary = measure.result(
        name, run, recorder, model, seconds=elapsed, final=await game.oracle(), perturbed=perturbed(game, recorder),
        **({"adaptation": adapted} if adapted is not None else {}),
        **({"page_end": stated, "end_agrees": end_agrees(run.record.get("outcome"), stated)} if site.get("ending") else {}))
    request = {"profile_id": profile.id if profile else None, "family": family_id, "url": url,
               "goal": goal.to_json(), "budget": budget.to_json(), "policy": args.policy}
    measure.write_game(args.out, name, summary, request, run, recorder, model.answers[answered:] if model else [],
                       await page.screenshot(type="png", scale="css"))
    return summary


async def main(args: argparse.Namespace) -> int:
    if not (args.profile or args.site):
        raise SystemExit("give --profile, --site or both")
    site = json.loads(SITES.read_text(encoding="utf-8")).get(args.site or args.profile)
    if site is None:
        raise SystemExit(f"no evaluation setup for {args.site or args.profile} in sites.json")
    if args.viewport:
        site = {**site, "resize": args.viewport}
    registered = profiles.load(args.profile) if args.profile else None
    if args.identify and not args.generate:
        raise SystemExit("--identify plays by a generated adapter: give --generate")
    if registered is None and ("url" not in site or not (args.identify or "family" in site)):
        raise SystemExit(f"{args.site} in sites.json names no url and family: give --profile")
    url = site.get("url") or registered.data["source"]["url"]
    # with --identify nobody says which game the page shows: the harness finds out from the screen
    family_id = None if args.identify else (registered.family_id if registered else site["family"])
    # with --unregistered the harness is given the game's family and no profile, and makes one from a look;
    # the profile here only names the page
    profile = None if args.unregistered else registered
    client, model = measure.vision_model(args)
    if args.generate and model is None:
        raise SystemExit("--generate needs a model: name one with --vision-model")
    name = args.site or args.profile
    results = []
    try:
        async with browser(viewport=site["viewport"], headless=not args.headed, scale=args.scale) as page:
            for number in range(1, args.games + 1):
                result = await play_one(args, number, page, name, url, profile, family_id, site, model)
                results.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        if client is not None:
            await client.close()
        if results:
            measure.write_summary(args.out, args, results, profile=args.profile, site=args.site,
                                  profile_revision=registered.data["revision"] if registered else None)
    return 0


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", help="the registered game to play (else --site names a site with none)")
    parser.add_argument("--site", help="the setup in sites.json each game starts with (the profile's unless given)")
    measure.game_arguments(parser)
    parser.add_argument("--viewport", type=int, nargs=2, metavar=("WIDTH", "HEIGHT"),
                        help="resize the window to this after the site's setup, before the harness plays")
    parser.add_argument("--scale", type=float, help="the pages' device scale factor (devicePixelRatio)")
    parser.add_argument("--perturb", type=perturbation, action="append", default=[], metavar="KIND@N[:ARG]",
                        help="disturb each game after the harness's Nth input (repeatable; see above)")
    parser.add_argument("--unregistered", action="store_true",
                        help="give the harness no profile: it makes one from a look at the site")
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--generate", action="store_true",
                        help="read the game by an adapter a model writes for this site (no profile, no hand-written reader)")
    parser.add_argument("--adapter-store", type=Path, default=measure.ROOT / "out" / "adapters",
                        help="where generated adapters are kept by site and reused")
    parser.add_argument("--regenerate", action="store_true", help="write a new adapter even when one is kept for the site")
    parser.add_argument("--loop", choices=("agent", "session"),
                        help="with --generate: the loop that plays (the agent's own unless told; session: the one kept as baseline)")
    parser.add_argument("--identify", action="store_true",
                        help="with --generate: do not tell the harness the game's family; it is found from the screen")
