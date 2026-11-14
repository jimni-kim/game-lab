"""Measure where a registered profile's board is on this screen (plan §4.4), never trusting a guess.

A profile registers the colour of its board's frame (the border and gutters around the cells) and the
rest of the layout relative to the board. Code finds the frame's largest areas of that colour; each
candidate board is then confirmed on the same image as any proposal would be (`confirm`): whole
pixels inside the screen with no regions overlapping, square cells, the frame around the cells on every side, most
board cells showing the profile's own colours, every board cell's samples agreeing (unless the profile reads its cells from the page), and
every tray slot read. No marks beside the board are registered: they are drawn at another scale than
the board or covered by the game itself (a long piece, a glow), so the board's own frame and cells
and the tray are the confirmation. A cell may show one flat colour the palette does not name (a tile the profile
lacks): it stays unknown, as it would on any later look, but a cell whose samples split (a gutter, an
edge or a held piece under them) refuses the layout.
A screen that confirms none (another game, a loading or covered screen) is refused before any input.
When the code finds no board, a model may say roughly where one is (`locate`): its box is only where to
look. The board's edges are taken there from the frame colour on this image, and the board is confirmed
like any other, so a box a little off still finds the board and a wrong one finds nothing.
The measured layout is the session's geometry: its `geometry_ref` binds every later reading and input.
A game with no registered profile has no frame colour either (`frame`): around a model's box and its
count of cells, the code takes as the frame the one colour whose lines cross the whole board at an even
pitch, exactly as many as the cells need, and the cells' inset from those lines.
"""
from __future__ import annotations

from typing import Any

from PIL import Image, ImageChops

from . import catalog, perception, pixels, profiles
from . import registered
from .contracts import NeedObservation

# the frame is found on blocks of this many pixels a side, any block mostly frame-coloured joining
# its neighbours: a gutter or border a few pixels wide still connects, a stray pixel does not
FRAME_BLOCK = 4
# the largest frame-coloured areas tried as the board, largest first: the board and a few panels
# of the same colour (a score box) at most
CANDIDATES = 3
# the share of a board's cells that must show one of the profile's own colours: a panel or another
# game's cells in a colour near the frame's show few or none, a board with a few unlisted or glowing
# tiles still shows most
REGISTERED_SHARE = 0.75
# how far a board's cells may be from square (width over height): every registered grid game draws
# square cells, and the part of a board left showing under something that covers the rest, its gutters
# taken for its frame, lays the cells out squashed
CELL_SKEW = 0.1
# how far from a model's box a board's edge is looked for, as a share of the box's width or height: a
# box drawn by eye on a downscaled picture, not one around another part of the screen
EDGE_REACH = 0.15
# the share of a box's span a line of the frame colour must cover to be the board's edge: the frame's
# outer line covers all of it even under a box a little off, while a panel of the same colour touching
# the board (a score box) covers only its own width
EDGE_SHARE = 0.75
# the most common colours of a model's box tried as the frame of a board no profile registers: the
# frame, the empty cells, the page and a few tile colours
FRAME_CANDIDATES = 8
# the middle share of a box's span a frame line is looked for across: inside the board even under a box
# EDGE_REACH off, so the page beside the board does not dilute it
FRAME_SPAN = 0.5
# the share of a box's middle a line of a candidate frame colour must cover to be one of its lines: a big
# tile's glow tints the gutters beside it past the frame's reach, and the count and even pitch of the
# lines, not their coverage, tell the frame from the page
LINE_SHARE = 0.5
# how far apart the frame's lines may lie from an even pitch (pixels, or this share of the pitch): a lattice
# drawn at fractional pixels with a tile's shadow on a gutter, not one line too few or a panel taken for one
PITCH_SLACK = 3, 0.06


def measure(png: bytes, profile: profiles.Profile) -> profiles.Profile | NeedObservation:
    """The profile laid out on this screen, or why it could not be: the screen shows no board of the
    profile's that every check confirms."""
    try:
        image = pixels.decode(png, None)
    except ValueError as error:
        return NeedObservation(str(error))
    with image:
        for board in candidates(image, profile):
            measured = confirm(image, profile, board)
            if isinstance(measured, profiles.Profile):
                return measured
    return NeedObservation("board_not_found")


def confirm(image: Any, profile: profiles.Profile, board: list[int]) -> profiles.Profile | NeedObservation:
    """A proposed board rectangle (left, top, width, height), from code or anywhere else, as the layout
    it implies, only when this image confirms all of it."""
    try:
        measured = profiles.measure(profiles.check(profile), list(image.size), board)
    except ValueError as error:
        return NeedObservation(str(error))
    _, _, width, height = measured.layout["board"]["rect"]
    rows, cols = measured.layout["board"]["rows"], measured.layout["board"]["cols"]
    if abs((width / cols) / (height / rows) - 1) > CELL_SKEW:
        return NeedObservation("board_unconfirmed")
    payload = perception.read(image, measured)
    if isinstance(payload, NeedObservation):
        return payload
    board = measured.layout["board"]
    cells = payload[registered.READERS[measured.family_id].BOARD]
    if sum(value is not None for values in cells for value in values) < REGISTERED_SHARE * board["rows"] * board["cols"]:
        return NeedObservation("board_unconfirmed")
    # a page-reading profile takes its cells from the page, not their colours: its board is confirmed by
    # its frame alone, and what it reads by the page's elements and these pixels together
    if not perception.reads_dom(measured) and any(value is None and pixels.spread(pixels.samples(image, board, row, col)) > measured.layout["tolerance"]
           for row, values in enumerate(cells) for col, value in enumerate(values)):
        return NeedObservation("board_unconfirmed")
    # an end banner over the tray, like a held piece, leaves no tray to confirm: measured on another look
    if any(piece == [] for piece in payload.get("tray", ())):
        return NeedObservation("tray_unconfirmed")
    return measured


def locate(image: Any, profile: profiles.Profile, box: list[int]) -> profiles.Profile | NeedObservation:
    """The board a model says is about at `box` (left, top, width, height of its outer edge), with each
    edge the frame-coloured line nearest the box's from outside, confirmed on this image."""
    left, top, width, height = box
    mask = _mask(image, profile)
    reach_x, reach_y = profiles.whole(width * EDGE_REACH), profiles.whole(height * EDGE_REACH)
    x0, x1 = max(0, left - reach_x), min(image.width, left + width + reach_x)
    y0, y1 = max(0, top - reach_y), min(image.height, top + height + reach_y)
    if x1 - x0 <= 2 * reach_x or y1 - y0 <= 2 * reach_y:
        return NeedObservation("board_not_found")
    # the frame's share of each column over the box's rows, and of each row over its columns
    columns = mask.crop((x0, max(0, top), x1, min(image.height, top + height))).resize((x1 - x0, 1), Image.BOX).tobytes()
    rows = mask.crop((max(0, left), y0, min(image.width, left + width), y1)).resize((1, y1 - y0), Image.BOX).tobytes()
    edges = (_edge(columns, range(0, min(len(columns), left + reach_x - x0))),
             _edge(rows, range(0, min(len(rows), top + reach_y - y0))),
             _edge(columns, range(len(columns) - 1, max(-1, left + width - reach_x - x0 - 1), -1)),
             _edge(rows, range(len(rows) - 1, max(-1, top + height - reach_y - y0 - 1), -1)))
    if None in edges:
        return NeedObservation("board_not_found")
    found_left, found_top, found_right, found_bottom = edges
    return confirm(image, profile, _inner(profile, x0 + found_left, y0 + found_top,
                                          x0 + found_right + 1, y0 + found_bottom + 1))


def frame(image: Any, box: list[int], rows: int, cols: int) -> tuple[dict[str, Any], list[int]] | None:
    """The frame (`{"rgb", "inset"}`, as a profile registers it) and the cells' rectangle of a board of
    `rows` by `cols` cells a model says is about at `box`, or None: no colour of the box draws
    `cols` + 1 lines down it and `rows` + 1 across it with cells of one size between them."""
    left, top, width, height = box
    reach_x, reach_y = profiles.whole(width * EDGE_REACH), profiles.whole(height * EDGE_REACH)
    x0, x1 = max(0, left - reach_x), min(image.width, left + width + reach_x)
    y0, y1 = max(0, top - reach_y), min(image.height, top + height + reach_y)
    if x1 - x0 <= 2 * reach_x or y1 - y0 <= 2 * reach_y or min(rows, cols) < 3:
        return None
    area = image.crop((x0, y0, x1, y1))
    common = sorted(area.getcolors((x1 - x0) * (y1 - y0)), reverse=True)
    margin_x, margin_y = profiles.whole(width * (1 - FRAME_SPAN) / 2), profiles.whole(height * (1 - FRAME_SPAN) / 2)
    tried: list[tuple[int, ...]] = []
    for _, rgb in common:
        if len(tried) == FRAME_CANDIDATES:
            break
        if any(max(abs(a - b) for a, b in zip(rgb, other)) <= pixels.FRAME_REACH for other in tried):
            continue
        tried.append(rgb)
        mask = _colour_mask(area, rgb)
        columns = mask.crop((0, top + margin_y - y0, x1 - x0, top + height - margin_y - y0)).resize((x1 - x0, 1), Image.BOX).tobytes()
        lines = mask.crop((left + margin_x - x0, 0, left + width - margin_x - x0, y1 - y0)).resize((1, y1 - y0), Image.BOX).tobytes()
        across, down = _lattice(columns, cols), _lattice(lines, rows)
        if across is None or down is None:
            continue
        # a border no thicker than the widest gutter: a panel of the frame's colour touching one side (a
        # score box) widens that side's line, not the board
        gutter = max(end - start for start, end in across[1:-1] + down[1:-1])
        outer = [x0 + max(across[0][0], across[0][1] - gutter), y0 + max(down[0][0], down[0][1] - gutter),
                 x0 + min(across[-1][1], across[-1][0] + gutter), y0 + min(down[-1][1], down[-1][0] + gutter)]
        # The gutters' middles lie on the cells' lattice whatever the border's width. A big tile's glow
        # narrows a gutter from its side, moving its middle, so the axis whose gutters show widest sets the
        # pitch both share (cells are square, CELL_SKEW), and each axis's gutters together place its cells.
        inner = [[(start + end) / 2 for start, end in found[1:-1]] for found in (across, down)]
        widths = [sum(end - start for start, end in found[1:-1]) / len(found[1:-1]) for found in (across, down)]
        clean = inner[widths.index(max(widths))]
        pitch = (clean[-1] - clean[0]) / (len(clean) - 1)
        cells = []
        for middles, count, start in zip(inner, (cols, rows), (x0, y0)):
            first = start + sum(middle - index * pitch for index, middle in enumerate(middles, 1)) / len(middles)
            cells.append((first, first + count * pitch))
        (cell_left, cell_right), (cell_top, cell_bottom) = cells
        across_px, down_px = outer[2] - outer[0], outer[3] - outer[1]
        inset = [round((cell_left - outer[0]) / across_px, 6), round((cell_top - outer[1]) / down_px, 6),
                 round((outer[2] - cell_right) / across_px, 6), round((outer[3] - cell_bottom) / down_px, 6)]
        if any(not 0 <= value <= 0.25 for value in inset):
            continue
        rect = [profiles.whole(cell_left), profiles.whole(cell_top)]
        return ({"rgb": list(rgb), "inset": inset},
                rect + [profiles.whole(cell_right) - rect[0], profiles.whole(cell_bottom) - rect[1]])
    return None


def _lattice(shares: bytes, cells: int) -> list[tuple[int, int]] | None:
    """The `cells` + 1 lines (start, end past the last pixel) a colour draws across a span, or None: another
    count of lines, or cells between them of different sizes."""
    lines: list[tuple[int, int]] = []
    for index, share in enumerate(shares):
        if share < LINE_SHARE * 255:
            continue
        if lines and lines[-1][1] == index:
            lines[-1] = (lines[-1][0], index + 1)
        else:
            lines.append((index, index + 1))
    if len(lines) != cells + 1:
        return None
    middles = [(start + end) / 2 for start, end in lines]
    pitch = (middles[-1] - middles[0]) / cells
    if any(abs(middle - (middles[0] + index * pitch)) > max(PITCH_SLACK[0], PITCH_SLACK[1] * pitch)
           for index, middle in enumerate(middles)):
        return None
    return lines


def _edge(shares: bytes, order: range) -> int | None:
    """The first line, in `order` (from outside the box inward), the frame colour covers."""
    return next((index for index in order if shares[index] >= EDGE_SHARE * 255), None)


def _mask(image: Any, profile: profiles.Profile) -> Any:
    """The image's frame-coloured pixels, white."""
    return _colour_mask(image, profile.data["board"]["frame"]["rgb"])


def _colour_mask(image: Any, rgb: Any) -> Any:
    solid = Image.new("RGB", image.size, tuple(rgb))
    red, green, blue = ImageChops.difference(image, solid).split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue).point(lambda value: 255 if value <= pixels.FRAME_REACH else 0)


def _inner(profile: profiles.Profile, x0: int, y0: int, x1: int, y1: int) -> list[int]:
    """The board's cells inside its frame's outer edge, by the profile's inset."""
    width, height = x1 - x0, y1 - y0
    inset = profile.data["board"]["frame"]["inset"]
    left_edge = profiles.whole(x0 + inset[0] * width)
    top_edge = profiles.whole(y0 + inset[1] * height)
    return [left_edge, top_edge, profiles.whole(x1 - inset[2] * width) - left_edge,
            profiles.whole(y1 - inset[3] * height) - top_edge]


def candidates(image: Any, profile: profiles.Profile) -> list[list[int]]:
    """Board rectangles inside the largest frame-coloured areas of the image, largest first."""
    mask = _mask(image, profile)
    coarse = mask.reduce(FRAME_BLOCK)
    across, cells = coarse.width, coarse.tobytes()
    seen = bytearray(len(cells))
    areas = []
    for start, value in enumerate(cells):
        if value < 128 or seen[start]:
            continue
        seen[start] = 1
        pending, count = [start], 0
        left = top = len(cells)
        right = bottom = -1
        while pending:
            index = pending.pop()
            count += 1
            x, y = index % across, index // across
            left, right, top, bottom = min(left, x), max(right, x), min(top, y), max(bottom, y)
            for neighbour, inside in ((index - 1, x > 0), (index + 1, x < across - 1),
                                      (index - across, y > 0), (index + across, index + across < len(cells))):
                if inside and not seen[neighbour] and cells[neighbour] >= 128:
                    seen[neighbour] = 1
                    pending.append(neighbour)
        areas.append((count, left, top, right, bottom))
    found = []
    for _, left, top, right, bottom in sorted(areas, reverse=True)[:CANDIDATES]:
        # the exact frame edge, within a block of the coarse one
        box = (max(0, (left - 1) * FRAME_BLOCK), max(0, (top - 1) * FRAME_BLOCK),
               min(image.width, (right + 2) * FRAME_BLOCK), min(image.height, (bottom + 2) * FRAME_BLOCK))
        edge = mask.crop(box).getbbox()
        found.append(_inner(profile, box[0] + edge[0], box[1] + edge[1], box[0] + edge[2], box[1] + edge[3]))
    return found
