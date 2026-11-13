"""How a placement is played on a screen: a drag of the tray's piece onto the board's cells."""
from __future__ import annotations

from typing import Any

from ... import pixels
from ...pieces import read_tray

# how long a drag may take: what the surfaces' pointer input carries out
DURATION_MIN_MS, DURATION_MAX_MS = 1, 3000

KIND = "drag"


def check(controls: dict, data: dict) -> None:
    """A drag needs every slot's piece geometry to find what it picks up, and a duration the
    surface's pointer input accepts."""
    if any("piece" not in slot for slot in data["tray"]):
        raise ValueError("piece_geometry_required")
    if not DURATION_MIN_MS <= controls["duration_ms"] <= DURATION_MAX_MS:
        raise ValueError("control_duration_unsupported")


def map_action(action: dict, payload: dict, layout: dict, controls: dict, png: bytes
               ) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    if action["kind"] != "place":
        raise ValueError("meta_action_unmapped")
    if action.get("orientation") != 0:
        raise ValueError("rotation_unmapped")
    with pixels.decode(png, layout["viewport"]) as image:
        shape, centres = read_tray(image, layout)[action["slot"]]
    if not shape or shape != payload["tray"][action["slot"]]:
        raise ValueError("piece_changed")
    row, col = action["anchor"]
    board = layout["board"]
    left, top, width, height = board["rect"]
    cell = width / board["cols"], height / board["rows"]
    pickup = controls["pickup"]
    # Source pixels select a solid cell. The held object's anchor may instead snap to
    # its bounding-box centre/bottom and expand to board-cell scale.
    rows, cols = max(r for r, _ in shape) + 1, max(c for _, c in shape) + 1
    first_row, first_col = shape[0]
    anchor = ((first_col + 0.5, first_row + 0.5) if pickup["anchor"] == "grab" else
              (cols * pickup["anchor"][0], rows * pickup["anchor"][1]))
    held_first = [(value + 0.5 - origin) * scale + offset for value, origin, scale, offset in
                  zip((first_col, first_row), anchor, pickup["cell_scale"], pickup["offset_cells"])]
    end = [left + (col + first_col + 0.5 - held_first[0]) * cell[0],
           top + (row + first_row + 0.5 - held_first[1]) * cell[1]]
    if not all(0 <= value < bound for value, bound in zip(end, layout["viewport"])):
        raise ValueError("drag_endpoint_outside_viewport")
    # Where the held piece is drawn depends on the grabbed cell only for a "grab" anchor. Otherwise any
    # cell will do, and the one farthest from the drop is grabbed: a game may take a drag that barely
    # moves (a piece dropped right below its tray slot) for a tap, and ignore it.
    start = centres[0] if pickup["anchor"] == "grab" else max(
        centres, key=lambda centre: (centre[0] - end[0]) ** 2 + (centre[1] - end[1]) ** 2)
    return ("browser_drag", {"start_x": start[0], "start_y": start[1], "end_x": end[0], "end_y": end[1],
                             "duration_ms": controls["duration_ms"]},
            {"slot": action["slot"], "cells": [[row + cell_row, col + cell_col] for cell_row, cell_col in shape]})
