"""Frames of a screen, each with the time its pixels were taken (PLAN §18).

Everything that waits on the screen asks for frames here instead of sleeping and capturing: a screen come to
rest, a held piece come to rest under the pointer, a move's result. `frame(after)` is a frame whose pixels were
taken no earlier than `after` (now, when not given), and `frames(source, count, gap_s)` gives that many, each at
least `gap_s` after the one before. How long a wait takes is then the source's business, not the caller's.

`Polled` captures one frame for each that is asked for (adb `screencap`, a browser's screenshot): a frame after a
time still to come is waited for, and frames come no faster than captures do (about 0.5 s on a phone over adb).
A source fed frames as they are drawn (a phone's own ImageReader, as ARMY's hidden screen keeps its newest)
answers the same calls with the next frame drawn after the time asked, and the same waits then take a fraction
of the time. A surface that has such a source offers it as `frame_source`; any other is polled.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import Any, Protocol


@dataclass(frozen=True)
class Frame:
    png: bytes
    # the clock (`time.monotonic`) when its pixels were taken: no earlier than this
    at: float
    # how long getting it took (a polled frame's capture)
    took: float = 0.0


class FrameSource(Protocol):
    async def frame(self, after: float | None = None) -> Frame:
        """A frame whose pixels were taken no earlier than `after`, or than now when it is None."""

    def summary(self) -> dict[str, Any]:
        """How many frames were given and what they cost."""


class Polled:
    """Frames captured from a surface one at a time, each when it is asked for."""

    def __init__(self, surface: Any) -> None:
        self.surface = surface
        # frames captured, the seconds the captures took, and the seconds waited before them for a time asked
        self.taken, self.seconds, self.waited = 0, 0.0, 0.0

    async def frame(self, after: float | None = None) -> Frame:
        if after is not None and (wait := after - time.monotonic()) > 0:
            self.waited += wait
            await asyncio.sleep(wait)
        at = time.monotonic()
        png = await self.surface.capture()
        took = time.monotonic() - at
        self.taken, self.seconds = self.taken + 1, self.seconds + took
        return Frame(png, at, took)

    def summary(self) -> dict[str, Any]:
        return {"source": "polled", "frames": self.taken, "seconds": round(self.seconds, 2),
                "waited_s": round(self.waited, 2),
                "per_frame_s": round(self.seconds / self.taken, 3) if self.taken else None}


async def frames(source: FrameSource, count: int, gap_s: float, first: Frame | None = None,
                 after: float | None = None) -> list[Frame]:
    """`count` frames, each taken at least `gap_s` after the one before: from `first` when given, else from one
    taken no earlier than `after`."""
    got = [first if first is not None else await source.frame(after)]
    while len(got) < count:
        got.append(await source.frame(got[-1].at + gap_s))
    return got


def source_of(surface: Any) -> FrameSource:
    """The surface's own frame source, or one that polls it."""
    return getattr(surface, "frame_source", None) or Polled(surface)
