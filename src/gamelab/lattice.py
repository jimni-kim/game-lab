"""A board of square cells on a picture, told to a model as text with no family behind it (PLAN §36, session E2).

A family's reader (`kits.grid.Valued`) names each cell by a value its rules know. A game of no family has no such
values, but its board can still be read by code as far as code goes: the model that plays it says once where the
grid is and how many rows and columns it has (`said`), the box is fitted to the picture's own lines, and from then
on every cell is gathered with the cells it looks like. Each kind of look gets a letter, the same from screen to
screen, and the model, which has the picture beside it, names each letter once (`name`). What the model is then
given of a screen is its rows as letters: exact where a picture's reading is not, and a few hundred characters.

Nothing here knows what a letter means, so nothing checks a reading by rules. What the code can say is whether
the board still stands (`read`: most cells look like kinds seen before) and whether it was a board of few kinds at
all (`KINDS_MOST`): a grid said a cell too wide, or laid over a picture, shows as many kinds as it has cells, and is
let go.
"""
from __future__ import annotations

from typing import Any

from .kits import grid

# the letters kinds are told by, in the order they were first seen
LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz"
# a board has this many cells a side, each at least this many pixels
SIDE_FEWEST, SIDE_MOST, CELL_LEAST = 2, 30, 12
# how a cell is looked at (as a family's reader of values does), and how near two looks are one kind
INSET, SIDE, NEAR = 0.04, 12, grid.MERGE
# a board shows at most this many kinds of cell, and no more than this share of its cells are each a kind alone
# (a board of 16 cells or more): more is no board of few kinds, or a grid laid wrongly over one
KINDS_MOST, ALONE_SHARE = 24, 0.5
# a screen this share of whose cells look like kinds seen before still shows the board
STANDS_SHARE = 0.6
# a name the model gives a kind is at most this long
NAME_MOST = 24
# the cells a move changed that are told one by one, at most
CHANGES_TOLD = 12


def _whole(value: Any, least: int, most: int) -> bool:
    return type(value) is int and least <= value <= most


class Lattice:
    """One board: where it is on the screen, its kinds of cell and the names given them."""

    def __init__(self, size: list[int], box: list[int], rows: int, cols: int) -> None:
        self.size, self.box, self.rows, self.cols = size, box, rows, cols
        # one look of each kind, the first seen of it, by its letter
        self.kinds: dict[str, bytes] = {}
        self.names: dict[str, str] = {}
        # screens read, and screens on which the board did not stand
        self.reads = self.missed = 0

    @classmethod
    def said(cls, png: bytes, told: Any, scale: float = 1.0) -> Lattice | None:
        """The board a model described on this screen: `{"box": [left, top, width, height], "rows": n, "cols": n}`
        in the pixels of the picture it was shown (`scale` times the screen's). The box is fitted to the lines the
        picture has between cells. None when what was said is no board on this screen, or reads as none."""
        if not isinstance(told, dict) or not _whole(told.get("rows"), SIDE_FEWEST, SIDE_MOST) \
                or not _whole(told.get("cols"), SIDE_FEWEST, SIDE_MOST):
            return None
        box = told.get("box")
        if not (isinstance(box, list) and len(box) == 4 and all(type(part) in (int, float) for part in box)):
            return None
        image = grid.open_picture(png)
        left, top, width, height = (part / scale for part in box)
        rows, cols = told["rows"], told["cols"]
        if left < -2 or top < -2 or left + width > image.width + 2 or top + height > image.height + 2 \
                or width < cols * CELL_LEAST or height < rows * CELL_LEAST:
            return None
        fitted = grid.fit(image, [round(left), round(top), round(width), round(height)], rows, cols)
        found = cls(list(image.size), fitted, rows, cols)
        return found if found.read(png) is not None else None

    def _looks(self, png: bytes) -> list[list[bytes]] | None:
        image = grid.open_picture(png)
        if list(image.size) != self.size:
            return None
        looks = [[b""] * self.cols for _ in range(self.rows)]
        for r, c, box in grid.cells(self.box, self.rows, self.cols):
            looks[r][c] = grid.look(image, box, INSET, SIDE)
        return looks

    def _kind(self, seen: bytes) -> str | None:
        near = min(self.kinds, key=lambda letter: grid.distance(seen, self.kinds[letter]), default=None)
        return near if near is not None and grid.distance(seen, self.kinds[near]) <= NEAR else None

    def read(self, png: bytes) -> list[str] | None:
        """The screen's rows as letters, one for each cell. A look not seen before is a new kind when the board
        stands (most of its cells are kinds seen before, or it is the first screen read). None when the picture is
        another size, the board does not stand, or it has more kinds than a board of few kinds has."""
        looks = self._looks(png)
        if looks is None:
            return None
        self.reads += 1
        named = [[self._kind(seen) for seen in row] for row in looks]
        known = sum(letter is not None for row in named for letter in row)
        cells = self.rows * self.cols
        if self.kinds and known < STANDS_SHARE * cells:
            self.missed += 1
            return None
        new: dict[str, bytes] = {}
        for r, row in enumerate(looks):
            for c, seen in enumerate(row):
                if named[r][c] is not None:
                    continue
                near = min(new, key=lambda letter: grid.distance(seen, new[letter]), default=None)
                if near is None or grid.distance(seen, new[near]) > NEAR:
                    if len(self.kinds) + len(new) >= min(KINDS_MOST, len(LETTERS)):
                        self.missed += 1
                        return None
                    near = LETTERS[len(self.kinds) + len(new)]
                    new[near] = seen
                named[r][c] = near
        if not self.kinds and cells >= 16:
            counts: dict[str, int] = {}
            for row in named:
                for letter in row:
                    counts[letter] = counts.get(letter, 0) + 1
            if sum(count == 1 for count in counts.values()) > ALONE_SHARE * cells:
                return None
        self.kinds.update(new)
        return ["".join(row) for row in named]

    def stands(self, png: bytes) -> bool:
        """Whether every cell of the screen is a kind seen before: the board as it was told, with nothing over it."""
        looks = self._looks(png)
        return looks is not None and bool(self.kinds) and all(self._kind(seen) is not None for row in looks for seen in row)

    def name(self, said: Any) -> None:
        """Names a model gave kinds by their letters are kept, short."""
        if not isinstance(said, dict):
            return
        for letter, name in said.items():
            if letter in self.kinds and isinstance(name, str) and name.strip():
                self.names[letter] = name.strip()[:NAME_MOST]

    def unnamed(self, rows: list[str]) -> dict[str, list[int]]:
        """The kinds this screen shows that have no name yet, each with one cell [row, column] that shows it."""
        found: dict[str, list[int]] = {}
        for r, row in enumerate(rows):
            for c, letter in enumerate(row):
                if letter not in self.names and letter not in found:
                    found[letter] = [r, c]
        return found

    def at(self, cell: Any, scale: float = 1.0) -> list[float] | None:
        """The middle of a cell [row, column] in the pixels of a picture `scale` times the screen's; None for a
        cell the board does not have."""
        if not (isinstance(cell, list) and len(cell) == 2 and _whole(cell[0], 0, self.rows - 1)
                and _whole(cell[1], 0, self.cols - 1)):
            return None
        left, top, width, height = self.box
        return [round((left + (cell[1] + 0.5) * width / self.cols) * scale, 1),
                round((top + (cell[0] + 0.5) * height / self.rows) * scale, 1)]

    def told(self, rows: list[str], scale: float = 1.0) -> dict[str, Any]:
        """What a model is given of a screen read: its rows, the names it gave, the kinds still to name, and
        where the board is in the picture it is shown."""
        shown = sorted({letter for row in rows for letter in row})
        unnamed = self.unnamed(rows)
        return {"rows": rows, "names": {letter: self.names[letter] for letter in shown if letter in self.names},
                **({"unnamed": unnamed} if unnamed else {}),
                "box": [round(part * scale) for part in self.box]}

    def changed(self, before: bytes, after: bytes) -> dict[str, Any] | None:
        """What changed between two screens by cells: how many, and the first of them as [row, column, was, is].
        None when either does not show the board."""
        one, other = self._letters(before), self._letters(after)
        if one is None or other is None:
            return None
        cells = [[r, c, one[r][c], other[r][c]] for r in range(self.rows) for c in range(self.cols) if one[r][c] != other[r][c]]
        return {"count": len(cells), "cells": cells[:CHANGES_TOLD]}

    def _letters(self, png: bytes) -> list[list[str]] | None:
        """A screen's cells by the kinds known, with no kind learned from it; None when a cell is none of them."""
        looks = self._looks(png)
        if looks is None:
            return None
        named = [[self._kind(seen) for seen in row] for row in looks]
        return named if all(letter is not None for row in named for letter in row) else None

    def kept(self) -> dict[str, Any]:
        """The board as it is kept for the next work on the game: where it is, its kinds and their names."""
        return {"size": self.size, "box": self.box, "rows": self.rows, "cols": self.cols,
                "kinds": {letter: seen.hex() for letter, seen in self.kinds.items()}, "names": dict(self.names)}

    @classmethod
    def restore(cls, kept: Any) -> Lattice | None:
        """A board kept before, or None when what was kept is none. Whether it still stands is the next screen's
        to say (`read`)."""
        try:
            found = cls([int(part) for part in kept["size"]], [int(part) for part in kept["box"]], kept["rows"], kept["cols"])
            found.kinds = {letter: bytes.fromhex(seen) for letter, seen in kept["kinds"].items()}
            found.names = {letter: str(name)[:NAME_MOST] for letter, name in kept["names"].items() if letter in found.kinds}
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        whole = len(found.size) == 2 and len(found.box) == 4 and _whole(found.rows, SIDE_FEWEST, SIDE_MOST) \
            and _whole(found.cols, SIDE_FEWEST, SIDE_MOST) and found.kinds \
            and all(letter in LETTERS and len(seen) == 3 * SIDE * SIDE for letter, seen in found.kinds.items())
        return found if whole else None

    def summary(self) -> dict[str, Any]:
        return {"rows": self.rows, "cols": self.cols, "box": self.box, "kinds": len(self.kinds),
                "named": len(self.names), "reads": self.reads, "missed": self.missed}
