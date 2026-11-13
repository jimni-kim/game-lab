"""Read tile colours, or the page's tile elements; an unrecognised or inconsistent tile remains unknown."""
from __future__ import annotations

import re
from typing import Any

from ...pixels import grid

# the largest tile a profile may report (the core's own bound for a corrected cell)
MAX_TILE = 65536
# the state's key for the board's cells
BOARD = "grid"


def tile(value: int) -> bool:
    """A value a cell may hold: empty, or a power of two the game can show."""
    return not value or (2 <= value <= MAX_TILE and not value & (value - 1))


def check_profile(data: dict, rules: dict) -> None:
    """A square grid of the rules' size, no tray, and a registered colour for every tile: a tile's value is
    its colour."""
    board = data["board"]
    if data["tray"] or (board["rows"], board["cols"]) != (rules["size"], rules["size"]):
        raise ValueError("rule_geometry_mismatch")
    if any(entry.get("any") for entry in board["palette"]) or not all(tile(entry["value"]) for entry in board["palette"]):
        raise ValueError("invalid_tile")
    if 0 not in {entry["value"] for entry in board["palette"]}:
        raise ValueError("missing_empty_tile")


def check_correction(payload: dict, revised: dict, data: dict) -> bool:
    """Nothing beside the grid to correct."""
    return True


def read(image: Any, layout: dict) -> dict:
    """What the pixels show, as a playing screen: a message over the tiles leaves them unread."""
    return {"grid": grid(image, layout["board"], layout["tolerance"]), "score": None, "scene": "playing"}


def check_reading(reading: dict) -> None:
    """A tile's value and its cell each come from one class: exactly the patterns' groups."""
    if "tiles" not in reading:
        raise ValueError("dom_reading_unsupported")
    try:
        if re.compile(reading["tiles"]["value"]).groups != 1 or re.compile(reading["tiles"]["position"]).groups != 2:
            raise ValueError("invalid_dom_pattern")
    except re.error as error:
        raise ValueError("invalid_dom_pattern") from error


def dom_queries(reading: dict) -> list[dict]:
    tiles = reading["tiles"]
    return [{"name": "tiles", "selector": tiles["selector"], "max": tiles["max"]}]


def read_dom(elements: dict, layout: dict, scene: str, score: int | None, image: Any) -> dict | None:
    """The grid from tile elements' classes (kernel.observation_scope), checked as pixels are: every
    tile names one value and one cell in range; a cell several elements claim is known only when one
    of them is the declared merge result and the rest add up to it (the game keeps the merged tiles
    until the next move). The board's pixels in the same observation must not show another value.
    None when the read cannot be trusted at all (a cut or protected item, a truncated list)."""
    spec, board = layout["reading"]["tiles"], layout["board"]
    found = elements["tiles"]
    if found["truncated"] or any(item.get("cut") or item.get("protected") for item in found["items"]):
        return None
    size = board["rows"]
    value_class, position_class = re.compile(spec["value"]), re.compile(spec["position"])
    claims: dict[tuple[int, int], list[tuple[int, bool]]] = {}
    for item in found["items"]:
        values = [int(m[1]) for name in item["classes"] if (m := value_class.fullmatch(name))]
        positions = [m for name in item["classes"] if (m := position_class.fullmatch(name))]
        if len(values) != 1 or len(positions) != 1:
            return None
        value = values[0]
        if not value or not tile(value):
            return None
        axes = dict(zip(spec["axes"], (int(positions[0][1]), int(positions[0][2]))))
        row, col = axes["row"] - spec["origin"], axes["col"] - spec["origin"]
        if not (0 <= row < size and 0 <= col < size):
            return None
        claims.setdefault((row, col), []).append((value, spec["merged"] in item["classes"]))
    cells: list[list[int | None]] = [[0] * size for _ in range(size)]
    for (row, col), claimed in claims.items():
        merged = [value for value, is_merged in claimed if is_merged]
        rest = [value for value, is_merged in claimed if not is_merged]
        if len(claimed) == 1 and not merged:
            cells[row][col] = rest[0]
        elif len(merged) == 1 and (not rest or (len(rest) == 2 and sum(rest) == merged[0])):
            cells[row][col] = merged[0]
        else:
            cells[row][col] = None
    seen = grid(image, {**board, "rect": [0, 0, *board["rect"][2:]]}, layout["tolerance"])
    for row in range(size):
        for col in range(size):
            if seen[row][col] is not None and seen[row][col] != cells[row][col]:
                cells[row][col] = None       # the page says one thing, its pixels another
    return {"grid": cells, "score": score, "scene": scene}
