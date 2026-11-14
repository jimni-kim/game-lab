"""An input as an adapter maps a move to it. A mapping is a proposal, never input authority."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# how long a tap holds the pointer down: within the authority's tap limit (300 ms), past which it is a long press
TAP_MS = 60


@dataclass(frozen=True)
class MappedAction:
    tool: str
    arguments: dict[str, Any]
    geometry_ref: str
    preview: dict[str, Any] | None = None
