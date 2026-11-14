"""An Android device or emulator as a surface, driven with adb alone.

A look is `adb exec-out screencap -p`; inputs are `adb shell input tap | swipe | keyevent`. Coordinates
are the device's own pixels, as its captures show them. Nothing is installed on the device.

A game is not given keys here unless asked (`keys=True`, a D-pad's key events): the environment swipes a
direction across the board instead, as a hand would.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
import os
from pathlib import Path
import re
from typing import Any, Awaitable, Callable

from .. import pixels
from ..surface import DRAG, KEYS, TAP

SDK = Path("/opt/homebrew/share/android-commandlinetools")
ADB_VARIABLE = "GAMELAB_ADB"
# how long one adb command may take: a capture of a large screen over a slow emulator link
COMMAND_TIMEOUT_S = 20.0
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# a press held longer than this is sent as a swipe that does not move (`input tap` holds for no set time)
LONG_PRESS_MS = 200
KEYCODES = {"ArrowUp": "KEYCODE_DPAD_UP", "ArrowDown": "KEYCODE_DPAD_DOWN", "ArrowLeft": "KEYCODE_DPAD_LEFT",
            "ArrowRight": "KEYCODE_DPAD_RIGHT", "Enter": "KEYCODE_ENTER", "Escape": "KEYCODE_BACK", " ": "KEYCODE_SPACE"}
_FOCUS = re.compile(r"mCurrentFocus=Window\{\S+ \S+ ([\w.]+)/")
_SIZE = re.compile(r"(Override|Physical) size: (\d+)x(\d+)")

Run = Callable[[list[str]], Awaitable[bytes]]


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
        # the app the game is, when the caller knows it; else whichever has the focus when asked
        self.package = package
        self.name = f"android:{serial or 'device'}"
        self.kinds = frozenset({TAP, DRAG, *([KEYS] if keys else [])})
        self._size: list[int] | None = None

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

        try:
            raw = await self.run(["exec-out", "screencap", "-p"])
        except RuntimeError as error:
            raise ValueError("capture_failed") from error
        if not raw.startswith(PNG_SIGNATURE):
            raise ValueError("capture_failed")
        # a device's capture carries an alpha channel the readers refuse: the picture as plain RGB
        with Image.open(BytesIO(raw)) as image:
            self._size = list(image.size)
            picture = image.convert("RGB")
            if region is not None:
                picture = picture.crop(tuple(region))
            for level in (3, 9):
                out = BytesIO()
                picture.save(out, format="PNG", compress_level=level)
                if out.tell() <= pixels.MAX_IMAGE_BYTES:
                    break
        return out.getvalue()

    async def elements(self, queries: list[dict[str, Any]]) -> dict[str, Any] | None:
        return None

    async def foreground(self) -> str | None:
        """The package of the window that has the focus."""
        found = _FOCUS.search((await self.run(["shell", "dumpsys", "window", "windows"])).decode(errors="replace"))
        return found[1] if found else None

    async def address(self) -> str:
        return f"android://{self.package or await self.foreground() or 'unknown'}"

    async def launch(self, package: str) -> None:
        """Bring the app's launcher activity to the front."""
        await self.run(["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"])
        self.package = package

    async def key(self, key: str) -> None:
        await self.run(["shell", "input", "keyevent", KEYCODES.get(key, key)])

    async def tap(self, x: float, y: float, duration_ms: int) -> None:
        if duration_ms > LONG_PRESS_MS:
            await self.drag(x, y, x, y, duration_ms)
        else:
            await self.run(["shell", "input", "tap", str(round(x)), str(round(y))])

    async def drag(self, x0: float, y0: float, x1: float, y1: float, duration_ms: int) -> None:
        await self.run(["shell", "input", "swipe", *(str(round(value)) for value in (x0, y0, x1, y1)), str(duration_ms)])

    async def release(self) -> None:
        """`input` presses and releases within one command: nothing stays held."""
