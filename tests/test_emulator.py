"""The emulator a demo plays on (PLAN §14, D4), against a fake adb and a fake start: no emulator is run."""
import pytest

from gamelab.eval.emulator import Emulator


class Adb:
    """`adb devices` lists `listed` in turn (the last one again); the device boots after `booting` asks."""

    def __init__(self, *listed, booting=0):
        self.listed, self.booting, self.ran = list(listed), booting, []

    async def __call__(self, args):
        self.ran.append(args)
        if args == ["devices"]:
            rows = self.listed.pop(0) if len(self.listed) > 1 else self.listed[0]
            return ("List of devices attached\n" + "".join(f"{serial}\t{state}\n" for serial, state in rows)).encode()
        if args[2:] == ["shell", "getprop", "sys.boot_completed"]:
            self.booting -= 1
            return b"1\n" if self.booting < 0 else b"\n"
        return b""


class Starts:
    def __init__(self):
        self.started = []

    def __call__(self, args):
        self.started.append(args)
        return object()


async def test_a_device_already_attached_is_used_and_never_shut_down():
    adb, start = Adb([("emulator-5554", "device")]), Starts()
    emulator = Emulator(adb=adb, start=start, poll_s=0)
    assert await emulator.ensure() == "emulator-5554"
    await emulator.stop()
    assert start.started == [] and not any("emu" in run for run in adb.ran)


async def test_with_no_device_the_avd_is_started_without_its_snapshot_waited_for_and_shut_down_afterwards():
    adb, start, told = Adb([], [("emulator-5554", "offline")], [("emulator-5554", "device")], booting=2), Starts(), []
    emulator = Emulator(adb=adb, start=start, poll_s=0)
    assert await emulator.ensure(lambda: told.append("starting")) == "emulator-5554"
    [started] = start.started
    assert started[0].endswith("emulator/emulator") and started[1:] == ["-avd", "game-lab", "-no-snapshot-load", "-no-snapshot-save"]
    assert told == ["starting"] and not any("-gpu" in arg for arg in started)
    await emulator.stop()
    assert adb.ran[-1] == ["-s", "emulator-5554", "emu", "kill"]


async def test_a_device_listed_while_it_boots_is_waited_for_not_started_twice():
    adb, start = Adb([("emulator-5554", "offline")], [("emulator-5554", "device")], booting=1), Starts()
    emulator = Emulator(adb=adb, start=start, poll_s=0)
    assert await emulator.ensure() == "emulator-5554" and start.started == []


async def test_an_emulator_that_never_boots_is_an_error_not_a_wait_without_end():
    emulator = Emulator(adb=Adb([]), start=Starts(), limit_s=0.05, poll_s=0.01)
    with pytest.raises(RuntimeError, match="booted"):
        await emulator.ensure()


async def test_of_several_devices_the_one_asked_for_is_played_on_and_no_other_in_its_place():
    adb = Adb([("emulator-5554", "device"), ("emulator-5556", "device")])
    assert await Emulator(adb=adb, start=Starts(), poll_s=0, prefer="emulator-5556").ensure() == "emulator-5556"
    alone = Adb([("emulator-5554", "device")])
    with pytest.raises(RuntimeError):
        await Emulator(adb=alone, start=Starts(), limit_s=0.05, poll_s=0.01, prefer="emulator-5556").ensure()
