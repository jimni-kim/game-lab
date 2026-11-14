"""A drag that puts what it picked up where it was meant to go (PLAN §10, G7).

A game rarely draws a held piece under the pointer: it lifts it clear of the finger, enlarges it, hangs it by
its middle or its foot. How far, nobody is told. So the drag is carried out in two legs with a look between
them: press on the thing, move a little, capture, find by what changed on the screen where the thing was
(`origin`) and where it is drawn now (`held`), then move the pointer by what is still missing for the held
drawing's middle to reach the place asked for, and let go. Nothing here knows the game: only that one thing
left one place and is drawn at another.

A surface that cannot hold a pointer between two calls (`HOLD` not among its kinds) is given the drag as one
movement, shifted by what the last measured carry showed.
"""
from __future__ import annotations

import asyncio
from collections import deque
from io import BytesIO
from typing import Any

from .surface import HOLD

# the picture the change is looked for in is this many times smaller a side; a change smaller than this many
# of 255 in every channel is none (a gradient redrawn, compression)
REDUCE, CHANGED = 4, 28
# changed spots this near (reduced pixels) are one thing: a piece's blocks have gaps between them
JOIN = 3
# a held drawing is the same shape as what was picked up: its width and height grew by the same factor, within this
ASPECT = 0.3
# the picked-up thing's own colours: looked for among this many, each filling at least this share of its box,
# matched within this much a channel; the box narrowed to them is used when it keeps this share of the area
OWN_COLOURS, OWN_SHARE, OWN_NEAR, OWN_KEPT = 6, 0.08, 36, 0.4
# how far along the way the first look is taken, and the least distance moved before it (pixels): far enough
# for the game to take the press as a drag, near enough that the held thing is not yet over the board
FIRST_LEG, LEAST_LEG = (0.2, 0.5), 24.0
LIFT_S, LEG_MS, REST_S = 0.25, 160, 0.08


def _boxes(before: bytes, after: bytes) -> list[list[int]]:
    """The things that changed between two captures, each as the box [left, top, width, height] around it."""
    from PIL import Image, ImageChops

    with Image.open(BytesIO(before)) as one, Image.open(BytesIO(after)) as other:
        if one.size != other.size:
            return []
        changed = ImageChops.difference(one.convert("RGB"), other.convert("RGB"))
    bands = [band.point(lambda value: 255 if value > CHANGED else 0) for band in changed.split()]
    full = ImageChops.lighter(ImageChops.lighter(bands[0], bands[1]), bands[2])
    mask = full.reduce(REDUCE)
    width, height = mask.size
    marked = [value > 40 for value in mask.tobytes()]
    seen = [False] * len(marked)
    found = []
    for start, on in enumerate(marked):
        if not on or seen[start]:
            continue
        seen[start] = True
        queue, left, top, right, bottom = deque([start]), width, height, 0, 0
        while queue:
            y, x = divmod(queue.popleft(), width)
            left, top, right, bottom = min(left, x), min(top, y), max(right, x), max(bottom, y)
            for ny in range(max(0, y - JOIN), min(height, y + JOIN + 1)):
                for nx in range(max(0, x - JOIN), min(width, x + JOIN + 1)):
                    index = ny * width + nx
                    if marked[index] and not seen[index]:
                        seen[index] = True
                        queue.append(index)
        # the reduced picture says which thing; its edges are taken from the whole one
        coarse = (left * REDUCE, top * REDUCE, (right + 1) * REDUCE, (bottom + 1) * REDUCE)
        tight = full.crop(coarse).getbbox()
        if tight is not None:
            found.append([coarse[0] + tight[0], coarse[1] + tight[1], tight[2] - tight[0], tight[3] - tight[1]])
    return found


def _gap(point: tuple[float, float], box: list[int]) -> float:
    left, top, width, height = box
    return max(left - point[0], point[0] - (left + width), top - point[1], point[1] - (top + height), 0.0)


def find(before: bytes, during: bytes, start: tuple[float, float], pointer: tuple[float, float]
         ) -> tuple[list[int], list[int]] | None:
    """Where the thing pressed at `start` was (`origin`) and where it is drawn while the pointer holds it at
    `pointer` (`held`), from what changed between the capture before the press and the one during the hold.
    None when the two cannot be told apart (still overlapping, or nothing of the right shape moved)."""
    boxes = [box for box in _boxes(before, during) if box[2] >= 2 * REDUCE and box[3] >= 2 * REDUCE]
    near = [box for box in boxes if _gap(start, box) <= REDUCE * JOIN]
    if not near:
        return None
    origin = min(near, key=lambda box: _gap(start, box))
    rest = [box for box in boxes if box is not origin]
    # what is held is drawn as large as what was picked up or larger, in the same proportions, near the pointer
    shaped = [box for box in rest if box[2] >= origin[2] * 0.7 and box[3] >= origin[3] * 0.7
              and abs((box[2] / origin[2]) / (box[3] / origin[3]) - 1) <= ASPECT]
    if not shaped:
        return None
    held = min(shaped, key=lambda box: _gap(pointer, box))
    return origin, _own(before, during, origin, held)


def _own(before: bytes, during: bytes, origin: list[int], held: list[int]) -> list[int]:
    """`held` narrowed to what is drawn in the colours of the thing picked up: a game may also draw where the
    thing would land (a paler shape on the board, a shadow), which changes the screen beside it and is not it."""
    from PIL import Image, ImageChops

    def corners(box: list[int]) -> tuple[int, int, int, int]:
        return box[0], box[1], box[0] + box[2], box[1] + box[3]
    with Image.open(BytesIO(before)) as one, Image.open(BytesIO(during)) as other:
        was, left_behind = one.convert("RGB").crop(corners(origin)), other.convert("RGB").crop(corners(origin))
        drawn = other.convert("RGB").crop(corners(held))
    # its colours: those the place it left held before and holds no more
    now = dict((colour, count) for count, colour in left_behind.quantize(OWN_COLOURS * 2).convert("RGB").getcolors() or [])
    own = [colour for count, colour in sorted(was.quantize(OWN_COLOURS).convert("RGB").getcolors() or [], reverse=True)
           if count >= origin[2] * origin[3] * OWN_SHARE and not any(
               max(abs(a - b) for a, b in zip(colour, other)) <= OWN_NEAR for other in now)]
    if not own:
        return held
    mask = None
    for colour in own:
        far = ImageChops.difference(drawn, Image.new("RGB", drawn.size, colour))
        bands = [band.point(lambda value: 255 if value <= OWN_NEAR else 0) for band in far.split()]
        near = ImageChops.darker(ImageChops.darker(bands[0], bands[1]), bands[2])
        mask = near if mask is None else ImageChops.lighter(mask, near)
    tight = mask.getbbox()
    # only when most of what was found is in those colours: a thing drawn held in other colours keeps its box
    if tight is None or (tight[2] - tight[0]) * (tight[3] - tight[1]) < held[2] * held[3] * OWN_KEPT:
        return held
    return [held[0] + tight[0], held[1] + tight[1], tight[2] - tight[0], tight[3] - tight[1]]


class Carrier:
    """Carries drags on one surface, remembering what the last measured one showed."""

    def __init__(self, surface: Any) -> None:
        self.surface = surface
        # the held drawing's middle minus the pointer, as last measured: all a surface that cannot hold is given
        self.offset: tuple[float, float] | None = None
        self.log: list[dict[str, Any]] = []

    async def carry(self, before: bytes, start: tuple[float, float], goal: tuple[float, float], duration_ms: int
                    ) -> dict[str, Any]:
        """Drag what is at `start` so that its drawing's middle comes to `goal`. Returns what was measured
        (`origin`, `held`), where the pointer was let go (`end`) and whether a measurement placed it."""
        surface = self.surface
        told: dict[str, Any] = {"start": list(start), "goal": list(goal), "measured": False}
        self.log.append(told)
        if HOLD not in surface.kinds:
            # one movement: the thing's own middle is taken to be under the pointer unless a carry showed otherwise
            end = (goal[0] - self.offset[0], goal[1] - self.offset[1]) if self.offset else goal
            told["end"] = list(end)
            await surface.drag(*start, *end, duration_ms)
            return told
        await surface.press(*start)
        try:
            pointer = start
            for share in FIRST_LEG:
                along = (goal[0] - start[0], goal[1] - start[1])
                length = max((along[0] ** 2 + along[1] ** 2) ** 0.5, 1.0)
                reach = max(share, min(1.0, LEAST_LEG / length))
                pointer = (start[0] + along[0] * reach, start[1] + along[1] * reach)
                await surface.move(*pointer, LEG_MS)
                await asyncio.sleep(LIFT_S)
                found = find(before, await surface.capture(), start, pointer)
                if found is not None:
                    origin, held = found
                    middle = (held[0] + held[2] / 2, held[1] + held[3] / 2)
                    self.offset = (middle[0] - pointer[0], middle[1] - pointer[1])
                    told.update(origin=origin, held=held, measured=True)
                    break
            end = (goal[0] - self.offset[0], goal[1] - self.offset[1]) if self.offset else goal
            told["end"] = list(end)
            await surface.move(*end, max(LEG_MS, duration_ms - LEG_MS))
            await asyncio.sleep(REST_S)
        finally:
            await surface.lift()
        return told
