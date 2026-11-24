"""A demo page: a request in words is played on a web page or on the Android emulator, and watched live
(PLAN §14).

  .venv/bin/python -m gamelab.eval serve --compute 200
      serves http://127.0.0.1:8765 and prints the address to open it with, its access token included

The page has the game's screen as it is played on one side and a chat with the agent on the other (PLAN §35):
one box takes what a person types, which is sorted (`talk.sort`) into a game asked for ("https://… 에서 이 퍼즐을
끝까지 풀어줘", "퍼즐 앱에서 Fifteen 끝까지"), a question ("어디까지 했어?") or a stop. A game asked for is answered
with what will be done, made by code from the goal the host checked (`talk.promise`), and its end with what was
handed back (`talk.handed_back`); a question, while a game is played or after, is answered by a model of its own
(`--vision-model`, asked apart from the game's questions and budget) from the last screen shown, the goal, the
time, the inputs, the game's progress as the loop tells it (`agent.progress`) and its handback, never stopping
the loop or capturing the screen for it. Words that go on with the game played last and name no game ("10단계까지
깨줘") are sorted and read with that game (`previous`, `request.understand`'s `last`); a request that cannot be
started without a guess ("다리까지") is asked back once (`about: "clarify"`), nothing started, and the answer read
together with it. The chat is kept across games (`CHAT_KEPT` lines) and sent to whoever
comes in; what the loop did (the inputs, the looks) is a log under it. A request is read by one model
question (`request.understand`): one that gives a page is played in Chromium, one that means an installed
app on the Android device, the emulator running the AVD `--avd` (`game-lab`), found by its name and started
first when it is not running (`emulator`). The game is the one `eval web` and `eval android` play (`play_one`),
with its files written under `--out` as theirs are.

While no game is played and someone watches, the device is looked at once a second: its screen is shown when it
is on, and the page offers to start it when it is off (`POST /emulator`); a request for an app starts it too.
Served with `--touch`, that screen also takes a tap, a swipe, a back or a home from the page (`POST /touch`)
while no game is played: for what only a person may get past (an age check, a sign-in, a notice). Off by
default: whoever has the link and its token moves the device then, whatever is on it.

One game at a time: a request while one is played is refused, never queued, and so is one past the day's
`--games-a-day`; what is typed past the day's `--chats-a-day` is not answered. Each game has a budget of inputs, seconds and model tokens. Stopping ends the game at its
next input. Seeing the page and asking both need the token printed at the start (made anew each start, or fixed by
`--token` or `GAMELAB_DEMO_TOKEN` so a fixed address keeps its link): opened once with `?k=`,
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
import math
import os
from pathlib import Path
import re
import secrets
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from .. import model as models, request
from ..environments.android import AndroidSurface
from ..environments.web import browser
from ..execution import TAP_MS
from . import android, guard, measure, talk, web
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
# the demo's device: the AVD `game-lab`, the one a session measures on too (the games and their progress are there,
# PLAN §35; the device's lock keeps the two apart), looked at this often while no game is played and someone watches
DEMO_AVD, DEVICE_EVERY_S = AVD, 1.0
# a game told to stop ends at its next input; one that has not ended this long after is cut short
STOP_GRACE_S = 30.0
# what of an input is shown in the log
WHERE = ("key", "x", "y", "x0", "y0", "x1", "y1", "at", "to", "points", "direction", "text")
# the chat's lines kept (across games, while the server runs), and how many of them a question's answer is given
CHAT_KEPT, CHAT_TOLD = 200, 8
# the game's progress events kept, and given to a question's answer; the last inputs given to it
PROGRESS_KEPT, PROGRESS_TOLD, RECENT_TOLD = 60, 30, 5
# a fixed access token: from --token or this variable (`.env`), long enough and fit for an address
TOKEN_VARIABLE, TOKEN_SHORTEST = "GAMELAB_DEMO_TOKEN", 24
TOKEN_LETTERS = re.compile(r"[A-Za-z0-9_-]+")
# the events said in the chat; every other event is the game's own, kept from its request to the next
CHAT = ("said", "reply")
# a progress event's words at most
SITUATION_MOST = 300
# a person's input from the page (`--touch`): a tap's press (TAP_MS, the loop's own, unless said; longer is a long
# press) and a swipe's time, held to these; the keys it may send, as the device names them
TOUCH_TAP_MS, TOUCH_SWIPE_MS = (50, 2000), (100, 1500)
SWIPE_MS = 300
TOUCH_KEYS = {"back": "KEYCODE_BACK", "home": "KEYCODE_HOME"}
# after an input from the page the idle screen is looked at this often for this long: once a second, what the
# input did would show up to a second late and the person would press again
TOUCHED_EVERY_S, TOUCHED_FOR_S = 0.3, 3.0


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
    """The game's recorder, with each look and input also told as an event (`agent.Watch`), and the loop's
    progress (`agent.progress`) to `progressed`."""

    def __init__(self, recorder: measure.Recorder, tell: Callable[[dict[str, Any]], None],
                 progressed: Callable[[dict[str, Any]], None] | None = None, asker: Any = None) -> None:
        self.recorder, self.tell, self.progressed, self.asker = recorder, tell, progressed, asker

    async def pick(self, asked: dict[str, Any]) -> str | None:
        """The person is asked in the chat which of the things a game offers to take (`agent.Agent._ask_person`)."""
        return await self.asker(asked) if self.asker is not None else None

    def progress(self, event: dict[str, Any]) -> None:
        if self.progressed is not None:
            self.progressed(event)

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


def chat_file(out: Path, day: str) -> Path:
    return out / f"chat-{day}.jsonl"


def chatted_on(out: Path, day: str) -> int:
    """What people typed into the chat on a day, by the lines the demo kept of it."""
    path = chat_file(out, day)
    if not path.is_file():
        return 0
    return sum('"kind": "said"' in line for line in path.read_text(encoding="utf-8").splitlines())


def token_for(args: argparse.Namespace) -> str | None:
    """The access token fixed by `--token` or `GAMELAB_DEMO_TOKEN`; None when neither gives one (a new one is
    made). One too short or with letters an address does not carry plainly is refused."""
    token = getattr(args, "token", None) or os.environ.get(TOKEN_VARIABLE, "").strip() or None
    if token is not None and (len(token) < TOKEN_SHORTEST or not TOKEN_LETTERS.fullmatch(token)):
        raise SystemExit(f"a fixed token is {TOKEN_SHORTEST} letters at least, of A-Z a-z 0-9 _ - alone "
                         f"(python -c 'import secrets; print(secrets.token_urlsafe(32))')")
    return token


def _share(value: Any) -> float | None:
    """A place on the shown screen as a share of its width or height, 0 to 1; None for anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        return None
    return float(value)


def _held_to(value: Any, default: int, bounds: tuple[int, int]) -> int | None:
    """An input's milliseconds held to `bounds`, `default` when not given; None for what is not a number."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return round(min(max(value, bounds[0]), bounds[1]))


def touch_from(body: Any) -> dict[str, Any] | None:
    """What the page asked to send to the device (`POST /touch`), its places shares of the shown screen; None
    when it is not one of a tap, a swipe, a back or a home, or its values are not fit."""
    if not isinstance(body, dict):
        return None
    kind = body.get("kind")
    if kind == "tap":
        x, y, ms = _share(body.get("x")), _share(body.get("y")), _held_to(body.get("ms"), TAP_MS, TOUCH_TAP_MS)
        return None if None in (x, y, ms) else {"input": "tap", "x": x, "y": y, "ms": ms}
    if kind == "swipe":
        ends = [_share(body.get(key)) for key in ("x0", "y0", "x1", "y1")]
        ms = _held_to(body.get("ms"), SWIPE_MS, TOUCH_SWIPE_MS)
        return None if None in (*ends, ms) else {"input": "swipe", **dict(zip(("x0", "y0", "x1", "y1"), ends)), "ms": ms}
    if kind == "key":
        key = body.get("key")
        return {"input": "key", "key": key} if isinstance(key, str) and key in TOUCH_KEYS else None
    return None


def merged(pending: dict[str, str], answer: str) -> str:
    """A request asked back about and the person's answer, read as one request."""
    if talk.korean(pending["words"]):
        return f"{pending['words']}\n(물음: {pending['question']}) 답: {answer}"
    return f"{pending['words']}\n(asked: {pending['question']}) answer: {answer}"


def _brief(event: dict[str, Any]) -> dict[str, Any]:
    """A progress event as it is shown and kept: its words cut short."""
    return {key: value[:SITUATION_MOST] if isinstance(value, str) else value for key, value in event.items()}


class Demo:
    """The server's state: the game being played, the people watching it, and what may be asked."""

    def __init__(self, args: argparse.Namespace, model: Any, writer: Any = None, *, token: str | None = None,
                 emulator: Any = None, device: Callable[[str], Any] | None = None,
                 apps: Callable[[Any], Any] | None = None, look: guard.Resolve = guard.resolve,
                 connect: guard.Connect | None = None, chat: Any = None, sorter: Any = None) -> None:
        # chat: the model that answers questions, apart from the game's (its tokens are not the game's); sorter: the
        # one that sorts what is typed (a light one), the chat's own when none is given
        self.args, self.model, self.writer = args, model, writer
        self.chat, self.sorter = chat, sorter or chat
        self.token = token or token_for(args) or secrets.token_urlsafe(24)
        self.emulator = emulator or Emulator(args.avd, prefer=getattr(args, "serial", None))
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
        self.chats = chatted_on(args.out, self.day)
        # the chat, kept across games; and of the game being played (or the last one): its request, how it was
        # read, when it started and ended, its progress, its last inputs and what it handed back
        self.chat_log: deque[dict[str, Any]] = deque(maxlen=CHAT_KEPT)
        self.current: dict[str, Any] | None = None
        # the game played last as a request may go on with it (`request.understand`'s `last`): kept apart from
        # `current`, which the next request takes over; and the question asked back about a request, waiting for the
        # person's answer (`{"words", "question"}`)
        self.previous: dict[str, Any] | None = None
        self.pending: dict[str, str] | None = None
        # the answer waited for when a game offers several things and the person is asked which to take (`pick`),
        # and whether the request being played was in Korean
        self.picking: asyncio.Future | None = None
        self.asked_ko = True
        # what `ask` took over from the game before, given back when the request is asked back instead of played
        self.before: dict[str, Any] | None = None
        self.progress: list[dict[str, Any]] = []
        self.recent: deque[dict[str, Any]] = deque(maxlen=RECENT_TOLD)
        self.inputs = 0
        self.answering = asyncio.Lock()
        # the device as last looked at (`on`, `starting`, `off`; `none` for pages only), the work that looks at it
        # while no game is played, the one starting it from the button, and the last screen shown (what a question
        # is answered from, kept when the live screen is cleared for a device that is off)
        self.device_state = "none" if args.web_only else "off"
        self.watcher: asyncio.Task | None = None
        self.powering: asyncio.Task | None = None
        self.idle_pictures: Pictures | None = None
        self.idle_surface: tuple[str, Any] | None = None
        self.last: bytes | None = None
        # a person's input from the page (`--touch`): one at a time, the watcher woken by it and looking often until
        self.touching = False
        self.touched_until = 0.0
        self.nudged = asyncio.Event()

    # ── what everyone watching is sent ─────────────────────────────────

    def tell(self, event: dict[str, Any]) -> None:
        event = {**event, "at": round(time.time(), 2)}
        if event["kind"] in CHAT:
            self.chat_log.append(event)
            self._keep(event)
        # the looks are counted, not listed: a run of them is one event (and so is a run of a falling game's pieces)
        elif self.events and self.events[-1]["kind"] == event["kind"] and (
                event["kind"] == "seen" or event["kind"] == "progress" and event.get("what") == "falling"
                and self.events[-1].get("what") == "falling"):
            self.events[-1] = event
        else:
            self.events.append(event)
        if event["kind"] == "sending":
            self.recent.append({key: event[key] for key in ("number", "input", "where", "at")})
            self.inputs = max(self.inputs, event["number"])
        for viewer in self.viewers:
            viewer.events.append(event)
            viewer.wake.set()

    def show(self, frame: bytes) -> None:
        self.frame = self.last = frame
        for viewer in self.viewers:
            viewer.frame = frame
            viewer.wake.set()

    def _keep(self, event: dict[str, Any]) -> None:
        """A line of the chat kept in the day's file under `--out` (what the day's chats are counted by)."""
        try:
            self.args.out.mkdir(parents=True, exist_ok=True)
            with chat_file(self.args.out, time.strftime("%Y%m%d")).open("a", encoding="utf-8") as kept:
                kept.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def say_back(self, text: str, about: str, **more: Any) -> None:
        """A line the agent says in the chat."""
        self.tell({"kind": "reply", "about": about, "text": text, **more})

    def status(self) -> dict[str, Any]:
        return {"kind": "status", "state": "playing" if self.playing else "idle",
                "left_today": max(0, self.args.games_a_day - self._played_today()),
                "left_chats": max(0, self.args.chats_a_day - self._chatted_today()), "device": self.device_state,
                "touch": self.touchable}

    @property
    def touchable(self) -> bool:
        """Whether the page may send inputs to the device (`--touch`, a device of this demo's)."""
        return bool(getattr(self.args, "touch", False)) and not self.args.web_only

    def tell_now(self, event: dict[str, Any]) -> None:
        """An event told to everyone watching now and not kept among the game's events (whoever comes in later is
        not sent it)."""
        event = {**event, "at": round(time.time(), 2)}
        for viewer in self.viewers:
            viewer.events.append(event)
            viewer.wake.set()

    @property
    def playing(self) -> bool:
        return self.game is not None and not self.game.done()

    def _played_today(self) -> int:
        day = time.strftime("%Y%m%d")
        if day != self.day:
            self.day, self.today, self.chats = day, 0, 0
        return self.today

    def _chatted_today(self) -> int:
        self._played_today()
        return self.chats

    # ── the chat ──────────────────────────────────────────────────────

    def say(self, words: str) -> str | None:
        """What a person typed into the chat, answered in it; why not, when it is not taken."""
        if self._chatted_today() >= self.args.chats_a_day:
            return "chat_limit"
        self.chats += 1
        self.tell({"kind": "said", "words": words})
        if self.picking is not None and not self.picking.done():
            # the answer to what the game offers: it goes to the game, not to the sorter
            self.picking.set_result(words)
            return None
        asyncio.get_running_loop().create_task(self.talk(words))
        return None

    async def talk(self, words: str) -> None:
        """The words sorted and acted on: a game started (only when none is played), stopped, or a question
        answered."""
        ko = talk.korean(words)
        try:
            await self._talk(words, ko)
        except Exception as error:
            self.say_back(talk.said("no_answer", ko), "question", detail=f"{type(error).__name__}"[:200])

    async def _talk(self, words: str, ko: bool) -> None:
        kind = await talk.sort(self.sorter, words, self.playing, last=self.previous, chat=self.chat_before(),
                               pending=(self.pending or {}).get("question")) if self.sorter is not None else \
            talk.QUESTION if self.playing else talk.GAME
        self.tell({"kind": "sorted", "as": kind})
        if kind == talk.STOP:
            self.pending = None
            self.say_back(talk.said("stopping" if self.stop() else "idle", ko), "stop")
        elif kind == talk.GAME:
            # a game is never started while one is played: the person is told to ask again after it
            if self.playing:
                self.say_back(talk.said("busy", ko), "refused", reason="busy")
            elif self.pending is not None:
                # the answer to the question asked back: the request and the answer read as one, never asked back again
                self.ask(merged(self.pending, words), said=False, clarified=True)
            else:
                self.ask(words, said=False)
        else:
            await self.answer(words, ko)

    def chat_before(self) -> list[dict[str, Any]]:
        """The chat's last lines before the words just typed (the last line kept), oldest first."""
        return [{"who": "person" if line["kind"] == "said" else "agent", "text": line.get("words") or line.get("text")}
                for line in list(self.chat_log)[:-1][-CHAT_TOLD:]]

    async def answer(self, words: str, ko: bool) -> None:
        """A question answered by the chat's model from the game as it stands, while it is played too: nothing of
        the game waits on it."""
        if self.chat is None:
            self.say_back(talk.said("no_answer", ko), "question")
            return
        async with self.answering:
            context = self.context(words)
            try:
                text, usage = await talk.reply(self.chat, context, self.last)
            except Exception as error:
                self.say_back(talk.said("no_answer", ko), "question", detail=f"{type(error).__name__}"[:200])
                return
            self.say_back(text or talk.said("no_answer", ko), "question",
                          tokens={"input": usage.input_tokens, "output": usage.output_tokens})

    def context(self, words: str) -> dict[str, Any]:
        """What a question is answered from: the game being played or the last one, what the code counted of it,
        its progress, its last inputs, its handback and the chat before the words."""
        current = self.current
        game = None
        if current is not None:
            asked = current.get("asked") or {}
            goal = asked.get("goal")
            game = {"request": current["words"], "where": asked.get("url") or asked.get("app"),
                    "goal": goal.to_json() if hasattr(goal, "to_json") else None,
                    "seconds": round((current.get("ended") or time.monotonic()) - current["started"], 1),
                    "inputs": self.inputs}
        summary = (current or {}).get("summary")
        return {"words": words, "playing": self.playing, "game": game, "known": self.counted(),
                **({"last": self.previous} if self.previous is not None else {}),
                "progress": [{key: value for key, value in event.items() if key not in ("kind", "at")}
                             for event in self.progress[-PROGRESS_TOLD:]],
                "recent": list(self.recent),
                "handback": None if summary is None else {
                    "told": summary.get("told"), "outcome": summary.get("outcome"), "reason": summary.get("reason"),
                    **{key: (summary.get("work") or {}).get(key) for key in ("levels", "stopped_at", "out_of_lives", "not_yet")
                       if (summary.get("work") or {}).get(key)},
                    **({"choice": summary["adaptation"]["choice"]} if (summary.get("adaptation") or {}).get("choice") else {})},
                "chat": self.chat_before()}

    def counted(self) -> dict[str, Any]:
        """What the code counted of the game: the levels, the family it was taken as, a falling game's lines and
        pieces, how it ended."""
        known: dict[str, Any] = {}
        for event in self.progress:
            what = event.get("what")
            if what == "part" and "won" in event:
                known["levels"] = {name: event[name] for name in ("won", "lost", "wanted", "until", "through") if name in event}
            elif what == "taken":
                known["family"] = event.get("family")
            elif what == "let_go":
                known["family_let_go"] = {"family": event.get("family"), "why": event.get("why")}
            elif what == "falling":
                known["falling"] = {"lines": event.get("lines"), "pieces": event.get("pieces")}
            elif what == "identified" and event.get("falling"):
                known["falling_game"] = True
        summary = (self.current or {}).get("summary")
        if summary is not None:
            known["ended"] = {"outcome": summary.get("outcome"), "reason": summary.get("reason"),
                              "inputs": summary.get("actions_used"), "moves": summary.get("confirmed_actions")}
            levels = (summary.get("work") or {}).get("levels")
            if levels:
                known["levels"] = levels
        return known

    def progressed(self, event: dict[str, Any]) -> None:
        """The loop's progress (`agent.progress`): kept for questions, shown in the log, and said in the chat for a
        game identified and a level's end."""
        what = event.get("what")
        if what == "handback":
            # the summary that follows holds it too; the end is said from that
            return
        kept = _brief(event)
        if what == "falling" and self.progress and self.progress[-1].get("what") == "falling":
            self.progress[-1] = kept
        else:
            self.progress = [*self.progress, kept][-PROGRESS_KEPT:]
        self.tell({"kind": "progress", **kept})
        ko = (self.current or {}).get("ko", True)
        if what == "identified":
            found = ("떨어지는 블록 게임으로 보고 코드가 두겠습니다" if ko else "It is a falling-block game: the code plays it") \
                if event.get("falling") else (("게임을 알아봤습니다. 계열 후보: " if ko else "Identified the game. Families to try: ")
                                              + ", ".join(event.get("candidates") or ()) if event.get("candidates") else
                                              ("맞는 계열이 없어 모델이 직접 둡니다" if ko else "No family fits: the model plays it itself"))
            self.say_back(found + ".", "progress")
        elif what == "part" and "won" in event and event.get("outcome") in ("goal_reached", "game_over"):
            # (a part that ended any other way ended no level: the end says why)
            won = event.get("outcome") == "goal_reached"
            # (levels up to one whose number was not read yet: how many are left is not known)
            wanted, until = event.get("wanted"), event.get("until")
            if ko:
                count = f"{event['won']}/{wanted}개 깸" if wanted is not None else f"레벨 {until}까지, 지금까지 {event['won']}개 깸"
                said = f"레벨 하나를 {'깼' if won else '졌'}습니다: {count}, {event['lost']}개 짐."
            else:
                count = f"{event['won']} of {wanted} won" if wanted is not None else f"up to level {until}, {event['won']} won so far"
                said = f"A level {'won' if won else 'lost'}: {count}, {event['lost']} lost."
            self.say_back(said, "progress")

    # ── a game ────────────────────────────────────────────────────────

    def ask(self, words: str, said: bool = True, clarified: bool = False) -> str | None:
        """Start a game for the words; why not, when it is not started (and said so in the chat). `said`: the words
        are put in the chat too (they came by `/ask`, not typed into it). `clarified`: the words hold the answer to
        a question asked back about them, and are not asked back again."""
        ko = talk.korean(words)
        if said:
            self.tell({"kind": "said", "words": words})
        why = "busy" if self.playing else "day_limit" if self._played_today() >= self.args.games_a_day else None
        if why is not None:
            self.say_back(talk.said(why, ko), "refused", reason=why)
            return why
        self.today += 1
        # (given back when the request is asked back instead: a question then is still about the game before)
        self.before = {"current": self.current, "events": self.events, "progress": self.progress,
                       "inputs": self.inputs, "recent": list(self.recent)}
        self.events = []
        self.current = {"words": words, "ko": ko, "started": time.monotonic()}
        self.progress, self.inputs = [], 0
        self.recent.clear()
        self.cancel = asyncio.Event()
        self.game = asyncio.get_running_loop().create_task(self.play(words, self.cancel, clarified))
        return None

    def clarify(self, asked: dict[str, Any], words: str, clarified: bool) -> bool:
        """Whether the request is asked back rather than played (`request.understand`'s `unclear`, once a request):
        the question said in the chat and kept for the answer, the day's game and the game before given back."""
        if clarified or not asked.get("unclear"):
            return False
        self.pending = {"words": words, "question": asked["unclear"]}
        self.today = max(self.today - 1, 0)
        before = self.before or {}
        self.current, self.events = before.get("current"), before.get("events") or []
        self.progress, self.inputs = before.get("progress") or [], before.get("inputs") or 0
        self.recent.clear()
        self.recent.extend(before.get("recent") or ())
        self.say_back(asked["unclear"], "clarify")
        return True

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

    def refuse(self, reason: str, detail: str = "") -> None:
        self.tell({"kind": "refused", "reason": reason, **({"detail": detail} if detail else {})})
        self.say_back(talk.said(reason, (self.current or {}).get("ko", True)), "refused", reason=reason)

    async def play(self, words: str, cancel: asyncio.Event, clarified: bool = False) -> None:
        self.tell({"kind": "asked", "words": words})
        self.tell(self.status())
        stamp = time.strftime("%Y%m%d-%H%M%S")
        ko = talk.korean(words)
        self.asked_ko = ko
        try:
            asked = await request.understand(self.model, words, last=self.previous)
            if self.clarify(asked, words, clarified):
                # asked back before anything is started (the emulator neither)
                return
            surface = serial = None
            if asked["url"] is None and asked["hint"] is None:
                # words that name no page and say nothing of a game do not start the emulator
                self.refuse("no_game")
                return
            if asked["url"] is None and self.args.web_only:
                # the device is not this demo's to move (another run measures on it)
                self.refuse("web_only")
                return
            label = None
            if asked["url"] is None:
                # no page: an app on the device, which is asked about again with the apps installed there
                serial = await self.switched_on(lambda: self.tell({"kind": "opening", "where": "emulator"}))
                surface = self.device(serial)
                installed = await self.apps(surface)
                asked = await request.understand(self.model, words, installed, last=self.previous)
                label = next((app.get("label") for app in installed if app["package"] == asked["app"]), None)
                if self.clarify(asked, words, clarified):
                    return
            # the question asked back, if any, is answered or passed by this request
            self.pending = None
            self.current["asked"] = asked
            self.tell({"kind": "understood", "url": asked["url"], "app": asked["app"], "goal": asked["goal"].to_json(),
                       "hint": asked["hint"], **({"same": True} if asked.get("same") else {})})
            if asked["url"] is None and asked["app"] is None:
                self.refuse("no_game")
                return
            # the game a next request may go on with, and where it stopped once it is over
            previous = {"words": words, "url": asked["url"], "app": asked["app"], "label": label, "game": asked["hint"],
                        "within": asked.get("within"), "goal": asked["goal"].to_json()}
            self.previous = previous
            self.say_back(talk.promise({**asked, "app": label or asked["app"]},
                                       {"inputs": self.args.max_actions, "seconds": self.args.max_seconds}, ko), "start")
            if asked["url"] is not None:
                summary = await self.web_game(asked, f"web-{stamp}", cancel)
            else:
                summary = await self.android_game(asked, f"app-{stamp}", serial, surface, cancel)
            self.current["summary"] = summary
            handback = summary.get("work") or {}
            stopped = handback.get("stopped_at") or {}
            previous["stopped"] = {"screen": stopped.get("screen"), "situation": stopped.get("situation"),
                                   "level": stopped.get("level")}
            if handback.get("levels"):
                previous["levels"] = handback["levels"]
            adapted = summary.get("adaptation") or {}
            self.tell({"kind": "result", "outcome": summary["outcome"], "reason": summary["reason"],
                       "actions": summary["actions_used"], "confirmed": summary["confirmed_actions"],
                       "tokens": adapted.get("tokens") or summary.get("model_tokens"), "seconds": summary["seconds"],
                       "family": (adapted.get("identified") or {}).get("family"), "generations": adapted.get("generations"),
                       "name": summary["name"], **({"told": summary["told"]} if summary.get("told") else {})})
            self.say_back(talk.handed_back(summary, ko), "end")
        except guard.Refused:
            self.refuse("not_public")
        except measure.Busy as busy:
            self.refuse("in_use", str(busy))
        except asyncio.CancelledError:
            self.tell({"kind": "result", "outcome": "budget_exhausted", "reason": "cancelled", "cut": True})
            self.say_back(talk.handed_back({"outcome": "budget_exhausted", "reason": "cancelled"}, ko), "end")
            raise
        except Exception as error:
            self.tell({"kind": "error", "detail": f"{type(error).__name__}: {error}"[:500]})
            self.say_back(talk.said("error", ko, f"{type(error).__name__}"), "end")
        finally:
            self.game = None
            # (a request asked back gave the game before back, which ended already)
            if self.current is not None and "ended" not in self.current:
                self.current["ended"] = time.monotonic()
            self.tell(self.status())

    def watching(self, recorder: measure.Recorder) -> Watched:
        return Watched(recorder, self.tell, self.progressed, self.pick)

    async def pick(self, asked: dict[str, Any]) -> str | None:
        """The game offers several things and one is taken: the chat says what is offered and what the agent
        would take, and the next words typed are the answer. The agent waits only so long (it cancels this), and
        then takes its own."""
        ko = self.asked_ko
        foreseen = asked.get("foreseen") or []
        options = " / ".join(f"{name} ({foreseen[index]})" if index < len(foreseen) and foreseen[index] else name
                             for index, name in enumerate(asked["options"]))
        mine = asked.get("picked") or "?"
        self.picking = asyncio.get_running_loop().create_future()
        self.say_back((f"{asked['asks'] or '무엇을 가질까요?'} {options}. 답이 없으면 '{mine}'을(를) 고릅니다"
                       + (f" ({asked['why']})" if asked.get("why") else "") + ".") if ko else
                      (f"{asked['asks'] or 'Which one?'} {options}. With no answer I take '{mine}'"
                       + (f" ({asked['why']})" if asked.get("why") else "") + "."), "pick", options=asked["options"])
        try:
            return await self.picking
        finally:
            self.picking = None

    async def web_game(self, asked: dict[str, Any], name: str, cancel: asyncio.Event) -> dict[str, Any]:
        args = self.game_args(web.configure, asked)
        _, site = web.site_for(args, asked)
        if not await guard.allowed(site["url"], self.look):
            raise guard.Refused(site["url"])
        self.tell({"kind": "opening", "where": "web"})
        async with web.guarded(site, self.look, self.connect) as held:
            async with browser(viewport=site["viewport"], headless=not self.args.headed, profile=web.profile_for(args, site),
                               **held) as page:
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
                                              steps=await android.steps_for(args, surface), cancel=cancel,
                                              watching=self.watching)
            finally:
                idle.cancel()

    # ── the device while no game is played ────────────────────────────

    def device_is(self, state: str) -> None:
        """The device's state as last looked at, told to everyone watching when it changed (and not kept among the
        game's events: whoever comes in is sent the status as it stands)."""
        if state != self.device_state:
            self.device_state = state
            self.tell_now(self.status())

    async def switched_on(self, starting: Callable[[], None] | None = None) -> str:
        """The device's serial once it has booted, started first when it is not running (`starting` told then),
        its state told as it goes."""
        def told() -> None:
            self.device_is("starting")
            if starting is not None:
                starting()
        try:
            serial = await self.emulator.ensure(told)
        except Exception:
            self.device_is("off")
            raise
        self.device_is("on")
        return serial

    def switch_on(self) -> str:
        """The device started in the background (the page's button); its state, as it stands or `starting`."""
        if self.device_state in ("on", "starting") or (self.powering is not None and not self.powering.done()):
            return self.device_state
        self.device_is("starting")
        self.powering = asyncio.get_running_loop().create_task(self._power())
        return "starting"

    async def _power(self) -> None:
        try:
            await self.switched_on()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.tell({"kind": "error", "detail": f"emulator: {type(error).__name__}: {error}"[:500]})

    async def look_at_device(self) -> None:
        """Once: the device's state, and its screen shown when it is on; nothing while a game is played (its own
        captures are shown) or nobody watches, nor for pages only."""
        if self.args.web_only or self.playing or not self.viewers:
            return
        state, serial = await self.emulator.look()
        if serial is None:
            starting = self.powering is not None and not self.powering.done()
            self.device_is("starting" if starting else state)
            if self.frame is not None:
                # the screen of a device that is off is not shown as if it were on
                self.frame = None
                for viewer in self.viewers:
                    viewer.frame = None
            return
        self.device_is("on")
        if measure.held(f"android-{serial}"):
            # another run moves the device (a session measuring on it): its screen is not captured over that run's
            # own captures, and the last one shown stays
            return
        if self.idle_surface is None or self.idle_surface[0] != serial:
            self.idle_surface = (serial, self.device(serial))
        if self.idle_pictures is None:
            self.idle_pictures = Pictures(ANDROID_WIDTH, self.show)
        png = await self.idle_surface[1].capture()
        if not self.playing:
            self.idle_pictures.offer(png)

    async def watch_device(self, every_s: float | None = None) -> None:
        """Until cancelled: the device looked at each `every_s` (`look_at_device`), a failure taken as nothing seen;
        at once after an input from the page, and each TOUCHED_EVERY_S for TOUCHED_FOR_S after it."""
        while True:
            try:
                await self.look_at_device()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            every = DEVICE_EVERY_S if every_s is None else every_s
            if time.monotonic() < self.touched_until:
                every = min(every, TOUCHED_EVERY_S)
            try:
                async with asyncio.timeout(every):
                    await self.nudged.wait()
            except TimeoutError:
                pass
            self.nudged.clear()

    async def touch(self, asked: dict[str, Any]) -> str | None:
        """An input from the page (`touch_from`) sent to the device while no game is played, its places turned into
        the device's pixels; why not, when it was not sent. One at a time, under the device's lock (another run
        moving it is not moved under), and told to everyone watching (not who sent it: nobody is known by name)."""
        if self.playing:
            return "playing"
        if self.touching:
            return "busy"
        self.touching = True
        try:
            try:
                async with asyncio.timeout(5):
                    state, serial = await self.emulator.look()
            except (TimeoutError, RuntimeError, OSError):
                return "device_off"
            if serial is None:
                self.device_is("starting" if self.powering is not None and not self.powering.done() else state)
                return "device_off"
            self.device_is("on")
            if self.idle_surface is None or self.idle_surface[0] != serial:
                self.idle_surface = (serial, self.device(serial))
            surface = self.idle_surface[1]
            try:
                with measure.lock(f"android-{serial}"):
                    # a game asked for while the device was looked for is not moved under
                    if self.playing:
                        return "playing"
                    await self._send(surface, asked)
            except measure.Busy:
                return "in_use"
            except (ValueError, RuntimeError, OSError):
                return "failed"
        finally:
            self.touching = False
        self.touched_until = time.monotonic() + TOUCHED_FOR_S
        self.nudged.set()
        self.tell_now({"kind": "touched", **{key: round(value, 3) if isinstance(value, float) else value
                                             for key, value in asked.items()}})
        return None

    @staticmethod
    async def _send(surface: Any, asked: dict[str, Any]) -> None:
        """The input on the device: its shares of the shown screen as the device's pixels (the size its captures have,
        the screen as it is turned)."""
        if asked["input"] == "key":
            await surface.key(TOUCH_KEYS[asked["key"]])
            return
        width, height = await surface.size()

        def at(x: float, y: float) -> tuple[float, float]:
            return min(x * width, width - 1), min(y * height, height - 1)
        if asked["input"] == "tap":
            await surface.tap(*at(asked["x"], asked["y"]), asked["ms"])
        else:
            await surface.drag(*at(asked["x0"], asked["y0"]), *at(asked["x1"], asked["y1"]), asked["ms"])

    def watch(self) -> None:
        """The device's watcher started (once, while the server runs), unless the device is not this demo's."""
        if not self.args.web_only and (self.watcher is None or self.watcher.done()):
            self.watcher = asyncio.get_running_loop().create_task(self.watch_device())

    async def close(self) -> None:
        """The game stopped and waited for, and the emulator shut down if this server started it."""
        for task in (self.watcher, self.powering):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
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
            if not self.args.web_only and not self.playing:
                # the device as it is now, so the page does not first offer to start one that is on
                try:
                    async with asyncio.timeout(5):
                        self.device_is("starting" if self.powering is not None and not self.powering.done()
                                       else await self.emulator.state())
                except (TimeoutError, RuntimeError, OSError):
                    pass
            # the chat first, then this game's own events
            viewer.events.extend([*self.chat_log, *self.events, self.status()])
            viewer.frame = self.frame
            viewer.wake.set()
            self.viewers.add(viewer)
            self.watch()
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

        async def say(req: Any) -> Any:
            try:
                words = (await req.json()).get("words")
            except (ValueError, AttributeError):
                words = None
            if not isinstance(words, str) or not words.strip() or len(words) > WORDS_MOST:
                return http.json_response({"refused": "words"}, status=400)
            why = self.say(words.strip())
            if why is not None:
                return http.json_response({"refused": why}, status=429)
            return http.json_response({"said": True}, status=202)

        async def stop(req: Any) -> Any:
            return http.json_response({"stopping": True}, status=202) if self.stop() else \
                http.json_response({"refused": "idle"}, status=409)

        async def emulator(req: Any) -> Any:
            # the page's button: the device started in the background; one on or starting is left as it is
            if self.args.web_only:
                return http.json_response({"refused": "web_only", "device": "none"}, status=409)
            if not self.playing and self.device_state == "off":
                try:
                    async with asyncio.timeout(5):
                        self.device_is(await self.emulator.state())
                except (TimeoutError, RuntimeError, OSError):
                    pass
            if self.device_state in ("on", "starting") or (self.powering is not None and not self.powering.done()):
                return http.json_response({"device": self.device_state}, status=200)
            return http.json_response({"device": self.switch_on()}, status=202)

        async def touch(req: Any) -> Any:
            # a person's input on the device from the page: only when the server was told so (`--touch`)
            if not getattr(self.args, "touch", False):
                return http.json_response({"refused": "touch_off"}, status=403)
            if self.args.web_only:
                return http.json_response({"refused": "web_only"}, status=409)
            try:
                asked = touch_from(await req.json())
            except ValueError:
                asked = None
            if asked is None:
                return http.json_response({"refused": "input"}, status=400)
            why = await self.touch(asked)
            if why is not None:
                return http.json_response({"refused": why}, status={"busy": 429, "failed": 502}.get(why, 409))
            return http.json_response({"sent": True}, status=202)

        async def closing(app: Any) -> None:
            await self.close()

        app = http.Application(middlewares=[admitted], client_max_size=64 * 1024)
        app.router.add_get("/", page)
        app.router.add_get("/ws", socket)
        app.router.add_post("/ask", ask)
        # what is typed into the chat: sorted there into a game, a question or a stop (`/ask` and `/stop` stay: a
        # game asked for in so many words and the stop button need no model question and no chat of the day)
        app.router.add_post("/say", say)
        app.router.add_post("/stop", stop)
        app.router.add_post("/emulator", emulator)
        app.router.add_post("/touch", touch)
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
    # the chat's own clients: its questions are not the game's (their tokens, their budget)
    key = models.api_key()
    chat_client = models.ChatClient(args.vision_model, key, base=args.model_base)
    light = args.light_model if args.light_model and args.light_model != "none" else None
    sort_client = models.ChatClient(light, key, base=args.model_base) if light else None
    fixed = token_for(args)
    demo = Demo(args, model, writer, token=fixed, chat=models.Recorded(chat_client), sorter=sort_client)
    runner = http.AppRunner(demo.application())
    await runner.setup()
    try:
        await http.TCPSite(runner, args.host, args.port).start()
        print(json.dumps({"serving": f"http://{args.host}:{args.port}/?k={demo.token}", "token": "fixed" if fixed else "new",
                          "out": str(args.out), "games_a_day": args.games_a_day, "chats_a_day": args.chats_a_day,
                          "sorter": light or args.vision_model}), flush=True)
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        for opened in (client, writer_client, chat_client, sort_client):
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
    parser.add_argument("--max-actions", type=int, default=6000,
                        help="one game's inputs at most: generous, since a falling game counts each key or swipe as "
                             "one (300 ended a request for five lines at four); the seconds and the tokens are what "
                             "bound a game")
    parser.add_argument("--max-seconds", type=float, default=900, help="one game's seconds at most")
    parser.add_argument("--max-input-tokens", type=int, default=3_000_000, help="the tokens one game's model questions may send")
    parser.add_argument("--games-a-day", type=int, default=20, help="games played a day at most")
    parser.add_argument("--chats-a-day", type=int, default=200,
                        help="what may be typed into the chat a day at most (each is a model question or two)")
    parser.add_argument("--token", help=f"a fixed access token ({TOKEN_VARIABLE} unless given; else a new one each start), "
                                        f"{TOKEN_SHORTEST} letters at least")
    parser.add_argument("--accept-consent", action="store_true",
                        help="agree to a site's terms when it offers no refusal (else the game stops there)")
    parser.add_argument("--serial", help="the device to play apps on, by its serial (adb -s), whatever its AVD; "
                                         "else the emulator running --avd, found by its name")
    parser.add_argument("--adapter-store", type=Path, default=measure.ROOT / "out" / "serve" / "adapters",
                        help="where generated adapters are kept by site and app and reused")
    parser.add_argument("--avd", default=DEMO_AVD,
                        help=f"the AVD the demo plays apps on, found by its name and started when it is not running "
                             f"({DEMO_AVD}, where a session measures too: the device's lock keeps them apart)")
    parser.add_argument("--headed", action="store_true", help="show the browser window too")
    parser.add_argument("--web-only", action="store_true",
                        help="play pages only: a request for an app is refused and the device is never touched")
    parser.add_argument("--touch", action="store_true",
                        help="let the page tap, swipe and press back or home on the device while no game is played (to "
                             "get past what only a person may: an age check, a sign-in, a notice). Whoever has the link "
                             "and its token then moves the device directly, whatever is on it (the store, the settings, "
                             "the accounts signed in): off unless given")
    measure.model_arguments(parser)
