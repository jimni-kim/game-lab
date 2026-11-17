"""Reading a match-three board by code written once for the family (PLAN §20, X4).

The rules ask only which cells hold the same kind of piece, so a piece's value is the number of its kind and
no name is needed: on the first screen a model read, the cells it called pieces are gathered by what they look
like, and each kind takes the number the model gave most of its cells (so that what the model read and what is
read here go together), or a free one. A model that calls two kinds one colour, or one kind two, is thereby set
right by the screen itself. A cell that looks like nothing learned (a special piece, an obstacle, a piece still
falling) is read as a cell no swap may use; nobody is asked what it is. Where the board has no cell (a board that
is no rectangle) is where the first reading said so.
"""
from __future__ import annotations

from typing import Any

from ..rules.match3 import BLOCK, COLOURS, EMPTY
from .grid import Looks, Valued, fit, groups, open_picture

# a reading agrees with a model's when this share of the cells both call pieces are gathered alike by both
ALIKE = 0.9


class Match3Kit(Valued):
    family_id = "match3"
    # a piece's middle: what shows between pieces (the cell behind them, a neighbour's glow) is left out
    inset, side = 0.3, 8
    # pieces of one kind are drawn a little differently from cell to cell (a gloss, a board fitted by the gaps
    # between pieces and so a few pixels out), and kinds differ by their whole colour: looser than a numeral needs
    # (2026-11-17, a game with no lines between its cells: at 6 its four kinds of candy came out as twelve)
    near, apart = 10.0, 5.0
    # what comes in from above is the game's: a screen is taken when it has come to rest, not as soon as it shows
    # what the rules foresee
    foresees = False

    def __init__(self, rules: dict[str, Any]) -> None:
        super().__init__(rules["rows"], rules["cols"])

    def calibrate(self, png: bytes, board: list[int], state: dict[str, Any], sources: Any = None) -> bool:
        grid = state.get(self.field)
        if not self._shaped(grid):
            return False
        image = open_picture(png)
        self._place(image, fit(image, board, self.rows, self.cols))
        self.looks = Looks(self.near, self.apart)
        cells = [(grid[r][c], self._look(image, box)) for r, c, box in self._cells() if grid[r][c] is not None]
        for value, seen in cells:
            if value in (EMPTY, BLOCK):
                self.looks.add(value, seen)
        pieces = [(value, seen) for value, seen in cells if value not in (EMPTY, BLOCK)]
        kinds = groups([seen for _, seen in pieces], self.near)
        free = [number for number in range(1, len(COLOURS) + 1)]
        for kind in kinds:
            said = [pieces[index][0] for index in kind]
            number = max(set(said), key=said.count)
            if number not in free:
                if not free:
                    break                    # more kinds than the rules have numbers for: the rest are no pieces
                number = free[0]
            free.remove(number)
            for index in kind:
                self.looks.add(number, pieces[index][1])
        return len(self.looks) > 0

    def read(self, png: bytes, prior: dict[str, Any] | None = None, unmoved: dict[str, Any] | None = None
             ) -> tuple[dict[str, Any] | None, str | None]:
        state, why = super().read(png, prior, unmoved)
        if state is not None:
            state[self.field] = [[BLOCK if cell is None else cell for cell in row] for row in state[self.field]]
        return state, why

    def same(self, state: dict[str, Any], said: dict[str, Any]) -> bool:
        """Whether a screen read here is read as a model read it: the same cells pieces, and gathered into kinds
        alike (the numbers themselves are the model's guess at a colour's name, and its to get wrong)."""
        mine, theirs = state.get(self.field), said.get(self.field)
        if not self._shaped(theirs):
            return False
        pairs = [(one, other) for row, told in zip(mine, theirs) for one, other in zip(row, told)
                 if one not in (EMPTY, BLOCK) and other not in (EMPTY, BLOCK, None)]
        if not pairs:
            return False
        named: dict[Any, list[Any]] = {}
        for one, other in pairs:
            named.setdefault(one, []).append(other)
        alike = sum(max(others.count(other) for other in set(others)) for others in named.values())
        return alike >= ALIKE * len(pairs) and len(pairs) >= ALIKE * sum(
            other not in (EMPTY, BLOCK, None) for told in theirs for other in told)
