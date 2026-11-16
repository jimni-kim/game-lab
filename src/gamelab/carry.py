"""A drag that puts what it picked up where it was meant to go (PLAN §10, G7; §16).

A game rarely draws a held piece under the pointer: it lifts it clear of the finger, enlarges it, hangs it by
its middle or its foot, and many move it further than the finger moves (a block game drew it 1.45 times the
finger's way, 2026-11-16) and let it catch up with the finger a moment late. How far, and by how much, nobody
is told, and it differs with the game, the screen and the piece. So the drag is carried out with looks on the
way, each taken once the held drawing has come to rest: press on the thing, move a little, look; move a little
further, look again. What is held is what moved between the two looks the way the pointer did (the same
direction, by some share of its way); from the two places it was drawn at, the code learns where it will be
drawn for any place of the pointer (`Follow`), moves the pointer so that the drawing's middle comes to the
place asked for, looks once more, makes up for what is still missing, and lets go. Nothing here knows the game:
only that one thing left one place and is drawn at another that follows the pointer.

Whatever else changes on the screen while the pointer holds (a tutorial's hand, a glow, a ghost of where the
piece would land) does not come to rest where the pointer did or does not follow it, and is not taken for it.

A carry that sees nothing drawn held picked nothing up: it lets go and presses again longer (HOLDS), within the
same carry. Once a carry here picked a thing up, none presses again, whatever its looks showed: letting go of a
held piece may drop it on the board.

A surface that cannot hold a pointer between two calls (`HOLD` not among its kinds) is given the drag as one
movement, aimed by what the last measured carry showed.
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
# for the game to take the press as a drag, near enough that the held thing is not yet far over the board; when
# nothing held shows there, the second share is tried
FIRST_LEG, LEAST_LEG = (0.2, 0.5), 24.0
# the second look is taken this much further along the way (pixels), or this share of the way when that is less,
# but no less than STEP_LEAST: the further, the better the two places tell how the drawing follows the pointer
STEP_PX, STEP_SHARE, STEP_LEAST = 96.0, 0.3, 32.0
# what is held moved between the two looks the way the pointer did: by this share of the pointer's move at least
# and at most (a game moves it further than the finger, or less), no further across that way than this share of
# its move or FOLLOW_PX, and keeping its size within SAME_SIZE
GAIN_RANGE, ACROSS, FOLLOW_PX, SAME_SIZE = (0.5, 3.0), 0.2, 12.0, 0.25
# a look is taken once what may be held is drawn in the same place on two captures in a row, within this many
# pixels a side (the drawing catches up with the pointer a moment late), and no longer looked for than this
STILL_PX, STILL_MOST_S = 4, 1.5
# before letting go, the drawing is looked at once more; it is moved further when its middle is off the place
# asked for by more than this share of its smaller side or AIM_PX, at most AIMS times
AIM_SHARE, AIM_PX, AIMS = 0.08, 10.0, 1
# the pause after a move before the first capture of a look, a leg's time, and the rest before letting go
LIFT_S, LEG_MS, REST_S = 0.1, 160, 0.08
# how long the pointer rests where it pressed before it moves: the first, then each next after a press that
# picked nothing up
HOLDS = (0.0, 0.4, 0.8, 1.2)
# how many of the ways the drawings followed the pointer are kept, the middle one standing in for a carry whose
# looks could not tell it
FOLLOWS_KEPT = 9


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


def _overlaps(one: list[int], other: list[int]) -> bool:
    return one[0] < other[0] + other[2] and other[0] < one[0] + one[2] and one[1] < other[1] + other[3] \
        and other[1] < one[1] + one[3]


def candidates(before: bytes, during: bytes, start: tuple[float, float], pointer: tuple[float, float],
               source: list[int] | None = None) -> tuple[list[int], list[list[int]]] | None:
    """Where the thing pressed at `start` was (`origin`), and every place it may be drawn while the pointer holds
    it at `pointer`, nearest the pointer first: what changed between the capture before the press and the one
    during the hold, as large as what was picked up or larger and in its proportions. `source`: where the screen
    was read to show the thing, taken for its place when something changed there. None when nothing can be
    told apart (still overlapping, or nothing of the right shape moved)."""
    boxes = [box for box in _boxes(before, during) if box[2] >= 2 * REDUCE and box[3] >= 2 * REDUCE]
    if source is not None and source[2] > 0 and source[3] > 0:
        if not any(_overlaps(box, source) for box in boxes):
            return None
        origin = list(source)
        rest = [box for box in boxes if not _overlaps(box, source)]
    else:
        near = [box for box in boxes if _gap(start, box) <= REDUCE * JOIN]
        if not near:
            return None
        origin = min(near, key=lambda box: _gap(start, box))
        rest = [box for box in boxes if box is not origin]
    shaped = [box for box in rest if box[2] >= origin[2] * 0.7 and box[3] >= origin[3] * 0.7
              and abs((box[2] / origin[2]) / (box[3] / origin[3]) - 1) <= ASPECT]
    if not shaped:
        return None
    return origin, [_own(before, during, origin, box) for box in sorted(shaped, key=lambda box: _gap(pointer, box))]


def gone(before: bytes, during: bytes, start: tuple[float, float], source: list[int] | None = None) -> bool:
    """Whether the thing pressed at `start` left its place: what the screen showed there changed. A press that
    picked nothing up leaves it as it was."""
    boxes = [box for box in _boxes(before, during) if box[2] >= 2 * REDUCE and box[3] >= 2 * REDUCE]
    if source is not None and source[2] > 0 and source[3] > 0:
        return any(_overlaps(box, source) for box in boxes)
    return any(_gap(start, box) <= REDUCE * JOIN for box in boxes)


def find(before: bytes, during: bytes, start: tuple[float, float], pointer: tuple[float, float]
         ) -> tuple[list[int], list[int]] | None:
    """Where the thing pressed at `start` was (`origin`) and where it is drawn while the pointer holds it at
    `pointer` (`held`): the likeliest of `candidates`, the one nearest the pointer."""
    found = candidates(before, during, start, pointer)
    return (found[0], found[1][0]) if found else None


def _own(before: bytes, during: bytes, origin: list[int], held: list[int]) -> list[int]:
    """`held` narrowed to what is drawn in the colours of the thing picked up: a game may also draw where the
    thing would land (a paler shape on the board, a shadow), which changes the screen beside it and is not it."""
    from PIL import Image, ImageChops

    def corners(box: list[int]) -> tuple[int, int, int, int]:
        return box[0], box[1], box[0] + box[2], box[1] + box[3]
    with Image.open(BytesIO(before)) as one, Image.open(BytesIO(during)) as other:
        was, left_behind = one.convert("RGB").crop(corners(origin)), other.convert("RGB").crop(corners(origin))
        under, drawn = one.convert("RGB").crop(corners(held)), other.convert("RGB").crop(corners(held))
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
    # and only where the screen changed: the board may hold blocks of the same colours that never moved
    moved = [band.point(lambda value: 255 if value > CHANGED else 0) for band in ImageChops.difference(under, drawn).split()]
    mask = ImageChops.darker(mask, ImageChops.lighter(ImageChops.lighter(moved[0], moved[1]), moved[2]))
    tight = mask.getbbox()
    # only when most of what was found is in those colours: a thing drawn held in other colours keeps its box
    if tight is None or (tight[2] - tight[0]) * (tight[3] - tight[1]) < held[2] * held[3] * OWN_KEPT:
        return held
    return [held[0] + tight[0], held[1] + tight[1], tight[2] - tight[0], tight[3] - tight[1]]


def _middle(box: list[int]) -> tuple[float, float]:
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def _same(one: list[int], other: list[int], within: float) -> bool:
    return all(abs(a - b) <= within for a, b in zip(one, other))


def follows(one: tuple[list[int], tuple[float, float]], other: tuple[list[int], tuple[float, float]]) -> float | None:
    """How many times the pointer's move a box found on one look and one found on the next moved, when they are
    the same thing following the pointer between them: the same way (across it no more than ACROSS of the move or
    FOLLOW_PX), by a share of it within GAIN_RANGE, keeping its size. None when they are not."""
    first, at = one
    second, then = other
    moved = (then[0] - at[0], then[1] - at[1])
    length = (moved[0] ** 2 + moved[1] ** 2) ** 0.5
    if length < 1:
        return None
    was, now = _middle(first), _middle(second)
    shifted = (now[0] - was[0], now[1] - was[1])
    gain = (shifted[0] * moved[0] + shifted[1] * moved[1]) / length ** 2
    across = abs(shifted[0] * moved[1] - shifted[1] * moved[0]) / length
    if not GAIN_RANGE[0] <= gain <= GAIN_RANGE[1] or across > max(FOLLOW_PX, ACROSS * gain * length):
        return None
    if abs(second[2] / max(first[2], 1) - 1) > SAME_SIZE or abs(second[3] / max(first[3], 1) - 1) > SAME_SIZE:
        return None
    return gain


def followed(one: tuple[list[int], tuple[float, float]], other: tuple[list[int], tuple[float, float]]) -> bool:
    """Whether a box found on one look and one found on the next are the same thing moving with the pointer."""
    return follows(one, other) is not None


class Follow:
    """Where a held thing is drawn for a place of the pointer: its middle at `start + anchor + gain × (pointer −
    start)`, `start` being where it was pressed. A gain of 1 is a drawing that keeps its distance from the
    finger; more is one that runs ahead of it."""

    def __init__(self, gain: float, anchor: tuple[float, float]) -> None:
        self.gain, self.anchor = gain, anchor

    @classmethod
    def through(cls, start: tuple[float, float], pointer: tuple[float, float], middle: tuple[float, float],
                gain: float) -> "Follow":
        """The way of following that draws the thing's middle at `middle` while the pointer is at `pointer`."""
        return cls(gain, (middle[0] - start[0] - gain * (pointer[0] - start[0]),
                          middle[1] - start[1] - gain * (pointer[1] - start[1])))

    def drawn(self, start: tuple[float, float], pointer: tuple[float, float]) -> tuple[float, float]:
        return (start[0] + self.anchor[0] + self.gain * (pointer[0] - start[0]),
                start[1] + self.anchor[1] + self.gain * (pointer[1] - start[1]))

    def pointer(self, start: tuple[float, float], goal: tuple[float, float]) -> tuple[float, float]:
        """Where the pointer is to be for the thing's middle to be drawn at `goal`."""
        return (start[0] + (goal[0] - start[0] - self.anchor[0]) / self.gain,
                start[1] + (goal[1] - start[1] - self.anchor[1]) / self.gain)


class Carrier:
    """Carries drags on one surface, remembering what the carries before showed."""

    def __init__(self, surface: Any) -> None:
        self.surface = surface
        # how the held drawing followed the pointer, as last measured: all a surface that cannot hold is given
        self.follow: Follow | None = None
        # how many times the pointer's move each measured drawing moved: their middle one stands in for a carry
        # whose looks could not tell it
        self.gains: list[float] = []
        self.log: list[dict[str, Any]] = []
        # the press before moving that picks things up here (HOLDS), and whether any carry here picked one up
        self.hold_s = HOLDS[0]
        self.picked_any = False

    @property
    def offset(self) -> tuple[float, float] | None:
        """The held drawing's middle minus the pointer where it was pressed, as last measured."""
        return self.follow.anchor if self.follow else None

    def _gain(self) -> float:
        return sorted(self.gains)[len(self.gains) // 2] if self.gains else 1.0

    async def _look(self, look: Any, before: bytes, start: tuple[float, float], pointer: tuple[float, float],
                    source: list[int] | None, near: tuple[float, float] | None = None) -> dict[str, Any]:
        """What the screen shows while the pointer holds still at `pointer`, once the held drawing has come to
        rest: captures in a row until the things that may be held are drawn where they were on the one before.
        `near`: the place the held thing is looked for nearest (else the pointer). Gives `still` (those things,
        nearest first), `gone` (whether the thing left its place), and how long it took."""
        began, shots, previous, seen = asyncio.get_running_loop().time(), 0, None, None
        if LIFT_S:
            await asyncio.sleep(LIFT_S)
        while True:
            png = await look()
            shots += 1
            found = candidates(before, png, start, near or pointer, source)
            boxes = found[1] if found else []
            seen = {"png": png, "origin": found[0] if found else None, "gone": gone(before, png, start, source),
                    "still": [], "boxes": boxes}
            if previous is not None:
                seen["still"] = [box for box in boxes if any(_same(box, other, STILL_PX) for other in previous)]
                if seen["still"] or (not boxes and not previous):
                    break
            if asyncio.get_running_loop().time() - began >= STILL_MOST_S:
                # never at rest: what was drawn last is all there is
                seen["still"], seen["restless"] = boxes, True
                break
            previous = boxes
        seen.update(shots=shots, waited_s=round(asyncio.get_running_loop().time() - began, 2))
        return seen

    async def carry(self, start: tuple[float, float], goal: tuple[float, float], duration_ms: int,
                    hold_s: float | None = None, before: bytes | None = None,
                    source: list[int] | None = None) -> dict[str, Any]:
        """Drag what is at `start` so that its drawing's middle comes to `goal`, pressed `hold_s` (else as long
        as picked things up here) before moving. `before`: the screen as it was last read, when nothing was done
        since; `source`: where it was read to show the thing. Returns what was measured: whether the thing was
        picked up (`picked`), how its drawing followed the pointer (`gain`, `anchor`, `measured` when both looks
        told it), each look, where the pointer was let go (`end`) and how far from `goal` the drawing was then
        (`off`)."""
        surface = self.surface
        hold_s = max(self.hold_s, hold_s or 0.0)
        told: dict[str, Any] = {"start": [round(part, 1) for part in start], "goal": [round(part, 1) for part in goal],
                                "measured": False, "picked": False, "hold_s": hold_s, "looks": []}
        self.log.append(told)
        if HOLD not in surface.kinds:
            # one movement: the thing's own middle is taken to be under the pointer unless a carry showed otherwise
            end = self.follow.pointer(start, goal) if self.follow else goal
            told["end"] = [round(part, 1) for part in end]
            await surface.drag(*start, *end, duration_ms)
            return told
        # the looks are captures as they are: a drawing at rest is told from what keeps moving by the looks
        # themselves, and a picture made of several captures would take seconds each
        look = getattr(surface, "snap", surface.capture)
        if before is not None and not await self._fits(before):
            before = None
        if before is None:
            before = await look()
        # pressed again longer only while no carry here has picked a thing up: after one has, a press that
        # shows nothing gone is a look that failed, and pressing a piece again and again may do something to it
        holds = [hold_s, *(step for step in HOLDS if step > hold_s)] if not self.picked_any else [hold_s]
        along = (goal[0] - start[0], goal[1] - start[1])
        length = max((along[0] ** 2 + along[1] ** 2) ** 0.5, 1.0)
        way = (along[0] / length, along[1] / length)
        for tried, hold in enumerate(holds, 1):
            told["hold_s"], told["tries"] = hold, tried
            await surface.press(*start)
            try:
                if hold:
                    await asyncio.sleep(hold)
                first = None
                for share in FIRST_LEG:
                    reach = max(share, min(1.0, LEAST_LEG / length))
                    pointer = (start[0] + along[0] * reach, start[1] + along[1] * reach)
                    await surface.move(*pointer, LEG_MS)
                    seen = await self._look(look, before, start, pointer, source)
                    told["looks"].append(self._told(seen, pointer))
                    if seen["still"]:
                        first = (seen, pointer)
                        break
                    if not seen["gone"]:
                        break
                # picked up when a drawing was seen at rest by the pointer: a place that only looks emptied may be
                # a tutorial's hand gone from the picture it was read from
                picked = first is not None
                if not picked and tried < len(holds):
                    # nothing drawn held: it was not picked up. Let go where it was pressed, press again longer
                    await surface.move(*start, LEG_MS)
                    await asyncio.sleep(REST_S)
                    continue
                told["picked"] = picked
                if picked:
                    self.picked_any = True
                    self.hold_s = max(self.hold_s, hold)
                follow = await self._measure(look, before, start, way, length, first, source, told) if first else None
                if follow is None and first is not None and first[0]["still"]:
                    # seen once only: it follows as the carries before showed, through the place it was seen at
                    follow = Follow.through(start, first[1], _middle(first[0]["still"][0]), self._gain())
                follow = follow or self.follow
                if follow is not None:
                    told.update(gain=round(follow.gain, 3), anchor=[round(part) for part in follow.anchor],
                                offset=[round(part) for part in follow.anchor])
                end = follow.pointer(start, goal) if follow else goal
                end = await self._aim(look, before, start, goal, end, follow, source, duration_ms, told)
                told["end"] = [round(part, 1) for part in end]
                await asyncio.sleep(REST_S)
                break
            finally:
                await surface.lift()
        return told

    async def _fits(self, png: bytes) -> bool:
        from PIL import Image

        try:
            with Image.open(BytesIO(png)) as image:
                return list(image.size) == list(await self.surface.size())
        except Exception:
            return False

    @staticmethod
    def _told(seen: dict[str, Any], pointer: tuple[float, float]) -> dict[str, Any]:
        return {"pointer": [round(part) for part in pointer], "gone": seen["gone"], "shots": seen["shots"],
                "waited_s": seen["waited_s"], "held": seen["still"][0] if seen["still"] else None,
                **({"restless": True} if seen.get("restless") else {})}

    async def _measure(self, look: Any, before: bytes, start: tuple[float, float], way: tuple[float, float],
                       length: float, first: tuple[dict[str, Any], tuple[float, float]], source: list[int] | None,
                       told: dict[str, Any]) -> Follow | None:
        """The pointer moved a little further and the screen looked at again: what moved the way it did is
        what is held, and the two places it was drawn at tell how it follows the pointer."""
        seen, at = first
        step = max(STEP_LEAST, min(STEP_PX, STEP_SHARE * length))
        pointer = (at[0] + way[0] * step, at[1] + way[1] * step)
        await self.surface.move(*pointer, LEG_MS)
        again = await self._look(look, before, start, pointer, source)
        told["looks"].append(self._told(again, pointer))
        pairs = sorted(((gain, one, other) for one in seen["still"] for other in again["still"]
                        if (gain := follows((one, at), (other, pointer))) is not None),
                       key=lambda pair: _gap(pointer, pair[2]))
        if not pairs:
            told["why"] = "nothing_followed"
            return None
        gain, _, held = pairs[0]
        told.update(measured=True, held=held, origin=again["origin"])
        self.gains = [*self.gains, gain][-FOLLOWS_KEPT:]
        self.follow = Follow.through(start, pointer, _middle(held), gain)
        return self.follow

    async def _aim(self, look: Any, before: bytes, start: tuple[float, float], goal: tuple[float, float],
                   end: tuple[float, float], follow: Follow | None, source: list[int] | None, duration_ms: int,
                   told: dict[str, Any]) -> tuple[float, float]:
        """The pointer moved to `end`, the drawing looked at once it rests there and the pointer moved further by
        what its middle is still off `goal`; where the pointer ends."""
        size = await self.surface.size()
        end = (min(max(end[0], 0.0), size[0] - 1.0), min(max(end[1], 0.0), size[1] - 1.0))
        await self.surface.move(*end, max(LEG_MS, duration_ms - LEG_MS))
        if follow is None:
            return end
        held = told.get("held") or (told["looks"][-1]["held"] if told["looks"] else None)
        for _ in range(AIMS + 1):
            seen = await self._look(look, before, start, end, source, near=goal)
            told["looks"].append(self._told(seen, end))
            same = [box for box in seen["still"] if held is None or (
                abs(box[2] / max(held[2], 1) - 1) <= SAME_SIZE and abs(box[3] / max(held[3], 1) - 1) <= SAME_SIZE)]
            if not same:
                told["off"] = None
                return end
            middle = _middle(same[0])
            off = (goal[0] - middle[0], goal[1] - middle[1])
            told["off"] = [round(part) for part in off]
            within = max(AIM_PX, AIM_SHARE * min(same[0][2], same[0][3]))
            if abs(off[0]) <= within and abs(off[1]) <= within or told.get("aimed", 0) >= AIMS:
                return end
            # the rest of the way, by how the drawing follows the pointer
            end = (min(max(end[0] + off[0] / follow.gain, 0.0), size[0] - 1.0),
                   min(max(end[1] + off[1] / follow.gain, 0.0), size[1] - 1.0))
            told["aimed"] = told.get("aimed", 0) + 1
            await self.surface.move(*end, LEG_MS)
        return end
