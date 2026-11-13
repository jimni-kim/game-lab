"""Find the tray's pieces and their coloured tile interiors without assuming a piece's size or alignment.

A piece is found in the tray band as the columns that show anything but the background, split from
its neighbours by background; it belongs to the slot its middle lies in, however far it reaches past
that slot (a long piece is wider than its slot). The background is read off the band's own edges, so
a page's gradient at another window size is still its background. Bevels separate the connected
interiors. Their centres must form the registered lattice; all pixels outside the inferred tiles
must match the background. An unknown colour or an overlay therefore cannot silently remove part
of a piece or become a consumed slot, and a mark no slot can claim (between slots, or wider than any
piece: an end banner across the tray) leaves the whole tray unread, never the other slots used.
"""
from __future__ import annotations

from typing import Any

from .pixels import ANY_DISTANCE, MAX_REACH, colour

# Rasterisation may move a tile centre by one pixel at a fractional layout boundary.
CENTRE_TOLERANCE = 1
# Interiors occupy the central half or more, with room for the registered game's bevels.
MIN_INTERIOR = 0.5
MAX_INTERIOR = 0.9
# Allow the antialiased rim to scale with a measured cell, while checking its solid core.
INTERIOR_RIM = 0.04
# An inclusive raster edge may cover half a pixel beyond the nominal tile footprint.
EDGE_PAD = 0.5


Piece = list[list[int]] | None
Centres = list[tuple[float, float]]


def read_tray(image: Any, layout: dict) -> list[tuple[Piece, Centres]]:
    """Each slot's piece and its tile centres in screen pixels: a shape, None for a slot whose piece
    was used, or [] for one that cannot be read."""
    slots, tolerance = layout["tray"], layout["tolerance"]
    unread: list[tuple[Piece, Centres]] = [([], []) for _ in slots]
    left, top, width, height = layout["tray_band"]["rect"]
    background = _band_background(image, layout["tray_band"]["rect"], slots[0]["piece"]["background"], tolerance)
    if background is None:
        return unread
    marked = _marked_columns(image, layout["tray_band"]["rect"], background, tolerance)
    runs, start = [], None
    for x, mark in enumerate([*marked, False]):
        if mark and start is None:
            start = x
        elif not mark and start is not None:
            runs.append((start, x))
            start = None
    widest = max(slot["cols"] * slot["piece"]["cell"][0] for slot in slots) + 2 * CENTRE_TOLERANCE
    if any(end - begin > widest for begin, end in runs):
        return unread                        # no piece is that wide: a banner across the tray hides it
    owners: dict[int, list[int]] = {}
    for index, (begin, end) in enumerate(runs):
        middle = left + (begin + end - 1) / 2
        owner = [number for number, slot in enumerate(slots)
                 if slot["rect"][0] <= middle < slot["rect"][0] + slot["rect"][2]]
        if not owner:
            return unread                    # a mark between slots: nothing says whose piece is whose
        owners.setdefault(owner[0], []).append(index)
    result = []
    for number, slot in enumerate(slots):
        found = owners.get(number, [])
        if not found:
            result.append((None, []))
            continue
        if len(found) > 1:
            result.append(([], []))
            continue
        index = found[0]
        begin, end = runs[index]
        # widen the piece's box over background up to its neighbours, so whole tile footprints fit in it
        reach = slot["piece"]["cell"][0]
        lower = max(runs[index - 1][1] if index else 0, begin - reach)
        upper = min(runs[index + 1][0] if index + 1 < len(runs) else width, end + reach)
        spec = {**slot, "rect": [left + lower, top, upper - lower, height]}
        result.append(piece_geometry(image, spec, tolerance, background))
    return result


def _band_background(image: Any, band: list[int], registered: list[list[int]], tolerance: int) -> list[tuple] | None:
    """The background of each row of the band, as its two edges show it; None when they disagree or
    are far from the registered background (a banner or overlay over the tray)."""
    left, top, width, height = band
    upper, lower = registered
    rows = []
    for y in range(height):
        first, last = image.getpixel((left, top + y)), image.getpixel((left + width - 1, top + y))
        expected = [a + (b-a)*y/max(1, height-1) for a, b in zip(upper, lower)]
        if _far(first, last, tolerance) or max(abs(a-b) for a, b in zip(first, expected)) > MAX_REACH:
            return None
        rows.append(tuple(round((a + b) / 2) for a, b in zip(first, last)))
    return rows


def _marked_columns(image: Any, band: list[int], background: list[tuple], tolerance: int) -> list[bool]:
    """Whether each column of the band shows any pixel farther than `tolerance` from its row's background."""
    from PIL import Image, ImageChops

    left, top, width, height = band
    rows = Image.new("RGB", (1, height))
    rows.putdata([tuple(round(channel) for channel in colour) for colour in background])
    red, green, blue = ImageChops.difference(image.crop((left, top, left + width, top + height)),
                                             rows.resize((width, height), Image.Resampling.NEAREST)).split()
    far = ImageChops.lighter(ImageChops.lighter(red, green), blue).point(lambda value: 255 if value > tolerance else 0)
    return [far.crop((x, 0, x + 1, height)).getbbox() is not None for x in range(width)]


def _far(pixel: tuple, background: tuple, tolerance: int) -> bool:
    return max(abs(a-b) for a, b in zip(pixel, background)) > tolerance


def _piece_colour(image: Any, spec: dict, background: list[tuple]) -> list[dict] | None:
    """A slot whose piece may be any colour: its tiles' face is the most common colour far from the
    background, read as that one registered colour; None when nothing is far from the background."""
    left, top, width, height = spec["rect"]
    counts: dict[tuple[int, ...], int] = {}
    for y in range(height):
        for x in range(width):
            pixel = image.getpixel((left + x, top + y))
            if max(abs(a-b) for a, b in zip(pixel, background[y])) >= ANY_DISTANCE:
                counts[pixel] = counts.get(pixel, 0) + 1
    if not counts:
        return None
    return [{"value": 1, "rgb": list(max(counts, key=counts.get))}]


def piece_geometry(image: Any, spec: dict, tolerance: int, background: list[tuple]) -> tuple[Piece, Centres]:
    """The piece in the box `spec["rect"]`, whose rows show `background` wherever no tile is."""
    left, top, width, height = spec["rect"]
    cell_w, cell_h = spec["piece"]["cell"]
    palette = [entry for entry in spec["palette"] if entry["value"] == 1]
    if any(entry.get("any") for entry in palette):
        # no face colour: no tiles, and every pixel must still be background (checked below)
        palette = _piece_colour(image, spec, background) or []
    points = {(x, y) for y in range(height) for x in range(width)
              if palette and colour(image.getpixel((left + x, top + y)), palette, tolerance) == 1}
    centres, fragments = [], []
    while points:
        pending = [points.pop()]
        component = []
        while pending:
            x, y = pending.pop()
            component.append((x, y))
            for neighbour in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if neighbour in points:
                    points.remove(neighbour)
                    pending.append(neighbour)
        x0, x1 = min(x for x, _ in component), max(x for x, _ in component)
        y0, y1 = min(y for _, y in component), max(y for _, y in component)
        if not (MIN_INTERIOR * cell_w <= x1-x0+1 <= MAX_INTERIOR * cell_w
                and MIN_INTERIOR * cell_h <= y1-y0+1 <= MAX_INTERIOR * cell_h):
            fragments.extend(component)
            continue
        # Permit antialiasing on the perimeter, but a hole inside is an occluded tile.
        filled = set(component)
        rim_x, rim_y = max(1, round(cell_w * INTERIOR_RIM)), max(1, round(cell_h * INTERIOR_RIM))
        if any((x, y) not in filled for y in range(y0+rim_y, y1-rim_y+1) for x in range(x0+rim_x, x1-rim_x+1)):
            return [], []
        centres.append(((x0+x1)/2, (y0+y1)/2))
        if len(centres) > spec["rows"] * spec["cols"]:
            return [], []
    cells = []
    if centres:
        origin_x, origin_y = min(x for x, _ in centres), min(y for _, y in centres)
        for x, y in centres:
            col, row = round((x-origin_x)/cell_w), round((y-origin_y)/cell_h)
            if not (0 <= row < spec["rows"] and 0 <= col < spec["cols"]
                    and abs(x-origin_x-col*cell_w) <= CENTRE_TOLERANCE
                    and abs(y-origin_y-row*cell_h) <= CENTRE_TOLERANCE):
                return [], []
            if x-cell_w/2 < 0 or y-cell_h/2 < 0 or x+cell_w/2 >= width or y+cell_h/2 >= height:
                return [], []
            cells.append([row, col])
        if len(set(map(tuple, cells))) != len(cells):
            return [], []
    # Palette-coloured bevel specks are allowed only inside a proven tile footprint.
    # An isolated mark elsewhere still makes the slot unknown, never consumed.
    if any(not any(abs(x-cx) <= cell_w/2+EDGE_PAD and abs(y-cy) <= cell_h/2+EDGE_PAD
                   for cx, cy in centres) for x, y in fragments):
        return [], []
    for y in range(height):
        for x in range(width):
            if any(abs(x-cx) <= cell_w/2+EDGE_PAD and abs(y-cy) <= cell_h/2+EDGE_PAD for cx, cy in centres):
                continue
            if _far(image.getpixel((left+x, top+y)), background[y], tolerance):
                return [], []
    ordered = sorted(zip(cells, centres))
    return ([cell for cell, _ in ordered] if cells else None,
            [(left+x, top+y) for _, (x, y) in ordered])
