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
from collections import OrderedDict, deque
from io import BytesIO
import time
from typing import Any

from .frames import source_of
from .surface import HOLD

# the picture the change is looked for in is this many times smaller a side; a change smaller than this many
# of 255 in every channel is none (a gradient redrawn, compression)
REDUCE, CHANGED = 4, 28
# a wait for a screen to settle compares it this many times smaller a side first (`coarse_boxes`)
COARSE = 4
# changed spots this near (reduced pixels) are one thing: a piece's blocks have gaps between them
JOIN = 3
# a held drawing is the same shape as what was picked up: its width and height grew by the same factor, within this
ASPECT = 0.3
# the picked-up thing's own colours: looked for among this many, each filling at least this share of its box and
# at least OWN_MAIN of what its commonest colour fills (its body, not its rim or shadow, whose dark shades a
# tutorial's overlay shares: 2026-11-16), matched within this much a channel; the box narrowed to them is used
# when it keeps this share of the area
OWN_COLOURS, OWN_SHARE, OWN_MAIN, OWN_NEAR, OWN_KEPT = 6, 0.08, 0.5, 36, 0.4
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
# a look is taken once what may be held is drawn in the same place on two frames in a row, within this many
# pixels a side (the drawing catches up with the pointer a moment late), the two at least STILL_GAP_S apart (from
# a source that gives frames as they are drawn, two in a row are milliseconds apart and say nothing of rest; a
# polled capture takes longer than this anyway), and no longer looked for than this
STILL_PX, STILL_GAP_S, STILL_MOST_S = 4, 0.15, 1.5
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
# once this many carries measured how the drawing follows the pointer, all within this share of each other, a
# carry looks once only on the way: where the thing is drawn there tells where it hangs from, the gain is the one
# measured (PLAN §17, F2). The look before letting go stays: a drawing that catches up late is let go only once
# it rests where it was meant to. A carry whose move the game did not take sends the next back to measuring
# (`doubt`)
SURE_AFTER, SURE_SPREAD = 2, 0.06
# what keeps moving where the screen is read (`motion.Steady.moving`) and is left out of what changed while a thing
# is held: each place no larger than this share of the screen (a glint on a gem, a twinkling star; 2026-11-16, the
# glints of a whole board joined into one change the size of the board and were taken for the held piece)
IGNORED_MOST = 0.02
# once carries measured how much larger the held drawing is than the thing picked up, a place is taken for it only
# when its sides are that many times the thing's, within this share
SCALE_SLACK = 0.35
# a carry that saw the held drawing once only hangs it from where it was seen there, unless that is further from
# where the carries before hung it than this many times the drawing's larger side: what was seen was not it
ANCHOR_SLACK = 1.0
# how a game hangs a held thing is taken as known (`Hang`) once this many carries that ended where they were meant
# to showed it; a thing as tall as one of them within HANG_SAME hangs as that one did, another by the line through
# those of different heights (at least HANG_APART apart); no more than HANG_KEPT are kept
HANG_SURE, HANG_SAME, HANG_APART, HANG_KEPT = 2, 0.15, 0.3, 30
# the lifts lie closer together counted from where the thing was pressed by more than this many pixels: the game
# hangs it from there
HANG_CLEAR = 4.0
HANG_SIZE = 0.04
UNSEEN_MOST = 2
# a carry with no looks that told how the thing hangs is moved by what the cells show this many times; a thing not
# let go is taken this many pixels from the foot of the screen first
AIMS_BLIND, BACK_FOOT = 3, 12
# after more than this many carries on which the cells showed a held thing but its shape nowhere, such a carry is let
# go by the game's way of hanging held things alone
UNFIT_MOST = 2
# once this many carries in a row lay right at the first look, a carry is let go with no look before it (the look is a
# capture, about half a second on a phone over adb); the pointer rests this long first for the drawing to catch up,
# twice as long after such a carry's move did not do what it should, up to the most
UNCHECKED_AFTER, UNCHECKED_REST_S, UNCHECKED_REST_MOST_S = 5, 0.25, 1.0
# a held piece whose blocks are drawn apart (`arranged`): its blocks are of one size within ARRANGED_ALIKE, each lies
# within ARRANGED_NEAR of where the piece's shape puts it (shares of how far apart blocks are drawn), and a block's
# side is between ARRANGED_LEAST and ARRANGED_MOST of that distance
ARRANGED_ALIKE, ARRANGED_NEAR, ARRANGED_LEAST, ARRANGED_MOST = 0.2, 0.25, 0.5, 1.05


def _boxes(before: bytes, after: bytes, ignore: list[list[int]] | None = None) -> list[list[int]]:
    """The things that changed between two captures, each as the box [left, top, width, height] around it.
    `ignore`: places whose changes are left out (what keeps moving there all the time)."""
    from PIL import Image, ImageChops

    with Image.open(BytesIO(before)) as one, Image.open(BytesIO(after)) as other:
        if one.size != other.size:
            return []
        changed = ImageChops.difference(one.convert("RGB"), other.convert("RGB"))
    return _found(changed, ignore)


def coarse_boxes(before: bytes, after: bytes) -> list[list[int]]:
    """`_boxes` on the two captures made COARSE times smaller a side first, its boxes given on the whole screen:
    whether a screen still changes, and where, for a wait on it to end (PLAN §36, 강제 대기 검토: a whole
    phone screen took 88 ms to compare, made smaller 9 ms). A change smaller than a few pixels is lost in the
    averaging, and changes near one another come out as one box: such a wait may end later, never earlier."""
    from PIL import ImageChops

    one, other = _coarse(before), _coarse(after)
    if one.size != other.size:
        return []
    return [[value * COARSE for value in box] for box in _found(ImageChops.difference(one, other))]


# the captures last made smaller, by their bytes: a wait compares each frame with the one before and the one after,
# and taking a phone's whole screen out of its PNG is most of what a comparison costs (about 21 ms of 25)
_COARSE_KEPT: "OrderedDict[bytes, Any]" = OrderedDict()


def _coarse(png: bytes) -> Any:
    from PIL import Image

    if (kept := _COARSE_KEPT.get(png)) is not None:
        _COARSE_KEPT.move_to_end(png)
        return kept
    with Image.open(BytesIO(png)) as image:
        small = image.convert("RGB").reduce(COARSE)
    _COARSE_KEPT[png] = small
    while len(_COARSE_KEPT) > 4:
        _COARSE_KEPT.popitem(last=False)
    return small


def _found(changed: Any, ignore: list[list[int]] | None = None) -> list[list[int]]:
    """The boxes around what changed in a picture of the differences between two captures."""
    from PIL import ImageChops, ImageDraw

    bands = [band.point(lambda value: 255 if value > CHANGED else 0) for band in changed.split()]
    full = ImageChops.lighter(ImageChops.lighter(bands[0], bands[1]), bands[2])
    if ignore:
        draw = ImageDraw.Draw(full)
        for left, top, w, h in ignore:
            draw.rectangle((left, top, left + w - 1, top + h - 1), fill=0)
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


def arranged(boxes: list[list[int]], cells: list[list[int]]) -> list[int] | None:
    """The box around a held piece whose blocks are drawn apart: of the things that changed, as many as the piece
    has blocks, of one size, lying as its blocks lie (`cells`, each a row and a column). A game that draws a held
    piece's blocks with a gap between them shows it as that many things, none of them in the piece's own
    proportions (2026-11-17: blocks of 84 pixels 14 apart, where what changed is joined within 12). None when no
    such group is there, or the piece is one block."""
    if len(cells) < 2 or len(boxes) < len(cells):
        return None
    middles = [(box[0] + box[2] / 2, box[1] + box[3] / 2) for box in boxes]
    (r0, c0), (r1, c1) = cells[0], cells[1]
    for one, first in enumerate(boxes):
        for other, second in enumerate(boxes):
            if one == other or not _alike(first, second):
                continue
            # the two as the piece's first two blocks: how far apart blocks are drawn, across and down
            across = (middles[other][0] - middles[one][0]) / (c1 - c0) if c1 != c0 else None
            down = (middles[other][1] - middles[one][1]) / (r1 - r0) if r1 != r0 else None
            pitch = across if down is None else down if across is None else (across + down) / 2
            if pitch is None or pitch <= 0 or (across is not None and down is not None and abs(across - down) > ARRANGED_NEAR * pitch):
                continue
            if c1 == c0 and abs(middles[other][0] - middles[one][0]) > ARRANGED_NEAR * pitch \
                    or r1 == r0 and abs(middles[other][1] - middles[one][1]) > ARRANGED_NEAR * pitch:
                continue
            # a block is drawn no larger than blocks lie apart, and not much smaller
            if not ARRANGED_LEAST * pitch <= max(first[2], first[3]) <= ARRANGED_MOST * pitch:
                continue
            taken, left = [], list(range(len(boxes)))
            for row, col in cells:
                at = (middles[one][0] + (col - c0) * pitch, middles[one][1] + (row - r0) * pitch)
                near = [index for index in left if _alike(boxes[index], first)
                        and abs(middles[index][0] - at[0]) <= ARRANGED_NEAR * pitch
                        and abs(middles[index][1] - at[1]) <= ARRANGED_NEAR * pitch]
                if not near:
                    break
                taken.append(near[0])
                left.remove(near[0])
            else:
                found = [boxes[index] for index in taken]
                x0, y0 = min(box[0] for box in found), min(box[1] for box in found)
                return [x0, y0, max(box[0] + box[2] for box in found) - x0, max(box[1] + box[3] for box in found) - y0]
    return None


def _alike(one: list[int], other: list[int]) -> bool:
    return abs(one[2] / max(other[2], 1) - 1) <= ARRANGED_ALIKE and abs(one[3] / max(other[3], 1) - 1) <= ARRANGED_ALIKE


def candidates(before: bytes, during: bytes, start: tuple[float, float], pointer: tuple[float, float],
               source: list[int] | None = None, scale: float | None = None,
               ignore: list[list[int]] | None = None, cells: list[list[int]] | None = None
               ) -> tuple[list[int], list[list[int]]] | None:
    """Where the thing pressed at `start` was (`origin`), and every place it may be drawn while the pointer holds
    it at `pointer`, nearest the pointer first: what changed between the capture before the press and the one
    during the hold, as large as what was picked up or larger and in its proportions. `source`: where the screen
    was read to show the thing, taken for its place when something changed there. `scale`: how many times larger
    than the thing the carries before saw it drawn held, when they measured it. `ignore`: places left out of what
    changed. None when nothing can be told apart (still overlapping, or nothing of the right shape moved)."""
    boxes = [box for box in _boxes(before, during, ignore) if box[2] >= 2 * REDUCE and box[3] >= 2 * REDUCE]
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
    # each narrowed to the thing's own colours first: a held drawing joined to what else changed beside it (a
    # tutorial's overlay gone from where it was) is not in its proportions until it is
    narrowed = [_own(before, during, origin, box) for box in rest
                if box[2] >= origin[2] * 0.7 and box[3] >= origin[3] * 0.7]
    # a piece's blocks drawn apart, each a thing of its own, lying as the piece's blocks lie: that is the piece,
    # before anything in its proportions is looked for (one block of a square piece is square too: 2026-11-17,
    # taken for the whole piece, it hung the piece half a block off)
    together = arranged(rest, cells) if cells else None
    if together is not None:
        return origin, [together]
    shaped = [box for box in narrowed if _shaped(box, origin) and (scale is None or _scaled(box, origin, scale))]
    if not shaped:
        return None
    return origin, sorted(shaped, key=lambda box: _gap(pointer, box))


def _scaled(box: list[int], origin: list[int], scale: float) -> bool:
    """Whether a box is `scale` times as large as what was picked up, side by side, within SCALE_SLACK."""
    return all(abs(side / max(scale * was, 1.0) - 1) <= SCALE_SLACK for side, was in ((box[2], origin[2]), (box[3], origin[3])))


def _shaped(box: list[int], origin: list[int]) -> bool:
    """Whether a box is as large as what was picked up or larger, and in its proportions."""
    return box[2] >= origin[2] * 0.7 and box[3] >= origin[3] * 0.7 \
        and abs((box[2] / origin[2]) / (box[3] / origin[3]) - 1) <= ASPECT


def gone(before: bytes, during: bytes, start: tuple[float, float], source: list[int] | None = None,
         ignore: list[list[int]] | None = None) -> bool:
    """Whether the thing pressed at `start` left its place: what the screen showed there changed. A press that
    picked nothing up leaves it as it was."""
    boxes = [box for box in _boxes(before, during, ignore) if box[2] >= 2 * REDUCE and box[3] >= 2 * REDUCE]
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
    own = [(count, colour) for count, colour in sorted(was.quantize(OWN_COLOURS).convert("RGB").getcolors() or [], reverse=True)
           if count >= origin[2] * origin[3] * OWN_SHARE and not any(
               max(abs(a - b) for a, b in zip(colour, other)) <= OWN_NEAR for other in now)]
    own = [colour for count, colour in own if count >= own[0][0] * OWN_MAIN] if own else []
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
    if tight is None:
        return held
    narrowed = [held[0] + tight[0], held[1] + tight[1], tight[2] - tight[0], tight[3] - tight[1]]
    # only when most of what was found is in those colours, or what is is in the thing's proportions: a thing
    # drawn held in other colours keeps its box
    if narrowed[2] * narrowed[3] < held[2] * held[3] * OWN_KEPT and not _shaped(narrowed, origin):
        return held
    return narrowed


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


def trusted(told: dict[str, Any] | None) -> bool:
    """Whether a carry is known to have put the thing where it was meant to go: it picked it up, and it either
    measured how the drawing follows or, sure of that from the carries before, saw the drawing on the way (`quick`).
    A move whose carry is not trusted and did nothing says nothing against how the screen was read."""
    return bool(told and told.get("picked") and (told.get("measured") or told.get("quick") or told.get("checked")
                                                 or told.get("by_rule") or told.get("unchecked")))


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


class Hang:
    """Where a game draws a held thing, for any thing and any place of the pointer, as its carries showed: the
    drawing's middle at `the thing's own middle where it waited + lift + gain × (pointer − start)`, the lift going
    with the thing's height (a game that holds every piece by its foot lifts a tall one's middle further). A game
    keeps to one such rule (three games of one block game, 63 looks: within 3 pixels at the middle, 11 at nine in
    ten; 2026-11-16), so once it is known a carry goes straight to where the thing is to lie.

    Some games hang the drawing from where it was pressed instead of from the thing's middle. Which this game
    does is told by the carries too: the lifts are compared both ways, and the way they agree better is taken."""

    def __init__(self) -> None:
        self.samples: list[dict[str, float]] = []
        # a carry by this rule did not do what it should: it is not used until one more carry bears it out
        self.shaken = False

    def add(self, source: list[int], start: tuple[float, float], pointer: tuple[float, float],
            middle: tuple[float, float], gain: float) -> None:
        was = (source[0] + source[2] / 2, source[1] + source[3] / 2)
        self.samples = [*self.samples, {
            "h": float(source[3]), "gain": gain,
            "x": middle[0] - was[0] - gain * (pointer[0] - start[0]),
            "y": middle[1] - was[1] - gain * (pointer[1] - start[1]),
            # where on the thing it was pressed, from its middle
            "px": start[0] - was[0], "py": start[1] - was[1]}][-HANG_KEPT:]
        self.shaken = False

    @property
    def gain(self) -> float | None:
        return _median([sample["gain"] for sample in self.samples]) if self.samples else None

    def _from_press(self) -> bool:
        """Whether the drawing hangs from where the thing was pressed, not from its middle: the lifts across,
        which no height changes, lie closer together counted that way."""
        def spread(values: list[float]) -> float:
            return max(values) - min(values)
        middle = spread([sample["x"] for sample in self.samples])
        press = spread([sample["x"] - sample["px"] for sample in self.samples])
        return press < middle - HANG_CLEAR

    def lift(self, source: list[int], start: tuple[float, float] | None = None) -> tuple[float, float] | None:
        """How far from where it waited a thing of this height is drawn when the pointer has not moved."""
        if len(self.samples) < HANG_SURE or self.shaken:
            return None
        share = 1.0 if start is not None and self._from_press() else 0.0
        seen = [{"h": sample["h"], "x": sample["x"] - share * sample["px"], "y": sample["y"] - share * sample["py"]}
                for sample in self.samples]
        pressed = (start[0] - source[0] - source[2] / 2, start[1] - source[1] - source[3] / 2) if share else (0.0, 0.0)
        height, across = float(source[3]), _median([sample["x"] for sample in seen])
        same = [sample["y"] for sample in seen if abs(sample["h"] - height) <= HANG_SAME * height]
        if same:
            return across + pressed[0], _median(same) + pressed[1]
        slopes = [(one["y"] - other["y"]) / (one["h"] - other["h"]) for index, one in enumerate(seen)
                  for other in seen[:index] if abs(one["h"] - other["h"]) >= HANG_APART * min(one["h"], other["h"])]
        if not slopes:
            return None
        slope = _median(slopes)
        return across + pressed[0], _median([sample["y"] - slope * sample["h"] for sample in seen]) + slope * height + pressed[1]

    def pointer(self, source: list[int] | None, start: tuple[float, float], goal: tuple[float, float]
                ) -> tuple[float, float] | None:
        """Where the pointer is to be for the thing that waits in `source` to be drawn with its middle at `goal`."""
        lift = self.lift(source, start) if source else None
        if lift is None:
            return None
        gain, was = self.gain, (source[0] + source[2] / 2, source[1] + source[3] / 2)
        return (start[0] + (goal[0] - was[0] - lift[0]) / gain, start[1] + (goal[1] - was[1] - lift[1]) / gain)

    def to_json(self) -> list[dict[str, float]]:
        return [{key: round(value, 3) for key, value in sample.items()} for sample in self.samples]

    @classmethod
    def from_json(cls, kept: Any) -> "Hang":
        hang, keys = cls(), ("h", "gain", "x", "y", "px", "py")
        if isinstance(kept, list):
            hang.samples = [{key: float(sample[key]) for key in keys} for sample in kept
                            if isinstance(sample, dict) and all(key in sample for key in keys)][-HANG_KEPT:]
        return hang


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    half = len(ordered) // 2
    return ordered[half] if len(ordered) % 2 else (ordered[half - 1] + ordered[half]) / 2


class Carrier:
    """Carries drags on one surface, remembering what the carries before showed."""

    def __init__(self, surface: Any, source: Any = None) -> None:
        self.surface = surface
        # the frames looked at on the way, as they are: a drawing at rest is told from what keeps moving by the
        # looks themselves, and a picture drawn through several frames would take seconds each
        self.frames = source or source_of(surface)
        # how the held drawing followed the pointer, as last measured: all a surface that cannot hold is given
        self.follow: Follow | None = None
        # how many times the pointer's move each measured drawing moved: their middle one stands in for a carry
        # whose looks could not tell it
        self.gains: list[float] = []
        # how many of them were measured since a carry's move was last doubted: only those make a carry sure
        self.since_doubt = 0
        # how many times larger than the thing picked up each measured held drawing was (its sides on average)
        self.scales: list[float] = []
        # this carry's places left out of what changed, and the scale it holds what it finds to
        self._ignore: list[list[int]] = []
        self._scale: float | None = None
        self.log: list[dict[str, Any]] = []
        # the press before moving that picks things up here (HOLDS), and whether any carry here picked one up
        self.hold_s = HOLDS[0]
        self.picked_any = False
        # how this game hangs a held thing, whatever the thing (`Hang`): kept for the site's next game
        self.hang = Hang()
        # where the pointer was and where the held thing was drawn, on the look of this carry that told it; and
        # the carries in a row whose check saw nothing held over the board (after UNSEEN_MOST the check is not
        # asked: it cannot see this game's held things, and a carry is aimed by its looks alone as before)
        self._drawn: tuple[tuple[float, float], list[int]] | None = None
        self._cells: list[list[int]] | None = None
        # carries in a row that went straight to where the thing was to lie and were seen to lie right at the first
        # look: from UNCHECKED_AFTER on, a carry is let go without the look, after resting this long
        self._passed = 0
        self._rest_s = UNCHECKED_REST_S
        self._unseen = 0
        # carries on which the cells showed something held but its shape nowhere (`_by_rule`)
        self._unfit = 0
        # told each frame a carry takes with the pointer held (whoever shows the game as it is played)
        self.on_frame: Any = None

    @property
    def offset(self) -> tuple[float, float] | None:
        """The held drawing's middle minus the pointer where it was pressed, as last measured."""
        return self.follow.anchor if self.follow else None

    def _gain(self) -> float:
        return sorted(self.gains)[len(self.gains) // 2] if self.gains else 1.0

    def _sure(self) -> bool:
        recent = self.gains[-SURE_AFTER:]
        return self.since_doubt >= SURE_AFTER and len(recent) >= SURE_AFTER \
            and max(recent) <= min(recent) * (1 + SURE_SPREAD)

    def _ignored(self, size: list[int]) -> list[list[int]]:
        """The small places things keep moving at where the screen was last read (`motion.Steady.moving`)."""
        area = size[0] * size[1]
        return [list(box) for box in getattr(self.surface, "moving", None) or [] if box[2] * box[3] <= IGNORED_MOST * area]

    def _scaled_by(self) -> float | None:
        return sorted(self.scales)[len(self.scales) // 2] if self.scales else None

    def doubt(self) -> None:
        """A carry's move did not do what it should: the next carries measure how the drawing follows again. What
        was measured before still stands in for a carry whose looks cannot tell it (2026-11-16: emptied, a carry
        that could not measure was aimed as if the drawing kept to the finger and landed two cells off)."""
        self.since_doubt = 0
        self.hang.shaken = True
        if self._passed >= UNCHECKED_AFTER:
            # it was let go unlooked at: perhaps before the drawing had caught up with the pointer
            self._rest_s = min(UNCHECKED_REST_MOST_S, self._rest_s * 2)
        self._passed = 0

    async def _look(self, before: bytes, start: tuple[float, float], pointer: tuple[float, float],
                    source: list[int] | None, near: tuple[float, float] | None = None) -> dict[str, Any]:
        """What the screen shows while the pointer holds still at `pointer`, once the held drawing has come to
        rest: frames from LIFT_S after the pointer stopped, until the things that may be held are drawn where they
        were on the one before. `near`: the place the held thing is looked for nearest (else the pointer). Gives
        `still` (those things, nearest first), `gone` (whether the thing left its place), and how long it took."""
        began, shots, previous, seen = time.monotonic(), 0, None, None
        after = began + LIFT_S
        while True:
            frame = await self.frames.frame(after)
            png, after = frame.png, frame.at + STILL_GAP_S
            shots += 1
            found = candidates(before, png, start, near or pointer, source, self._scale, self._ignore, self._cells)
            boxes = found[1] if found else []
            seen = {"png": png, "origin": found[0] if found else None,
                    "gone": gone(before, png, start, source, self._ignore),
                    "still": [], "boxes": boxes}
            if previous is not None:
                seen["still"] = [box for box in boxes if any(_same(box, other, STILL_PX) for other in previous)]
                if seen["still"] or (not boxes and not previous):
                    break
            if time.monotonic() - began >= STILL_MOST_S:
                # never at rest: what was drawn last is all there is
                seen["still"], seen["restless"] = boxes, True
                break
            previous = boxes
        seen.update(shots=shots, waited_s=round(time.monotonic() - began, 2))
        return seen

    async def carry(self, start: tuple[float, float], goal: tuple[float, float], duration_ms: int,
                    hold_s: float | None = None, before: bytes | None = None,
                    source: list[int] | None = None, check: Any = None,
                    shape: list[list[int]] | None = None) -> dict[str, Any]:
        """Drag what is at `start` so that its drawing's middle comes to `goal`, pressed `hold_s` (else as long
        as picked things up here) before moving. `before`: the screen as it was last read, when nothing was done
        since; `source`: where it was read to show the thing. Returns what was measured: whether the thing was
        picked up (`picked`), how its drawing followed the pointer (`gain`, `anchor`, `measured` when both looks
        told it), each look, where the pointer was let go (`end`) and how far from `goal` the drawing was then
        (`off`).

        `check`: what tells from a frame whether the held thing lies where it is to go (a board's reader: `ok`,
        else `shift` in pixels and `covered`). With it the thing is let go only where it lies right, and taken back
        to where it waited when it does not come to (`cancelled`); and once the game's way of hanging held things
        is known (`Hang`), the pointer goes straight there with no looks on the way (`known`)."""
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
        self._drawn = None
        if self._unseen >= UNSEEN_MOST:
            check = None
        known = self.hang.pointer(source, start, goal) if check is not None else None
        if known is not None:
            return await self._straight(start, known, duration_ms, hold_s, check, told)
        if before is not None and not await self._fits(before):
            before = None
        if before is None:
            before = (await self.frames.frame()).png
        self._ignore, self._scale = self._ignored(await surface.size()), self._scaled_by()
        # the thing's blocks, as rows and columns, when whoever read the screen knows them (`arranged`)
        self._cells = shape
        if self._ignore:
            told["ignored"] = len(self._ignore)
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
                    seen = await self._look(before, start, pointer, source)
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
                quick = first is not None and self._sure()
                if quick:
                    # how it follows is known: where it is drawn now tells where it hangs from
                    told["quick"] = True
                    follow = Follow.through(start, first[1], _middle(first[0]["still"][0]), self._gain())
                    self._drawn = (first[1], first[0]["still"][0])
                else:
                    follow = await self._measure(before, start, way, length, first, source, told) if first else None
                if follow is None and first is not None and first[0]["still"]:
                    # seen once only: it follows as the carries before showed, through the place it was seen at,
                    # unless that hangs it far from where the carries before did (what was seen was not it)
                    seen_once = Follow.through(start, first[1], _middle(first[0]["still"][0]), self._gain())
                    if self._plausible(seen_once, first[0]["still"][0]):
                        follow = seen_once
                    else:
                        told["anchor_refused"] = [round(part) for part in seen_once.anchor]
                follow = follow or self.follow
                if follow is not None:
                    told.update(gain=round(follow.gain, 3), anchor=[round(part) for part in follow.anchor],
                                offset=[round(part) for part in follow.anchor])
                end = follow.pointer(start, goal) if follow else goal
                if check is not None:
                    # with no looks that told how the thing hangs, the cells tell: the pointer goes where the thing
                    # is to lie, and is moved by how far off the cells show it, as often as AIMS_BLIND
                    gain = follow.gain if follow is not None else self.hang.gain or (self._gain() if self.gains else 1.0)
                    size = await surface.size()
                    end = (min(max(end[0], 0.0), size[0] - 1.0), min(max(end[1], 0.0), size[1] - 1.0))
                    await surface.move(*end, max(LEG_MS, duration_ms - LEG_MS))
                    end, lies = await self._settle(check, end, gain, told, aims=AIMS if follow is not None else AIMS_BLIND)
                    if lies and told.get("checked"):
                        told["picked"] = True
                        self.picked_any = True
                    if lies and told.get("checked") and source and self._drawn is not None:
                        # borne out by where it lay: this is how the game hangs a thing of this height
                        self.hang.add(source, start, self._drawn[0], _middle(self._drawn[1]), follow.gain)
                    elif lies and told.get("checked") and source and (self.gains or self.hang.samples):
                        # no look told where it was drawn, but the cells did: it lay where it was to go with the
                        # pointer here (a game whose held drawing the looks cannot tell from what else changes)
                        self.hang.add(source, start, end, goal, gain)
                    self._unseen = 0 if any(seen["seen"] for seen in told["checks"]) else self._unseen + 1
                    if not lies and follow is not None and told.get("measured") and self._by_rule(told):
                        told["by_rule"] = True
                    elif not lies:
                        await self._back(start, told)
                else:
                    end = await self._aim(before, start, goal, end, follow, source, duration_ms, told)
                told["end"] = [round(part, 1) for part in end]
                await asyncio.sleep(REST_S)
                break
            finally:
                await surface.lift()
        return told

    async def _straight(self, start: tuple[float, float], end: tuple[float, float], duration_ms: int, hold_s: float,
                        check: Any, told: dict[str, Any]) -> dict[str, Any]:
        """A carry in a game whose way of hanging held things is known: pressed, moved to where the pointer must
        be, let go once the thing lies where it is to go."""
        surface, gain = self.surface, self.hang.gain
        size = await surface.size()
        end = (min(max(end[0], 0.0), size[0] - 1.0), min(max(end[1], 0.0), size[1] - 1.0))
        told.update(known=True, gain=round(gain, 3), tries=1)
        await surface.press(*start)
        try:
            if hold_s:
                await asyncio.sleep(hold_s)
            # a short way first: a game takes a press that jumps as a tap
            length = max(((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5, 1.0)
            reach = max(FIRST_LEG[0], min(1.0, LEAST_LEG / length))
            await surface.move(start[0] + (end[0] - start[0]) * reach, start[1] + (end[1] - start[1]) * reach, LEG_MS)
            # the rest of the way in few steps where the surface can (each step of a held pointer costs its own time)
            await getattr(surface, "sweep", surface.move)(*end, max(LEG_MS, duration_ms - LEG_MS))
            if self._passed >= UNCHECKED_AFTER:
                # the cells bore the game's way of hanging held things out carry after carry: this one is let go
                # where that puts it, with no look first. The screen read after the move says whether it lay
                # right, and a move that did not do what it should brings the look back (`doubt`)
                told.update(unchecked=True, picked=True, end=[round(part, 1) for part in end])
                await asyncio.sleep(self._rest_s)
                return told
            end, lies = await self._settle(check, end, gain, told)
            # borne out once more when it lay right at the first look, nothing moved to make it so
            self._passed = self._passed + 1 if lies and told.get("checked") and not told.get("aimed") else 0
            told["picked"] = any(seen["seen"] for seen in told["checks"])
            if told["picked"]:
                self.picked_any = True
            elif not self.picked_any:
                # nothing was seen held, and nothing ever was here: pressed longer next time
                self.hold_s = next((step for step in HOLDS if step > self.hold_s), self.hold_s)
            self._unseen = 0 if told["picked"] else self._unseen + 1
            if lies:
                self._by_rule(told)
            if not lies and self._by_rule(told):
                # the cells do not show this game's held things as they lie (`_by_rule`): let go where the game's
                # way of hanging them puts it
                told["by_rule"] = True
            elif not lies:
                self.hang.shaken = True
                await self._back(start, told)
            told["end"] = [round(part, 1) for part in end]
            await asyncio.sleep(REST_S)
        finally:
            await surface.lift()
        return told

    async def _settle(self, check: Any, end: tuple[float, float], gain: float, told: dict[str, Any],
                      aims: int = AIMS) -> tuple[tuple[float, float], bool]:
        """Frames taken with the pointer at rest at `end` until the held thing lies where it is to go (`check`) or
        is seen the same on two in a row; when it lies off, the pointer is moved by that once and it is looked at
        again. Where the pointer ends, and whether the thing may be let go there."""
        size = await self.surface.size()
        checks = told.setdefault("checks", [])
        seen: dict[str, Any] = {"ok": False, "seen": 0}
        for aimed in range(aims + 1):
            began, previous, shots = time.monotonic(), None, 0
            after = began + LIFT_S
            while True:
                frame = await self.frames.frame(after)
                after, shots = frame.at + STILL_GAP_S, shots + 1
                if self.on_frame is not None:
                    await self.on_frame(frame.png)
                seen = check(frame.png)
                key = (seen.get("seen"), tuple(seen.get("shift") or ()))
                if seen.get("ok") or key == previous or time.monotonic() - began >= STILL_MOST_S:
                    break
                previous = key
            checks.append({**seen, "pointer": [round(part) for part in end], "shots": shots,
                           "waited_s": round(time.monotonic() - began, 2)})
            if seen.get("ok"):
                told["checked"] = True
                return end, True
            if not seen.get("shift") or aimed >= aims:
                break
            end = (min(max(end[0] - seen["shift"][0] / gain, 0.0), size[0] - 1.0),
                   min(max(end[1] - seen["shift"][1] / gain, 0.0), size[1] - 1.0))
            told["aimed"] = told.get("aimed", 0) + 1
            await self.surface.move(*end, LEG_MS)
        # over the cells it is to cover, though something beside them shows too (a glow): let go there
        return end, bool(seen.get("covered"))

    def _by_rule(self, told: dict[str, Any]) -> bool:
        """Whether a held thing the cells did not show lying right is let go all the same, by how the game hangs
        held things. The check by cells takes it that a held thing is drawn the size of the board's cells, or
        that the game shows where it would land; a game that draws it smaller and shows nothing (2026-11-17: its
        blocks 84 pixels on cells of 98) fills the cells under it only in part, and its shape is found nowhere.
        A carry on which the cells showed something held but its shape nowhere counts toward that; one on which
        they showed it lying right counts against. From UNFIT_MOST on, such carries are not taken back."""
        last = told["checks"][-1] if told.get("checks") else {}
        if any(seen.get("ok") for seen in told.get("checks", [])):
            self._unfit = max(0, self._unfit - 1)
        elif last.get("seen") and not last.get("shift") and not last.get("covered"):
            self._unfit += 1
        return self._unfit > UNFIT_MOST

    async def _back(self, start: tuple[float, float], told: dict[str, Any]) -> None:
        """The held thing does not lie where it is to go: taken back to where it waited, so that letting go
        puts it nowhere on the board."""
        told["cancelled"] = True
        # below where it waited, at the foot of the screen: a game that hangs the held thing well above the pointer
        # still has it over the board's last rows when the pointer is back where it pressed (2026-11-17: let go
        # there, a piece was put two rows up from the foot of the board)
        size = await self.surface.size()
        await self.surface.move(start[0], max(start[1], size[1] - BACK_FOOT), LEG_MS)
        await asyncio.sleep(LIFT_S)

    def _plausible(self, follow: Follow, drawn: list[int]) -> bool:
        """Whether a way of following hangs the drawing near where the last measured one did: within ANCHOR_SLACK
        times the drawing's larger side (where on the thing it was pressed moves its middle by less than that)."""
        if self.follow is None:
            return True
        reach = ANCHOR_SLACK * max(drawn[2], drawn[3])
        return all(abs(a - b) <= reach for a, b in zip(follow.anchor, self.follow.anchor))

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

    async def _measure(self, before: bytes, start: tuple[float, float], way: tuple[float, float],
                       length: float, first: tuple[dict[str, Any], tuple[float, float]], source: list[int] | None,
                       told: dict[str, Any]) -> Follow | None:
        """The pointer moved a little further and the screen looked at again: what moved the way it did is
        what is held, and the two places it was drawn at tell how it follows the pointer."""
        seen, at = first
        step = max(STEP_LEAST, min(STEP_PX, STEP_SHARE * length))
        pointer = (at[0] + way[0] * step, at[1] + way[1] * step)
        await self.surface.move(*pointer, LEG_MS)
        again = await self._look(before, start, pointer, source)
        told["looks"].append(self._told(again, pointer))
        pairs = sorted(((gain, one, other) for one in seen["still"] for other in again["still"]
                        if (gain := follows((one, at), (other, pointer))) is not None),
                       key=lambda pair: _gap(pointer, pair[2]))
        if not pairs:
            told["why"] = "nothing_followed"
            return None
        gain, _, held = pairs[0]
        told.update(measured=True, held=held, origin=again["origin"])
        origin = again["origin"] or source
        if origin and origin[2] > 0 and origin[3] > 0:
            self.scales = [*self.scales, (held[2] / origin[2] + held[3] / origin[3]) / 2][-FOLLOWS_KEPT:]
        self.gains = [*self.gains, gain][-FOLLOWS_KEPT:]
        self.since_doubt += 1
        self.follow = Follow.through(start, pointer, _middle(held), gain)
        # what the game's way of hanging things is learned from: only a drawing seen at rest and the same size on
        # both looks (2026-11-16: one caught among glints, 404 by 384 then 382 by 399, gave a gain of 1.32 for 1.45)
        was = next(one for g, one, other in pairs if other is held)
        steady = not again.get("restless") and not seen.get("restless") and all(
            abs(now / max(then, 1) - 1) <= HANG_SIZE for now, then in ((held[2], was[2]), (held[3], was[3])))
        self._drawn = (pointer, held) if steady else None
        return self.follow

    async def _aim(self, before: bytes, start: tuple[float, float], goal: tuple[float, float],
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
            seen = await self._look(before, start, end, source, near=goal)
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
