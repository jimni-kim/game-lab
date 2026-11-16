"""The Android emulator a demo plays on (PLAN §14, D4): the device already attached when there is one, else
the AVD `army-game` started for it and shut down again afterwards.

The emulator is started as a person would start it (`emulator -avd army-game -no-snapshot-load
-no-snapshot-save`), in a session of its own so it outlives whatever started it, and is waited for until the
system says it has booted. Its GPU and snapshot options are never changed: loading a snapshot once threw
away the device's sign-in and apps (PLAN §11, Android). An emulator that was running before is never shut
down here.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import time
from typing import Any, Callable

from ..environments.android import SDK, Run, command
from . import measure

AVD = "army-game"
EMULATOR = SDK / "emulator" / "emulator"
# a cold boot of the AVD takes about a minute on this Mac; past this it is taken not to have started
BOOT_LIMIT_S, BOOT_POLL_S = 180.0, 2.0
LOG = measure.ROOT / "out" / "emulator.log"


def attached(listing: bytes) -> list[tuple[str, str]]:
    """Each device `adb devices` lists, with its state (`device`, `offline`, `unauthorized`)."""
    rows = [line.split() for line in listing.decode(errors="replace").splitlines()[1:]]
    return [(row[0], row[1]) for row in rows if len(row) >= 2]


def spawn(args: list[str]) -> Any:
    """The emulator started in a session of its own, its output in out/emulator.log."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "ab") as log:
        return subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


class Emulator:
    """The device a demo plays on; `started` when it was started here (and so is shut down here)."""

    def __init__(self, avd: str = AVD, *, adb: Run | None = None, start: Callable[[list[str]], Any] = spawn,
                 limit_s: float = BOOT_LIMIT_S, poll_s: float = BOOT_POLL_S) -> None:
        self.avd, self.adb, self.start = avd, adb or command(), start
        self.limit_s, self.poll_s = limit_s, poll_s
        self.started: Any = None
        self.serial: str | None = None

    async def ensure(self, starting: Callable[[], None] | None = None) -> str:
        """The serial of a device that has booted: one attached, else the emulator started and waited for.
        `starting` is told when the emulator has to be started."""
        listed = attached(await self.adb(["devices"]))
        # a device listed in any state (one still booting, perhaps another run's) is waited for, never doubled
        if not listed and self.started is None:
            if starting is not None:
                starting()
            self.started = self.start([str(EMULATOR), "-avd", self.avd, "-no-snapshot-load", "-no-snapshot-save"])
        deadline = time.monotonic() + self.limit_s
        while True:
            ready = [serial for serial, state in listed if state == "device"]
            if ready and await self._booted(ready[0]):
                self.serial = ready[0]
                return self.serial
            if time.monotonic() >= deadline:
                raise RuntimeError(f"no Android device booted within {self.limit_s:.0f} s")
            await asyncio.sleep(self.poll_s)
            listed = attached(await self.adb(["devices"]))

    async def _booted(self, serial: str) -> bool:
        try:
            return (await self.adb(["-s", serial, "shell", "getprop", "sys.boot_completed"])).strip() == b"1"
        except RuntimeError:
            return False

    async def stop(self) -> None:
        """Shut the emulator down if it was started here; one that was running before is left as it is."""
        if self.started is None:
            return
        if self.serial is not None:
            try:
                await self.adb(["-s", self.serial, "emu", "kill"])
            except RuntimeError:
                pass
        elif hasattr(self.started, "terminate"):
            # it never came up as a device: its process is all there is to stop
            self.started.terminate()
        self.started = None
