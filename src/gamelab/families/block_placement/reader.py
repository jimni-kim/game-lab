"""Read occupancy and tray shapes without confusing an unread tray with a consumed slot."""
from __future__ import annotations

from typing import Any

from ...pixels import grid
from ...pieces import read_tray

# the state's key for the board's cells
BOARD = "board"


def tile(value: int) -> bool:
    """A value a cell may hold: empty or filled."""
    return value in (0, 1)


def check_profile(data: dict, rules: dict) -> None:
    """A board and tray of the rules' sizes, every grid read as empty or filled."""
    board = data["board"]
    if (board["rows"], board["cols"], len(data["tray"])) != (rules["rows"], rules["cols"], rules["tray_size"]):
        raise ValueError("rule_geometry_mismatch")
    if any({entry["value"] for entry in grid["palette"]} != {0, 1} for grid in [board, *data["tray"]]):
        raise ValueError("invalid_block_palette")


def check_correction(payload: dict, revised: dict, data: dict) -> bool:
    """A corrected tray fills only unread slots, each with a piece that fits its slot, and keeps what was
    read (a used slot stays used)."""
    tray = revised["tray"]
    if not isinstance(tray, list) or len(tray) != len(payload["tray"]):
        return False
    for index, (before, after) in enumerate(zip(payload["tray"], tray)):
        if after is not None and not _piece(after, data["tray"][index]):
            return False
        if before != []:
            if after != before:
                return False
        elif not _piece(after, data["tray"][index]):
            return False
    return True


def _piece(piece: Any, slot: dict) -> bool:
    if piece == []:
        return True
    if not isinstance(piece, list) or not piece or len(piece) > slot["rows"] * slot["cols"]:
        return False
    if any(not isinstance(cell, list) or len(cell) != 2 or any(type(value) is not int for value in cell)
           or not 0 <= cell[0] < slot["rows"] or not 0 <= cell[1] < slot["cols"] for cell in piece):
        return False
    return (piece == [list(cell) for cell in sorted(set(map(tuple, piece)))]
            and min(cell[0] for cell in piece) == 0 and min(cell[1] for cell in piece) == 0)


def read(image: Any, layout: dict) -> dict:
    """What the pixels show, as a playing screen: an end banner over the tray leaves the tray unread."""
    board = grid(image, layout["board"], layout["tolerance"])
    if "tray_band" in layout:
        return {"board": board, "tray": [piece for piece, _ in read_tray(image, layout)], "score": None, "scene": "playing"}
    tray = []
    for slot in layout["tray"]:
        cells = grid(image, slot, layout["tolerance"])
        if any(value is None for row in cells for value in row):
            tray.append([])
            continue
        occupied = [[row, col] for row, values in enumerate(cells) for col, value in enumerate(values) if value == 1]
        if not occupied:
            tray.append(None)
            continue
        top, left = min(row for row, col in occupied), min(col for row, col in occupied)
        tray.append([[row - top, col - left] for row, col in occupied])
    return {"board": board, "tray": tray, "score": None, "scene": "playing"}
