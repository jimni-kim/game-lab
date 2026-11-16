"""A screen read without what keeps moving on it (PLAN §13, C1).

Many games draw something that never stops over the board they wait on: a tutorial's hand dragging a ghost
piece from the tray to the board and back, a hint glowing, a button pulsing. Waiting for the screen to come to
rest is no help there: it never does. But such a thing covers any one place only part of the time, so a few
captures a little apart, taken pixel by pixel at their middle value, show the screen as it is under it.

`Steady` wraps a surface and gives that picture for `capture()`: one more capture a moment after the first
tells whether anything moves; when nothing does, that capture is the picture. When something does, FRAMES are
taken and their middle drawn, and the places where they differed are kept (`moving`) for whoever reads the
screen to be told of. A screen that changes over most of its area (a new scene sliding in) is no animation to
draw through: the last capture is given as it is. Nothing here knows any game: only which pixels keep changing.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Any

from .carry import _boxes

# the second look, this long after the first; when the two differ, this many captures in all, this far apart
GAP_S, FRAMES, FRAME_GAP_S = 0.25, 7, 0.3
# what moves over more than this share of the screen is a change of scene, not an animation over a still one
MOVING_MOST = 0.5


def middle(pngs: list[bytes]) -> bytes:
    """The picture whose every pixel is, channel by channel, the middle of the captures' values there: a thing
    that covers a place in fewer than half of them is not in it."""
    from PIL import Image, ImageChops

    order = []
    for png in pngs:
        with Image.open(BytesIO(png)) as image:
            order.append(image.convert("RGB"))
    # an odd-even transposition sort of the whole pictures, darker before lighter
    for round_ in range(len(order)):
        for index in range(round_ % 2, len(order) - 1, 2):
            one, other = order[index], order[index + 1]
            order[index], order[index + 1] = ImageChops.darker(one, other), ImageChops.lighter(one, other)
    out = BytesIO()
    order[len(order) // 2].save(out, format="PNG")
    return out.getvalue()


def joined(boxes: list[list[int]]) -> list[list[int]]:
    """Boxes that overlap made one, until none do."""
    boxes = [list(box) for box in boxes]
    merged = True
    while merged:
        merged = False
        for index, one in enumerate(boxes):
            for other in boxes[index + 1:]:
                if one[0] < other[0] + other[2] and other[0] < one[0] + one[2] \
                        and one[1] < other[1] + other[3] and other[1] < one[1] + one[3]:
                    left, top = min(one[0], other[0]), min(one[1], other[1])
                    right = max(one[0] + one[2], other[0] + other[2])
                    bottom = max(one[1] + one[3], other[1] + other[3])
                    one[:] = [left, top, right - left, bottom - top]
                    boxes.remove(other)
                    merged = True
                    break
            if merged:
                break
    return boxes


def inside(box: list[int], moving: list[list[int]]) -> bool:
    """Whether most of a box lies in a place that was moving."""
    area = max(1, box[2] * box[3])
    for other in moving:
        width = min(box[0] + box[2], other[0] + other[2]) - max(box[0], other[0])
        height = min(box[1] + box[3], other[1] + other[3]) - max(box[1], other[1])
        if width > 0 and height > 0 and width * height >= area / 2:
            return True
    return False


def outside(boxes: list[list[int]], moving: list[list[int]]) -> list[list[int]]:
    """The changed places that are not mostly where something was moving all along."""
    return [box for box in boxes if not inside(box, moving)]


def _area(png: bytes) -> int:
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        return image.width * image.height


async def still(surface: Any) -> tuple[bytes, list[list[int]]]:
    """The screen as it is under whatever keeps moving on it, and where that was."""
    first = await surface.capture()
    await asyncio.sleep(GAP_S)
    frames = [first, await surface.capture()]
    if not _boxes(frames[0], frames[1]):
        return frames[1], []
    while len(frames) < FRAMES:
        await asyncio.sleep(FRAME_GAP_S)
        frames.append(await surface.capture())
    drawn = middle(frames)
    moving = joined([box for frame in frames for box in _boxes(frame, drawn)])
    if sum(box[2] * box[3] for box in moving) > MOVING_MOST * _area(drawn):
        return frames[-1], []
    return drawn, moving


class Steady:
    """A surface whose captures leave out what keeps moving on it; everything else is the surface's own."""

    def __init__(self, surface: Any) -> None:
        self.surface = surface
        # where something kept moving at the last capture: nothing when the screen held still
        self.moving: list[list[int]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.surface, name)

    @property
    def animating(self) -> bool:
        return bool(self.moving)

    async def snap(self, region: list[int] | None = None) -> bytes:
        """One capture as it is, moving things and all."""
        return await self.surface.capture(region)

    async def capture(self, region: list[int] | None = None) -> bytes:
        if region is not None:
            return await self.surface.capture(region)
        png, self.moving = await still(self.surface)
        return png
