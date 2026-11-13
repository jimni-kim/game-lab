"""Bounded PNG decoding and colour samples for image readers.

A sample takes the nearest registered colour only when it is clearly nearer than any other value's
(a light or glow that tints a tile still names it; a pixel halfway between two tiles names neither)
and within reach (a foreign overlay names nothing). A cell or region is a value only when every
sample names the same one: a sample that names none is not skipped, since a piece held over the
board is told apart from a placed one only by its few samples on bevels and shadow.

A palette entry may stand for any other colour (`any`: a block game's pieces come in colours no profile
lists): a sample far from every registered colour names it, and a cell names it only when all its
samples are one flat colour, as a placed piece's face is and a held piece's bevels and shadow are not.
"""
from __future__ import annotations

from io import BytesIO
import warnings
from typing import Any

from .profiles import MAX_IMAGE_PIXELS

MAX_IMAGE_BYTES = 4_000_000
SAMPLE_FRACTIONS = (0.22, 0.78)
SAMPLE_RADIUS = 1
# how much nearer (per channel) the nearest value's colour must be than any other value's
DECISION_MARGIN = 6
# no registered colour farther than this (per channel) is a sample's colour, however clear the margin
MAX_REACH = 24
# a sample at least this far (per channel) from every registered colour shows "any other colour"
ANY_DISTANCE = 2 * MAX_REACH
# the most an "any colour" cell's samples may differ per channel: one flat face, not a bevel or shadow
ANY_SPREAD = MAX_REACH
# how far apart (per channel) the pixels of one sample may lie for it to show a face: a tile's own shading
# across a few pixels, not its edge, a numeral or a neighbour
FACE_SPREAD = MAX_REACH // 2
# how far a face's middle colour may lie from the one its value lists on an `exact` board: the same tile's
# gradient caught a pixel or two apart, not another value's face
FACE_REACH = 3
# how far a board frame's pixel may be from its registered colour: its own shading and a
# tile's light on it, not another colour of the page
FRAME_REACH = 12


def decode(png: bytes, viewport: list[int] | None) -> Any:
    """The PNG as RGB, of exactly `viewport` pixels, or of any bounded size when there is none yet to
    hold it to (a screen whose board is still to be measured)."""
    from PIL import Image, UnidentifiedImageError

    if not isinstance(png, bytes) or not png or len(png) > MAX_IMAGE_BYTES:
        raise ValueError("invalid_image")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(png)) as image:
                if image.format != "PNG" or (viewport is not None and image.size != tuple(viewport)) or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("image_geometry_mismatch")
                if getattr(image, "n_frames", 1) != 1 or image.mode not in ("RGB", "RGBA"):
                    raise ValueError("unsupported_image")
                image.load()
                if image.mode == "RGBA" and image.getchannel("A").getextrema() != (255, 255):
                    raise ValueError("transparent_image")
                return image.convert("RGB")
    except (OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("invalid_image") from error


def colour(pixel: tuple[int, ...], palette: list[dict], tolerance: int) -> Any:
    """The value this pixel shows, or None. A one-colour palette (a layout anchor, a piece's tile) has
    no second colour to be nearer than, so it keeps the registered tolerance."""
    nearest: dict[Any, int] = {}
    for entry in palette:
        for rgb in [entry["rgb"], *entry.get("alternates", [])]:
            distance = max(abs(left - right) for left, right in zip(pixel, rgb))
            nearest[entry["value"]] = min(distance, nearest.get(entry["value"], distance))
    ranked = sorted(nearest.items(), key=lambda item: item[1])
    value, distance = ranked[0]
    if len(ranked) == 1:
        return value if distance <= tolerance else None
    if distance <= tolerance or (distance <= MAX_REACH and ranked[1][1] - distance >= DECISION_MARGIN):
        return value
    return None


def classify(pixel: tuple[int, ...], palette: list[dict], tolerance: int) -> Any:
    """colour(), for a palette that may hold one `any` entry (the colour everything else is)."""
    anything = next((entry["value"] for entry in palette if entry.get("any")), None)
    if anything is None:
        return colour(pixel, palette, tolerance)
    known = [entry for entry in palette if not entry.get("any")]
    nearest = min(max(abs(left - right) for left, right in zip(pixel, rgb))
                  for entry in known for rgb in [entry["rgb"], *entry.get("alternates", [])])
    if nearest >= ANY_DISTANCE:
        return anything
    if len(known) == 1:
        # the only other value lies ANY_DISTANCE away: that is the margin a lone colour lacks
        return known[0]["value"] if nearest <= MAX_REACH else None
    return colour(pixel, known, tolerance)

def agreed(samples: list[Any]) -> Any:
    """The one value every sample names, or None."""
    return samples[0] if None not in samples and len(set(samples)) == 1 else None


def region(image: Any, rect: list[int], palette: list[dict], tolerance: int) -> Any:
    left, top, width, height = rect
    return agreed([colour(image.getpixel((col, row)), palette, tolerance)
                   for row in range(top, top + height) for col in range(left, left + width)])


def grid(image: Any, spec: dict, tolerance: int) -> list[list[int | None]]:
    result = []
    for row in range(spec["rows"]):
        cells = []
        for col in range(spec["cols"]):
            if spec.get("exact"):
                cells.append(face_value(faces(image, spec, row, col, tolerance), spec["palette"]))
                continue
            seen = samples(image, spec, row, col)
            value = agreed([classify(pixel, spec["palette"], tolerance) for pixel in seen])
            if any(entry.get("any") and entry["value"] == value for entry in spec["palette"]) and spread(seen) > ANY_SPREAD:
                value = None
            cells.append(value)
        result.append(cells)
    return result


def samples(image: Any, spec: dict, row: int, col: int) -> list[tuple[int, ...]]:
    """The pixels a grid cell is read from: small squares around points well inside it, clear of its edges."""
    left, top, width, height = spec["rect"]
    cell_width, cell_height = width / spec["cols"], height / spec["rows"]
    seen = []
    for vertical in SAMPLE_FRACTIONS:
        for horizontal in SAMPLE_FRACTIONS:
            center_x = int(left + (col + horizontal) * cell_width)
            center_y = int(top + (row + vertical) * cell_height)
            for offset_y in range(-SAMPLE_RADIUS, SAMPLE_RADIUS + 1):
                for offset_x in range(-SAMPLE_RADIUS, SAMPLE_RADIUS + 1):
                    seen.append(image.getpixel((center_x + offset_x, center_y + offset_y)))
    return seen


def faces(image: Any, spec: dict, row: int, col: int, tolerance: int) -> list[tuple[int, ...]] | None:
    """The colours a grid cell's sample points show, each the middle of one point's pixels, those within
    `tolerance` of one already found left out; None when a point's pixels are not one face (FACE_SPREAD)."""
    seen = samples(image, spec, row, col)
    size = len(seen) // len(SAMPLE_FRACTIONS) ** 2
    found: list[tuple[int, ...]] = []
    for index in range(0, len(seen), size):
        group = seen[index:index + size]
        if spread(group) > FACE_SPREAD:
            return None
        middle = tuple(sorted(channel)[len(channel) // 2] for channel in zip(*group))
        if all(max(abs(a - b) for a, b in zip(middle, other)) > tolerance for other in found):
            found.append(middle)
    return found


def face_value(found: list[tuple[int, ...]] | None, palette: list[dict]) -> Any:
    """On an `exact` board (one whose palette may lack values whose colours lie near listed ones), the one
    value every face of a cell matches (`faces`, each within FACE_REACH of one of the value's colours), or
    None: a cell is told by all of its faces at once, so a value whose lighter face is near another's darker
    one is still told apart, and a value not listed matches nothing rather than its nearest neighbour."""
    if not found:
        return None
    fits = [entry["value"] for entry in palette if "rgb" in entry and covers([entry["rgb"], *entry.get("alternates", [])], found)]
    return fits[0] if len(fits) == 1 else None


def covers(listed: list[Any], found: list[tuple[int, ...]]) -> bool:
    """Whether every face found lies within FACE_REACH of one of the colours listed."""
    return all(min(max(abs(a - b) for a, b in zip(face, rgb)) for rgb in listed) <= FACE_REACH for face in found)


def spread(seen: list[tuple[int, ...]]) -> int:
    """How far apart (per channel) the most different of these pixels are."""
    return max(max(channel) - min(channel) for channel in zip(*seen))
