"""A demo page: a request in words is played on a web page or on the Android emulator, and watched live
(PLAN §14).

  .venv/bin/python -m gamelab.eval serve --compute 200
      serves http://127.0.0.1:8765 and prints the address to open it with, its access token included

The page has a box for a request ("https://… 에서 이 퍼즐을 끝까지 풀어줘", "퍼즐 앱에서 Fifteen 끝까지"), a
stop button, the game's screen as it is played and a log of what happens. A request is read by one model
question (`request.understand`): one that gives a page is played in Chromium, one that means an installed
app on the Android device, which is started first when none is attached (`emulator`). The game is the one
`eval web` and `eval android` play (`play_one`), with its files written under `--out` as theirs are.

One game at a time: a request while one is played is refused, never queued, and so is one past the day's
`--games-a-day`. Each game has a budget of inputs, seconds and model tokens. Stopping ends the game at its
next input. Seeing the page and asking both need the token printed at the start: opened once with `?k=`,
it is kept in a cookie and the address is shown without it. Requests and the live connection are taken only
from the server's own address (their Origin). A page a request named is opened only at a public address,
and everything it loads is held to public addresses (`guard`).

It needs aiohttp (`uv pip install -e ".[demo]"`). The server listens on this Mac alone; a tunnel
(`cloudflared`, see the README) is what lets others reach it.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from collections import deque
import hmac
from importlib.resources import files
from io import BytesIO
import json
from pathlib import Path
import secrets
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from .. import request
from ..environments.android import AndroidSurface
from ..environments.web import browser
from . import android, guard, measure, web
from .emulator import AVD, Emulator

PAGE = files(__package__) / "serve.html"
HOST, PORT = "127.0.0.1", 8765
COOKIE = "gamelab_demo"
# a request's words at most
WORDS_MOST = 2000
# the live screen: a page's frames this wide at most, the device's this wide; the device's screen is
# captured once a second more only while nothing else has captured it for IDLE_S (a model being asked)
WEB_WIDTH, ANDROID_WIDTH, JPEG_QUALITY = 1280, 540, 70
IDLE_S, IDLE_EVERY_S = 2.0, 1.0
# a game told to stop ends at its next input; one that has not ended this long after is cut short
STOP_GRACE_S = 30.0
# what of an input is shown in the log
WHERE = ("key", "x", "y", "x0", "y0", "x1", "y1", "at", "to", "points", "direction", "text")


def rounded(value: Any) -> Any:
    """A number in the log to a tenth of a pixel, through lists of points too."""
    if isinstance(value, float):
        return round(value, 1)
    return [rounded(item) for item in value] if isinstance(value, list) else value


def jpeg(png: bytes, width: int) -> bytes:
    """A capture as a JPEG no wider than `width`."""
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        picture = image.convert("RGB")
    if picture.width > width:
        picture = picture.resize((width, round(picture.height * width / picture.width)))
    out = BytesIO()
    picture.save(out, format="JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


class Pictures:
    """Captures turned into the live screen's JPEGs away from the loop, the latest one only: a capture
    waiting while another is made is replaced by the next."""

    def __init__(self, width: int, show: Callable[[bytes], None]) -> None:
        self.width, self.show = width, show
        self.waiting: bytes | None = None
        self.task: asyncio.Task | None = None

    def offer(self, png: bytes) -> None:
        self.waiting = png
        if self.task is None or self.task.done():
            self.task = asyncio.get_running_loop().create_task(self._make())

    async def _make(self) -> None:
        while self.waiting is not None:
            png, self.waiting = self.waiting, None
            try:
                made = await asyncio.to_thread(jpeg, png, self.width)
            except Exception:
                continue
            self.show(made)


class Shown:
    """A surface whose every whole capture is also shown live; everything else is the surface's own.

    It sits right on the device's surface, under what the loop wraps it in (`motion.Steady`), so what is
    shown is the screen as it was captured: the model's readings, the adapter's, recovery's and direct
    play's alike. A capture of a region only is not shown. While nothing has been captured for IDLE_S,
    `idle` captures once a second more (the loop waiting on a model)."""

    def __init__(self, surface: Any, offer: Callable[[bytes], None]) -> None:
        self.surface, self.offer = surface, offer
        self.last = time.monotonic()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.surface, name)

    async def capture(self, region: list[int] | None = None) -> bytes:
        png = await self.surface.capture(region)
        if region is None:
            self.last = time.monotonic()
            self.offer(png)
        return png

    async def idle(self, after_s: float = IDLE_S, every_s: float = IDLE_EVERY_S) -> None:
        """Until cancelled: a capture each `every_s` once `after_s` has passed with none."""
        while True:
            await asyncio.sleep(every_s)
            if time.monotonic() - self.last >= after_s:
                try:
                    self.offer(await self.surface.capture())
                except (ValueError, RuntimeError, OSError):
                    pass


class Watched:
    """The game's recorder, with each look and input also told as an event (`agent.Watch`)."""

    def __init__(self, recorder: measure.Recorder, tell: Callable[[dict[str, Any]], None]) -> None:
        self.recorder, self.tell = recorder, tell

    async def seen(self, png: bytes) -> None:
        await self.recorder.seen(png)
        # the picture is the live screen's: the log counts the looks only
        self.tell({"kind": "seen", "looks": self.recorder.shots})

    async def sending(self, number: int, action: dict[str, Any], state: dict[str, Any], sent: dict[str, Any],
                      observed_s: float) -> None:
        await self.recorder.sending(number, action, state, sent, observed_s)
        self.tell({"kind": "sending", "number": number, "input": sent.get("kind"),
                   "where": {key: rounded(sent[key]) for key in WHERE if key in sent}})

    async def sent(self) -> None:
        await self.recorder.sent()
        self.tell({"kind": "sent", "inputs": len(self.recorder.inputs)})


async def screencast(page: Any, show: Callable[[bytes], None], width: int = WEB_WIDTH) -> Any:
    """The page's own frames (CDP `Page.startScreencast`) shown as they come, each one acknowledged so the
    next is sent."""
    session = await page.context.new_cdp_session(page)

    async def ack(number: int) -> None:
        try:
            await session.send("Page.screencastFrameAck", {"sessionId": number})
        except Exception:
            pass                         # the page closed under it

    def frame(params: dict[str, Any]) -> None:
        show(base64.b64decode(params["data"]))
        asyncio.get_running_loop().create_task(ack(params["sessionId"]))

    session.on("Page.screencastFrame", frame)
    await session.send("Page.startScreencast", {"format": "jpeg", "quality": JPEG_QUALITY, "maxWidth": width,
                                                "maxHeight": width * 2, "everyNthFrame": 1})
    return session


class Viewer:
    """One person watching: the events not yet sent, and the latest frame only."""

    def __init__(self) -> None:
        self.events: deque[dict[str, Any]] = deque()
        self.frame: bytes | None = None
        self.wake = asyncio.Event()

    async def send(self, socket: Any) -> None:
        while not socket.closed:
            await self.wake.wait()
            self.wake.clear()
            while self.events:
                await socket.send_str(json.dumps(self.events.popleft(), ensure_ascii=False))
            if self.frame is not None:
                frame, self.frame = self.frame, None
                await socket.send_bytes(frame)


def played_on(out: Path, day: str) -> int:
    """The games a demo played on a day, by the files they left."""
    return len(list(out.glob(f"*-{day}-??????-01.json"))) if out.is_dir() else 0


class Demo:
    """The server's state: the game being played, the people watching it, and what may be asked."""

    def __init__(self, args: argparse.Namespace, model: Any, writer: Any = None, *, token: str | None = None,
                 emulator: Any = None, device: Callable[[str], Any] | None = None,
                 apps: Callable[[Any], Any] | None = None, look: guard.Resolve = guard.resolve,
                 connect: guard.Connect | None = None) -> None:
        self.args, self.model, self.writer = args, model, writer
        self.token = token or secrets.token_urlsafe(24)
        self.emulator = emulator or Emulator(args.avd)
        self.device = device or (lambda serial: AndroidSurface(serial=serial))
        self.apps = apps or (lambda surface: android.installed(surface.run))
        self.look, self.connect = look, connect
        self.origins = {origin.rstrip("/") for origin in args.origin}
        # this game's events from its request on, which someone who comes in later is sent first
        self.events: list[dict[str, Any]] = []
        self.viewers: set[Viewer] = set()
        self.frame: bytes | None = None
        self.game: asyncio.Task | None = None
        self.cancel: asyncio.Event | None = None
        self.day = time.strftime("%Y%m%d")
        self.today = played_on(args.out, self.day)

    # ── what everyone watching is sent ─────────────────────────────────

    def tell(self, event: dict[str, Any]) -> None:
        event = {**event, "at": round(time.time(), 2)}
        # the looks are counted, not listed: a run of them is one event
        if event["kind"] == "seen" and self.events and self.events[-1]["kind"] == "seen":
            self.events[-1] = event
        else:
            self.events.append(event)
        for viewer in self.viewers:
            viewer.events.append(event)
            viewer.wake.set()

    def show(self, frame: bytes) -> None:
        self.frame = frame
        for viewer in self.viewers:
            viewer.frame = frame
            viewer.wake.set()

    def status(self) -> dict[str, Any]:
        return {"kind": "status", "state": "playing" if self.playing else "idle",
                "left_today": max(0, self.args.games_a_day - self._played_today())}

    @property
    def playing(self) -> bool:
        return self.game is not None and not self.game.done()

    def _played_today(self) -> int:
        day = time.strftime("%Y%m%d")
        if day != self.day:
            self.day, self.today = day, 0
        return self.today

    # ── a game ────────────────────────────────────────────────────────

    def ask(self, words: str) -> str | None:
        """Start a game for the words; why not, when it is not started."""
        if self.playing:
            return "busy"
        if self._played_today() >= self.args.games_a_day:
            return "day_limit"
        self.today += 1
        self.events = []
        self.cancel = asyncio.Event()
        self.game = asyncio.get_running_loop().create_task(self.play(words, self.cancel))
        return None

    def stop(self) -> bool:
        if not self.playing:
            return False
        self.cancel.set()
        self.tell({"kind": "stopping"})
        asyncio.get_running_loop().create_task(self._cut(self.game))
        return True

    async def _cut(self, game: asyncio.Task) -> None:
        await asyncio.sleep(STOP_GRACE_S)
        if not game.done():
            game.cancel()

    def game_args(self, configure: Callable[[argparse.ArgumentParser], None], asked: dict[str, Any]) -> argparse.Namespace:
        """What `eval web` or `eval android` would be given for one game of this demo."""
        parser = argparse.ArgumentParser()
        configure(parser)
        served = self.args
        args = parser.parse_args(["--compute", str(served.compute), "--out", str(served.out),
                                  "--max-actions", str(served.max_actions), "--max-seconds", str(served.max_seconds),
                                  "--max-input-tokens", str(served.max_input_tokens),
                                  "--adapter-store", str(served.adapter_store),
                                  *(["--accept-consent"] if served.accept_consent else [])])
        args.asked = asked
        return args

    async def play(self, words: str, cancel: asyncio.Event) -> None:
        self.tell({"kind": "asked", "words": words})
        self.tell(self.status())
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            asked = await request.understand(self.model, words)
            surface = serial = None
            if asked["url"] is None and asked["hint"] is None:
                # words that name no page and say nothing of a game do not start the emulator
                self.tell({"kind": "refused", "reason": "no_game"})
                return
            if asked["url"] is None and self.args.web_only:
                # the device is not this demo's to move (another run measures on it)
                self.tell({"kind": "refused", "reason": "web_only"})
                return
            if asked["url"] is None:
                # no page: an app on the device, which is asked about again with the apps installed there
                serial = await self.emulator.ensure(lambda: self.tell({"kind": "opening", "where": "emulator"}))
                surface = self.device(serial)
                asked = await request.understand(self.model, words, await self.apps(surface))
            self.tell({"kind": "understood", "url": asked["url"], "app": asked["app"], "goal": asked["goal"].to_json(),
                       "hint": asked["hint"]})
            if asked["url"] is not None:
                summary = await self.web_game(asked, f"web-{stamp}", cancel)
            elif asked["app"] is not None:
                summary = await self.android_game(asked, f"app-{stamp}", serial, surface, cancel)
            else:
                self.tell({"kind": "refused", "reason": "no_game"})
                return
            adapted = summary.get("adaptation") or {}
            self.tell({"kind": "result", "outcome": summary["outcome"], "reason": summary["reason"],
                       "actions": summary["actions_used"], "confirmed": summary["confirmed_actions"],
                       "tokens": adapted.get("tokens") or summary.get("model_tokens"), "seconds": summary["seconds"],
                       "family": (adapted.get("identified") or {}).get("family"), "generations": adapted.get("generations"),
                       "name": summary["name"]})
        except guard.Refused:
            self.tell({"kind": "refused", "reason": "not_public"})
        except measure.Busy as busy:
            self.tell({"kind": "refused", "reason": "in_use", "detail": str(busy)})
        except asyncio.CancelledError:
            self.tell({"kind": "result", "outcome": "budget_exhausted", "reason": "cancelled", "cut": True})
            raise
        except Exception as error:
            self.tell({"kind": "error", "detail": f"{type(error).__name__}: {error}"[:500]})
        finally:
            self.game = None
            self.tell(self.status())

    def watching(self, recorder: measure.Recorder) -> Watched:
        return Watched(recorder, self.tell)

    async def web_game(self, asked: dict[str, Any], name: str, cancel: asyncio.Event) -> dict[str, Any]:
        args = self.game_args(web.configure, asked)
        _, site = web.site_for(args, asked)
        if not await guard.allowed(site["url"], self.look):
            raise guard.Refused(site["url"])
        self.tell({"kind": "opening", "where": "web"})
        async with web.guarded(site, self.look, self.connect) as held:
            async with browser(viewport=site["viewport"], headless=not self.args.headed, **held) as page:
                await screencast(page, self.show)
                return await web.play_one(args, 1, page, name, site["url"], None, site, self.model, self.writer,
                                          cancel=cancel, watching=self.watching)

    async def android_game(self, asked: dict[str, Any], name: str, serial: str, surface: Any,
                           cancel: asyncio.Event) -> dict[str, Any]:
        args = self.game_args(android.configure, asked)
        args.package = surface.package = asked["app"]
        with measure.lock(f"android-{serial}"):
            self.tell({"kind": "opening", "where": "android", "app": asked["app"]})
            shown = Shown(surface, Pictures(ANDROID_WIDTH, self.show).offer)
            idle = asyncio.get_running_loop().create_task(shown.idle())
            try:
                return await android.play_one(args, 1, shown, name, self.model, self.writer,
                                              steps=android.prepared(asked["app"]), cancel=cancel, watching=self.watching)
            finally:
                idle.cancel()

    async def close(self) -> None:
        """The game stopped and waited for, and the emulator shut down if this server started it."""
        if self.playing:
            game = self.game
            self.cancel.set()
            try:
                async with asyncio.timeout(STOP_GRACE_S):
                    await asyncio.shield(game)
            except (TimeoutError, asyncio.CancelledError, Exception):
                game.cancel()
                await asyncio.gather(game, return_exceptions=True)
        await self.emulator.stop()

    # ── the server ────────────────────────────────────────────────────

    def known(self, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(token.encode(), self.token.encode())

    def own(self, req: Any) -> bool:
        """Whether a request comes from the page this server gave: its Origin is the server's own address."""
        origin = req.headers.get("Origin")
        return bool(origin) and (urlsplit(origin).netloc == req.host or origin.rstrip("/") in self.origins)

    def application(self) -> Any:
        from aiohttp import web as http

        @http.middleware
        async def admitted(req: Any, handler: Any) -> Any:
            if "k" in req.query:
                if not self.known(req.query["k"]):
                    return http.Response(status=401, text="unknown token")
                # the token is kept in a cookie and the address shown without it: it stays out of history and Referer
                rest = {key: value for key, value in req.query.items() if key != "k"}
                answer = http.Response(status=303, headers={"Location": str(req.rel_url.with_query(rest))})
                answer.set_cookie(COOKIE, self.token, httponly=True, samesite="Strict", path="/",
                                  secure=req.secure or req.headers.get("X-Forwarded-Proto") == "https")
                return answer
            if not self.known(req.cookies.get(COOKIE)):
                return http.Response(status=401, text="open the address printed when the server started")
            if (req.method != "GET" or req.path == "/ws") and not self.own(req):
                return http.Response(status=403, text="not from this page")
            return await handler(req)

        async def page(req: Any) -> Any:
            return http.Response(text=PAGE.read_text(encoding="utf-8"), content_type="text/html", headers={
                "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                           "img-src blob:; connect-src 'self' ws: wss:; frame-ancestors 'none'"})

        async def socket(req: Any) -> Any:
            connection = http.WebSocketResponse(heartbeat=30)
            await connection.prepare(req)
            viewer = Viewer()
            viewer.events.extend([*self.events, self.status()])
            viewer.frame = self.frame
            viewer.wake.set()
            self.viewers.add(viewer)
            sending = asyncio.get_running_loop().create_task(viewer.send(connection))
            try:
                async for _ in connection:
                    pass                 # the page says nothing on it
            finally:
                self.viewers.discard(viewer)
                sending.cancel()
            return connection

        async def ask(req: Any) -> Any:
            try:
                words = (await req.json()).get("words")
            except (ValueError, AttributeError):
                words = None
            if not isinstance(words, str) or not words.strip() or len(words) > WORDS_MOST:
                return http.json_response({"refused": "words"}, status=400)
            why = self.ask(words.strip())
            if why is not None:
                return http.json_response({"refused": why}, status=409 if why == "busy" else 429)
            return http.json_response({"started": True}, status=202)

        async def stop(req: Any) -> Any:
            return http.json_response({"stopping": True}, status=202) if self.stop() else \
                http.json_response({"refused": "idle"}, status=409)

        async def closing(app: Any) -> None:
            await self.close()

        app = http.Application(middlewares=[admitted], client_max_size=64 * 1024)
        app.router.add_get("/", page)
        app.router.add_get("/ws", socket)
        app.router.add_post("/ask", ask)
        app.router.add_post("/stop", stop)
        app.on_cleanup.append(closing)
        return app


async def main(args: argparse.Namespace) -> int:
    try:
        from aiohttp import web as http
    except ImportError:
        raise SystemExit('the demo needs aiohttp: uv pip install -e ".[demo]"') from None
    client, model = measure.vision_model(args)
    if model is None:
        raise SystemExit("a game is played with a model: name one with --vision-model")
    writer_client, writer = measure.writer_model(args, model)
    demo = Demo(args, model, writer)
    runner = http.AppRunner(demo.application())
    await runner.setup()
    try:
        await http.TCPSite(runner, args.host, args.port).start()
        print(json.dumps({"serving": f"http://{args.host}:{args.port}/?k={demo.token}",
                          "out": str(args.out), "games_a_day": args.games_a_day}), flush=True)
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        for opened in (client, writer_client):
            if opened is not None:
                await opened.close()
    return 0


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=HOST, help="the address to listen on (this Mac alone unless changed)")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--origin", action="append", default=[], metavar="URL",
                        help="another address the page is opened at whose requests are taken (the Origin), beside "
                             "the Host a request names; a tunnel passes its own host on, so it needs none")
    parser.add_argument("--out", type=Path, default=measure.ROOT / "out" / "serve", help="each game's files")
    parser.add_argument("--compute", type=int, default=200, help="evaluated boards per decision")
    parser.add_argument("--max-actions", type=int, default=300, help="one game's inputs at most")
    parser.add_argument("--max-seconds", type=float, default=900, help="one game's seconds at most")
    parser.add_argument("--max-input-tokens", type=int, default=300_000, help="the tokens one game's model questions may send")
    parser.add_argument("--games-a-day", type=int, default=20, help="games played a day at most")
    parser.add_argument("--accept-consent", action="store_true",
                        help="agree to a site's terms when it offers no refusal (else the game stops there)")
    parser.add_argument("--adapter-store", type=Path, default=measure.ROOT / "out" / "adapters",
                        help="where generated adapters are kept by site and app and reused")
    parser.add_argument("--avd", default=AVD, help="the emulator started when no device is attached")
    parser.add_argument("--headed", action="store_true", help="show the browser window too")
    parser.add_argument("--web-only", action="store_true",
                        help="play pages only: a request for an app is refused and the device is never touched")
    measure.model_arguments(parser)
