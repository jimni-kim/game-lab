"""What every family's reader of a grid shares (PLAN §20, X0 and X1): nothing here knows a family, a game or a
colour.

    cells     a board box divided evenly into rows and columns
    look      what a cell looks like: its inner part made a few pixels a side
    distance  how unlike two looks are
    groups    looks gathered into the kinds a screen shows
    pitch     how many rows and columns a board has, counted from its picture alone
    Grid      where a game's board is on its screen, and what is kept of that between games
    Looks     the values a cell may hold, each with the looks it was seen to have (X1): a cell is the value whose
              look it has, and no value when it looks like none or like two

A family's reader stands on these and adds what its game needs (`block_placement`: which cells are empty, and
the tray; `slide_merge`, `minesweeper`, `match3`, `sokoban`: a value for every cell).
"""
from __future__ import annotations

from io import BytesIO
from typing import Any

# a cell is compared by its inner part (this share cut off each side), made this many pixels a side
INSET, SIDE = 0.2, 8
# looks nearer than this (mean difference a channel, of 255) are one look
MERGE = 6.0
# a board this share of whose cells disagree with what the rules expect is another scene
CHANGED_SHARE = 0.25
# counting a board's rows and columns from its picture (`pitch`): a board has from GAUGE_FEWEST to GAUGE_MOST cells
# a side and a cell is at least GAUGE_LEAST pixels; an edge is looked for this many pixels either side of where a
# count puts it; the best count's lines must stand this far above its cells' middles (of 255) and that margin be
# this share larger than the next count's
GAUGE_FEWEST, GAUGE_MOST, GAUGE_LEAST, GAUGE_REACH, GAUGE_CLEAR, GAUGE_APART = 3, 14, 8, 2, 6.0, 0.2
# fitting a board box to the picture's own lines (`fit`): each side is looked for within this share of a cell of
# where it was said to be; an edge counts for a line within this share of a cell of it (a gutter's two edges are one
# line, at its middle); the best place's lines must stand this many times above what a place has on average; and
# the cells keep the shape they were said to have within this share; a board with this many lines between its
# cells is fitted by those alone
FIT_REACH, FIT_SPREAD, FIT_CLEAR, FIT_SHAPE, FIT_INNER = 0.4, 0.12, 1.5, 0.04, 4
# a value's looks kept at most, and the values a reader keeps looks of at most
LOOKS_MOST, VALUES_MOST = 24, 64
# the number a game shows of the moves left (`Grid.counter`): compared made this many pixels a side, it changed
# when the two pictures differ by more than this (mean difference a channel, of 255); it is believed once it was
# seen to change with a move the game took and has not failed to more often than that
TICK_SIDE, TICK = 12, 12.0
# counting a board by the cells whose looks are known (`Valued.survey`): counts this many either side of the one a
# reading gave are tried; a line of cells beside the board is the board's when this many of its cells look like
# something known; and a board has at most this many cells a side
SURVEY_SPREAD, SURVEY_LINE, SURVEY_MOST = 3, 2, 14


def open_picture(png: bytes) -> Any:
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        return image.convert("RGB")


def distance(one: bytes, other: bytes) -> float:
    return sum(abs(a - b) for a, b in zip(one, other)) / len(one)


def median(values: list[float]) -> float:
    ordered = sorted(values)
    half = len(ordered) // 2
    return ordered[half] if len(ordered) % 2 else (ordered[half - 1] + ordered[half]) / 2


def cells(board: list[int], rows: int, cols: int) -> list[tuple[int, int, list[float]]]:
    """Every cell of a board box divided evenly: its row, its column and its box."""
    left, top, width, height = board
    w, h = width / cols, height / rows
    return [(r, c, [left + c * w, top + r * h, w, h]) for r in range(rows) for c in range(cols)]


def look(image: Any, box: list[float], inset: float = INSET, side: int = SIDE) -> bytes:
    x, y, w, h = box
    inner = image.crop((round(x + w * inset), round(y + h * inset), round(x + w * (1 - inset)), round(y + h * (1 - inset))))
    return inner.resize((side, side)).tobytes()


def groups(looks: list[bytes], merge: float = MERGE) -> list[list[int]]:
    """The looks gathered into kinds, each look with the first kind whose first look it is near: the indexes of
    each kind's looks, the kind most looks have first."""
    found: list[list[int]] = []
    for index, one in enumerate(looks):
        for group in found:
            if distance(one, looks[group[0]]) <= merge:
                group.append(index)
                break
        else:
            found.append([index])
    found.sort(key=len, reverse=True)
    return found


def pitch(png: bytes, box: list[int]) -> tuple[int, int] | None:
    """How many rows and columns the board in `box` has, counted from the picture alone: of every count a
    board may have, the one whose lines between cells lie where the picture has its edges and whose cell
    middles lie where it has none (half the count puts middles on lines, twice it puts lines on middles).
    None when no count stands out (a board with no lines between its cells and nothing on it)."""
    from PIL import Image, ImageFilter

    image = open_picture(png)
    left, top, width, height = (round(part) for part in box)
    if width < GAUGE_MOST * GAUGE_LEAST or height < GAUGE_MOST * GAUGE_LEAST:
        return None
    edges = image.crop((left, top, left + width, top + height)).convert("L").filter(ImageFilter.FIND_EDGES)

    def count(profile: list[int]) -> int | None:
        size = len(profile)

        def near(at: float) -> float:
            middle = min(size - 1, max(0, round(at)))
            return max(profile[max(0, middle - GAUGE_REACH):middle + GAUGE_REACH + 1])
        scores = {}
        for number in range(GAUGE_FEWEST, GAUGE_MOST + 1):
            side = size / number
            lines = [near(index * side) for index in range(1, number)]
            middles = [near((index + 0.5) * side) for index in range(number)]
            scores[number] = sum(lines) / len(lines) - sum(middles) / len(middles)
        ranked = sorted(scores, key=scores.get, reverse=True)
        best, second = ranked[0], ranked[1]
        if scores[best] < GAUGE_CLEAR or scores[best] - scores[second] < GAUGE_APART * scores[best]:
            return None
        return best
    across = list(edges.resize((width, 1), Image.BOX).tobytes())
    down = list(edges.resize((1, height), Image.BOX).tobytes())
    cols, rows = count(across), count(down)
    return (rows, cols) if rows and cols else None


def _fit_side(profile: list[int], first: float, last: float, lines: int, start: int, *, inner: bool = False,
              gaps: bool = False) -> tuple[float, float] | None:
    """Where the first and the last of `lines` evenly spaced lines lie along a profile of edges that begins at
    `start`: the pair, each within FIT_REACH of a cell of where it was said to be, on whose lines the profile is
    strongest (`inner`: on those of its lines that lie between two cells), or, with `gaps`, weakest: a board with
    no lines drawn between its cells shows its pieces' edges inside the cells and nothing between them. None when
    no pair stands out."""
    cell = (last - first) / (lines - 1)
    reach, spread = max(1, round(FIT_REACH * cell)), max(1, round(FIT_SPREAD * cell))
    # each place's edges with those within `spread` of it, the nearer counting for more (by the square of how far):
    # a line between two edges is then strongest at their middle, not anywhere between them
    sums, firsts, seconds = [0.0], [0.0], [0.0]
    for index, value in enumerate(profile):
        sums.append(sums[-1] + value)
        firsts.append(firsts[-1] + value * index)
        seconds.append(seconds[-1] + value * index * index)
    size = len(profile)

    def near(at: float) -> float:
        middle = at - start
        low, high = max(0, int(middle - spread) + 1), min(size, int(middle + spread) + 1)
        if high <= low:
            return 0.0
        plain, first, second = sums[high] - sums[low], firsts[high] - firsts[low], seconds[high] - seconds[low]
        return plain - (second - 2 * middle * first + middle * middle * plain) / (spread * spread)
    counted = range(1, lines - 1) if inner else range(lines)
    scores = {}
    for one in range(-reach, reach + 1):
        for other in range(-reach, reach + 1):
            step = (last + other - first - one) / (lines - 1)
            scores[one, other] = sum(near(first + one + index * step) for index in counted)
    mean = sum(scores.values()) / len(scores)
    if gaps:
        if mean <= 0:
            return None
        # turned over: the emptiest place stands as far above the average as the average is above it
        scores = {pair: 2 * mean - score for pair, score in scores.items()}
    best = max(scores, key=lambda pair: (scores[pair], -abs(pair[0]) - abs(pair[1])))
    if scores[best] <= 0 or scores[best] < FIT_CLEAR * mean:
        return None
    # of places as good (a flat top), the middle one
    top = [pair for pair, score in scores.items() if score >= 0.999 * scores[best]]
    return first + median([one for one, _ in top]), last + median([other for _, other in top])


def fit(image: Any, box: list[int], rows: int, cols: int) -> list[int]:
    """A board box said to hold `rows` by `cols` cells, moved and sized so that the lines between its cells lie
    where the picture has its own (a model's box is a few pixels out, and those pixels are a share of a small
    cell). Each way it is left as it was said when the picture shows no lines to go by."""
    from PIL import Image, ImageFilter

    left, top, width, height = (round(part) for part in box)
    if width < 2 * cols or height < 2 * rows:
        return [left, top, width, height]
    pad_x, pad_y = round(FIT_REACH * width / cols) + 2, round(FIT_REACH * height / rows) + 2
    x0, y0 = max(0, left - pad_x), max(0, top - pad_y)
    x1, y1 = min(image.width, left + width + pad_x), min(image.height, top + height + pad_y)
    edges = image.crop((x0, y0, x1, y1)).convert("L").filter(ImageFilter.FIND_EDGES)
    # the filter marks the cut's own border
    edges = edges.crop((1, 1, edges.width - 1, edges.height - 1))
    x0, y0 = x0 + 1, y0 + 1
    across = list(edges.resize((edges.width, 1), Image.BOX).tobytes())
    down = list(edges.resize((1, edges.height), Image.BOX).tobytes())
    def side(profile: list[int], first: float, last: float, lines: int, start: int) -> tuple[float, float] | None:
        # a board's outer lines are drawn otherwise than those between two cells (a frame, the page beside it)
        # and pull the fit their way: a board with enough lines between its cells is fitted by those alone. One
        # with few is fitted by all its lines: those between its cells are too few to stand out, and moved by
        # them the box came out differently from screen to screen of one game (2026-11-17, kept screens)
        found = _fit_side(profile, first, last, lines, start, inner=lines > FIT_INNER + 2)
        # no lines to go by: the gaps between the pieces, where the picture has no edges
        return found if found is not None else _fit_side(profile, first, last, lines, start, gaps=True)
    sides = side(across, left, left + width, cols + 1, x0)
    ends = side(down, top, top + height, rows + 1, y0)
    wide, high = (sides[1] - sides[0]) / width if sides else 1.0, (ends[1] - ends[0]) / height if ends else 1.0
    if abs(wide / high - 1) > FIT_SHAPE:
        # the cells came out another shape than they were said to have: the way that changed more caught other
        # lines than the board's (a row of buttons above it), and is left as it was said
        sides, ends = (sides, None) if abs(wide - 1) <= abs(high - 1) else (None, ends)
    if sides is not None:
        left, width = round(sides[0]), round(sides[1] - sides[0])
    if ends is not None:
        top, height = round(ends[0]), round(ends[1] - ends[0])
    return [left, top, width, height]


class Grid:
    """Where a game's board is on its screen: the screen's size, the board's box, its rows and columns."""

    # how a cell of this family is looked at
    inset, side = INSET, SIDE

    def __init__(self, rows: int, cols: int) -> None:
        self.rows, self.cols = rows, cols
        self.size: list[int] | None = None
        self.board: list[int] | None = None
        # what the last reading left out and why
        self.told: dict[str, Any] = {}
        # how often what a cell looks like was learned again from the screen alone
        self.relearned = 0
        # where the game shows how many moves are left, as a model gave it, and how often it was seen to change
        # with a move the game took and not to
        self.counter: list[int] | None = None
        self.ticks = {"seen": 0, "missed": 0}

    pitch = staticmethod(pitch)

    def _cells(self) -> list[tuple[int, int, list[float]]]:
        return cells(self.board, self.rows, self.cols)

    def _look(self, image: Any, box: list[float]) -> bytes:
        return look(image, box, self.inset, self.side)

    def _looks(self, image: Any) -> list[list[bytes]]:
        found = [[b""] * self.cols for _ in range(self.rows)]
        for r, c, box in self._cells():
            found[r][c] = self._look(image, box)
        return found

    def _shown(self, png: bytes) -> Any:
        """The screen as a picture, when it is of the size the board was learned on; None otherwise."""
        if self.board is None:
            return None
        image = open_picture(png)
        return image if list(image.size) == self.size else None

    def _shaped(self, grid: Any) -> bool:
        return isinstance(grid, list) and len(grid) == self.rows and all(
            isinstance(row, list) and len(row) == self.cols for row in grid)

    def _place(self, image: Any, board: list[int]) -> None:
        self.size, self.board = list(image.size), [round(part) for part in board]

    def place_counter(self, box: Any) -> None:
        """Where the game shows how many moves are left, as a model gave it: what was seen of it before is kept
        while the box is where it was."""
        if not (isinstance(box, list) and len(box) == 4):
            return
        if self.counts and any(abs(one - other) > max(4, box[3] // 4) for one, other in zip(box, self.counter)):
            # one seen to change with the moves is not let go for a box one reading gives elsewhere (2026-11-17: a
            # reading at a level's end gave a box a fifth the size, away from the number)
            return
        if self.counter is None or any(abs(one - other) > max(4, box[3] // 4) for one, other in zip(box, self.counter)):
            self.ticks = {"seen": 0, "missed": 0}
        self.counter = [round(part) for part in box]

    def ticked(self, before: bytes, png: bytes) -> bool | None:
        """Whether the number of moves left changed between two screens; None when nothing tells (no counter
        known, or one not believed yet)."""
        if self.counter is None:
            return None
        one, other = open_picture(before), open_picture(png)
        if one.size != other.size or list(one.size) != self.size:
            return None
        return distance(look(one, self.counter, 0.0, TICK_SIDE), look(other, self.counter, 0.0, TICK_SIDE)) > TICK

    @property
    def counts(self) -> bool:
        """Whether the counter is believed: it changed with a move the game took, and failed to no more often."""
        return self.counter is not None and self.ticks["seen"] >= 1 and self.ticks["missed"] <= self.ticks["seen"]

    def tally(self, ticked: bool | None) -> None:
        """A move the game took, and whether the counter changed with it. One that keeps not changing is no
        counter: it is let go."""
        if ticked is None:
            return
        self.ticks["seen" if ticked else "missed"] += 1
        if self.ticks["missed"] >= 2 and self.ticks["missed"] > self.ticks["seen"]:
            self.counter, self.ticks = None, {"seen": 0, "missed": 0}

    def cell_picture(self, png: bytes, cell: tuple[int, int], around: float = 0.0) -> bytes | None:
        """One cell cut out of a screen as a picture of its own, with this share of a cell around it."""
        from PIL import Image

        image = self._shown(png)
        if image is None:
            return None
        width, height = self.board[2] / self.cols, self.board[3] / self.rows
        x, y = self.board[0] + cell[1] * width, self.board[1] + cell[0] * height
        cut = image.crop((max(0, round(x - around * width)), max(0, round(y - around * height)),
                          min(image.width, round(x + (1 + around) * width)), min(image.height, round(y + (1 + around) * height))))
        out = BytesIO()
        cut.save(out, format="PNG")
        return out.getvalue()


class Looks:
    """The values a cell may hold, each with the looks it was seen to have. A look is named by the value of the
    nearest look kept, when that is within `near` and no other value has a look within `apart` further; else it
    is no value known (None): one not seen yet, or one caught changing."""

    def __init__(self, near: float = MERGE, apart: float = MERGE / 2) -> None:
        self.near, self.apart = near, apart
        self.kept: dict[Any, list[bytes]] = {}

    def __len__(self) -> int:
        return sum(len(looks) for looks in self.kept.values())

    def far(self, seen: bytes, value: Any) -> float:
        """How far a look is from the nearest look kept of a value; 255 when none is."""
        return min((distance(seen, other) for other in self.kept.get(value, ())), default=255.0)

    def name(self, seen: bytes) -> tuple[Any, float]:
        """The value a look is, or None, and how far the nearest look kept is."""
        ranked = sorted((self.far(seen, value), index) for index, value in enumerate(self.kept))
        if not ranked or ranked[0][0] > self.near:
            return None, ranked[0][0] if ranked else 255.0
        if len(ranked) > 1 and ranked[1][0] - ranked[0][0] < self.apart:
            return None, ranked[0][0]
        return list(self.kept)[ranked[0][1]], ranked[0][0]

    def add(self, value: Any, seen: bytes) -> None:
        """A look is this value's: kept unless one as good is, and no other value keeps a look that is the same
        (what was said of the screen now holds over what was said before)."""
        for other, looks in self.kept.items():
            if other != value:
                looks[:] = [kept for kept in looks if distance(seen, kept) > self.apart]
        looks = self.kept.setdefault(value, [])
        if all(distance(seen, kept) > self.near / 2 for kept in looks):
            looks.append(seen)
            del looks[:-LOOKS_MOST]
        for other in [other for other, looks in self.kept.items() if not looks]:
            del self.kept[other]
        while len(self.kept) > VALUES_MOST:
            del self.kept[next(iter(self.kept))]

    def to_json(self) -> list[list[Any]]:
        return [[value, [seen.hex() for seen in looks]] for value, looks in self.kept.items()]

    def restore(self, kept: Any) -> None:
        self.kept = {value: [bytes.fromhex(seen) for seen in looks] for value, looks in kept}


class Valued(Grid):
    """A reader for a board every cell of which holds one value of several (PLAN §20, X1): a cell is the value
    whose look it has. The looks are learned from a screen whose state is known (a model's reading of it), and
    after that from each cell left unread whose value something else tells: the rules, when every result the
    move can have says the same there; a light model shown the cell alone; a model shown the screen. A cell
    that looks like no value known is left unread (None), never guessed."""

    # the state's field that is the grid, and how a cell is looked at: nearly all of it (cells of some games
    # differ only at their edges), in more pixels than a cell that is only empty or not needs
    field = "grid"
    inset, side = 0.04, 12
    near, apart = MERGE, MERGE / 2
    # whether the rules say enough of a move's result for a screen to be taken as soon as it shows one
    foresees = True
    # whether a reading's count of rows and columns is checked against the looks of its cells (`survey`)
    surveys = False
    # a board box learned again within this many pixels of the one kept is the one kept
    SAME_BOX = 2
    # the kinds of unread cells put to a light model at most at once, and the pictures shown of each
    KINDS_MOST, SHOWN = 6, 2
    # how much of the cells around it a cell is shown to a light model with
    AROUND = 0.15

    def __init__(self, rows: int, cols: int) -> None:
        super().__init__(rows, cols)
        self.looks = Looks(self.near, self.apart)
        # values told by the rules and by a light model, not by a model that read the screen
        self.taught = {"rules": 0, "light": 0}

    # ── learning ──────────────────────────────────────────────────────

    def calibrate(self, png: bytes, board: list[int], state: dict[str, Any], sources: Any = None) -> bool:
        """Learn this screen from a reading of it: where the board is (the box said, fitted to the picture's
        own lines) and what each value read looks like. What was learned of the same board before is kept, and
        gives way where this reading says otherwise."""
        grid = state.get(self.field)
        if not self._shaped(grid):
            return False
        image = open_picture(png)
        fitted = fit(image, board, self.rows, self.cols)
        same = self.board is not None and self.size == list(image.size) and (all(
            abs(one - other) <= self.SAME_BOX for one, other in zip(fitted, self.board)) or self._still_reads(image, grid))
        if not same:
            self._place(image, fitted)
            self.looks = Looks(self.near, self.apart)
        for r, c, box in self._cells():
            if grid[r][c] is not None:
                self.looks.add(self._key(grid[r][c]), self._look(image, box))
        return len(self.looks) > 0

    def _still_reads(self, image: Any, grid: list[list[Any]]) -> bool:
        """Whether the board as it was learned still reads this screen as it was just read: most of its cells
        named, and named as said. A model places the same board a little differently each time it is asked, and
        what was learned of the board is not let go for that."""
        named = [(self.looks.name(self._look(image, box))[0], grid[r][c]) for r, c, box in self._cells() if grid[r][c] is not None]
        told = [(seen, said) for seen, said in named if seen is not None]
        return len(told) >= (1 - CHANGED_SHARE) * len(named) > 0 and all(seen == self._key(said) for seen, said in told)

    @staticmethod
    def _key(value: Any) -> Any:
        return value

    def teach(self, png: bytes, state: dict[str, Any], by: str = "rules") -> int:
        """The cells this screen leaves unread are what `state` has there: how many were learned."""
        image, grid = self._shown(png), state.get(self.field)
        if image is None or not self._shaped(grid):
            return 0
        learned = 0
        for r, c, box in self._cells():
            seen = self._look(image, box)
            if grid[r][c] is not None and self.looks.name(seen)[0] is None:
                self.looks.add(self._key(grid[r][c]), seen)
                learned += 1
        self.taught[by] = self.taught.get(by, 0) + learned
        return learned

    def excludes(self, png: bytes, state: dict[str, Any]) -> bool:
        """Whether a state cannot be what this screen shows: it has, at a cell the screen leaves unread, a
        value whose looks are known and none of which the cell has (a cell unlike every 2 seen is no 2)."""
        image, grid = self._shown(png), state.get(self.field)
        if image is None or not self._shaped(grid):
            return True
        for r, c, box in self._cells():
            seen = self._look(image, box)
            if self.looks.name(seen)[0] is None and self.looks.far(seen, self._key(grid[r][c])) > self.near \
                    and self._key(grid[r][c]) in self.looks.kept:
                return True
        return False

    def unread(self, png: bytes) -> list[tuple[tuple[int, int], bytes]]:
        """The cells a screen leaves unread, each with its look."""
        image = self._shown(png)
        if image is None:
            return []
        found = []
        for r, c, box in self._cells():
            seen = self._look(image, box)
            if self.looks.name(seen)[0] is None:
                found.append(((r, c), seen))
        return found

    def kinds(self, png: bytes) -> list[list[tuple[int, int]]]:
        """The cells a screen leaves unread, gathered by what they look like: one question each."""
        unread = self.unread(png)
        return [[unread[index][0] for index in kind] for kind in groups([seen for _, seen in unread], self.near)]

    def options(self) -> dict[str, str] | None:
        """What a cell may be, for a light model shown one: each value's key and what it looks like in words.
        None for a family whose cells a light model is not asked about."""
        return None

    def value(self, key: str) -> Any:
        """The value a light model's answer names."""
        return int(key)

    # ── a screen ──────────────────────────────────────────────────────

    def read(self, png: bytes, prior: dict[str, Any] | None = None, unmoved: dict[str, Any] | None = None
             ) -> tuple[dict[str, Any] | None, str | None]:
        """The state this screen shows, each cell the value whose look it has and None where it has none."""
        self.told = {}
        image = self._shown(png)
        if image is None or not len(self.looks):
            return None, "board_not_found"
        grid: list[list[Any]] = [[None] * self.cols for _ in range(self.rows)]
        for r, c, box in self._cells():
            grid[r][c] = self.looks.name(self._look(image, box))[0]
        self.told = {"unread": sum(cell is None for row in grid for cell in row)}
        return {self.field: grid, "score": None, "scene": "playing"}, None

    def survey(self, png: bytes, box: list[int]) -> tuple[list[int], int, int] | None:
        """The board's box, rows and columns as the cells whose looks are known lay it out (PLAN §23, Y1): of the
        counts near this reader's that divide `box`, the one under which most cells look like something known;
        then, a cell at a time, the lines beside it that hold known looks too (a reading that took a board for
        narrower than it is gave a box as narrow), and none at its edges that hold none. A board with no lines
        drawn between its cells is counted so, where `pitch` cannot. None when too few cells are known."""
        if not len(self.looks):
            return None
        image = open_picture(png)
        left, top, width, height = box

        def known(x: float, y: float, w: float, h: float) -> bool:
            return 0 <= x and 0 <= y and x + w <= image.width and y + h <= image.height \
                and self.looks.name(self._look(image, [x, y, w, h]))[0] is not None

        def share(rows: int, cols: int) -> float:
            w, h = width / cols, height / rows
            return sum(known(left + c * w, top + r * h, w, h) for r in range(rows) for c in range(cols)) / (rows * cols)

        def near(count: int) -> range:
            return range(max(GAUGE_FEWEST, count - SURVEY_SPREAD), min(SURVEY_MOST, count + SURVEY_SPREAD) + 1)
        # the columns with the rows as said, then the rows with the columns so found: the count said wins a tie
        cols = max(near(self.cols), key=lambda count: (share(self.rows, count), count == self.cols))
        rows = max(near(self.rows), key=lambda count: (share(count, cols), count == self.rows))
        if share(rows, cols) < 0.5:
            return None
        w, h = width / cols, height / rows
        # [first row, first column, rows, columns] in cells of the box said
        first_row, first_col = 0, 0

        def line(index: int, across: bool) -> int:
            """How many cells of a row (`across`) or a column at `index` (in cells from the box) look known."""
            if across:
                return sum(known(left + (first_col + c) * w, top + index * h, w, h) for c in range(cols))
            return sum(known(left + index * w, top + (first_row + r) * h, w, h) for r in range(rows))
        changed = True
        while changed:
            changed = False
            if cols < SURVEY_MOST and line(first_col - 1, False) >= SURVEY_LINE:
                first_col, cols, changed = first_col - 1, cols + 1, True
            if cols < SURVEY_MOST and line(first_col + cols, False) >= SURVEY_LINE:
                cols, changed = cols + 1, True
            if rows < SURVEY_MOST and line(first_row - 1, True) >= SURVEY_LINE:
                first_row, rows, changed = first_row - 1, rows + 1, True
            if rows < SURVEY_MOST and line(first_row + rows, True) >= SURVEY_LINE:
                rows, changed = rows + 1, True
        while cols > GAUGE_FEWEST and not line(first_col, False):
            first_col, cols = first_col + 1, cols - 1
        while cols > GAUGE_FEWEST and not line(first_col + cols - 1, False):
            cols -= 1
        while rows > GAUGE_FEWEST and not line(first_row, True):
            first_row, rows = first_row + 1, rows - 1
        while rows > GAUGE_FEWEST and not line(first_row + rows - 1, True):
            rows -= 1
        return [round(left + first_col * w), round(top + first_row * h), round(cols * w), round(rows * h)], rows, cols

    def same(self, state: dict[str, Any], said: dict[str, Any]) -> bool:
        """Whether a screen read here is read as a model read it."""
        return state.get(self.field) == said.get(self.field)

    def awaits(self, prior: dict[str, Any] | None) -> bool:
        return False

    def summary(self) -> dict[str, Any]:
        return {"values": len(self.looks.kept), "looks": len(self.looks), "taught": dict(self.taught),
                **({"counter": dict(self.ticks)} if self.counter else {})}

    # ── kept between games ────────────────────────────────────────────

    def to_json(self) -> dict[str, Any]:
        return {"rows": self.rows, "cols": self.cols, "size": self.size, "board": self.board, "looks": self.looks.to_json(),
                **({"counter": self.counter, "ticks": dict(self.ticks)} if self.counter else {})}

    def restore(self, kept: dict[str, Any]) -> bool:
        if (kept.get("rows"), kept.get("cols")) != (self.rows, self.cols) or not kept.get("board") or not kept.get("looks"):
            return False
        self.size, self.board = kept["size"], kept["board"]
        self.looks.restore(kept["looks"])
        if isinstance(kept.get("counter"), list):
            self.counter, self.ticks = kept["counter"], {"seen": 0, "missed": 0, **kept.get("ticks", {})}
        return True
