"""Readers written once for a family (PLAN §19): a game of a family that has one is read by it, with no code
written for the game. A family with none is read by code a model writes (`adapters.generated`)."""
from __future__ import annotations

from typing import Any

from .block_placement import BlockKit

KITS: dict[str, Any] = {BlockKit.family_id: BlockKit}


def has(family_id: str | None) -> bool:
    return family_id in KITS


def make(family_id: str, rules: dict[str, Any]) -> Any:
    return KITS[family_id](rules)
