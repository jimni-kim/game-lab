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
GAP_S, FRAMES, FRAME_GAP_S = 0.25, 5, 0.2
# what moves over more than this share of the screen is a change of scene, not an animation over a still one
MOVING_MOST = 0.5
# where things kept moving is remembered this long; while every such place is no larger than this share of the
# screen (a glint on a tile, a pulsing badge: nothing that hides what is under it), a capture whose changes all
# lie there is given as it is, with those places, and not drawn through again (PLAN §17, F1)
REMEMBER_S, SMALL_SHARE = 30.0, 0.01


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


async def _still(surface: Any, known: list[list[int]] | None = None, first: bytes | None = None
                 ) -> tuple[bytes, list[list[int]], bytes]:
    if first is None:
        first = await surface.capture()
    await asyncio.sleep(GAP_S)
    frames = [first, await surface.capture()]
    changed = _boxes(frames[0], frames[1])
    if not changed:
        return frames[1], [], frames[1]
    if known and not outside(changed, known):
        return frames[1], known, frames[1]
    while len(frames) < FRAMES:
        await asyncio.sleep(FRAME_GAP_S)
        frames.append(await surface.capture())
    drawn = middle(frames)
    moving = joined([box for frame in frames for box in _boxes(frame, drawn)])
    if sum(box[2] * box[3] for box in moving) > MOVING_MOST * _area(drawn):
        return frames[-1], [], frames[-1]
    return drawn, moving, frames[-1]


async def still(surface: Any, known: list[list[int]] | None = None) -> tuple[bytes, list[list[int]]]:
    """The screen as it is under whatever keeps moving on it, and where that was. `known`: small places things
    were last seen to keep moving at; when the two first captures differ only there, the second is given as it
    is, with them."""
    png, moving, _ = await _still(surface, known)
    return png, moving


class Steady:
    """A surface whose captures leave out what keeps moving on it; everything else is the surface's own."""

    def __init__(self, surface: Any) -> None:
        self.surface = surface
        # where something kept moving at the last capture: nothing when the screen held still
        self.moving: list[list[int]] = []
        # when the places things keep moving at were last found by drawing through captures, and the last
        # capture as it was
        self.known_at: float | None = None
        self.last: bytes | None = None
        # captures given as they were because what changed lay where small things keep moving (F1)
        self.quick = 0

    def forget(self) -> None:
        """What was seen of the moving things is not to be trusted (a reading of the screen failed): the next
        capture draws through them again."""
        self.known_at = None

    def _small(self, area: int) -> bool:
        return bool(self.moving) and self.known_at is not None \
            and asyncio.get_running_loop().time() - self.known_at < REMEMBER_S \
            and all(box[2] * box[3] <= SMALL_SHARE * area for box in self.moving)

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
        small = self.last is not None and self._small(_area(self.last))
        first = None
        if small:
            first = await self.surface.capture()
            # nothing changed but where the small things keep moving: the screen is as it was
            if not outside(_boxes(self.last, first), self.moving):
                self.last, self.quick = first, self.quick + 1
                return first
        png, moving, self.last = await _still(self.surface, self.moving if small else None, first)
        if moving and moving is not self.moving:
            # drawn through again: where things keep moving is known afresh
            self.known_at = asyncio.get_running_loop().time()
        elif not moving:
            self.known_at = None
        else:
            self.quick += 1
        self.moving = moving
        return png
