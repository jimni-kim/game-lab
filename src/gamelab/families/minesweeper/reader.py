"""Read covered cells, opened numbers, marks and the mine opened from each cell's middle; what is not clear
stays unknown.

A profile registers each cell's face (palette value 0, an opened cell's; 9, a covered one's), the ink of each
digit (1 to 8) and one `any` entry for a face like neither (12, the mine opened: games draw it on a colour of
its own, often the same red as a digit, which a face and an ink may share without being told apart by colour
alone). A cell is read from its middle (MIDDLE), clear of its edges, where a covered cell's bevel or the grid's
lines lie: the face is the colour most of it shows; on an opened face, the digit is the ink that most of the
rest shows; on a covered face, any ink is a mark (a flag). Only the screen is read: a covered cell is 9
whatever the game holds under it.
"""
from __future__ import annotations

from typing import Any

from ... import pixels
from ...rules.minesweeper import COVERED, EXPLODED, FLAG

# the state's key for the board's cells
BOARD = "grid"
# the part of a cell read, as fractions of its width and height from its top left: clear of its edges, around
# its digit
MIDDLE = (0.2, 0.8)
# the share of the middle a face must fill to be the cell's face, around a digit or a mark (a bold digit
# covers half the middle)
FACE_SHARE = 0.35
# the share of the middle a mark must cover to be one: a digit's or a flag's strokes, not a stray pixel
INK_SHARE = 0.04
# how many times the next ink's pixels the digit's own ink must show; only pixels of an ink itself (within the
# profile's tolerance) are counted, since a stroke's blended edges pass through other digits' colours (a black
# digit's greys through a grey one)
INK_LEAD = 2
OPENED_FACE = 0
DIGITS = range(1, 9)


def tile(value: int) -> bool:
    """A value a cell may hold: a number, covered, a mark, a mine shown or the mine opened."""
    return 0 <= value <= EXPLODED


def check_profile(data: dict, rules: dict) -> None:
    """A grid of the rules' size, no tray, both faces, every digit's ink and the face of the mine opened."""
    board = data["board"]
    if data["tray"] or (board["rows"], board["cols"]) != (rules["rows"], rules["cols"]):
        raise ValueError("rule_geometry_mismatch")
    values = {entry["value"]: entry for entry in board["palette"]}
    if set(values) != {OPENED_FACE, *DIGITS, COVERED, EXPLODED} or not values[EXPLODED].get("any"):
        raise ValueError("invalid_minesweeper_palette")


def check_correction(payload: dict, revised: dict, data: dict) -> bool:
    """Nothing beside the grid to correct."""
    return True


def read(image: Any, layout: dict) -> dict:
    """What the pixels show, as a playing screen: a message over the cells leaves them unread."""
    board, tolerance = layout["board"], layout["tolerance"]
    palette = board["palette"]
    faces = [entry for entry in palette if entry["value"] in (OPENED_FACE, COVERED)]
    inks = [entry for entry in palette if entry["value"] in DIGITS]
    left, top, width, height = board["rect"]
    across, down = width / board["cols"], height / board["rows"]
    grid = []
    for row in range(board["rows"]):
        cells = []
        for col in range(board["cols"]):
            box = (int(left + (col + MIDDLE[0]) * across), int(top + (row + MIDDLE[0]) * down),
                   int(left + (col + MIDDLE[1]) * across), int(top + (row + MIDDLE[1]) * down))
            data = image.crop(box).tobytes()
            cells.append(_cell([tuple(data[i:i + 3]) for i in range(0, len(data), 3)], faces, inks, tolerance))
        grid.append(cells)
    return {"grid": grid, "score": None, "scene": "playing"}


def _cell(seen: list[tuple[int, ...]], faces: list[dict], inks: list[dict], tolerance: int) -> int | None:
    named = [pixels.colour(pixel, faces, tolerance) for pixel in seen]
    shares = {value: named.count(value) for value in (OPENED_FACE, COVERED)}
    face = max(shares, key=shares.get) if max(shares.values()) >= FACE_SHARE * len(seen) else None
    if face is None:
        # a face far from both (the mine opened) fills most of the middle; a face near one but not it (a flash)
        # does not
        far = sum(_distance(pixel, faces) >= pixels.ANY_DISTANCE for pixel in seen)
        return EXPLODED if 2 * far > len(seen) else None
    listed = [entry for entry in faces if entry["value"] == face]
    marks = [pixel for pixel in seen if _distance(pixel, listed) > pixels.MAX_REACH]
    if len(marks) < INK_SHARE * len(seen):
        return face
    if face == COVERED:
        return FLAG
    counts = sorted(((sum(_distance(pixel, [ink]) <= tolerance for pixel in marks), ink["value"]) for ink in inks),
                    reverse=True)
    (most, digit), (next_most, _) = counts[0], counts[1]
    return digit if most >= INK_SHARE * len(seen) and most >= INK_LEAD * next_most else None


def _distance(pixel: tuple[int, ...], entries: list[dict]) -> int:
    """How far (per channel) a pixel lies from the nearest colour these entries list."""
    return min(max(abs(a - b) for a, b in zip(pixel, rgb)) for entry in entries
               for rgb in [entry["rgb"], *entry.get("alternates", [])])
