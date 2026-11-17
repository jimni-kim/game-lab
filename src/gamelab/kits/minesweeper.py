"""Reading a minesweeper board by code written once for the family (PLAN §20, X3).

A cell is covered, opened with a count of the mines around it, flagged, or a mine; which is told by what the
cell looks like (`grid.Valued`), learned from the first screen a model read (a new game: every cell covered).
The rules cannot say what a cell opens to, so a look not seen before (the first 1, the first 2, an opened cell
with nothing on it) is put to a light model, the cell cut out alone, once for each look; its answer is kept
only when the board then is one the rules allow.
"""
from __future__ import annotations

from typing import Any

from ..rules.minesweeper import COVERED, EXPLODED, FLAG, MINE
from .grid import Valued


class MinesKit(Valued):
    family_id = "minesweeper"
    # all of a cell: on many boards a covered cell and an opened one differ only at their edges (a raised rim)
    inset = 0.04

    def __init__(self, rules: dict[str, Any]) -> None:
        super().__init__(rules["rows"], rules["cols"])

    def options(self) -> dict[str, str]:
        return {"0": "an opened cell with nothing on it: flat, no digit (the cells around it may be raised or digits)",
                **{str(count): f"an opened cell showing the digit {count}" for count in range(1, 9)},
                str(COVERED): "a covered cell nobody opened yet: a raised or darker button with nothing on it",
                str(FLAG): "a covered cell marked with a flag", str(MINE): "a mine shown on the cell",
                str(EXPLODED): "the mine that was opened: a mine on a red or otherwise marked ground"}
