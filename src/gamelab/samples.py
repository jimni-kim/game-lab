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
    width, height = one[0], one[1]
    across = -(-width // PART)
    sums = [0] * (across * -(-height // PART))
    for index in range(width * height):
        at = 2 + index * 3
        sums[(index // width) // PART * across + (index % width) // PART] += (
            abs(one[at] - other[at]) + abs(one[at + 1] - other[at + 1]) + abs(one[at + 2] - other[at + 2]))
    return max(sums) / (PART * PART * 3)


class Bank:
    """The samples kept for one adapter, by the value each was read as."""

    def __init__(self) -> None:
        self.samples: dict[str, list[bytes]] = {}
        self.exact: dict[bytes, str | None] = {}
        # how much one and the same thing was seen to differ between captures of one screen, by value
        self.jitter: dict[str, float] = {}

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
        near = sorted((min(distance(sample, kept) for kept in cuts), key) for key, cuts in self.samples.items())
        near = [(far, key) for far, key in near if far <= self._reach(key)]
        if not near or (len(near) > 1 and near[1][0] - near[0][0] < APART):
            return False, None
        return True, json.loads(near[0][1])


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
