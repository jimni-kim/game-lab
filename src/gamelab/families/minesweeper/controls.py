"""How a cell is opened on a screen: a tap at its middle."""
from __future__ import annotations

from typing import Any

from ...execution import tap

KIND = "tap"


def check(controls: dict, data: dict) -> None:
    """Nothing beyond the schema: a tap needs no registered value."""


def map_action(action: dict, payload: dict, layout: dict, controls: dict, png: bytes
               ) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    if action["kind"] != "open":
        raise ValueError("meta_action_unmapped")
    row, col = action["cell"]
    left, top, width, height = layout["board"]["rect"]
    across, down = width / layout["board"]["cols"], height / layout["board"]["rows"]
    pressed = tap([left + col * across, top + row * down, across, down])
    return pressed.tool, pressed.arguments, {"cell": [row, col]}
