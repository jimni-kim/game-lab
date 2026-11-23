"""Readers written once for a family (PLAN §19): a game of a family that has one is read by it, with no code
written for the game. A family with none is read by code a model writes (`adapters.generated`)."""
from __future__ import annotations

from typing import Any

from . import grid
from .block_placement import BlockKit
from .match3 import Match3Kit
from .minesweeper import MinesKit
from .slide_merge import SlideKit
from .sokoban import SokobanKit

KITS: dict[str, Any] = {kit.family_id: kit for kit in (BlockKit, SlideKit, MinesKit, Match3Kit, SokobanKit)}


def has(family_id: str | None) -> bool:
    return family_id in KITS


def count(family_id: str, png: bytes, box: list[int]) -> tuple[int, int] | None:
    """The rows and columns of the board in `box`, counted from the picture alone by the family's reader; None
    when it cannot tell."""
    return grid.pitch(png, box) if family_id in KITS else None


def survey(family_id: str, rules: dict[str, Any], png: bytes, box: list[int], state: dict[str, Any]
           ) -> tuple[list[int], int, int] | None:
    """The board's box, rows and columns as a reader taught this one reading of the screen lays them out by the
    looks it learned (`Valued.survey`): a reading of a board with no lines between its cells that took it for
    another size than it is shows so. None for a family whose reader does not count so, or when it cannot tell."""
    kit = KITS.get(family_id)
    grid_ = state.get(getattr(kit, "field", "grid")) if isinstance(state, dict) else None
    if kit is None or not getattr(kit, "surveys", False) or not isinstance(grid_, list) or not grid_ \
            or not isinstance(grid_[0], list):
        return None
    scratch = kit({**rules, "rows": len(grid_), "cols": len(grid_[0])})
    try:
        if not scratch.calibrate(png, box, state):
            return None
        return scratch.survey(png, scratch.board)
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def make(family_id: str, rules: dict[str, Any]) -> Any:
    return KITS[family_id](rules)
