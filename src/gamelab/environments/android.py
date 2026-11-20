"""An Android device or emulator as a surface, driven with adb alone.

A look is `adb exec-out screencap -p`, or on an emulator its console's own screenshot (`adb emu screenrecord
screenshot`), which takes about a tenth of a second where `screencap` took two (2026-11-17, `game-lab` at
1080×2400 under software rendering: 0.1 s against 1.8 to 2.7 s, the same picture); inputs are `adb shell input tap | swipe | keyevent | text`, and a pointer
held, moved and let go is `input motionevent DOWN | MOVE | UP` (a stroke's whole way sent as one shell line, so
it is not slowed by a round trip a point). Coordinates are the device's own pixels, as its captures show them.
Nothing is installed on the device.

A game is not given keys here unless asked (`keys=True`, a D-pad's key events): the environment swipes a
direction across the board instead, as a hand would.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Awaitable, Callable

from .. import pixels
from ..surface import BACK, DRAG, HOLD, KEYS, RELAUNCH, SCROLL, TAP, TEXT, segments

SDK = Path("/opt/homebrew/share/android-commandlinetools")
ADB_VARIABLE = "GAMELAB_ADB"
# how long one adb command may take: a capture of a large screen over a slow emulator link
COMMAND_TIMEOUT_S = 20.0
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# a press held longer than this is sent as a swipe that does not move (`input tap` holds for no set time)
LONG_PRESS_MS = 200
# a held pointer moves at most this far (device pixels) between two MOVE events, in at most this many a leg;
# one `input motionevent` takes about this long itself (measured 40 to 80 ms on the emulator, 2026-11-15), so
# only the rest of a step's time is slept
STEP_PX, STEPS_MOST, MOTION_MS = 48, 24, 50
# a held pointer swept straight to a place (`sweep`) moves in this many steps at most: each MOVE costs MOTION_MS
# whatever time the move was given, so a sweep across the screen in STEPS_MOST steps took about 1.3 s
SWEEP_STEPS = 6
# an app started again draws its first screen: wait this long
RELAUNCH_WAIT_S = 3.0
KEYCODES = {"ArrowUp": "KEYCODE_DPAD_UP", "ArrowDown": "KEYCODE_DPAD_DOWN", "ArrowLeft": "KEYCODE_DPAD_LEFT",
            "ArrowRight": "KEYCODE_DPAD_RIGHT", "Enter": "KEYCODE_ENTER", "Escape": "KEYCODE_BACK", " ": "KEYCODE_SPACE"}
_FOCUS = re.compile(r"mCurrentFocus=Window\{\S+ \S+ ([\w.]+)/")
# how the display is turned from its natural way: the emulator's console takes the screen as it is in memory, not
# as it is shown, and is turned by this to be what `screencap` gives (2026-11-17, a game held sideways: its
# captures came portrait, and the model's taps on them landed elsewhere)
_ROTATION = re.compile(r"mCurrentRotation=ROTATION_(\d+)")
_SIZE = re.compile(r"(Override|Physical) size: (\d+)x(\d+)")

Run = Callable[[list[str]], Awaitable[bytes]]


def _sleep(ms: float) -> list[str]:
    return [f"sleep {ms / 1000:.3f}"] if ms > 0 else []


def _moves(start: tuple[float, float] | list[float], end: tuple[float, float] | list[float], duration_ms: int,
           most: int = STEPS_MOST) -> list[str]:
    """The MOVE events from one point to another over `duration_ms`, no further than STEP_PX apart (in `most`
    steps at most)."""
    length = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
    steps = max(1, min(most, -(-int(length) // STEP_PX)))
    lines = []
    for step in range(1, steps + 1):
        ratio = step / steps
        lines += [*_sleep(duration_ms / steps - MOTION_MS),
                  f"input motionevent MOVE {round(start[0] + (end[0] - start[0]) * ratio)} {round(start[1] + (end[1] - start[1]) * ratio)}"]
    return lines


def adb_path() -> str:
    """The adb to run: GAMELAB_ADB, the SDK's own, or whatever `adb` the PATH has."""
    if configured := os.environ.get(ADB_VARIABLE, "").strip():
        return configured
    bundled = SDK / "platform-tools" / "adb"
    return str(bundled) if bundled.is_file() else "adb"


def command(serial: str | None = None, adb: str | None = None) -> Run:
    """adb commands against one device (the only one attached unless `serial` names it), each one's stdout."""
    prefix = [adb or adb_path(), *(["-s", serial] if serial else [])]

    async def run(args: list[str]) -> bytes:
        process = await asyncio.create_subprocess_exec(*prefix, *args, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE)
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT_S):
                out, err = await process.communicate()
        except TimeoutError:
            process.kill()
            raise RuntimeError(f"adb {args[0]} timed out") from None
        if process.returncode:
            raise RuntimeError(f"adb {' '.join(args)}: {err.decode(errors='replace').strip() or process.returncode}")
        return out
    return run


class AndroidSurface:
    reads_dom = False

    def __init__(self, run: Run | None = None, *, package: str | None = None, keys: bool = False,
                 serial: str | None = None) -> None:
        self.run = run or command(serial)
        # an emulator is captured by its console, until that fails once
        self.console = serial is not None and serial.startswith("emulator-")
        # the app the game is, when the caller knows it; else whichever has the focus when asked
        self.package = package
        self.name = f"android:{serial or 'device'}"
        self.kinds = frozenset({TAP, DRAG, HOLD, TEXT, SCROLL, BACK, RELAUNCH, *([KEYS] if keys else [])})
        self._size: list[int] | None = None
        # where a pointer pressed in one call and let go in another is held, if it is
        self._held: tuple[float, float] | None = None

    async def size(self) -> list[int]:
        if self._size is None:
            sizes = {kind: [int(width), int(height)] for kind, width, height in
                     _SIZE.findall((await self.run(["shell", "wm", "size"])).decode(errors="replace"))}
            if not sizes:
                raise ValueError("size_unknown")
            self._size = sizes.get("Override") or sizes["Physical"]
        return self._size

    async def capture(self, region: list[int] | None = None) -> bytes:
        from PIL import Image

        taken = await self._console_capture() if self.console else None
        raw, way = taken if taken is not None else (None, None)
        if raw is None:
            self.console = False
            try:
                raw = await self.run(["exec-out", "screencap", "-p"])
            except RuntimeError as error:
                raise ValueError("capture_failed") from error
        if not raw.startswith(PNG_SIGNATURE):
            raise ValueError("capture_failed")
        # a device's capture carries an alpha channel the readers refuse: the picture as plain RGB
        with Image.open(BytesIO(raw)) as image:
            picture = image.convert("RGB")
            if way is not None:
                picture = picture.transpose(way)
            self._size = list(picture.size)
            if region is not None:
                picture = picture.crop(tuple(region))
            for level in (3, 9):
                out = BytesIO()
                picture.save(out, format="PNG", compress_level=level)
                if out.tell() <= pixels.MAX_IMAGE_BYTES:
                    break
        return out.getvalue()

    async def _console_capture(self) -> tuple[bytes, Any] | None:
        """The emulator's screen as its console takes it, written to a file here and turned the way the display is
        turned; None when it could not be, or the display's turn could not be read."""
        from PIL import Image

        handle, path = tempfile.mkstemp(suffix=".png")
        os.close(handle)
        try:
            turned = _ROTATION.search((await self.run(["shell", "dumpsys", "window", "displays"])).decode(errors="replace"))
            said = await self.run(["emu", "screenrecord", "screenshot", path])
            raw = Path(path).read_bytes()
        except (RuntimeError, OSError):
            return None
        finally:
            Path(path).unlink(missing_ok=True)
        way = {0: None, 90: Image.Transpose.ROTATE_90, 180: Image.Transpose.ROTATE_180,
               270: Image.Transpose.ROTATE_270}.get(int(turned[1]), "unknown") if turned else "unknown"
        if way == "unknown" or b"OK" not in said or not raw.startswith(PNG_SIGNATURE):
            return None
        return raw, way

    async def elements(self, queries: list[dict[str, Any]]) -> dict[str, Any] | None:
        return None

    async def foreground(self) -> str | None:
        """The package of the window that has the focus."""
        found = _FOCUS.search((await self.run(["shell", "dumpsys", "window", "displays"])).decode(errors="replace"))
        return found[1] if found else None

    async def address(self) -> str:
        return f"android://{self.package or await self.foreground() or 'unknown'}"

    async def launch(self, package: str) -> None:
        """Bring the app's launcher activity to the front."""
        found = (await self.run(["shell", "cmd", "package", "resolve-activity", "--brief", "-c",
                                 "android.intent.category.LAUNCHER", package])).decode(errors="replace").split()
        if not found or "/" not in found[-1]:
            raise RuntimeError(f"no launcher activity for {package}")
        await self.run(["shell", "am", "start", "-n", found[-1]])
        self.package = package

    async def key(self, key: str, hold_ms: int = 0) -> None:
        await self.run(["shell", "input", "keyevent", KEYCODES.get(key, key)])

    async def tap(self, x: float, y: float, duration_ms: int) -> None:
        if duration_ms > LONG_PRESS_MS:
            await self.drag(x, y, x, y, duration_ms)
        else:
            await self.run(["shell", "input", "tap", str(round(x)), str(round(y))])

    async def drag(self, x0: float, y0: float, x1: float, y1: float, duration_ms: int) -> None:
        await self.run(["shell", "input", "swipe", *(str(round(value)) for value in (x0, y0, x1, y1)), str(duration_ms)])

    async def shell(self, lines: list[str]) -> None:
        """Commands run one after another in one shell on the device."""
        await self.run(["shell", "; ".join(lines)])

    async def press(self, x: float, y: float) -> None:
        await self.run(["shell", "input", "motionevent", "DOWN", str(round(x)), str(round(y))])
        self._held = (x, y)

    async def move(self, x: float, y: float, duration_ms: int) -> None:
        """The held pointer moved to a point over `duration_ms`, in steps a game follows as a hand's."""
        await self.shell(_moves(self._held or (x, y), (x, y), duration_ms))
        self._held = (x, y)

    async def sweep(self, x: float, y: float, duration_ms: int) -> None:
        """The held pointer taken straight to a point in few steps: for a move whose way there does not matter."""
        await self.shell(_moves(self._held or (x, y), (x, y), duration_ms, SWEEP_STEPS))
        self._held = (x, y)

    async def lift(self) -> None:
        x, y = self._held or (0, 0)
        self._held = None
        await self.run(["shell", "input", "motionevent", "UP", str(round(x)), str(round(y))])

    async def stroke(self, points: list[list[float]], duration_ms: int, hold_ms: int) -> None:
        lines = [f"input motionevent DOWN {round(points[0][0])} {round(points[0][1])}", *_sleep(hold_ms)]
        for start, (end, share) in zip(points, segments(points)):
            lines += _moves(start, end, round(duration_ms * share))
        lines += [*_sleep(hold_ms), f"input motionevent UP {round(points[-1][0])} {round(points[-1][1])}"]
        await self.shell(lines)

    async def taps(self, x: float, y: float, count: int, gap_ms: int) -> None:
        # in one shell: taps sent from here one by one would come further apart than a double tap allows
        tap = f"input tap {round(x)} {round(y)}"
        await self.shell([line for index in range(count) for line in ([*_sleep(gap_ms)] if index else []) + [tap]])

    async def type(self, text: str) -> None:
        # `input text` takes a space as `%s`; it types only plain letters and digits (a Hangul syllable fails)
        await self.run(["shell", "input", "text", text.replace(" ", "%s")])

    async def scroll(self, x: float, y: float, steps: int) -> None:
        # a wheel's VSCROLL is up when more than 0
        await self.run(["shell", "input", "mouse", "scroll", str(round(x)), str(round(y)), "--axis", f"VSCROLL,{-steps}"])

    async def back(self) -> None:
        await self.run(["shell", "input", "keyevent", "KEYCODE_BACK"])

    async def relaunch(self) -> None:
        package = self.package or await self.foreground()
        if package is None:
            raise RuntimeError("no app to start again")
        await self.run(["shell", "am", "force-stop", package])
        await self.launch(package)
        await asyncio.sleep(RELAUNCH_WAIT_S)

    async def release(self) -> None:
        """Let go of a pointer a stroke in separate calls still holds; `input` commands release by themselves."""
        if self._held is not None:
            await self.lift()
