"""A slide game's profile from one look with no registered profile (plan §4.5): its size from the board's
cells, and its colours from the values a model read on them, kept only when the pixels agree."""
from __future__ import annotations

from typing import Any

from ... import pixels
from .reader import tile

# the most colours one tile value may show across its sample points (a profile's alternates and its colour)
MAX_FACES = 17


def rules(rows: int, cols: int, ranges: dict[str, dict[str, int]]) -> dict[str, int] | None:
    """The rule values a board of `rows` by `cols` cells sets, or None: a grid that is not square or a size
    the code does not play."""
    size = ranges["size"]
    if rows != cols or not size["minimum"] <= rows <= size["maximum"]:
        return None
    return {"size": rows}


def palette(image: Any, spec: dict, cells: list[list[Any]], tolerance: int) -> list[dict] | str:
    """The board's palette from every cell's value, or why there is none: "board_full" when no cell is
    empty (its colour cannot be learned, and the game is about over), "cells_unconfirmed" for a cell not
    read or not a tile, a cell that is not one face, or two cells said to hold one value that show
    different colours (one of them misread)."""
    if not any(value == 0 for values in cells for value in values):
        return "board_full"
    shown: dict[int, list[tuple[int, ...]]] = {}
    for row, values in enumerate(cells):
        for col, value in enumerate(values):
            if type(value) is not int or not tile(value):
                return "cells_unconfirmed"
            found = pixels.faces(image, spec, row, col, tolerance)
            if found is None:
                return "cells_unconfirmed"
            if value not in shown:
                shown[value] = found
            elif not (_covers(shown[value], found) and _covers(found, shown[value])):
                return "cells_unconfirmed"
    if any(len(found) > MAX_FACES for found in shown.values()):
        return "cells_unconfirmed"
    # each value told from every other by its faces, as the board reads them (`pixels.face_value`)
    values = sorted(shown)
    if any(pixels.covers(shown[one], shown[other]) or pixels.covers(shown[other], shown[one])
           for index, one in enumerate(values) for other in values[index + 1:]):
        return "cells_unconfirmed"
    return [{"value": value, "rgb": list(found[0]),
             **({"alternates": [list(colour) for colour in found[1:]]} if found[1:] else {})}
            for value, found in sorted(shown.items())]


def _covers(known: list[tuple[int, ...]], found: list[tuple[int, ...]]) -> bool:
    """Whether every colour found lies within a clear margin of one already known: the same tile drawn again."""
    return all(min(max(abs(a - b) for a, b in zip(colour, other)) for other in known) < pixels.DECISION_MARGIN
               for colour in found)
