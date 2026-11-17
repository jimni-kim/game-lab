"""Reading a match-three board by code written once for the family (PLAN §20, X4).

The rules ask only which cells hold the same kind of piece, so a piece's value is the number of its kind and
no name is needed: on the first screen a model read, the cells it called pieces are gathered by what they look
like, and each kind takes the number the model gave most of its cells (so that what the model read and what is
read here go together), or a free one. A model that calls two kinds one colour, or one kind two, is thereby set
right by the screen itself. Where the board has no cell (a board that is no rectangle) is where the first reading said so,
or a light model shown the place; one that shows a piece on a board at rest is a cell after all. A reading whose
pieces lay out a board of another count than it read is read again by that count (`grid.Valued.survey`).

What the rules ask of a cell is what it does (`rules.match3`): whether it is swapped, falls, lines up, has a
power. A look not learned yet is learned for what it does, in this order (`settle`):

    the rules    a move that lined up four or more leaves a piece with a power where the moved piece lies: a
                 look not known at that cell, once the board is at rest, is that piece's
    a light model, shown the cell alone: a piece with a power and of which kind, something fixed, something
                 that falls, or an ordinary piece of a colour not seen yet
    nobody       it is taken for a piece with a power of a kind not known: something worth using, which the game
                 will show to be otherwise when it does not take a move that uses it (`refused`)

Until then, and while a piece is still falling, such a cell reads as that last one.
"""
from __future__ import annotations

from typing import Any

from ..rules.match3 import AREA, BLOCK, COLOUR, COLOURS, EMPTY, LINE, LOOSE, POWER, POWERED, SEEKER, power_of
from .grid import CHANGED_SHARE, Looks, Valued, fit, groups, open_picture

# a reading agrees with a model's when this share of the cells both call pieces are gathered alike by both
ALIKE = 0.9
# moves refused that needed a kind swapped which no move ever moved, after which the kind is no piece
DOUBTS = 2
# a piece with a power of a kind nobody knows: what a look not learned yet reads as
UNKNOWN = POWERED * POWER
# what a light model may call a cell it is shown, and the value each is
ASKED = {"line": ("a piece with a power that clears a row or a column: a striped piece, a rocket", POWERED * LINE),
         "area": ("a piece with a power that clears what is around it: a wrapped piece, a bomb, TNT", POWERED * AREA),
         "colour": ("a piece with a power that clears every piece of one colour: a colour bomb, a rainbow or light ball", POWERED * COLOUR),
         "seeker": ("a piece with a power that flies to a target: a fish, a propeller, a paper plane", POWERED * SEEKER),
         "power": ("a piece with some other power", UNKNOWN),
         "fixed": ("something that is no piece and stays where it is: an obstacle, a box, a covered or locked cell", BLOCK),
         "loose": ("something that is no piece but falls like one", LOOSE),
         "piece": ("an ordinary piece like the others on the board, with no power", None),
         "none": ("no cell of the board: the background or the page around the board shows there", EMPTY)}


class Match3Kit(Valued):
    family_id = "match3"
    # a piece's middle: what shows between pieces (the cell behind them, a neighbour's glow) is left out
    inset, side = 0.3, 8
    # pieces of one kind are drawn a little differently from cell to cell (a gloss, a board fitted by the gaps
    # between pieces and so a few pixels out), and kinds differ by their whole colour: looser than a numeral needs
    # (2026-11-17, a game with no lines between its cells: at 6 its four kinds of candy came out as twelve)
    near, apart = 10.0, 5.0
    # what comes in from above is the game's: a screen is taken when it has come to rest, not as soon as it shows
    # what the rules foresee
    foresees = False
    # a board with no lines between its cells is counted by the looks of its pieces (`survey`)
    surveys = True

    def __init__(self, rules: dict[str, Any]) -> None:
        super().__init__(rules["rows"], rules["cols"])
        # where the board has no cell, as the reading it was learned from said: what shows there is the page
        # behind the game, which looks different from place to place and is nothing to learn
        self.holes: set[tuple[int, int]] = set()
        # what the game showed of each value a model called a piece: those a move it took moved (`took`), and
        # how often a move that needed one swapped was not taken (`refused`)
        self.proven: set[int] = set()
        self.doubted: dict[int, int] = {}

    def calibrate(self, png: bytes, board: list[int], state: dict[str, Any], sources: Any = None) -> bool:
        grid = state.get(self.field)
        if not self._shaped(grid):
            return False
        image = open_picture(png)
        self._place(image, fit(image, board, self.rows, self.cols))
        self.looks = Looks(self.near, self.apart)
        self.holes = {(r, c) for r in range(self.rows) for c in range(self.cols) if grid[r][c] == EMPTY}
        cells = [(grid[r][c], self._look(image, box)) for r, c, box in self._cells()
                 if grid[r][c] is not None and (r, c) not in self.holes]
        for value, seen in cells:
            if not self._plain(value):
                self.looks.add(value, seen)
        pieces = [(value, seen) for value, seen in cells if self._plain(value)]
        kinds = groups([seen for _, seen in pieces], self.near)
        free = [number for number in range(1, len(COLOURS) + 1)]
        for kind in kinds:
            said = [pieces[index][0] for index in kind]
            number = max(set(said), key=said.count)
            if number not in free:
                if not free:
                    break                    # more kinds than the rules have numbers for: the rest are no pieces
                number = free[0]
            free.remove(number)
            for index in kind:
                self.looks.add(number, pieces[index][1])
        return len(self.looks) > 0

    def read(self, png: bytes, prior: dict[str, Any] | None = None, unmoved: dict[str, Any] | None = None
             ) -> tuple[dict[str, Any] | None, str | None]:
        state, why = super().read(png, prior, unmoved)
        if state is None:
            return state, why
        grid = state[self.field]
        for r, c in self.holes:
            grid[r][c] = EMPTY
        unknown = sum(cell is None for row in grid for cell in row)
        if unknown > CHANGED_SHARE * (self.rows * self.cols - len(self.holes)):
            # most of the board looks like nothing learned: another level's board, or something over this one
            self.told = {"off": unknown}
            return None, "scene_changed"
        state[self.field] = [[UNKNOWN if cell is None else cell for cell in row] for row in grid]
        return state, why

    def unread(self, png: bytes) -> list[tuple[tuple[int, int], bytes]]:
        return [(cell, seen) for cell, seen in super().unread(png) if cell not in self.holes]

    @staticmethod
    def _plain(value: Any) -> bool:
        return type(value) is int and EMPTY < value <= len(COLOURS)

    # ── what a look not learned yet is ────────────────────────────────

    def settle(self, png: bytes, prior: dict[str, Any] | None) -> list[tuple[list[tuple[int, int]], list[bytes]]]:
        """A board at rest whose looks not learned yet are learned as far as the rules say: a cell where the
        move just played left a piece with a power (`prior`: what the rules say it left) is that piece. What is
        left is given back gathered by look, each kind with pictures of a few of its cells, to be named
        (`name`); nothing when every look is known."""
        image = self._shown(png)
        if image is None:
            return []
        # a place taken for one where the board has no cell that shows a piece on a board at rest is a cell (a
        # reading that called a cell at the board's edge none)
        for r, c, box in self._cells():
            if (r, c) in self.holes and self._plain(self.looks.name(self._look(image, box))[0]):
                self.holes.discard((r, c))
        told = prior.get(self.field) if isinstance(prior, dict) else None
        if self._shaped(told):
            for r, c, box in self._cells():
                seen = self._look(image, box)
                if (r, c) not in self.holes and self.looks.name(seen)[0] is None and told[r][c] is not None and 0 < power_of(told[r][c]) < POWER:
                    self.looks.add(told[r][c], seen)
                    self.taught["rules"] += 1
        kinds = self.kinds(png)
        return [(kind, [picture for cell in kind[:self.SHOWN]
                        if (picture := self.cell_picture(png, cell, self.AROUND)) is not None]) for kind in kinds]

    def asked(self) -> dict[str, str]:
        return {key: meaning for key, (meaning, _) in ASKED.items()}

    def name(self, png: bytes, cells: list[tuple[int, int]], key: str | None) -> Any:
        """The cells of one kind of look are what a light model called them (`key`, one of `asked`), or, when
        nobody could say, a piece with a power of a kind not known. The value they are from now on."""
        image = self._shown(png)
        if image is None:
            return None
        value = ASKED[key][1] if key in ASKED else UNKNOWN
        if value == EMPTY:
            # where the board has no cell: what shows there differs from place to place and is nothing to learn
            self.holes.update(tuple(cell) for cell in cells)
            self.taught["light"] = self.taught.get("light", 0) + len(cells)
            return value
        if key == "piece":
            free = [number for number in range(1, len(COLOURS) + 1) if number not in self.looks.kept]
            value = free[0] if free else UNKNOWN
        boxes = {(r, c): box for r, c, box in self._cells()}
        for cell in cells:
            self.looks.add(value, self._look(image, boxes[tuple(cell)]))
        self.taught["light" if key in ASKED else "nobody"] = self.taught.get("light" if key in ASKED else "nobody", 0) + len(cells)
        return value

    def took(self, png: bytes, cells: list[list[int]]) -> None:
        """The game took a move that swapped what stands at `cells` on this screen: those are pieces."""
        image = self._shown(png)
        if image is None:
            return
        boxes = {(r, c): box for r, c, box in self._cells()}
        for cell in cells:
            named = self.looks.name(self._look(image, boxes[tuple(cell)]))[0]
            if named is not None:
                self.proven.add(named)
                self.doubted.pop(named, None)

    def refused(self, png: bytes, cells: list[list[int]]) -> bool:
        """The game did not take a move that swapped what stands at `cells` on this screen. What was only taken
        for a piece with a power (its kind not known) is no piece to swap: it reads from now on as something
        that falls and is not swapped. And a kind a model called a piece that no move the game took ever moved
        is none either, once a move swapping it with a piece known to be one was refused, or two moves were (a
        model calls an obstacle by its colour as readily as a piece): it reads as something fixed. True when
        something was learned so."""
        image = self._shown(png)
        if image is None:
            return False
        boxes, learned = {(r, c): box for r, c, box in self._cells()}, False
        named = []
        for cell in cells:
            seen = self._look(image, boxes[tuple(cell)])
            value = self.looks.name(seen)[0]
            named.append(value)
            if value is None or value == UNKNOWN:
                self.looks.add(LOOSE, seen)
                learned = True
        plain = [value for value in named if self._plain(value) and value not in self.proven]
        for value in plain:
            self.doubted[value] = self.doubted.get(value, 0) + 1
            beside_proven = any(other in self.proven for other in named if other != value)
            if beside_proven or self.doubted[value] >= DOUBTS:
                self.looks.kept.setdefault(BLOCK, []).extend(self.looks.kept.pop(value, []))
                self.doubted.pop(value, None)
                learned = True
        return learned

    def to_json(self) -> dict[str, Any]:
        return {**super().to_json(), "holes": sorted(self.holes), "proven": sorted(self.proven)}

    def restore(self, kept: dict[str, Any]) -> bool:
        if not super().restore(kept):
            return False
        self.holes = {tuple(cell) for cell in kept.get("holes", [])}
        self.proven = set(kept.get("proven", []))
        return True
