"""Readers written once for a family (PLAN §19): a game of a family that has one is read by it, with no code
written for the game. A family with none is read by code a model writes (`adapters.generated`)."""
from __future__ import annotations

from typing import Any

from . import grid
from .block_placement import BlockKit
from .minesweeper import MinesKit
from .slide_merge import SlideKit

KITS: dict[str, Any] = {kit.family_id: kit for kit in (BlockKit, SlideKit, MinesKit)}


def has(family_id: str | None) -> bool:
    return family_id in KITS


def count(family_id: str, png: bytes, box: list[int]) -> tuple[int, int] | None:
    """The rows and columns of the board in `box`, counted from the picture alone by the family's reader; None
    when it cannot tell."""
    return grid.pitch(png, box) if family_id in KITS else None


def make(family_id: str, rules: dict[str, Any]) -> Any:
    return KITS[family_id](rules)
