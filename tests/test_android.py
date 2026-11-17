"""The Android surface, against a fake adb: what it runs for a look and for each input."""
from io import BytesIO

from PIL import Image
import pytest

from gamelab import adaptation, agent, pixels, recovery
from gamelab.adaptation import Adaptation, Made, Store
from gamelab.contracts import Budget, GoalSpec
from gamelab.environments.android import AndroidSurface
from gamelab.environments import android
from gamelab.surface import HOLD, KEYS
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
        if args[1:3] == ["cmd", "package"]:
            return b"priority=0 preferredOrder=0\n" + args[-1].encode() + b"/.Main\n"
        return b""


async def test_a_look_is_a_screencap_as_opaque_rgb_and_may_be_cut_to_a_region():
    adb = Adb()
    surface = AndroidSurface(adb)
    with pixels.decode(await surface.capture(), [108, 240]) as image:
        assert image.mode == "RGB" and image.getpixel((0, 0)) == (10, 20, 30)
    with pixels.decode(await surface.capture([8, 40, 58, 140]), [50, 100]):
        pass
    assert adb.ran == [["exec-out", "screencap", "-p"]] * 2 and await surface.size() == [108, 240]


async def test_an_emulator_is_captured_by_its_console_and_by_screencap_once_that_fails():
    adb, shots = Adb(Image.new("RGBA", (108, 240), (40, 50, 60, 255))), []

    async def console(args):
        if args[:3] == ["emu", "screenrecord", "screenshot"]:
            shots.append(args[3])
            if len(shots) > 1:
                return b"KO: no display"
            adb.picture.save(args[3], format="PNG")
            return b"OK"
        return await adb(args)
    surface = AndroidSurface(console, serial="emulator-5556")
    with pixels.decode(await surface.capture(), [108, 240]) as image:
        assert image.getpixel((0, 0)) == (40, 50, 60)
    assert adb.ran == [] and surface.console
    await surface.capture()
    await surface.capture()
    # the console failed once: screencap from then on, and nothing is left behind where the console wrote
    assert len(shots) == 2 and adb.ran == [["exec-out", "screencap", "-p"]] * 2 and not surface.console
    import os
    assert not any(os.path.exists(path) for path in shots)
    assert not AndroidSurface(adb, serial="R58M123").console


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


async def test_a_stroke_is_one_shell_line_of_motion_events_through_every_point():
    adb = Adb()
    surface = AndroidSurface(adb)
    await surface.stroke([[100, 100], [100, 196], [292, 196]], 600, 150)
    [run] = adb.ran
    assert run[0] == "shell" and len(run) == 2
    lines = run[1].split("; ")
    assert lines[:2] == ["input motionevent DOWN 100 100", "sleep 0.150"]
    assert lines[-2:] == ["sleep 0.150", "input motionevent UP 292 196"]
    moves = [line for line in lines if line.startswith("input motionevent MOVE")]
    # no step longer than STEP_PX: the bend is passed, the first leg in two steps and the second in four
    assert len(moves) == 6 and "input motionevent MOVE 100 196" in moves and moves[-1] == "input motionevent MOVE 292 196"
    # the leg's time less what an event takes itself: 600 ms over the 288 pixels, 100 ms a step
    assert lines[2] == f"sleep {(100 - android.MOTION_MS) / 1000:.3f}"


async def test_a_pointer_may_be_held_between_calls_and_is_let_go_by_release():
    adb = Adb()
    surface = AndroidSurface(adb)
    assert HOLD in surface.kinds
    await surface.press(10, 20)
    await surface.move(10, 68, 0)
    await surface.release()
    await surface.release()
    assert adb.ran == [["shell", "input", "motionevent", "DOWN", "10", "20"], ["shell", "input motionevent MOVE 10 68"],
                       ["shell", "input", "motionevent", "UP", "10", "68"]]


async def test_a_double_tap_text_the_wheel_back_and_a_restart_are_adb_commands(monkeypatch):
    monkeypatch.setattr(android, "RELAUNCH_WAIT_S", 0)
    adb = Adb()
    surface = AndroidSurface(adb, package="com.example.game")
    await surface.taps(5, 6, 2, 120)
    await surface.type("go on")
    await surface.scroll(50, 60, 3)
    await surface.back()
    await surface.relaunch()
    assert [run[1:] for run in adb.ran] == [
        ["input tap 5 6; sleep 0.120; input tap 5 6"], ["input", "text", "go%son"],
        ["input", "mouse", "scroll", "50", "60", "--axis", "VSCROLL,-3"], ["input", "keyevent", "KEYCODE_BACK"],
        ["am", "force-stop", "com.example.game"],
        ["cmd", "package", "resolve-activity", "--brief", "-c", "android.intent.category.LAUNCHER", "com.example.game"],
        ["am", "start", "-n", "com.example.game/.Main"]]


async def test_the_size_and_the_app_are_asked_of_the_device():
    surface = AndroidSurface(Adb())
    assert await surface.size() == [720, 1600]
    assert await surface.address() == "android://com.example.game"
    await surface.launch("com.other")
    assert await surface.address() == "android://com.other"
    assert surface.run.ran[-1] == ["shell", "am", "start", "-n", "com.other/.Main"]


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
