"""What a thing on the screen is, told by what it looked like before (PLAN §10, G9).

Generated code is good at saying where things are and poor at telling small drawings apart (a card's rank and
suit, a numeral, one of many pieces). So a reading may leave that to the host: wherever the code cannot tell a
value, `read()` gives `{"look": [left, top, width, height]}` in its place, the box whose pixels tell it. The
host cuts that box out and names it by the nearest of the samples it keeps, each cut from a screen a model
read (the value the model gave at the same place in the state). A cut like no sample is a value nobody read
(null): the screen then goes to the model, and its reading adds the samples; the code is not written again.

A sample is the cut's own pixels. How far a cut may be from a label's samples and still be it
is learned per label from how much the thing differed between captures of one and the same screen: a game that
draws a thing the same every time holds a cut to nearly the same pixels, one that animates it is given what the
animation was seen to need. Sightings on different screens that look unlike each other (a card in the open, the
same card half covered) are kept as different looks of the value and widen nothing (2026-11-14: widened by
them, a covered seven of diamonds was named a seven of spades).
"""
from __future__ import annotations

from io import BytesIO
import json
from typing import Any, Iterator

MARK = "look"
# how far (`distance`) a cut may be from a label's nearest sample: at least NEAR, what the label was seen to
# differ by between captures of one screen times SPREAD when that is more, never beyond FAR; and two labels this close to each
# other for a cut are not told apart. NEAR is tight on purpose: two cards of one suit differ by a small numeral
# (2026-11-14: by a mean over the whole cut a six was named a five), and a thing not named costs one
# question, a thing misnamed a game
NEAR, SPREAD, FAR, APART = 6.0, 1.5, 24.0, 3.0
# a sample keeps its box's pixels as they are up to this many, and is compared in parts this many pixels a side
MOST, PART = 4096, 6
# what is drawn on a sample's ground: pixels further than this (a channel, of 255) from its commonest colour;
# two drawings are held against each other when their boxes are the same size within GLYPH_SLACK pixels
INK, GLYPH_SLACK = 24, 1


def is_mark(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == {MARK} and isinstance(value[MARK], list) and len(value[MARK]) == 4
            and all(type(part) in (int, float) and part == part for part in value[MARK]))


def marks(payload: Any, path: tuple = ()) -> Iterator[tuple[tuple, list[float]]]:
    """Every place in a reading the code left to the host, with its box."""
    if is_mark(payload):
        yield path, payload[MARK]
    elif isinstance(payload, dict):
        for key, value in payload.items():
            yield from marks(value, (*path, key))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            yield from marks(value, (*path, index))


def _at(state: Any, path: tuple) -> Any:
    for step in path:
        state = state[step]              # KeyError, IndexError or TypeError: the reading is not shaped as the state
    return state


def _shaped(raw: Any, state: Any) -> bool:
    """Whether a reading with marks is shaped as a state: the same keys, the same lengths."""
    if is_mark(raw):
        return not isinstance(state, (dict, list))
    if isinstance(raw, dict):
        return isinstance(state, dict) and set(raw) == set(state) and all(_shaped(raw[key], state[key]) for key in raw)
    if isinstance(raw, list):
        return isinstance(state, list) and len(raw) == len(state) and all(_shaped(a, b) for a, b in zip(raw, state))
    return True


def _open(png: bytes) -> Any:
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        return image.convert("RGB")


def cut(image: Any, box: list[float]) -> bytes | None:
    """The pixels of a box as a sample, or None for a box that is not on the picture: its width and height,
    then its pixels as they are. A small drawing is told by a few pixels, so nothing is made smaller unless
    the box is larger than MOST pixels, and then by a whole factor."""
    left, top, width, height = (round(value) for value in box)
    if width < 2 or height < 2 or left < 0 or top < 0 or left + width > image.width or top + height > image.height:
        return None
    region = image.crop((left, top, left + width, top + height))
    factor = 1
    while (width // factor) * (height // factor) > MOST:
        factor += 1
    if factor > 1:
        region = region.resize((max(1, width // factor), max(1, height // factor)))
    return bytes(region.size) + region.tobytes() if max(region.size) < 256 else None


def distance(one: bytes, other: bytes) -> float:
    """How unlike two samples are: the mean difference a channel (of 255) in the part of them that differs
    most (parts of PART by PART pixels). Things often differ in one small place (a numeral beside the same
    suit), which a mean over the whole would lose. Samples of different sizes are not the same thing."""
    if one[:2] != other[:2]:
        return 255.0
    if one == other:
        return 0.0
    from PIL import Image, ImageChops

    size = (one[0], one[1])
    differing = ImageChops.difference(Image.frombytes("RGB", size, one[2:]), Image.frombytes("RGB", size, other[2:])).tobytes()
    # (summed a part at a time from the picture's bytes: a bank of some hundred samples is gone through for every
    # cell like none of them, on every frame, and a sum a pixel at a time took most of a move's time. PLAN §27, S3)
    row, most = size[0] * 3, 0
    for top in range(0, size[1] * row, PART * row):
        band = differing[top:top + PART * row]
        for left in range(0, row, PART * 3):
            right = min(left + PART * 3, row)
            most = max(most, sum(sum(band[at + left:at + right]) for at in range(0, len(band), row)))
    return most / (PART * PART * 3)


def _picture(sample: bytes) -> Any:
    from PIL import Image

    return Image.frombytes("RGB", (sample[0], sample[1]), sample[2:])


def glyph(sample: bytes) -> tuple[tuple[int, int, int], Any]:
    """A sample as its ground (its commonest colour) and what is drawn on it, cut to the drawing's own box
    (None for a plain one): the same wherever in the sample's box the thing was drawn."""
    from PIL import Image, ImageChops

    image = _picture(sample)
    ground = max(image.getcolors(image.width * image.height))[1]
    bands = [band.point(lambda value: 255 if value > INK else 0)
             for band in ImageChops.difference(image, Image.new("RGB", image.size, ground)).split()]
    box = ImageChops.lighter(ImageChops.lighter(bands[0], bands[1]), bands[2]).getbbox()
    return ground, image.crop(box) if box else None


def shifted(one: tuple[tuple[int, int, int], Any], other: tuple[tuple[int, int, int], Any]) -> float:
    """How unlike two samples are once what is drawn on each is laid over the other's (`glyph`): the larger
    of how far their grounds are apart and the most a part of the drawings differs. A grid divided evenly
    over a board with gutters cuts the same tile a few pixels off from cell to cell; this does not care."""
    from PIL import Image, ImageChops

    (ground, drawn), (other_ground, other_drawn) = one, other
    apart = float(max(abs(a - b) for a, b in zip(ground, other_ground)))
    if drawn is None or other_drawn is None:
        return apart if drawn is None and other_drawn is None else 255.0
    if abs(drawn.width - other_drawn.width) > GLYPH_SLACK or abs(drawn.height - other_drawn.height) > GLYPH_SLACK:
        return 255.0
    width, height = min(drawn.width, other_drawn.width), min(drawn.height, other_drawn.height)
    differing = ImageChops.difference(drawn.crop((0, 0, width, height)), other_drawn.crop((0, 0, width, height)))
    parts = differing.resize((max(1, width // PART), max(1, height // PART)), Image.BOX)
    return max(apart, max(sum(pixel) / 3 for pixel in parts.get_flattened_data()))


class Bank:
    """The samples kept for one adapter, by the value each was read as."""

    def __init__(self) -> None:
        self.samples: dict[str, list[bytes]] = {}
        self.exact: dict[bytes, str | None] = {}
        # how much one and the same thing was seen to differ between captures of one screen, by value
        self.jitter: dict[str, float] = {}
        # each kept sample as its ground and drawing (`glyph`), made when first needed
        self._glyphs: dict[bytes, tuple[Any, Any]] = {}

    def __len__(self) -> int:
        return sum(len(cuts) for cuts in self.samples.values())

    def add(self, label: Any, sample: bytes, *others: bytes) -> None:
        """Keep a sample of a value, and with it the same box cut from other captures of the same screen
        (`others`): what differs between those is how the game draws the thing from moment to moment, and
        only that widens what is taken for it. Two sightings of a value elsewhere that look unlike each other
        (a card in the open and one half covered) are two things to know, not a wider one."""
        key = json.dumps(label, sort_keys=True)
        kept = self.samples.setdefault(key, [])
        for cut_ in (sample, *others):
            if cut_ not in kept:
                kept.append(cut_)
                # the same pixels read as two values tell neither
                self.exact[cut_] = key if self.exact.get(cut_, key) == key else None
        moved = max((distance(sample, other) for other in others), default=0.0)
        if moved < 255.0:
            self.jitter[key] = max(self.jitter.get(key, 0.0), moved)

    def _reach(self, key: str) -> float:
        return min(FAR, max(NEAR, self.jitter.get(key, 0.0) * SPREAD))

    def name(self, sample: bytes) -> tuple[bool, Any]:
        """Whether the cut is one of the things kept, and which."""
        if sample in self.exact:
            key = self.exact[sample]
            return (True, json.loads(key)) if key is not None else (False, None)
        near = self._near(lambda kept: distance(sample, kept))
        if not near:
            # like none as it lies in its box: perhaps the same thing drawn a few pixels off
            mine = glyph(sample)
            near = self._near(lambda kept: shifted(mine, self._glyph(kept)))
        if not near or (len(near) > 1 and near[1][0] - near[0][0] < APART):
            return False, None
        return True, json.loads(near[0][1])

    def _glyph(self, kept: bytes) -> tuple[Any, Any]:
        if kept not in self._glyphs:
            self._glyphs[kept] = glyph(kept)
        return self._glyphs[kept]

    def _near(self, far: Any) -> list[tuple[float, str]]:
        """The values whose nearest sample is within reach by `far`, nearest first."""
        found = sorted((min(far(kept) for kept in cuts), key) for key, cuts in self.samples.items())
        return [(least, key) for least, key in found if least <= self._reach(key)]


def learn(raw: Any, state: dict[str, Any], pngs: list[bytes]) -> tuple[list[tuple[Any, list[bytes]]], str | None]:
    """The samples a screen a model read gives for a reading with marks: for each mark the value the state has
    there, and its box cut from each capture of the screen (the screen itself first). Also what is wrong when
    the reading is not shaped as the state (the code counted the things of a place differently), or a box is
    off the screen."""
    if not _shaped(raw, state):
        return [], "read() gave a state shaped differently (other keys, or another number of things in a list)"
    found = []
    images = [_open(png) for png in pngs]
    for path, box in marks(raw):
        cuts = [cut(image, box) for image in images]
        if any(sample is None for sample in cuts):
            return [], f"read() gave a box off the screen at {list(path)}: {box}"
        found.append((_at(state, path), cuts))
    return found, None


def placed(raw: Any, png: bytes) -> list[tuple[tuple, bytes]]:
    """Each mark of a reading with the sample its box gives on a capture (marks off the picture left out)."""
    image = _open(png)
    return [(path, sample) for path, box in marks(raw) if (sample := cut(image, box)) is not None]


def put(state: Any, path: tuple, value: Any) -> Any:
    """A copy of a state with the value at `path` replaced."""
    if not path:
        return value
    if isinstance(state, dict):
        return {**state, path[0]: put(state[path[0]], path[1:], value)}
    return [put(part, path[1:], value) if index == path[0] else part for index, part in enumerate(state)]


def resolve(raw: Any, png: bytes, bank: Bank) -> Any:
    """A reading with every mark replaced by the value its box's pixels are, or null for one like no sample."""
    if not any(True for _ in marks(raw)):
        return raw
    image = _open(png)

    def told(value: Any) -> Any:
        if is_mark(value):
            sample = cut(image, value[MARK])
            return bank.name(sample)[1] if sample is not None else None
        if isinstance(value, dict):
            return {key: told(part) for key, part in value.items()}
        if isinstance(value, list):
            return [told(part) for part in value]
        return value
    return told(raw)
