"""The Android emulator a demo plays on (PLAN §14, D4): the one running the AVD it is given (`army-game` for
the demo, `game-lab` for a session's measuring), found by its AVD's name, else started for it and shut down
again afterwards.

A device is this demo's when it is an emulator whose console names the AVD (`adb -s SERIAL emu avd name`):
its serial (`emulator-5554`, `emulator-5556`) is given by the order the emulators were started in and says
nothing. Another AVD's emulator or a phone attached beside it is never played on, and does not keep this AVD
from being started. Given a serial (`prefer`), that device alone is played on, whatever its AVD.

The emulator is started as a person would start it (`emulator -avd NAME -no-snapshot-load
-no-snapshot-save`), in a session of its own so it outlives whatever started it, and is waited for until the
system says it has booted. Its GPU and snapshot options are never changed: loading a snapshot once threw
away the device's sign-in and apps (PLAN §11, Android). An emulator that was running before is never shut
down here.
"""
from __future__ import annotations

import asyncio
import subprocess
import time
from typing import Any, Callable, Literal

from ..environments.android import SDK, Run, command
from . import measure

AVD = "game-lab"
EMULATOR = SDK / "emulator" / "emulator"
# a cold boot of the AVD takes about a minute on this Mac; past this it is taken not to have started
BOOT_LIMIT_S, BOOT_POLL_S = 180.0, 2.0
# an emulator listed whose console has not named its AVD yet (one just started) is given this long before
# the AVD is taken not to be running
NAMING_S = 10.0
LOG = measure.ROOT / "out" / "emulator.log"
State = Literal["on", "starting", "off"]


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
                 limit_s: float = BOOT_LIMIT_S, poll_s: float = BOOT_POLL_S, prefer: str | None = None,
                 naming_s: float = NAMING_S) -> None:
        self.avd, self.adb, self.start = avd, adb or command(), start
        # a serial asked for: that device alone is played on (another run's emulator beside this one's)
        self.prefer = prefer
        self.limit_s, self.poll_s, self.naming_s = limit_s, poll_s, naming_s
        self.started: Any = None
        self.serial: str | None = None
        # the AVD each emulator's console named, by serial, while it stays listed
        self.names: dict[str, str] = {}
        # one start at a time: a button and a request asking together start it once
        self.lock = asyncio.Lock()
        self.ensuring = False

    # ── which device is this demo's ───────────────────────────────────

    async def _name(self, serial: str) -> str | None:
        """The AVD an emulator runs, as its console says (`emu avd name`: the name, then OK); None for a
        device that is not an emulator or whose console does not answer."""
        if serial in self.names:
            return self.names[serial]
        if not serial.startswith("emulator-"):
            return None
        try:
            said = (await self.adb(["-s", serial, "emu", "avd", "name"])).decode(errors="replace").splitlines()
        except RuntimeError:
            return None
        name = said[0].strip() if said and said[0].strip() and said[0].strip() != "OK" else None
        if name is not None:
            self.names[serial] = name
        return name

    async def _listed(self) -> tuple[list[tuple[str, str]], bool]:
        """The devices listed that are this demo's, with their states, and whether one may be this demo's
        without saying so yet: an emulator listed whose console has not named its AVD, or (given a serial) any
        device listed at all."""
        listed = attached(await self.adb(["devices"]))
        for gone in set(self.names) - {serial for serial, _ in listed}:
            del self.names[gone]
        if self.prefer is not None:
            # (as before names were read: the AVD is started only when no device at all is listed)
            return [(serial, state) for serial, state in listed if serial == self.prefer], bool(listed)
        mine, unnamed = [], False
        for serial, state in listed:
            name = await self._name(serial)
            if name == self.avd:
                mine.append((serial, state))
            elif name is None and serial.startswith("emulator-"):
                unnamed = True
        return mine, unnamed

    async def _ready(self, mine: list[tuple[str, str]]) -> str | None:
        for serial, state in mine:
            if state == "device" and await self._booted(serial):
                return serial
        return None

    async def look(self) -> tuple[State, str | None]:
        """This demo's device as it stands, nothing started: `on` with its serial when it has booted;
        `starting` when it is being started here, or is listed and has not booted yet; `off` when neither."""
        try:
            mine, _ = await self._listed()
            serial = await self._ready(mine)
        except RuntimeError:
            mine, serial = [], None
        if serial is not None:
            return "on", serial
        return ("starting" if mine or self.ensuring or self._running() else "off"), None

    async def find(self) -> str | None:
        """The serial of this demo's device when it has booted, else None; nothing is started."""
        return (await self.look())[1]

    async def state(self) -> State:
        return (await self.look())[0]

    def _running(self) -> bool:
        """Whether the emulator started here is still running (as far as its process tells)."""
        if self.started is None:
            return False
        poll = getattr(self.started, "poll", None)
        return poll is None or poll() is None

    # ── starting it ───────────────────────────────────────────────────

    async def ensure(self, starting: Callable[[], None] | None = None) -> str:
        """The serial of this demo's device once it has booted: one running, else the emulator started and
        waited for. `starting` is told when the emulator has to be started."""
        async with self.lock:
            self.ensuring = True
            try:
                return await self._ensure(starting)
            finally:
                self.ensuring = False

    async def _ensure(self, starting: Callable[[], None] | None) -> str:
        began = time.monotonic()
        deadline, started = began + self.limit_s, False
        while True:
            mine, unnamed = await self._listed()
            serial = await self._ready(mine)
            if serial is not None:
                self.serial = serial
                return serial
            # one listed in any state (one still booting) is waited for, never doubled, and it is started once at
            # most; an emulator not yet named may be this AVD just started, and is given a little while first
            if self.prefer is not None:
                wanted = not mine and not unnamed
            else:
                wanted = not mine and (not unnamed or time.monotonic() - began >= self.naming_s)
            if wanted and not started and not self._running():
                started = True
                if starting is not None:
                    starting()
                self.started = self.start([str(EMULATOR), "-avd", self.avd, "-no-snapshot-load", "-no-snapshot-save"])
            if time.monotonic() >= deadline:
                raise RuntimeError(f"no Android device booted within {self.limit_s:.0f} s")
            await asyncio.sleep(self.poll_s)

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
