"""Reading a block game's screen by code written once for the family (PLAN §19).

Nothing here knows a game, a theme or a colour. What differs between games and levels is kept as numbers learned
from the screen itself and learned again when they stop fitting:

    board     the box of the board and its rows and columns: where a model said it is, once
    empties   what an empty cell of this board looks like (each cell cut to its inner part and made 8 by 8
              pixels; several looks when cells differ, as on a chequered board). A cell is a block when it looks
              like none of them, whatever its colour, pattern or glint
    tray      where the pieces wait and how large one of their blocks is drawn there. A piece is what differs
              from the tray's own background (taken from each slot's edges, row by row), cut into blocks

A value is given only when it is clear: a cell near the line between empty and block, or a piece whose blocks do
not come out whole, is left unread (None, []), and what the rules say the last move left fills it when they say.
A board whose cells mostly disagree with what the rules expect is not read at all (`scene_changed`): the level or
the theme may have changed, and the empties are learned again, from the screen alone when most cells share one
look, else from one reading by a model.
"""
from __future__ import annotations

from typing import Any

from .grid import CHANGED_SHARE, MERGE, Grid, distance as _distance, groups, median as _median, open_picture as _open

# the line between empty and block when no block was seen yet: this many times MERGE, and no less than CUT_LEAST
CUT_BLIND, CUT_LEAST = 3.0, 20.0
# a cell this share of the line either side of it is not told
UNSURE = 0.15
# empties learned from the screen alone: the commonest look, when this share of the cells have it
MAJORITY = 0.55
EMPTIES_MOST = 64
# the tray: a pixel differs from the background by more than this in some channel; a block of a piece is there
# when this share of its place differs, not there when less than BLOCK_NOT; a piece is at most PIECE_MOST blocks
# a side; a slot reaches this many blocks either side of the middle of the pieces
DIFFERS, BLOCK_IS, BLOCK_NOT, PIECE_MOST, SLOT_REACH = 16, 0.6, 0.25, 5, 3.1
# parts of a slot's coarse picture this near each other (a part is a quarter of a block) are one piece
SEAM = 2
# a block's four sides: each looked for in a strip this deep (a share of the block) along the side, left out this
# share at either end, and there when this share of the strip differs from the background
SIDE_DEEP, SIDE_IN, SIDE_IS = 0.22, 0.15, 0.25
# how many of the places a slot's pieces were seen to wait at are kept
MIDDLES_KEPT = 9
# the strip at each edge of a slot its background is taken from
EDGE = 4
# while a piece is held over the board: its cells are looked for this many cells around where it is to go, and it
# lies right when it is within this share of a cell of there
HOVER_REACH, HOVER_WITHIN = 2, 0.3


def _piece(cells: list[tuple[int, int]]) -> list[list[int]]:
    top, left = min(r for r, _ in cells), min(c for _, c in cells)
    return [list(cell) for cell in sorted((r - top, c - left) for r, c in cells)]


def _joined(cells: list[tuple[int, int]]) -> bool:
    todo, seen = [cells[0]], {cells[0]}
    while todo:
        r, c = todo.pop()
        # corner to corner too: a piece may be blocks set on a slant
        for near in ((r + dr, c + dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if dr or dc):
            if near in cells and near not in seen:
                seen.add(near)
                todo.append(near)
    return len(seen) == len(cells)


class BlockKit(Grid):
    family_id = "block_placement"
    # the move's result is the rules' to say: a screen is asked for it as soon as it may show it
    foresees = True

    def __init__(self, rules: dict[str, Any]) -> None:
        super().__init__(rules["rows"], rules["cols"])
        self.slots = rules["tray_size"]
        self.empties: list[bytes] = []
        self.cut = CUT_LEAST
        # the tray: each slot's box and how large one block of a waiting piece is drawn
        self.tray: dict[str, Any] | None = None
        # where each slot's piece was last read to be
        self.sources: list[list[int] | None] = [None] * self.slots

    # ── the board ─────────────────────────────────────────────────────

    def _far(self, look: bytes) -> float:
        best = 255.0
        for empty in self.empties:
            best = min(best, _distance(look, empty))
            if best <= MERGE / 2:
                break
        return best

    def _learn(self, empties: list[bytes], blocks: list[bytes]) -> bool:
        """The looks of empty cells and the line a block lies beyond. False when the two cannot be told apart."""
        kept: list[bytes] = []
        for look in empties:
            if all(_distance(look, other) > MERGE for other in kept):
                kept.append(look)
        if not kept:
            return False
        nearest = min((min(_distance(look, empty) for empty in kept) for look in blocks), default=None)
        if nearest is not None and nearest <= 2 * MERGE:
            return False
        self.empties = kept[:EMPTIES_MOST]
        self.cut = max(CUT_LEAST, CUT_BLIND * MERGE) if nearest is None else max(1.5 * MERGE, min((MERGE + nearest) / 2, 2 * CUT_LEAST))
        return True

    def calibrate(self, png: bytes, board: list[int], state: dict[str, Any], sources: list[Any] | None) -> bool:
        """Learn this screen: where the board is, what its empty cells look like, and where the pieces wait, from
        a reading of it (`state`, and `sources`: the box of each waiting piece)."""
        image = _open(png)
        grid = state.get("board")
        if not self._shaped(grid):
            return False
        was = (self.size, self.board, self.empties, self.cut)
        self._place(image, board)
        looks = [(grid[r][c], self._look(image, box)) for r, c, box in self._cells()]
        empties = [look for value, look in looks if value == 0]
        blocks = [look for value, look in looks if value == 1]
        # a cell said to be empty that looks more like the blocks than like any other empty cell was misread
        if blocks and len(empties) > 1:
            empties = [look for look in empties
                       if min(_distance(look, other) for other in empties if other is not look)
                       <= min(_distance(look, other) for other in blocks)] or empties
        if not self._learn(empties, blocks):
            self.size, self.board, self.empties, self.cut = was
            return False
        self._tray_from(image, state.get("tray"), sources)
        return True

    def _board(self, image: Any) -> tuple[list[list[int | None]], list[list[float]]]:
        grid: list[list[int | None]] = [[None] * self.cols for _ in range(self.rows)]
        far = [[0.0] * self.cols for _ in range(self.rows)]
        for r, c, box in self._cells():
            distance = far[r][c] = self._far(self._look(image, box))
            if distance >= self.cut * (1 + UNSURE):
                grid[r][c] = 1
            elif distance <= self.cut * (1 - UNSURE):
                grid[r][c] = 0
        return grid, far

    def _majority(self, image: Any) -> bool:
        """The empties learned from the screen alone: the look most cells share, when most do."""
        looks = [self._look(image, box) for _, _, box in self._cells()]
        kinds = groups(looks)
        if len(kinds[0]) < MAJORITY * len(looks):
            return False
        return self._learn([looks[index] for index in kinds[0]], [looks[index] for kind in kinds[1:] for index in kind])

    # ── the tray ──────────────────────────────────────────────────────

    def _tray_from(self, image: Any, tray: Any, sources: list[Any] | None) -> None:
        """Where the pieces wait, from the boxes a reading gave around them: how large one block of a waiting piece
        is drawn, which way the slots lie (side by side, or one above the other), and where each piece seen
        waited. The slots themselves are laid from every place a piece was seen to wait (`_lay`)."""
        if not isinstance(tray, list) or not isinstance(sources, list):
            return
        seen = [(slot, box, piece) for slot, (piece, box) in enumerate(zip(tray, sources))
                if piece and isinstance(box, list) and len(box) == 4 and box[2] > 0 and box[3] > 0]
        if not seen:
            return
        minis = sorted(side for _, box, piece in seen
                       for side in (box[2] / (max(c for _, c in piece) + 1), box[3] / (max(r for r, _ in piece) + 1)))
        mini = minis[len(minis) // 2]
        middles = [(slot, box[0] + box[2] / 2, box[1] + box[3] / 2) for slot, box, _ in seen]
        across = max(x for _, x, _ in middles) - min(x for _, x, _ in middles)
        down = max(y for _, _, y in middles) - min(y for _, _, y in middles)
        level = len(middles) < 2 or across >= down
        other = sorted((y if level else x) for _, x, y in middles)[len(middles) // 2]
        same = self.tray is not None and self.tray.get("board") == self.board and self.tray.get("level") == level \
            and len(self.tray["slots"]) == self.slots and abs(self.tray["mini"] - mini) <= 0.2 * mini
        # the same tray seen again keeps where its pieces were seen to wait, and what was measured of it by the
        # reader itself (a reading's boxes are a few pixels out)
        kept = self.tray if same else {"slots": [], "middles": {}}
        self.tray = {"slots": kept["slots"], "mini": kept.get("mini", round(mini, 2)), "board": list(self.board),
                     "level": level, "other": kept.get("other", round(other, 1)), "middles": kept["middles"]}
        for slot, box, _ in seen:
            self._waited(slot, [round(part) for part in box])
        if not self._lay(image.size):
            self.tray = kept if same else None

    def _waited(self, slot: int, source: list[int]) -> None:
        """Where a piece waited in its slot: its middle, along the way the slots lie, and how long it is that way."""
        level = self.tray["level"]
        seen = [round(source[0] + source[2] / 2, 1), source[2]] if level else [round(source[1] + source[3] / 2, 1), source[3]]
        kept = self.tray["middles"].setdefault(str(slot), [])
        # the same piece still waiting says nothing new
        if not kept or abs(kept[-1][0] - seen[0]) > 1 or abs(kept[-1][1] - seen[1]) > 1:
            kept.append(seen)
            del kept[:-MIDDLES_KEPT]

    def _lay(self, size: tuple[int, int]) -> bool:
        """The slots laid where the pieces were seen to wait: each slot's middle at the middle of the pieces seen
        in it, the slots as far apart as those middles are (a game's slots are often wider than a third of its
        board: 2026-11-17, laid a third of the board apart, a web game's slots cut its longer pieces off and they
        were left unread move after move). With one slot seen only, they are a share of the board's side apart.
        False when nothing is known to lay them by."""
        tray = self.tray
        mini, level = tray["mini"], tray["level"]
        known = {int(slot): _median([middle for middle, _ in kept]) for slot, kept in tray["middles"].items() if kept}
        if not known:
            return False
        left, top, width, height = self.board
        steps = [(known[one] - known[other]) / (one - other) for one in known for other in known if one > other]
        apart = _median(steps) if steps else (width if level else height) / self.slots
        if apart < 2 * mini:
            return False
        first = _median([middle - slot * apart for slot, middle in known.items()])
        reach, other, boxes = SLOT_REACH * mini, tray["other"], []
        for slot in range(self.slots):
            middle = first + slot * apart
            box = [middle - apart / 2, other - reach, apart, 2 * reach] if level else [other - reach, middle - apart / 2, 2 * reach, apart]
            x0, y0 = max(0, round(box[0])), max(0, round(box[1]))
            x1, y1 = min(size[0], round(box[0] + box[2])), min(size[1], round(box[1] + box[3]))
            # the tray does not reach into the board
            if level and y0 < top + height <= y1 and other > top + height:
                y0 = top + height + 1
            if x1 - x0 < mini or y1 - y0 < mini:
                return False
            boxes.append([x0, y0, x1 - x0, y1 - y0])
        tray["slots"] = boxes
        return True

    def _slot(self, image: Any, box: list[int]) -> tuple[Any, list[int] | None]:
        """The piece waiting in one slot and the box around it: None when the slot is empty, [] when what is
        there does not come out as whole blocks. The background is what the slot's two side edges show, row by
        row; a piece that reaches those edges (one as long as the slot is wide: 2026-11-17, a piece five blocks
        long left 2.5 pixels either side) is read again against what the slot's top and bottom show, column by
        column."""
        found = self._piece_in(image, box, upright=False)
        if found[0] == [] and found[2]:
            again = self._piece_in(image, box, upright=True)
            if again[0]:
                return again[0], again[1]
        return found[0], found[1]

    def _piece_in(self, image: Any, box: list[int], upright: bool) -> tuple[Any, list[int] | None, bool]:
        """`_slot` against one background: the piece, its box, and whether it ran out of the slot at the edges the
        background was taken from."""
        from PIL import Image, ImageChops

        mini = self.tray["mini"]
        x, y, w, h = box
        crop = image.crop((x, y, x + w, y + h))
        edge = min(EDGE, max(1, (h if upright else w) // 8))
        if upright:
            one = crop.crop((0, 0, w, edge)).resize((w, 1), Image.BOX)
            other = crop.crop((0, h - edge, w, h)).resize((w, 1), Image.BOX)
        else:
            one = crop.crop((0, 0, edge, h)).resize((1, h), Image.BOX)
            other = crop.crop((w - edge, 0, w, h)).resize((1, h), Image.BOX)
        ground = Image.blend(one, other, 0.5).resize((w, h), Image.NEAREST)
        bands = ImageChops.difference(crop, ground).split()
        mask = ImageChops.lighter(ImageChops.lighter(bands[0], bands[1]), bands[2]).point(lambda value: 255 if value > DIFFERS else 0)
        # specks (a glint) are not a piece: what differs is looked for in parts a quarter of a block a side
        part = max(2, round(mini / 4))
        coarse = mask.resize((max(1, w // part), max(1, h // part)), Image.BOX).point(lambda value: 255 if value >= 128 else 0)
        found = self._nearest(coarse)
        if found is None:
            return None, None, False
        # the coarse picture leaves out a part less than half covered: the thing's own edge is looked for one part out
        x0, y0 = max(0, (found[0] - 1) * part), max(0, (found[1] - 1) * part)
        tight = mask.crop((x0, y0, min(w, (found[2] + 1) * part), min(h, (found[3] + 1) * part))).getbbox()
        if tight is None:
            return None, None, False
        left, top, right, bottom = x0 + tight[0], y0 + tight[1], x0 + tight[2], y0 + tight[3]
        source = [x + left, y + top, right - left, bottom - top]
        if (right - left) * (bottom - top) < 0.4 * mini * mini:
            return None, None, False
        side, ends = (0, edge) if upright else (edge, 0)
        if left <= side or top <= ends or right >= w - side or bottom >= h - ends:
            if abs((left + right) / 2 - w / 2) > 1.5 * mini or abs((top + bottom) / 2 - h / 2) > 1.5 * mini:
                return None, None, False         # it reaches in from outside, far from the slot's middle: no piece
            # it runs out of the slot: not a piece seen whole
            return [], source, (top > ends and bottom < h - ends) if not upright else (left > side and right < w - side)
        cols, rows = max(1, round((right - left) / mini)), max(1, round((bottom - top) / mini))
        if cols > PIECE_MOST or rows > PIECE_MOST:
            return [], source, False
        cw, ch = (right - left) / cols, (bottom - top) / rows
        cells = []
        for r in range(rows):
            for c in range(cols):
                inner = mask.crop((round(left + (c + 0.2) * cw), round(top + (r + 0.2) * ch),
                                   round(left + (c + 0.8) * cw), round(top + (r + 0.8) * ch)))
                share = inner.resize((1, 1), Image.BOX).getpixel((0, 0)) / 255
                if share >= BLOCK_IS:
                    cells.append((r, c))
                    continue
                # a block of the background's own colour differs from it only along its four bevelled sides; the
                # shadow other blocks throw into a place with no block lies along two of them at most (2026-11-17:
                # a teal piece on a board turned teal was not read, 9 times in one game)
                sides = 0
                for x0, y0, x1, y1 in ((SIDE_IN, 0, 1 - SIDE_IN, SIDE_DEEP), (SIDE_IN, 1 - SIDE_DEEP, 1 - SIDE_IN, 1),
                                       (0, SIDE_IN, SIDE_DEEP, 1 - SIDE_IN), (1 - SIDE_DEEP, SIDE_IN, 1, 1 - SIDE_IN)):
                    strip = mask.crop((round(left + (c + x0) * cw), round(top + (r + y0) * ch),
                                       max(round(left + (c + x0) * cw) + 1, round(left + (c + x1) * cw)),
                                       max(round(top + (r + y0) * ch) + 1, round(top + (r + y1) * ch))))
                    sides += strip.resize((1, 1), Image.BOX).getpixel((0, 0)) / 255 >= SIDE_IS
                if sides == 4:
                    cells.append((r, c))
                elif sides == 3 or (share > BLOCK_NOT and sides == 0):
                    return [], source, False
        if not cells or not _joined(cells):
            return [], source, False
        return _piece(cells), source, False

    @staticmethod
    def _nearest(coarse: Any) -> tuple[int, int, int, int] | None:
        """The box of the thing in a slot's coarse picture of what differs: of all that hang together, the one
        nearest the slot's middle, with whatever lies within half a block of it (a piece's blocks have seams
        between them, and blocks set on a slant meet only at their corners). Something else that reaches into the
        slot from further off (a notice over its corner) is not the piece."""
        width, height = coarse.size
        marked = [value > 0 for value in coarse.tobytes()]
        seen, boxes = [False] * len(marked), []
        for start, on in enumerate(marked):
            if not on or seen[start]:
                continue
            seen[start] = True
            todo, left, top, right, bottom = [start], width, height, 0, 0
            while todo:
                y, x = divmod(todo.pop(), width)
                left, top, right, bottom = min(left, x), min(top, y), max(right, x), max(bottom, y)
                for ny in range(max(0, y - 1), min(height, y + 2)):
                    for nx in range(max(0, x - 1), min(width, x + 2)):
                        index = ny * width + nx
                        if marked[index] and not seen[index]:
                            seen[index] = True
                            todo.append(index)
            boxes.append([left, top, right + 1, bottom + 1])
        if not boxes:
            return None
        best = min(boxes, key=lambda box: abs((box[0] + box[2]) / 2 - width / 2) + abs((box[1] + box[3]) / 2 - height / 2))
        rest = [box for box in boxes if box is not best]
        grown = True
        while grown:
            grown = False
            for box in list(rest):
                # a part is a quarter of a block: two parts apart or less is one piece
                if box[0] - best[2] <= SEAM and best[0] - box[2] <= SEAM and box[1] - best[3] <= SEAM and best[1] - box[3] <= SEAM:
                    best = [min(best[0], box[0]), min(best[1], box[1]), max(best[2], box[2]), max(best[3], box[3])]
                    rest.remove(box)
                    grown = True
        return best[0], best[1], best[2], best[3]

    # ── a screen ──────────────────────────────────────────────────────

    def read(self, png: bytes, prior: dict[str, Any] | None = None, unmoved: dict[str, Any] | None = None
             ) -> tuple[dict[str, Any] | None, str | None]:
        """The state this screen shows, or why none is told. `prior`: what the rules say the last move left (its
        board, and its tray unless a new one was dealt), which fills what the screen leaves unclear. `unmoved`:
        the state the move was played from, which the screen still shows when the game did not take the move."""
        self.told = {}
        if self.board is None or not self.empties:
            return None, "board_not_found"
        image = _open(png)
        if list(image.size) != self.size:
            return None, "board_not_found"
        grid, far = self._board(image)
        expected = prior.get("board") if isinstance(prior, dict) else None
        cells = self.rows * self.cols

        def apart(other: Any) -> list[tuple[int, int]]:
            return [(r, c) for r in range(self.rows) for c in range(self.cols)
                    if grid[r][c] is not None and other[r][c] is not None and grid[r][c] != other[r][c]]
        if isinstance(expected, list) and len(expected) == self.rows:
            off = apart(expected)
            stayed = unmoved.get("board") if isinstance(unmoved, dict) else None
            if len(off) > CHANGED_SHARE * cells and isinstance(stayed, list) and len(stayed) == self.rows \
                    and len(apart(stayed)) <= CHANGED_SHARE * cells / 2:
                # the board the move was played from: the game did not take the move
                prior, expected, off = unmoved, stayed, apart(stayed)
            if len(off) > CHANGED_SHARE * cells and not self._relearned(image, expected):
                self.told = {"off": len(off)}
                return None, "scene_changed"
            if len(off) > CHANGED_SHARE * cells:
                grid, far = self._board(image)
            unsure = 0
            for r in range(self.rows):
                for c in range(self.cols):
                    if grid[r][c] is None and expected[r][c] is not None:
                        grid[r][c], unsure = expected[r][c], unsure + 1
            self._keep_empties(image, grid, expected, far)
            self.told["filled"] = unsure
        elif not any(cell == 0 for row in grid for cell in row):
            # no cell looks like the empties learned: another board, read by what most of its cells share
            if not self._majority(image):
                return None, "scene_changed"
            self.relearned += 1
            grid, far = self._board(image)
        tray: list[Any] = [[] for _ in range(self.slots)]
        left_tray = prior.get("tray") if isinstance(prior, dict) else None
        dealt = isinstance(left_tray, list) and all(piece is None for piece in left_tray)
        if self.tray is not None:
            for slot, box in enumerate(self.tray["slots"]):
                tray[slot], source = self._slot(image, box)
                if tray[slot]:
                    self.sources[slot] = source
                    self._waited(slot, source)
            self._lay(image.size)
        if isinstance(left_tray, list) and len(left_tray) == self.slots and not dealt:
            # the pieces that wait are the ones that waited: a slot seen unclearly holds what it held
            tray = [left if seen == [] else seen for seen, left in zip(tray, left_tray)]
        return {"board": grid, "tray": tray, "score": None, "scene": "playing"}, None

    def _relearned(self, image: Any, expected: list[list[int | None]]) -> bool:
        """The empties learned again from the screen alone, kept when the board then reads as the rules expect."""
        was = (self.empties, self.cut)
        if self._majority(image):
            grid, _ = self._board(image)
            off = sum(grid[r][c] is not None and expected[r][c] is not None and grid[r][c] != expected[r][c]
                      for r in range(self.rows) for c in range(self.cols))
            if off <= 2:
                self.relearned += 1
                return True
        self.empties, self.cut = was
        return False

    def _keep_empties(self, image: Any, grid: list[list[int | None]], expected: list[list[int | None]],
                      far: list[list[float]]) -> None:
        """A cell the rules and the screen both call empty that looks a little unlike the empties kept (a board
        that shades as it fills) is kept as one more look."""
        if len(self.empties) >= EMPTIES_MOST:
            return
        for r, c, box in self._cells():
            if grid[r][c] == 0 and expected[r][c] == 0 and MERGE < far[r][c] <= self.cut * (1 - UNSURE):
                look = self._look(image, box)
                if all(_distance(look, other) > MERGE for other in self.empties):
                    self.empties.append(look)
                    if len(self.empties) >= EMPTIES_MOST:
                        return

    def hover(self, png: bytes, before: list[list[int | None]], cells: list[list[int]]) -> dict[str, Any]:
        """Where a piece held over the board lies, told by the cells: the cells that were empty and now are not,
        near where the piece is to go (`cells`). The piece is taken to lie where its shape, moved whole cells from
        there, has every one of its cells that was empty now filled; of several such places, the one that fills
        most (a glow along the lines the piece would clear, or over a cell beside it, fills cells too, but not the
        piece's own shape somewhere else). `ok` when that is where it is to go; `shift`, in pixels, how far from
        there it lies; `covered` whether the cells it is to go to are all filled; `seen` how many cells changed."""
        image = _open(png)
        if self.board is None or list(image.size) != self.size:
            return {"ok": False, "seen": 0}
        grid, _ = self._board(image)
        rows, cols = [r for r, _ in cells], [c for _, c in cells]
        new = {(r, c) for r in range(max(0, min(rows) - HOVER_REACH), min(self.rows, max(rows) + HOVER_REACH + 1))
               for c in range(max(0, min(cols) - HOVER_REACH), min(self.cols, max(cols) + HOVER_REACH + 1))
               if before[r][c] == 0 and grid[r][c] != 0}
        told: dict[str, Any] = {"ok": False, "seen": len(new), "covered": {tuple(cell) for cell in cells} <= new}
        if not new:
            return told
        fits: list[tuple[int, int, int]] = []
        for down in range(-HOVER_REACH, HOVER_REACH + 1):
            for across in range(-HOVER_REACH, HOVER_REACH + 1):
                moved = [(r + down, c + across) for r, c in cells]
                if any(not (0 <= r < self.rows and 0 <= c < self.cols) for r, c in moved):
                    continue
                free = [cell for cell in moved if before[cell[0]][cell[1]] == 0]
                if free and all(cell in new for cell in free):
                    fits.append((len(free), down, across))
        if not fits:
            return told
        most = max(count for count, _, _ in fits)
        best = [(down, across) for count, down, across in fits if count == most]
        if (0, 0) in best:
            # where it is to go is among the places its shape fills best: what else is filled beside it (the
            # piece's own drawing hanging over the next row, a glow) does not say it lies elsewhere (2026-11-17:
            # counted as lying halfway, a piece that lay right was moved half a cell off and not taken)
            told["ok"] = True
            return told
        # between two places, neither where it is to go: it lies halfway between them
        down = sum(d for d, _ in best) / len(best)
        across = sum(a for _, a in best) / len(best)
        if down or across:
            told["shift"] = [round(across * self.board[2] / self.cols), round(down * self.board[3] / self.rows)]
        return told

    def same(self, state: dict[str, Any], said: dict[str, Any]) -> bool:
        """Whether a screen read here is read as a model read it (a piece left unread says nothing)."""
        pairs = [(seen, told) for seen, told in zip(state["tray"], said.get("tray") or []) if seen != []]
        return state["board"] == said.get("board") and all(seen == told for seen, told in pairs)

    def awaits(self, prior: dict[str, Any] | None) -> bool:
        """Whether the screen about to be read shows what the rules cannot say: a tray dealt anew, which is
        taken once two frames in a row read it the same (pieces still flying in read as others)."""
        listed = prior.get("tray") if isinstance(prior, dict) else None
        return isinstance(listed, list) and bool(listed) and all(entry is None for entry in listed)

    def summary(self) -> dict[str, Any]:
        return {"empties": len(self.empties)}

    # ── kept between games ────────────────────────────────────────────

    def to_json(self) -> dict[str, Any]:
        return {"rows": self.rows, "cols": self.cols, "slots": self.slots, "size": self.size, "board": self.board,
                "empties": [look.hex() for look in self.empties], "cut": self.cut, "tray": self.tray}

    def restore(self, kept: dict[str, Any]) -> bool:
        if (kept.get("rows"), kept.get("cols"), kept.get("slots")) != (self.rows, self.cols, self.slots) \
                or not kept.get("board") or not kept.get("empties"):
            return False
        self.size, self.board, self.cut, self.tray = kept["size"], kept["board"], kept["cut"], kept.get("tray")
        if self.tray is not None and not {"level", "other", "middles", "slots", "mini"} <= set(self.tray):
            self.tray = None                 # kept before the slots were laid from where pieces waited: learned anew
        self.empties = [bytes.fromhex(look) for look in kept["empties"]]
        return True
