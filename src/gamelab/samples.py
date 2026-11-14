"""What a thing on the screen is, told by what it looked like before (PLAN §10, G9).

Generated code is good at saying where things are and poor at telling small drawings apart (a card's rank and
suit, a numeral, one of many pieces). So a reading may leave that to the host: wherever the code cannot tell a
value, `read()` gives `{"look": [left, top, width, height]}` in its place, the box whose pixels tell it. The
host cuts that box out and names it by the nearest of the samples it keeps, each cut from a screen a model
read (the value the model gave at the same place in the state). A cut like no sample is a value nobody read
(null): the screen then goes to the model, and its reading adds the samples; the code is not written again.

A sample is the cut made small (`SIDE` a side). How far a cut may be from a label's samples and still be it
is learned per label from how much its own samples differ (other captures of the same screen, the same thing
seen again): a game that draws a thing the same every time holds a cut to nearly the same pixels, one that
animates it is given what the animation was seen to need.
"""
from __future__ import annotations

from io import BytesIO
import json
from typing import Any, Iterator

MARK = "look"
SIDE = 16
# how far (mean difference a channel, of 255) a cut may be from a label's nearest sample: at least NEAR, the
# label's own spread times SPREAD when that is more, never beyond FAR; and two labels this close to each
# other for a cut are not told apart
NEAR, SPREAD, FAR, APART = 2.0, 1.5, 14.0, 1.0


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
    """The pixels of a box made small, or None for a box that is not on the picture."""
    left, top, width, height = box
    if width < 2 or height < 2 or left < 0 or top < 0 or left + width > image.width or top + height > image.height:
        return None
    return image.crop((round(left), round(top), round(left + width), round(top + height))).resize((SIDE, SIDE)).tobytes()


def distance(one: bytes, other: bytes) -> float:
    return sum(abs(a - b) for a, b in zip(one, other)) / len(one)


class Bank:
    """The samples kept for one adapter, by the value each was read as."""

    def __init__(self) -> None:
        self.samples: dict[str, list[bytes]] = {}
        self.exact: dict[bytes, str | None] = {}

    def __len__(self) -> int:
        return sum(len(cuts) for cuts in self.samples.values())

    def add(self, label: Any, sample: bytes) -> None:
        key = json.dumps(label, sort_keys=True)
        if sample not in self.samples.setdefault(key, []):
            self.samples[key].append(sample)
            # the same pixels read as two values tell neither
            self.exact[sample] = key if self.exact.get(sample, key) == key else None

    def _reach(self, key: str) -> float:
        cuts = self.samples[key]
        spread = max((distance(a, b) for index, a in enumerate(cuts) for b in cuts[index + 1:]), default=0.0)
        return min(FAR, max(NEAR, spread * SPREAD))

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


def learn(raw: Any, state: dict[str, Any], pngs: list[bytes]) -> tuple[list[tuple[Any, bytes]], str | None]:
    """The samples a screen a model read gives for a reading with marks: each marked box cut from each capture
    of the screen, with the value the state has there. Also what is wrong when the reading is not shaped as
    the state (the code counted the things of a place differently), or a box is off the screen."""
    if not _shaped(raw, state):
        return [], "read() gave a state shaped differently (other keys, or another number of things in a list)"
    found = []
    images = [_open(png) for png in pngs]
    for path, box in marks(raw):
        label = _at(state, path)
        for image in images:
            sample = cut(image, box)
            if sample is None:
                return [], f"read() gave a box off the screen at {list(path)}: {box}"
            found.append((label, sample))
    return found, None


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
