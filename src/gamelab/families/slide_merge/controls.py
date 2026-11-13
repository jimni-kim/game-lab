"""How a slide is played on a screen: one key, the one the profile registers for its direction."""
from __future__ import annotations

from typing import Any

KIND = "keys"


def check(controls: dict, data: dict) -> None:
    """Nothing beyond the schema: a key for every direction."""


def map_action(action: dict, payload: dict, layout: dict, controls: dict, png: bytes
               ) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    if action["kind"] != "slide":
        raise ValueError("meta_action_unmapped")
    return "browser_press", {"keys": [controls["keys"][action["direction"]]]}, None
