"""Reading a sokoban board by code written once for the family.

A cell is floor, wall (or the ground outside the walls), a goal, a box, a box on a goal, or the player; which is
told by what the cell looks like (`grid.Valued`), learned from the first screen a model read. The figure hides the
mark of a goal it stands on, so it is one look wherever it stands, and whether its cell is a goal is told by the
state before (`rules.sokoban.beneath`), not by the picture (2026-11-23: a level was let go when the player stepped
onto a goal and the board read had a goal fewer than boxes).
A move leaves one board, so the rules name what a cell shows after it; a look not seen before that they leave open
is put to a light model, the cell cut out alone, once for each look.
"""
from __future__ import annotations

from typing import Any

from ..rules.sokoban import BOX, BOX_ON_GOAL, FLOOR, GOAL, PLAYER, PLAYER_ON_GOAL, WALL, beneath
from .grid import Valued


class SokobanKit(Valued):
    family_id = "sokoban"

    def __init__(self, rules: dict[str, Any]) -> None:
        super().__init__(rules["rows"], rules["cols"])

    def options(self) -> dict[str, str]:
        return {str(FLOOR): "plain floor someone can walk on: nothing on it, no mark",
                str(WALL): "a wall (bricks, a raised block) or the plain ground outside the walls where nobody walks",
                str(GOAL): "floor with a goal mark on it (a dot, a cross, a ring, a lit tile) and nothing standing on it",
                str(BOX): "a box or crate on plain floor",
                str(BOX_ON_GOAL): "a box or crate on a goal: often another colour, lit up or marked",
                str(PLAYER): "the player's figure, whatever it stands on"}

    @staticmethod
    def _key(value: Any) -> Any:
        return PLAYER if value == PLAYER_ON_GOAL else value

    def read(self, png: bytes, prior: dict[str, Any] | None = None, unmoved: dict[str, Any] | None = None
             ) -> tuple[dict[str, Any] | None, str | None]:
        state, why = super().read(png, prior, unmoved)
        return (beneath(state, prior if prior is not None else unmoved) if state is not None else None), why
