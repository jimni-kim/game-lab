"""Reading a sliding-tiles game (2048 and its kin) by code written once for the family (PLAN §20, X2).

A cell holds a tile's number or nothing; which is told by what the cell looks like (`grid.Valued`), learned
from the first screen a model read. Nothing here knows a game, a theme or a colour: a game whose tiles are
pictures is read the same way. The rules say what a move leaves but for the one tile the game adds, so a tile
of a value not seen before is named by them (two tiles joined), and a new tile unlike every value it may not be
is the one it may (`Valued.excludes`): a model is asked only when they leave it open.
"""
from __future__ import annotations

from typing import Any

from .grid import Valued

# the tiles a light model is offered for a cell it is shown
TILES = [2 ** power for power in range(1, 18)]


class SlideKit(Valued):
    family_id = "slide_merge"
    # a tile's face and numeral, without the gutter around it (on 302 kept screens of six games, 2026-11-17: no
    # cell misread, and fewest looks to learn, at this share cut off each side)
    inset = 0.1

    def __init__(self, rules: dict[str, Any]) -> None:
        super().__init__(rules["size"], rules["size"])

    def options(self) -> dict[str, str]:
        return {"0": "an empty cell: no tile, no number", **{str(tile): f"a tile showing the number {tile}" for tile in TILES}}
