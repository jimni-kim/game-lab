"""What generated adapter code may use to read a picture: a small library, not a schema.

    from gamelab.sandbox.helpers import Picture, distance, nearest, runs

A `Picture` is handed to the adapter's functions; boxes are `[left, top, width, height]` in its pixels.
"""
from __future__ import annotations

from collections import Counter

Colour = tuple[int, int, int]


def distance(a: Colour, b: Colour) -> int:
    """How far apart two colours are: the largest difference in any channel."""
    return max(abs(x - y) for x, y in zip(a, b))


def nearest(colour: Colour, table: dict) -> tuple[object, int]:
    """The key of `table` ({key: colour}) whose colour is nearest, and how far it is."""
    key = min(table, key=lambda name: distance(colour, tuple(table[name])))
    return key, distance(colour, tuple(table[key]))


def runs(values: list[bool]) -> list[tuple[int, int]]:
    """The stretches of True in `values` as (start, length)."""
    found, start = [], None
    for index, value in enumerate([*values, False]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            found.append((start, index - start))
            start = None
    return found


class Picture:
    def __init__(self, image) -> None:
        self._image = image
        self._pixels = image.load()
        self.width, self.height = image.size

    @classmethod
    def open(cls, path: str) -> Picture:
        from PIL import Image

        with Image.open(path) as image:
            return cls(image.convert("RGB"))

    def pixel(self, x: int, y: int) -> Colour:
        return self._pixels[int(x), int(y)]

    def crop(self, box: list[int]) -> Picture:
        left, top, width, height = (int(value) for value in box)
        return Picture(self._image.crop((left, top, left + width, top + height)))

    def mean(self, box: list[int]) -> Colour:
        """The average colour of a box."""
        from PIL import ImageStat

        left, top, width, height = (int(value) for value in box)
        return tuple(round(value) for value in ImageStat.Stat(self._image.crop((left, top, left + width, top + height))).mean)

    def common(self, box: list[int], count: int = 1) -> list[tuple[Colour, float]]:
        """The `count` most frequent colours of a box, each with the share of its pixels."""
        left, top, width, height = (int(value) for value in box)
        colours = Counter(self._image.crop((left, top, left + width, top + height)).getdata())
        total = max(1, width * height)
        return [(colour, round(seen / total, 4)) for colour, seen in colours.most_common(count)]

    def row(self, y: int, left: int = 0, right: int | None = None) -> list[Colour]:
        return [self._pixels[x, int(y)] for x in range(int(left), int(self.width if right is None else right))]

    def column(self, x: int, top: int = 0, bottom: int | None = None) -> list[Colour]:
        return [self._pixels[int(x), y] for y in range(int(top), int(self.height if bottom is None else bottom))]

    def box_of(self, colour: Colour, tolerance: int = 8) -> list[int] | None:
        """The box around every pixel within `tolerance` of `colour`, or None when there is none."""
        from PIL import ImageChops

        mask = None
        for band, centre in zip(self._image.split(), colour):
            near = band.point(lambda value, centre=centre: 255 if abs(value - centre) <= tolerance else 0)
            mask = near if mask is None else ImageChops.multiply(mask, near)
        found = mask.getbbox()
        return None if found is None else [found[0], found[1], found[2] - found[0], found[3] - found[1]]
