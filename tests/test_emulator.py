"""The emulator a demo plays on (PLAN §14, D4; PLAN §35 the demo's device), against a fake adb and a fake start:
no emulator is run."""
import asyncio

import pytest

from gamelab.eval.emulator import Emulator


class Adb:
    """`adb devices` lists `listed` in turn (the last one again), each row `(serial, state)` or `(serial, state,
    avd)`: an emulator's console names its AVD (`emu avd name`) when it has one, and fails when it has none
    (not an emulator, or its console not up yet). A device boots after `booting` asks."""

    def __init__(self, *listed, booting=0):
        self.listed, self.booting, self.ran = list(listed), booting, []
        self.rows = self.listed[0] if self.listed else []

    async def __call__(self, args):
        self.ran.append(args)
        if args == ["devices"]:
            self.rows = self.listed.pop(0) if len(self.listed) > 1 else self.listed[0]
            return ("List of devices attached\n" + "".join(f"{row[0]}\t{row[1]}\n" for row in self.rows)).encode()
        if args[2:] == ["emu", "avd", "name"]:
            avd = next((row[2] for row in self.rows if row[0] == args[1] and len(row) > 2), None)
            if avd is None:
                raise RuntimeError("error: could not connect to the console")
            return f"{avd}\r\nOK\r\n".encode()
        if args[2:] == ["shell", "getprop", "sys.boot_completed"]:
            self.booting -= 1
            return b"1\n" if self.booting < 0 else b"\n"
        return b""

    def named(self):
        return [run[1] for run in self.ran if run[2:] == ["emu", "avd", "name"]]


class Starts:
    def __init__(self):
        self.started = []

    def __call__(self, args):
        self.started.append(args)
        return object()


async def test_the_emulator_running_the_avd_already_is_used_and_never_shut_down():
    adb, start = Adb([("emulator-5554", "device", "game-lab")]), Starts()
    emulator = Emulator(adb=adb, start=start, poll_s=0)
    assert await emulator.ensure() == "emulator-5554"
    await emulator.stop()
    assert start.started == [] and not any(run[2:] == ["emu", "kill"] for run in adb.ran)


async def test_with_no_device_the_avd_is_started_without_its_snapshot_waited_for_and_shut_down_afterwards():
    adb, start, told = Adb([], [("emulator-5554", "offline")], [("emulator-5554", "device", "game-lab")], booting=2), Starts(), []
    emulator = Emulator(adb=adb, start=start, poll_s=0)
    assert await emulator.ensure(lambda: told.append("starting")) == "emulator-5554"
    [started] = start.started
    assert started[0].endswith("emulator/emulator") and started[1:] == ["-avd", "game-lab", "-no-snapshot-load", "-no-snapshot-save"]
    assert told == ["starting"] and not any("-gpu" in arg for arg in started)
    await emulator.stop()
    assert adb.ran[-1] == ["-s", "emulator-5554", "emu", "kill"]


async def test_the_avds_emulator_listed_while_it_boots_is_waited_for_not_started_twice():
    adb, start = Adb([("emulator-5554", "offline", "game-lab")], [("emulator-5554", "device", "game-lab")], booting=1), Starts()
    emulator = Emulator(adb=adb, start=start, poll_s=0)
    assert await emulator.ensure() == "emulator-5554" and start.started == []


async def test_an_emulator_that_never_boots_is_an_error_not_a_wait_without_end():
    emulator = Emulator(adb=Adb([]), start=Starts(), limit_s=0.05, poll_s=0.01)
    with pytest.raises(RuntimeError, match="booted"):
        await emulator.ensure()


# ── by the AVD's name (PLAN §35: the demo on army-game, a session's measuring on game-lab) ──


async def test_of_several_devices_the_one_running_the_avd_is_played_on_whatever_its_serial():
    adb, start = Adb([("emulator-5554", "device", "game-lab"), ("emulator-5556", "device", "army-game"),
                      ("R58M12ABC", "device")]), Starts()
    emulator = Emulator("army-game", adb=adb, start=start, poll_s=0)
    assert await emulator.ensure() == "emulator-5556" and start.started == []
    # a phone is not asked for an AVD's name; each emulator's name is read once while it stays listed
    assert await emulator.ensure() == "emulator-5556"
    assert sorted(adb.named()) == ["emulator-5554", "emulator-5556"]
    other = Emulator("game-lab", adb=Adb([("emulator-5554", "device", "game-lab"), ("emulator-5556", "device", "army-game")]),
                     start=Starts(), poll_s=0)
    assert await other.ensure() == "emulator-5554"


async def test_with_another_avd_and_a_phone_attached_alone_this_avd_is_started_and_neither_is_played_on():
    adb, start = Adb([("emulator-5554", "device", "game-lab"), ("R58M12ABC", "device")],
                     [("emulator-5554", "device", "game-lab"), ("R58M12ABC", "device"), ("emulator-5556", "offline")],
                     [("emulator-5554", "device", "game-lab"), ("R58M12ABC", "device"), ("emulator-5556", "device", "army-game")]), Starts()
    emulator = Emulator("army-game", adb=adb, start=start, poll_s=0)
    assert await emulator.ensure() == "emulator-5556"
    [started] = start.started
    assert started[1:] == ["-avd", "army-game", "-no-snapshot-load", "-no-snapshot-save"]
    # only this AVD's device was asked whether it had booted
    assert {run[1] for run in adb.ran if run[2:] == ["shell", "getprop", "sys.boot_completed"]} == {"emulator-5556"}
    # and it is the one shut down: the other AVD's emulator is left as it is
    await emulator.stop()
    assert [run for run in adb.ran if run[2:] == ["emu", "kill"]] == [["-s", "emulator-5556", "emu", "kill"]]


async def test_an_emulator_whose_console_has_not_named_its_avd_yet_is_given_a_while_before_this_avd_is_started():
    # (one just started by someone else: it names itself on the third look)
    adb, start = Adb([("emulator-5554", "offline")], [("emulator-5554", "offline")],
                     [("emulator-5554", "device", "army-game")]), Starts()
    assert await Emulator("army-game", adb=adb, start=start, poll_s=0).ensure() == "emulator-5554" and start.started == []
    # one that never names an AVD is not waited on past that while
    stuck, start = Adb([("emulator-5554", "device")]), Starts()
    with pytest.raises(RuntimeError):
        await Emulator("army-game", adb=stuck, start=start, limit_s=0.1, poll_s=0.01, naming_s=0.03).ensure()
    assert len(start.started) == 1


async def test_asked_twice_at_once_the_avd_is_started_once():
    adb, start = Adb([], [], [], [("emulator-5554", "offline")], [("emulator-5554", "device", "army-game")], booting=1), Starts()
    emulator, told = Emulator("army-game", adb=adb, start=start, poll_s=0.01), []
    serials = await asyncio.gather(emulator.ensure(lambda: told.append("button")), emulator.ensure(lambda: told.append("request")))
    assert serials == ["emulator-5554", "emulator-5554"] and len(start.started) == 1 and told == ["button"]


async def test_what_the_device_is_now_is_looked_at_without_starting_anything():
    start = Starts()
    off = Emulator("army-game", adb=Adb([("emulator-5554", "device", "game-lab")]), start=start)
    assert await off.find() is None and await off.state() == "off" and await off.look() == ("off", None)
    booting = Emulator("army-game", adb=Adb([("emulator-5556", "offline", "army-game")]), start=start)
    assert await booting.find() is None and await booting.state() == "starting"
    on = Emulator("army-game", adb=Adb([("emulator-5554", "device", "game-lab"), ("emulator-5556", "device", "army-game")]), start=start)
    assert await on.find() == "emulator-5556" and await on.look() == ("on", "emulator-5556")
    assert start.started == []

    # started here and not listed yet: starting; its process gone: off again
    class Process:
        code = None

        def poll(self):
            return self.code

    process = Process()
    mine = Emulator("army-game", adb=Adb([]), start=lambda args: process, limit_s=0.02, poll_s=0.01)
    with pytest.raises(RuntimeError):
        await mine.ensure()
    assert await mine.state() == "starting"
    process.code = 1
    assert await mine.state() == "off"

    # adb itself failing is nothing found
    async def broken(args):
        raise RuntimeError("adb: no server")
    assert await Emulator("army-game", adb=broken, start=start).look() == ("off", None)


async def test_while_it_is_being_started_its_state_is_starting():
    adb, start = Adb([], [("emulator-5554", "offline")], [("emulator-5554", "device", "army-game")], booting=3), Starts()
    emulator = Emulator("army-game", adb=adb, start=start, poll_s=0.02)
    starting = asyncio.get_running_loop().create_task(emulator.ensure())
    await asyncio.sleep(0.01)
    assert emulator.ensuring and await emulator.state() == "starting"
    assert await starting == "emulator-5554" and not emulator.ensuring and await emulator.state() == "on"


async def test_of_several_devices_the_one_asked_for_is_played_on_and_no_other_in_its_place():
    # a serial asked for is played on whatever its AVD, as before the names were read
    adb = Adb([("emulator-5554", "device"), ("emulator-5556", "device")])
    assert await Emulator(adb=adb, start=Starts(), poll_s=0, prefer="emulator-5556").ensure() == "emulator-5556"
    assert adb.named() == []
    alone, start = Adb([("emulator-5554", "device")]), Starts()
    with pytest.raises(RuntimeError):
        await Emulator(adb=alone, start=start, limit_s=0.05, poll_s=0.01, prefer="emulator-5556").ensure()
    # a device listed keeps the AVD from being started in its place; with none listed at all it is started
    assert start.started == []
    nothing, start = Adb([], [("emulator-5556", "device")]), Starts()
    assert await Emulator(adb=nothing, start=start, poll_s=0, prefer="emulator-5556").ensure() == "emulator-5556"
    assert len(start.started) == 1
