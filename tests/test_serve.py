"""The demo page's server (PLAN §14, D5 to D7) through aiohttp's test client: a request played to its end on a
fake device by the agent's real loop, one stopped on a local page in real Chromium, and who may ask. The
models are fakes, the device a fake adb, the emulator a fake, and a page's names resolve by a fake to this
Mac's test server; nothing leaves the machine."""
import argparse
import asyncio
from io import BytesIO
import json

from aiohttp import WSMsgType, web as http
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image, ImageDraw
import pytest

from gamelab import adaptation, agent, direct, motion, recovery
from gamelab.contracts import Budget, GoalSpec
from gamelab.environments.android import AndroidSurface
from gamelab.eval import android, measure, serve
from gamelab.eval import web as web_eval
from gamelab.model import Recorded, TurnResult
from fake_screen import Screen
from test_adaptation import Author
from test_android_ask import screen_device
from test_direct import PRESS, Player
from test_generated import source

OUTSIDE = "93.184.216.34"
TOKEN = "a-test-token"


class Asked:
    """A model that answers a request in words with `answer` (after `wait`, when given) and leaves every
    other question to `rest`."""

    def __init__(self, answer, rest=None, wait=None):
        self.answer, self.rest, self.wait, self.requests = answer, rest, wait, []

    async def complete(self, turn):
        context = json.loads(turn.messages[0].content[0]["text"])
        if "request" in context and "objectives" in context:
            self.requests.append(context)
            if self.wait is not None:
                await self.wait.wait()
            return TurnResult(text=json.dumps(self.answer(context) if callable(self.answer) else self.answer))
        return await self.rest.complete(turn)


class Running:
    """The emulator as a fake: a device already booted."""

    def __init__(self):
        self.ensured = self.stopped = 0

    async def ensure(self, starting=None):
        self.ensured += 1
        return "emulator-5554"

    async def stop(self):
        self.stopped += 1


def options(tmp_path, *more):
    parser = argparse.ArgumentParser()
    serve.configure(parser)
    return parser.parse_args(["--out", str(tmp_path / "serve"), "--adapter-store", str(tmp_path / "kept"), "--compute", "20",
                              "--max-seconds", "60", "--vision-model", "", *more])


@pytest.fixture(autouse=True)
def quick(monkeypatch, tmp_path):
    for module, name in ((adaptation, "SETTLE_S"), (adaptation, "FRAME_GAP_S"), (agent, "STILL_GAP_S"), (recovery, "SETTLE_S"),
                         (direct, "SETTLE_S"), (direct, "STILL_GAP_S")):
        monkeypatch.setattr(module, name, 0.0)
    monkeypatch.setattr(android, "LAUNCH_WAIT_MS", 0)
    monkeypatch.setattr(web_eval, "ASKED_WAIT_MS", 0)
    monkeypatch.setattr(measure, "LOCKS", tmp_path / ".lock")


async def started(demo):
    client = TestClient(TestServer(demo.application()))
    await client.start_server()
    return client


def origin(client):
    return str(client.make_url("")).rstrip("/")


async def admitted(client):
    answer = await client.get(f"/?k={TOKEN}", allow_redirects=False)
    assert answer.status == 303
    return answer


async def until(socket, kind, then=None, limit_s=60):
    """The events and frames the live connection sends until an event of `kind`; `then(event)` is told each."""
    events, frames = [], []
    async with asyncio.timeout(limit_s):
        async for message in socket:
            if message.type == WSMsgType.BINARY:
                frames.append(message.data)
                continue
            event = json.loads(message.data)
            events.append(event)
            if then is not None:
                await then(event)
            if event["kind"] == kind:
                return events, frames
    raise AssertionError(f"the connection closed before {kind}: {events}")


class Device:
    """The fake 2048 game behind a fake adb, and the adapter kept for its app (`kept`)."""

    def __init__(self, tmp_path):
        self.game, self.adb, self.kept = screen_device(tmp_path)


def android_demo(tmp_path, answer, device):
    model = Recorded(Asked(answer, Author(device.game, [])))

    async def installed(surface):
        return [{"package": "com.example.game", "label": "Merge Tiles"}]
    return serve.Demo(options(tmp_path), model, token=TOKEN, emulator=Running(),
                      device=lambda serial: AndroidSurface(device.adb), apps=installed)


async def test_a_request_for_an_app_is_played_to_its_end_and_watched_from_the_first_look_to_the_result(tmp_path):
    device = Device(tmp_path)
    await device.kept()
    demo = android_demo(tmp_path, lambda context: {"url": None, "app": "com.example.game" if "apps" in context else None,
                                                   "objective": "actions", "target_value": 3, "game": "2048"}, device)
    client = await started(demo)
    try:
        await admitted(client)
        socket = await client.ws_connect("/ws", headers={"Origin": origin(client)})
        answer = await client.post("/ask", json={"words": "Merge Tiles 앱에서 3수"}, headers={"Origin": origin(client)})
        assert answer.status == 202
        events, frames = await until(socket, "result")
        kinds = [event["kind"] for event in events]
        result = events[-1]
        assert (result["outcome"], result["reason"], result["actions"]) == ("goal_reached", "actions", 3), events
        assert result["family"] == "slide_merge" and result["name"].startswith("app-") and "seconds" in result
        assert kinds.index("asked") < kinds.index("understood") < kinds.index("opening") < kinds.index("sending")
        assert kinds.count("sending") == 3 and kinds.count("sent") == 3 and "seen" in kinds
        understood = events[kinds.index("understood")]
        assert understood["app"] == "com.example.game" and (understood["goal"]["objective"], understood["goal"]["target_value"]) == ("actions", 3)
        # the device's captures, as JPEGs no wider than the live screen's
        assert frames and all(Image.open(BytesIO(frame)).format == "JPEG" for frame in frames)
        # the game's files are those eval android writes
        assert list((tmp_path / "serve").glob("app-*-01.json"))
        # someone who comes in now is sent this game's events from its request on
        late = await client.ws_connect("/ws", headers={"Origin": origin(client)})
        again, _ = await until(late, "result")
        # (the looks counted in one event, where they came one by one to those watching from the start)
        unseen = [kind for kind in kinds[1:] if kind != "seen"]
        assert [event["kind"] for event in again if event["kind"] != "seen"] == unseen and "seen" in [event["kind"] for event in again]
        # and the last screen, with the game over
        messages = [await late.receive(timeout=5) for _ in range(3)]
        assert [message.type for message in messages] == [WSMsgType.TEXT, WSMsgType.TEXT, WSMsgType.BINARY]
    finally:
        await client.close()
    assert demo.emulator.stopped == 1


async def test_a_game_stopped_on_the_device_ends_cancelled(tmp_path):
    device = Device(tmp_path)
    await device.kept()
    demo = android_demo(tmp_path, lambda context: {"url": None, "app": "com.example.game" if "apps" in context else None,
                                                   "objective": "play_until_end", "game": "2048"}, device)
    client = await started(demo)
    try:
        await admitted(client)
        socket = await client.ws_connect("/ws", headers={"Origin": origin(client)})
        assert (await client.post("/ask", json={"words": "Merge Tiles 끝까지"}, headers={"Origin": origin(client)})).status == 202

        async def stop(event):
            if event["kind"] == "sent" and event["inputs"] == 1:
                assert (await client.post("/stop", headers={"Origin": origin(client)})).status == 202

        events, _ = await until(socket, "result", stop)
        assert (events[-1]["outcome"], events[-1]["reason"]) == ("budget_exhausted", "cancelled")
        assert events[-1]["actions"] < 10
    finally:
        await client.close()


LAMPS = """<body style="margin:0;background:#f5f5f5"><div id="lamps"></div>
<button id="press" style="position:absolute;left:250px;top:300px;width:100px;height:50px;background:#2878dc;border:0"></button>
<script>let lit = 0; const draw = () => { document.getElementById('lamps').innerHTML = [0, 1, 2].map(i =>
`<div style="position:absolute;left:${150 + i * 110}px;top:120px;width:80px;height:80px;background:${i < lit ? '#fac800' : '#5a5a5a'}"></div>`).join(''); };
document.getElementById('press').onclick = () => { if (lit < 3) lit++; draw(); }; draw();</script></body>"""


@pytest.fixture
async def lamps():
    async def page(request):
        return http.Response(text=LAMPS, content_type="text/html")
    app = http.Application()
    app.router.add_get("/", page)
    runner = http.AppRunner(app)
    await runner.setup()
    site = http.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield site._server.sockets[0].getsockname()[1]
    await runner.cleanup()


async def test_a_request_for_a_page_is_played_in_chromium_shown_by_its_screencast_and_stopped(tmp_path, lamps):
    url = f"http://lamps.test:{lamps}/"

    async def look(host, port):
        return [OUTSIDE] if host == "lamps.test" else ["10.0.0.1"]

    async def connect(address, port):
        assert address == OUTSIDE
        return await asyncio.open_connection("127.0.0.1", port)

    model = Recorded(Asked({"url": url, "objective": "play_until_end", "game": "lamps"}, Player([PRESS])))
    demo = serve.Demo(options(tmp_path), model, token=TOKEN, emulator=Running(), look=look, connect=connect)
    client = await started(demo)
    try:
        await admitted(client)
        socket = await client.ws_connect("/ws", headers={"Origin": origin(client)})
        assert (await client.post("/ask", json={"words": f"{url} 에서 끝까지"}, headers={"Origin": origin(client)})).status == 202

        async def stop(event):
            if event["kind"] == "sent":
                await client.post("/stop", headers={"Origin": origin(client)})

        events, frames = await until(socket, "result", stop)
        kinds = [event["kind"] for event in events]
        assert (events[-1]["outcome"], events[-1]["reason"]) == ("budget_exhausted", "cancelled"), events
        assert events[kinds.index("opening")]["where"] == "web" and "sending" in kinds
        assert frames and Image.open(BytesIO(frames[-1])).size[0] <= serve.WEB_WIDTH
        assert list((tmp_path / "serve").glob("web-*-01.json"))
        # a page inside this network is not opened
        demo.model = Recorded(Asked({"url": "http://10.0.0.1/", "objective": "play_until_end"}))
        assert (await client.post("/ask", json={"words": "http://10.0.0.1/ 에서"}, headers={"Origin": origin(client)})).status == 202
        events, _ = await until(socket, "refused")
        assert events[-1]["reason"] == "not_public"
    finally:
        await client.close()
    # the emulator was never needed
    assert demo.emulator.ensured == 0


async def test_without_the_token_nothing_is_served_and_with_it_once_it_is_kept_and_taken_out_of_the_address(tmp_path):
    demo = serve.Demo(options(tmp_path), Recorded(Asked({})), token=TOKEN, emulator=Running())
    client = await started(demo)
    try:
        for path in ("/", "/ws"):
            assert (await client.get(path)).status == 401
        assert (await client.post("/ask", json={"words": "x"}, headers={"Origin": origin(client)})).status == 401
        assert (await client.get("/?k=wrong", allow_redirects=False)).status == 401
        answer = await client.get(f"/?k={TOKEN}&lang=ko", allow_redirects=False)
        assert answer.headers["Location"] == "/?lang=ko"
        cookie = answer.cookies[serve.COOKIE]
        assert cookie.value == TOKEN and cookie["httponly"] and cookie["samesite"] == "Strict" and not cookie["secure"]
        page = await client.get("/")
        assert page.status == 200 and "<textarea" in await page.text() and "no-referrer" in page.headers["Referrer-Policy"]
        # behind the tunnel the cookie is sent over https alone
        behind = await client.get(f"/?k={TOKEN}", allow_redirects=False, headers={"X-Forwarded-Proto": "https"})
        assert behind.cookies[serve.COOKIE]["secure"]
    finally:
        await client.close()


async def test_a_live_connection_or_a_request_from_another_origin_is_refused(tmp_path):
    demo = serve.Demo(options(tmp_path), Recorded(Asked({})), token=TOKEN, emulator=Running())
    client = await started(demo)
    try:
        await admitted(client)
        elsewhere = {"Origin": "https://evil.example"}
        assert (await client.post("/ask", json={"words": "play"}, headers=elsewhere)).status == 403
        assert (await client.post("/stop", headers=elsewhere)).status == 403
        assert (await client.post("/ask", json={"words": "play"})).status == 403
        assert (await client.get("/ws", headers=elsewhere)).status == 403
        assert not demo.playing
    finally:
        await client.close()


async def test_a_request_while_a_game_is_played_and_one_past_the_days_games_are_refused(tmp_path):
    held = asyncio.Event()
    demo = serve.Demo(options(tmp_path, "--games-a-day", "1"), Recorded(Asked({"url": None, "app": None}, wait=held)),
                      token=TOKEN, emulator=Running(), apps=lambda surface: asyncio.sleep(0, []),
                      device=lambda serial: AndroidSurface(lambda args: asyncio.sleep(0, b"")))
    client = await started(demo)
    try:
        await admitted(client)
        mine = {"Origin": origin(client)}
        socket = await client.ws_connect("/ws", headers=mine)
        assert (await client.post("/ask", json={"words": "play something"}, headers=mine)).status == 202
        second = await client.post("/ask", json={"words": "play another"}, headers=mine)
        assert second.status == 409 and (await second.json())["refused"] == "busy"
        held.set()
        events, _ = await until(socket, "refused")
        assert events[-1]["reason"] == "no_game" and demo.emulator.ensured == 0
        await until(socket, "status")
        third = await client.post("/ask", json={"words": "play again"}, headers=mine)
        assert third.status == 429 and (await third.json())["refused"] == "day_limit"
        assert (await client.post("/ask", json={"words": " "}, headers=mine)).status == 400
        assert (await client.post("/stop", headers=mine)).status == 409
    finally:
        await client.close()


async def test_a_device_another_run_holds_is_not_played_on(tmp_path):
    device = Device(tmp_path)
    demo = android_demo(tmp_path, lambda context: {"url": None, "app": "com.example.game" if "apps" in context else None,
                                                   "objective": "play_until_end", "game": "2048"}, device)
    client = await started(demo)
    try:
        await admitted(client)
        socket = await client.ws_connect("/ws", headers={"Origin": origin(client)})
        with measure.lock("android-emulator-5554"):
            await client.post("/ask", json={"words": "Merge Tiles"}, headers={"Origin": origin(client)})
            events, _ = await until(socket, "refused")
        assert events[-1]["reason"] == "in_use"
    finally:
        await client.close()


# ── the live screen of a device (D7) ─────────────────────────────────


class Counted(Screen):
    """The fake game, counting its whole captures."""

    def __init__(self):
        super().__init__()
        self.whole = []

    async def capture(self, region=None):
        png = await super().capture(region)
        if region is None:
            self.whole.append(png)
        return png


async def test_every_whole_capture_the_loop_makes_is_shown_the_models_readings_and_recoverys_too(monkeypatch):
    monkeypatch.setattr(adaptation, "PLAIN_INSETS", ())
    fake = Counted()
    fake.covered = True
    shown = []
    surface = serve.Shown(fake, shown.append)
    assert (surface.name, surface.kinds, surface.reads_dom) == (fake.name, fake.kinds, fake.reads_dom)
    recorder = measure.Recorder("shown")
    played, made = await agent.play(surface, Author(fake, [source(fake)]), "slide_merge", goal=GoalSpec("actions", 12),
                                    budget=Budget(40, 20, 120), watch=recorder)
    assert played.record["outcome"] == "goal_reached", (played.record, made)
    # recovery got past the notice, and the model read the first moves before the adapter took over
    assert played.recoveries and played.record["levels"]["L2"] > 0
    assert shown == fake.whole and len(shown) > recorder.shots
    await surface.capture([0, 0, 10, 10])
    assert len(shown) == len(fake.whole)


async def test_what_is_shown_under_steady_is_the_screen_as_captured_not_the_picture_with_motion_left_out(monkeypatch):
    monkeypatch.setattr(motion, "GAP_S", 0.0)
    monkeypatch.setattr(motion, "FRAME_GAP_S", 0.0)

    class Moving:
        name, reads_dom, kinds = "moving", False, frozenset()

        def __init__(self):
            self.at, self.made = 0, []

        async def capture(self, region=None):
            # a hand moving across a still screen
            image = Image.new("RGB", (400, 300), (240, 240, 240))
            ImageDraw.Draw(image).rectangle((20 + self.at * 60, 200, 60 + self.at * 60, 240), fill=(200, 40, 40))
            self.at += 1
            out = BytesIO()
            image.save(out, format="PNG")
            self.made.append(out.getvalue())
            return self.made[-1]

    raw, shown = Moving(), []
    steady = motion.Steady(serve.Shown(raw, shown.append))
    drawn = await steady.capture()
    assert steady.animating and shown == raw.made and drawn not in shown


async def test_a_waiting_device_is_captured_once_a_second_only_while_nothing_else_captured_it():
    fake, shown = Screen(), []
    surface = serve.Shown(fake, shown.append)
    idle = asyncio.get_running_loop().create_task(surface.idle(after_s=0.05, every_s=0.02))
    await asyncio.sleep(0.2)
    waited = len(shown)
    for _ in range(10):
        await surface.capture()
        await asyncio.sleep(0.02)
    idle.cancel()
    assert waited >= 2 and len(shown) - waited <= 12


async def test_a_viewer_is_sent_the_latest_frame_only():
    viewer, sent = serve.Viewer(), []

    class Socket:
        closed = False

        async def send_str(self, text):
            sent.append(json.loads(text))

        async def send_bytes(self, data):
            sent.append(data)
            self.closed = True

    viewer.events.append({"kind": "status"})
    for frame in (b"one", b"two", b"three"):
        viewer.frame = frame
    viewer.wake.set()
    await viewer.send(Socket())
    assert sent == [{"kind": "status"}, b"three"]


async def test_a_demo_for_pages_only_refuses_an_app_without_touching_the_device(tmp_path):
    demo = serve.Demo(options(tmp_path, "--web-only"), Recorded(Asked({"url": None, "objective": "play_until_end", "game": "2048"})),
                      token=TOKEN, emulator=Running())
    client = await started(demo)
    try:
        await admitted(client)
        mine = {"Origin": origin(client)}
        socket = await client.ws_connect("/ws", headers=mine)
        assert (await client.post("/ask", json={"words": "2048 앱에서 30수"}, headers=mine)).status == 202
        events, _ = await until(socket, "refused")
        assert events[-1]["reason"] == "web_only" and demo.emulator.ensured == 0
    finally:
        await client.close()
