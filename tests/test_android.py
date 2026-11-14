"""The Android surface, against a fake adb: what it runs for a look and for each input."""
from io import BytesIO

from PIL import Image
import pytest

from gamelab import adaptation, agent, pixels, recovery
from gamelab.adaptation import Adaptation, Made, Store
from gamelab.contracts import Budget, GoalSpec
from gamelab.environments.android import AndroidSurface
from gamelab.surface import KEYS
from fake_screen import Screen
from test_adaptation import Author
from test_generated import source


class Adb:
    def __init__(self, picture=None):
        self.ran = []
        self.picture = picture or Image.new("RGBA", (108, 240), (10, 20, 30, 255))

    async def __call__(self, args):
        self.ran.append(args)
        if args[:2] == ["exec-out", "screencap"]:
            out = BytesIO()
            self.picture.save(out, format="PNG")
            return out.getvalue()
        if args[1:3] == ["wm", "size"]:
            return b"Physical size: 1080x2400\nOverride size: 720x1600\n"
        if args[1:3] == ["dumpsys", "window"]:
            return b"  mCurrentFocus=Window{1a2b3c u0 com.example.game/com.example.game.Main}\n"
        return b""


async def test_a_look_is_a_screencap_as_opaque_rgb_and_may_be_cut_to_a_region():
    adb = Adb()
    surface = AndroidSurface(adb)
    with pixels.decode(await surface.capture(), [108, 240]) as image:
        assert image.mode == "RGB" and image.getpixel((0, 0)) == (10, 20, 30)
    with pixels.decode(await surface.capture([8, 40, 58, 140]), [50, 100]):
        pass
    assert adb.ran == [["exec-out", "screencap", "-p"]] * 2 and await surface.size() == [108, 240]


async def test_a_capture_that_is_no_picture_is_an_unread_look_not_a_crash():
    async def broken(args):
        return b"error: device offline"
    with pytest.raises(ValueError, match="capture_failed"):
        await AndroidSurface(broken).capture()


async def test_inputs_are_adb_input_commands_in_device_pixels():
    adb = Adb()
    surface = AndroidSurface(adb, keys=True)
    await surface.tap(100.4, 200.6, 60)
    await surface.tap(5, 6, 800)
    await surface.drag(10, 20, 300.2, 20, 120)
    await surface.key("ArrowLeft")
    assert [run[1:] for run in adb.ran] == [["input", "tap", "100", "201"], ["input", "swipe", "5", "6", "5", "6", "800"],
                                           ["input", "swipe", "10", "20", "300", "20", "120"],
                                           ["input", "keyevent", "KEYCODE_DPAD_LEFT"]]
    assert KEYS in surface.kinds and KEYS not in AndroidSurface(adb).kinds


async def test_the_size_and_the_app_are_asked_of_the_device():
    surface = AndroidSurface(Adb())
    assert await surface.size() == [720, 1600]
    assert await surface.address() == "android://com.example.game"
    await surface.launch("com.other")
    assert await surface.address() == "android://com.other"


async def test_a_game_is_played_on_a_device_by_swipes(tmp_path, monkeypatch):
    """The whole path on a device-shaped surface: a capture with alpha, read by the adapter kept for the app, a
    direction sent as `input swipe` inside the board, and the result confirmed on the next capture."""
    for module, name in ((adaptation, "SETTLE_S"), (adaptation, "FRAME_GAP_S"), (agent, "STILL_GAP_S"), (recovery, "SETTLE_S")):
        monkeypatch.setattr(module, name, 0.0)
    game = Screen()
    earlier = Screen()
    trace = await Adaptation(Author(earlier, []), "slide_merge").explore(earlier)
    trace.site = "android://com.example.game"
    Store(tmp_path).save(trace, Made(source(game).strip(), 1, False, {"passed": True}))

    class Device(Adb):
        async def __call__(self, args):
            if args[:2] == ["exec-out", "screencap"]:
                self.picture = Image.open(BytesIO(await game.capture())).convert("RGBA")
            elif args[1:3] == ["input", "swipe"]:
                await game.drag(*map(float, args[3:7]), int(args[7]))
            return await super().__call__(args)

    adb = Device()
    author = Author(game, [])
    played, made = await agent.play(AndroidSurface(adb), author, "slide_merge", goal=GoalSpec("actions", 2),
                                    budget=Budget(3, 20, 60), store=Store(tmp_path))
    assert played.record["outcome"] == "goal_reached" and played.record["confirmed_actions"] == 2, (played.record, made)
    assert made["reused"] and not author.asked
    assert len(game.drags) == 2 and sum(run[1:3] == ["input", "swipe"] for run in adb.ran) == 2
