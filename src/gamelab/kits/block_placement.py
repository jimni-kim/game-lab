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

from io import BytesIO
from typing import Any

# a cell is compared by its inner part (this share cut off each side), made this many pixels a side
INSET, SIDE = 0.2, 8
# looks of empty cells nearer than this (mean difference a channel, of 255) are one look
MERGE = 6.0
# the line between empty and block when no block was seen yet: this many times MERGE, and no less than CUT_LEAST
CUT_BLIND, CUT_LEAST = 3.0, 20.0
# a cell this share of the line either side of it is not told
UNSURE = 0.15
# a board this share of whose cells disagree with what the rules expect is another scene
CHANGED_SHARE = 0.25
# empties learned from the screen alone: the commonest look, when this share of the cells have it
MAJORITY = 0.55
EMPTIES_MOST = 64
# the tray: a pixel differs from the background by more than this in some channel; a block of a piece is there
# when this share of its place differs, not there when less than BLOCK_NOT; a piece is at most PIECE_MOST blocks
# a side; a slot reaches this many blocks either side of the middle of the pieces
DIFFERS, BLOCK_IS, BLOCK_NOT, PIECE_MOST, SLOT_REACH = 16, 0.6, 0.25, 5, 3.1
# the strip at each edge of a slot its background is taken from
EDGE = 4
# while a piece is held over the board: its cells are looked for this many cells around where it is to go, and it
# lies right when it is within this share of a cell of there
HOVER_REACH, HOVER_WITHIN = 2, 0.3


def _open(png: bytes) -> Any:
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        return image.convert("RGB")


def _distance(one: bytes, other: bytes) -> float:
    return sum(abs(a - b) for a, b in zip(one, other)) / len(one)


def _piece(cells: list[tuple[int, int]]) -> list[list[int]]:
    top, left = min(r for r, _ in cells), min(c for _, c in cells)
    return [list(cell) for cell in sorted((r - top, c - left) for r, c in cells)]


def _joined(cells: list[tuple[int, int]]) -> bool:
    todo, seen = [cells[0]], {cells[0]}
    while todo:
        r, c = todo.pop()
        for near in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
            if near in cells and near not in seen:
                seen.add(near)
                todo.append(near)
    return len(seen) == len(cells)


class BlockKit:
    family_id = "block_placement"

    def __init__(self, rules: dict[str, Any]) -> None:
        self.rows, self.cols, self.slots = rules["rows"], rules["cols"], rules["tray_size"]
        self.size: list[int] | None = None
        self.board: list[int] | None = None
        self.empties: list[bytes] = []
        self.cut = CUT_LEAST
        # the tray: each slot's box and how large one block of a waiting piece is drawn
        self.tray: dict[str, Any] | None = None
        # where each slot's piece was last read to be
        self.sources: list[list[int] | None] = [None] * self.slots
        # what the last reading left out and why
        self.told: dict[str, Any] = {}
        self.relearned = 0

    # ── the board ─────────────────────────────────────────────────────

    def _cells(self) -> list[tuple[int, int, list[float]]]:
        left, top, width, height = self.board
        w, h = width / self.cols, height / self.rows
        return [(r, c, [left + c * w, top + r * h, w, h]) for r in range(self.rows) for c in range(self.cols)]

    @staticmethod
    def _look(image: Any, box: list[float]) -> bytes:
        x, y, w, h = box
        inner = image.crop((round(x + w * INSET), round(y + h * INSET), round(x + w * (1 - INSET)), round(y + h * (1 - INSET))))
        return inner.resize((SIDE, SIDE)).tobytes()

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
        if not isinstance(grid, list) or len(grid) != self.rows or any(len(row) != self.cols for row in grid):
            return False
        was = (self.size, self.board, self.empties, self.cut)
        self.size, self.board = list(image.size), [round(part) for part in board]
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
        groups: list[list[bytes]] = []
        for look in looks:
            for group in groups:
                if _distance(look, group[0]) <= MERGE:
                    group.append(look)
                    break
            else:
                groups.append([look])
        groups.sort(key=len, reverse=True)
        if len(groups[0]) < MAJORITY * len(looks):
            return False
        rest = [look for group in groups[1:] for look in group]
        return self._learn(groups[0], rest)

    # ── the tray ──────────────────────────────────────────────────────

    def _tray_from(self, image: Any, tray: Any, sources: list[Any] | None) -> None:
        """Where the pieces wait, from the boxes a reading gave around them: slots side by side (or one above
        the other), each as wide as the pieces lie apart and reaching SLOT_REACH blocks from their middle."""
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
        along = [(slot, x if level else y) for slot, x, y in middles]
        other = sorted((y if level else x) for _, x, y in middles)[len(middles) // 2]
        left, top, width, height = self.board
        start, span = (left, width) if level else (top, height)
        if len(along) >= 2 and along[-1][0] != along[0][0]:
            # pieces wait in the middle of their slots: the slots lie as far apart as the pieces seen do
            apart = (along[-1][1] - along[0][1]) / (along[-1][0] - along[0][0])
            first = along[0][1] - along[0][0] * apart
        else:
            apart = span / self.slots
            first = start + apart / 2
            # the one piece seen says where its slot's middle is, when that is not where an even split puts it
            if abs(along[0][1] - (first + along[0][0] * apart)) > apart / 4:
                first = along[0][1] - along[0][0] * apart
        if apart < mini * 2:
            return
        reach, limit = SLOT_REACH * mini, image.size
        boxes = []
        for slot in range(self.slots):
            middle = first + slot * apart
            if level:
                box = [middle - apart / 2, other - reach, apart, 2 * reach]
            else:
                box = [other - reach, middle - apart / 2, 2 * reach, apart]
            x0, y0 = max(0, round(box[0])), max(0, round(box[1]))
            x1, y1 = min(limit[0], round(box[0] + box[2])), min(limit[1], round(box[1] + box[3]))
            # the tray does not reach into the board
            if level and y0 < top + height <= y1 and other > top + height:
                y0 = top + height + 1
            if x1 - x0 < mini or y1 - y0 < mini:
                return
            boxes.append([x0, y0, x1 - x0, y1 - y0])
        if self.tray is not None and self.tray.get("board") == self.board and len(self.tray["slots"]) == len(boxes) \
                and abs(self.tray["mini"] - mini) <= 0.2 * mini:
            # the same tray seen again: a slot reaches as far as any piece seen in it did (pieces that do not wait
            # in the middle of their slot)
            grown = []
            for old, new in zip(self.tray["slots"], boxes):
                x0, y0 = min(old[0], new[0]), min(old[1], new[1])
                grown.append([x0, y0, max(old[0] + old[2], new[0] + new[2]) - x0, max(old[1] + old[3], new[1] + new[3]) - y0])
            boxes = grown
        self.tray = {"slots": boxes, "mini": round(mini, 2), "board": list(self.board)}

    def _slot(self, image: Any, box: list[int]) -> tuple[Any, list[int] | None]:
        """The piece waiting in one slot and the box around it: None when the slot is empty, [] when what is
        there does not come out as whole blocks."""
        from PIL import Image, ImageChops

        mini = self.tray["mini"]
        x, y, w, h = box
        crop = image.crop((x, y, x + w, y + h))
        edge = min(EDGE, max(1, w // 8))
        # the background of each row: what the slot's two edges show there
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
            return None, None
        # the coarse picture leaves out a part less than half covered: the thing's own edge is looked for one part out
        x0, y0 = max(0, (found[0] - 1) * part), max(0, (found[1] - 1) * part)
        tight = mask.crop((x0, y0, min(w, (found[2] + 1) * part), min(h, (found[3] + 1) * part))).getbbox()
        if tight is None:
            return None, None
        left, top, right, bottom = x0 + tight[0], y0 + tight[1], x0 + tight[2], y0 + tight[3]
        source = [x + left, y + top, right - left, bottom - top]
        if (right - left) * (bottom - top) < 0.4 * mini * mini:
            return None, None
        if left <= edge or top <= 0 or right >= w - edge or bottom >= h:
            if abs((left + right) / 2 - w / 2) > 1.5 * mini or abs((top + bottom) / 2 - h / 2) > 1.5 * mini:
                return None, None                # it reaches in from outside, far from the slot's middle: no piece
            return [], source                    # it runs out of the slot: not a piece seen whole
        cols, rows = max(1, round((right - left) / mini)), max(1, round((bottom - top) / mini))
        if cols > PIECE_MOST or rows > PIECE_MOST:
            return [], source
        cw, ch = (right - left) / cols, (bottom - top) / rows
        cells = []
        for r in range(rows):
            for c in range(cols):
                inner = mask.crop((round(left + (c + 0.2) * cw), round(top + (r + 0.2) * ch),
                                   round(left + (c + 0.8) * cw), round(top + (r + 0.8) * ch)))
                share = inner.resize((1, 1), Image.BOX).getpixel((0, 0)) / 255
                if share >= BLOCK_IS:
                    cells.append((r, c))
                elif share > BLOCK_NOT:
                    return [], source
        if not cells or not _joined(cells):
            return [], source
        return _piece(cells), source

    @staticmethod
    def _nearest(coarse: Any) -> tuple[int, int, int, int] | None:
        """The box of the thing in a slot's coarse picture of what differs: of all that hang together (corner to
        corner too: a piece's blocks have seams), the one nearest the slot's middle. Something else that reaches
        into the slot (a notice over its corner) is not the piece."""
        width, height = coarse.size
        marked = [value > 0 for value in coarse.tobytes()]
        seen, best, least = [False] * len(marked), None, None
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
            away = abs((left + right + 1) / 2 - width / 2) + abs((top + bottom + 1) / 2 - height / 2)
            if least is None or away < least:
                best, least = (left, top, right + 1, bottom + 1), away
        return best

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
        if best == [(0, 0)]:
            told["ok"] = True
            return told
        # between two places (its shape fills both): it lies halfway
        down = sum(d for d, _ in best) / len(best)
        across = sum(a for _, a in best) / len(best)
        if down or across:
            told["shift"] = [round(across * self.board[2] / self.cols), round(down * self.board[3] / self.rows)]
        return told

    # ── kept between games ────────────────────────────────────────────

    def to_json(self) -> dict[str, Any]:
        return {"rows": self.rows, "cols": self.cols, "slots": self.slots, "size": self.size, "board": self.board,
                "empties": [look.hex() for look in self.empties], "cut": self.cut, "tray": self.tray}

    def restore(self, kept: dict[str, Any]) -> bool:
        if (kept.get("rows"), kept.get("cols"), kept.get("slots")) != (self.rows, self.cols, self.slots) \
                or not kept.get("board") or not kept.get("empties"):
            return False
        self.size, self.board, self.cut, self.tray = kept["size"], kept["board"], kept["cut"], kept.get("tray")
        self.empties = [bytes.fromhex(look) for look in kept["empties"]]
        return True
