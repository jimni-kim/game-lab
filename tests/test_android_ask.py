"""An app asked for in words (PLAN §14, D1 and D2): the apps a device has, named by their APKs' labels, the
steps that start a requested app's game, one game played and stopped, and the lock that keeps two runs off
one device. A fake adb and a fake aapt2; no device is used."""
import argparse
import asyncio
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from gamelab import adaptation, agent, recovery
from gamelab.adaptation import Adaptation, Made, Store
from gamelab.environments.android import AndroidSurface
from gamelab.eval import android, measure
from gamelab.model import Recorded
from fake_screen import Screen
from test_adaptation import Author
from test_android import Adb
from test_generated import source

BADGING = {"com.example.game": b"package: name='com.example.game' versionCode='7'\napplication-label:'Merge Tiles'\n"
                               b"application-label-ko:'\xed\x83\x80\xec\x9d\xbc \xed\x95\xa9\xec\xb9\x98\xea\xb8\xb0'\n"
                               b"application: label='Merge Tiles' icon='res/icon.png'\n",
           "name.boyle.chris.sgtpuzzles": b"application-label:'Puzzles'\n"}


class Device(Adb):
    """A device with two apps a person installed and one that has no launcher activity."""

    async def __call__(self, args):
        if args[1:4] == ["pm", "list", "packages"]:
            self.ran.append(args)
            return (b"package:name.boyle.chris.sgtpuzzles versionCode:149\npackage:com.example.game versionCode:7\n"
                    b"package:com.example.service versionCode:1\n")
        if args[1:4] == ["cmd", "package", "query-activities"]:
            self.ran.append(args)
            return (b"priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=false\n"
                    b"com.example.game/.Main\npriority=0 preferredOrder=0\nname.boyle.chris.sgtpuzzles/.GamePicker\n")
        if args[1:3] == ["pm", "path"]:
            self.ran.append(args)
            return f"package:/data/app/~~a/{args[3]}-1/base.apk\npackage:/data/app/~~a/{args[3]}-1/split_config.arm64_v8a.apk\n".encode()
        if args[0] == "pull":
            self.ran.append(args)
            # the pulled file says which app it is, for the fake aapt2 to read
            Path(args[2]).write_text(args[1].split("/")[-2].rsplit("-", 1)[0])
            return b""
        return await super().__call__(args)


class Aapt:
    def __init__(self, failing=()):
        self.read, self.failing = [], set(failing)

    async def __call__(self, args):
        package = Path(args[-1]).read_text()
        self.read.append(package)
        if package in self.failing:
            raise RuntimeError("aapt2: not a valid APK")
        return BADGING[package]


async def test_the_apps_shown_are_those_with_a_launcher_named_by_their_apks_and_the_labels_are_kept(tmp_path):
    adb, aapt = Device(), Aapt()
    apps = await android.installed(adb, read=aapt, kept=tmp_path, named={})
    assert apps == [{"package": "com.example.game", "label": "타일 합치기 / Merge Tiles"},
                    {"package": "name.boyle.chris.sgtpuzzles", "label": "Puzzles"}]
    # only the base APK is pulled, and each app's label is kept by its version
    assert [run[1] for run in adb.ran if run[0] == "pull"] == ["/data/app/~~a/com.example.game-1/base.apk",
                                                              "/data/app/~~a/name.boyle.chris.sgtpuzzles-1/base.apk"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["com.example.game-7.json", "name.boyle.chris.sgtpuzzles-149.json"]
    again = Device()
    assert await android.installed(again, read=aapt, kept=tmp_path, named={}) == apps
    assert len(aapt.read) == 2 and not any(run[0] == "pull" for run in again.ran)


async def test_an_apk_that_cannot_be_read_names_its_app_by_its_package_and_a_name_given_by_hand_comes_first(tmp_path):
    apps = await android.installed(Device(), read=Aapt(failing={"com.example.game"}), kept=tmp_path,
                                   named={"name.boyle.chris.sgtpuzzles": {"label": "퍼즐 모음", "clear": True}})
    assert apps == [{"package": "com.example.game", "label": "com.example.game"},
                    {"package": "name.boyle.chris.sgtpuzzles", "label": "퍼즐 모음"}]


async def test_an_app_apps_json_hides_is_not_among_those_a_request_is_read_against(tmp_path):
    aapt = Aapt()
    apps = await android.installed(Device(), read=aapt, kept=tmp_path, named={"com.example.game": {"hide": True}})
    assert apps == [{"package": "name.boyle.chris.sgtpuzzles", "label": "Puzzles"}]
    # its APK is not even read for a name
    assert aapt.read == ["name.boyle.chris.sgtpuzzles"]
    # the app on the demo's device that is not a game is hidden in the file itself
    assert android.chosen()["ai.agentic.army"] == {"hide": True}


async def test_a_requested_apps_game_starts_with_it_stopped_and_started_again_its_data_cleared_only_when_asked():
    assert android.prepared("com.example.game", {}) == [{"force_stop": "com.example.game"}, {"launch": "com.example.game"},
                                                       {"wait_ms": android.LAUNCH_WAIT_MS}]
    steps = android.prepared("com.example.game", {"com.example.game": {"clear": True}})
    assert [next(iter(step)) for step in steps] == ["force_stop", "clear", "launch", "wait_ms"]
    adb = Adb()
    await android.setup(AndroidSurface(adb), steps[:3])
    assert adb.ran[0] == ["shell", "am", "force-stop", "com.example.game"]
    assert adb.ran[1] == ["shell", "pm", "clear", "com.example.game"]
    assert adb.ran[-1][:3] == ["shell", "am", "start"]


def game_args(tmp_path, *more):
    parser = argparse.ArgumentParser()
    android.configure(parser)
    return parser.parse_args(["--compute", "20", "--out", str(tmp_path / "out"), "--adapter-store", str(tmp_path / "kept"),
                              "--max-actions", "30", "--max-seconds", "60", *more])


def screen_device(tmp_path):
    """A device whose screen is the fake 2048 game, with an adapter kept for its app."""
    game = Screen()
    earlier = Screen()

    async def kept():
        trace = await Adaptation(Author(earlier, []), "slide_merge").explore(earlier)
        trace.site = "android://com.example.game"
        Store(tmp_path / "kept").save(trace, Made(source(game).strip(), 1, False, {"passed": True}))

    class Played(Adb):
        async def __call__(self, args):
            if args[:2] == ["exec-out", "screencap"]:
                self.picture = Image.open(BytesIO(await game.capture())).convert("RGBA")
            elif args[1:3] == ["input", "swipe"]:
                await game.drag(*map(float, args[3:7]), int(args[7]))
            return await super().__call__(args)
    return game, Played(), kept


@pytest.fixture
def quick(monkeypatch, tmp_path):
    for module, name in ((adaptation, "SETTLE_S"), (adaptation, "FRAME_GAP_S"), (agent, "STILL_GAP_S"), (recovery, "SETTLE_S")):
        monkeypatch.setattr(module, name, 0.0)
    monkeypatch.setattr(android, "LAUNCH_WAIT_MS", 0)
    monkeypatch.setattr(measure, "LOCKS", tmp_path / ".lock")


async def test_one_game_on_the_device_is_played_from_its_steps_and_a_cancel_ends_it(tmp_path, quick):
    game, adb, kept = screen_device(tmp_path)
    await kept()
    args = game_args(tmp_path, "--objective", "actions", "--target", "3")
    surface = AndroidSurface(adb, package="com.example.game")
    model = Recorded(Author(game, []))
    result = await android.play_one(args, 1, surface, "com.example.game", model, steps=android.prepared("com.example.game", {}))
    assert (result["outcome"], result["reason"], result["actions_used"]) == ("goal_reached", "actions", 3), result
    assert adb.ran[0] == ["shell", "am", "force-stop", "com.example.game"] and adb.ran[2][:3] == ["shell", "am", "start"]
    assert (tmp_path / "out" / "com.example.game-01.json").is_file()
    cancel = asyncio.Event()
    cancel.set()
    stopped = await android.play_one(args, 2, surface, "com.example.game", model, cancel=cancel)
    assert (stopped["outcome"], stopped["reason"], stopped["actions_used"]) == ("budget_exhausted", "cancelled", 0)


def test_a_second_run_on_a_held_device_is_refused_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(measure, "LOCKS", tmp_path / ".lock")
    with measure.lock("android-emulator-5554"):
        with pytest.raises(measure.Busy):
            with measure.lock("android-emulator-5554"):
                pass
        # another device is free
        with measure.lock("android-R5CT1234"):
            pass
    with measure.lock("android-emulator-5554"):
        pass


async def test_work_that_goes_on_from_the_screen_does_not_start_the_app_again(tmp_path, quick):
    """PLAN §25, R4: a request to go on from what is open is played from the screen as it stands. The app in
    front is not touched; an app behind another is brought to the front, never stopped first."""
    from gamelab.contracts import GoalSpec, Scope

    args = game_args(tmp_path)
    args.package, args.asked = "com.example.game", {"goal": GoalSpec("levels", 3, scope=Scope.CURRENT_GAME), "hint": None}
    assert await android.steps_for(args, AndroidSurface(Adb())) == []
    args.package = "name.boyle.chris.sgtpuzzles"
    steps = await android.steps_for(args, AndroidSurface(Adb()))
    assert steps == [{"launch": "name.boyle.chris.sgtpuzzles"}, {"wait_ms": android.LAUNCH_WAIT_MS}]
    # the same request started afresh stops the app first, and --resume holds any run to the screen as it stands
    args.asked = {"goal": GoalSpec("levels", 3, scope=Scope.NEW_GAME), "hint": None}
    assert next(iter((await android.steps_for(args, AndroidSurface(Adb())))[0])) == "force_stop"
    resumed = game_args(tmp_path, "--package", "com.example.game", "--resume")
    resumed.asked = None
    assert await android.steps_for(resumed, AndroidSurface(Adb())) == []
    assert measure.goal_and_budget(resumed)[0].scope == "current_game"


async def test_a_game_played_on_from_the_screen_is_handed_back_with_where_it_stopped(tmp_path, quick):
    game, adb, kept = screen_device(tmp_path)
    await kept()
    args = game_args(tmp_path, "--objective", "actions", "--target", "3", "--resume", "--allow", "boosters", "--stop", "lost")
    args.asked = None
    surface = AndroidSurface(adb, package="com.example.game")
    result = await android.play_one(args, 1, surface, "com.example.game", Recorded(Author(game, [])),
                                    steps=await android.steps_for(args, surface))
    assert (result["outcome"], result["reason"]) == ("goal_reached", "actions"), result
    # nothing was stopped or started: the app was in front
    assert not any(run[1:3] in (["am", "force-stop"], ["am", "start"]) for run in adb.ran)
    handback = result["work"]
    assert handback["goal"]["scope"] == "current_game" and handback["goal"]["allowed_meta_actions"] == ["boosters"]
    assert handback["goal"]["stop_conditions"] == ["lost"] and handback["spent"]["inputs"] == 3
    assert handback["stopped_at"]["where"] == "android://com.example.game" and result["told"].startswith("goal_reached")
