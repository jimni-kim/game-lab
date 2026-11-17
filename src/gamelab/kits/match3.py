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

from ..rules.match3 import AREA, BLOCK, COLOUR, COLOURS, EMPTY, LINE, LOOSE, POWER, POWERED, SEEKER, UNDER, power_of
from .grid import CHANGED_SHARE, Looks, Valued, distance, fit, groups, look, open_picture

# a reading agrees with a model's when this share of the cells both call pieces are gathered alike by both
ALIKE = 0.9
# moves refused that needed a kind swapped which no move ever moved, after which the kind is no piece
DOUBTS = 2
# a goal's picture is the value of the cells it looks like (PLAN §23, Y2): it is compared cut this share in from
# each side (a picture drawn larger or smaller than a cell), and is that value when it is within this many times
# `near` of its looks and no other value's are within `apart` further
GOAL_INSETS, GOAL_NEAR = (0.2, 0.3, 0.4), 1.8
GOAL_KINDS = ("collect", "bring_down", "other")
# what lies under a cell's piece (`under`) is told by the cell's corners, where the piece does not reach: a square
# this share of a cell a side, this share in from each corner, each made this many pixels a side; corners nearer
# than UNDER_NEAR (mean difference a channel, of 255) are one look
UNDER_SIDE, UNDER_IN, UNDER_PIXELS, UNDER_NEAR = 0.14, 0.03, 2, 12.0
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
        # how many layers lie under a cell's piece, by what its corners look like, when the reading learned from
        # said some cell had any (`under`); None for a board with none
        self.unders: Looks | None = None

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
        under = state.get("under")
        self.unders = None
        if self._shaped(under) and any(isinstance(cell, int) and cell > 0 for row in under for cell in row):
            self.unders = Looks(UNDER_NEAR, UNDER_NEAR / 2)
            for r, c, box in self._cells():
                if (r, c) not in self.holes and isinstance(under[r][c], int):
                    self.unders.add(under[r][c], self._corners(image, box))
        return len(self.looks) > 0

    def _corners(self, image: Any, box: list[float]) -> bytes:
        """What a cell's four corners look like, where what lies under its piece shows."""
        x, y, w, h = box
        side, inward = UNDER_SIDE * w, UNDER_IN * w
        found = b""
        for left, top in ((x + inward, y + inward), (x + w - inward - side, y + inward),
                          (x + inward, y + h - inward - side), (x + w - inward - side, y + h - inward - side)):
            found += image.crop((round(left), round(top), round(left + side), round(top + side))).resize(
                (UNDER_PIXELS, UNDER_PIXELS)).tobytes()
        return found

    def _under(self, image: Any, prior: dict[str, Any] | None, unmoved: dict[str, Any] | None) -> list[list[int]]:
        """How many layers lie under each cell's piece by its corners. A corner not seen before is what the rules
        say the move just played left there (`prior`), and when they say it took a layer off, it is learned as that
        many from now on; with nothing to say, as many as it had."""
        told = prior.get("under") if isinstance(prior, dict) and self._shaped(prior.get("under")) else None
        was = unmoved.get("under") if isinstance(unmoved, dict) and self._shaped(unmoved.get("under")) else None
        under = [[0] * self.cols for _ in range(self.rows)]
        for r, c, box in self._cells():
            if (r, c) in self.holes:
                continue
            seen = self._corners(image, box)
            value = self.unders.name(seen)[0]
            if value is None:
                value = told[r][c] if told is not None else (was[r][c] if was is not None else 0)
                if told is not None and was is not None and told[r][c] < was[r][c]:
                    self.unders.add(value, seen)
                    self.taught["rules"] += 1
            under[r][c] = value
        return under

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
        if self.unders is not None:
            image = self._shown(png)
            if image is not None:
                state["under"] = self._under(image, prior, unmoved)
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

    def struck(self, before: bytes, after: bytes, cells: list[list[int]]) -> bool | None:
        """Whether things fixed the rules say a move's lines hit beside them look otherwise once the board is at
        rest (PLAN §23, Y3): one hit and broken a layer shows its next layer, or what took its place. A look not
        known that one now shows is that thing's next layer, learned so. None when nothing tells."""
        one, other = self._shown(before), self._shown(after)
        if one is None or other is None or not cells:
            return None
        boxes = {(r, c): box for r, c, box in self._cells()}
        changed = False
        for cell in cells:
            box = boxes.get(tuple(cell))
            if box is None or tuple(cell) in self.holes:
                continue
            was, now = self._look(one, box), self._look(other, box)
            if distance(was, now) <= self.near:
                continue
            changed = True
            if self.looks.name(now)[0] is None:
                self.looks.add(BLOCK, now)
                self.taught["rules"] += 1
        return changed

    # ── a level's goals ───────────────────────────────────────────────

    def goals(self, png: bytes, listed: Any) -> list[dict[str, Any]] | None:
        """A level's goals as a model listed them beside the board (each its kind, its count and the box of its
        picture), each with the value of the cells its picture looks like (`target`), or None when it looks like
        none clearly: what a goal is about is told by the looks the board was learned by, not by what a model
        calls it. None when the list is no list of goals."""
        image = self._shown(png)
        if image is None or not isinstance(listed, list):
            return None
        layered = self.unders is not None and any(value for value in self.unders.kept if value)
        found = []
        for thing in listed:
            if not isinstance(thing, dict) or thing.get("kind") not in GOAL_KINDS:
                continue
            count = thing.get("count")
            count = count if type(count) is int and count >= 0 else None
            target = self._pictured(image, thing["box"]) if thing["kind"] != "other" else None
            if target is None and thing["kind"] == "collect" and layered:
                # a goal whose picture is like no cell's on a board with layers under its pieces is about those
                target = UNDER
            found.append({"kind": thing["kind"], "target": target, "count": count})
        return found

    def _pictured(self, image: Any, box: list[int]) -> int | None:
        """The value whose looks a picture beside the board has, at whichever cut of it is nearest."""
        far: dict[Any, float] = {}
        for inset in GOAL_INSETS:
            seen = look(image, box, inset, self.side)
            for value in self.looks.kept:
                far[value] = min(far.get(value, 255.0), self.looks.far(seen, value))
        ranked = sorted((distance_, value) for value, distance_ in far.items() if value != EMPTY)
        if not ranked or ranked[0][0] > GOAL_NEAR * self.near:
            return None
        if len(ranked) > 1 and ranked[1][0] - ranked[0][0] < self.apart:
            return None
        return ranked[0][1]

    def to_json(self) -> dict[str, Any]:
        return {**super().to_json(), **({"unders": self.unders.to_json()} if self.unders is not None else {}), "holes": sorted(self.holes), "proven": sorted(self.proven)}

    def restore(self, kept: dict[str, Any]) -> bool:
        if not super().restore(kept):
            return False
        self.holes = {tuple(cell) for cell in kept.get("holes", [])}
        if kept.get("unders"):
            self.unders = Looks(UNDER_NEAR, UNDER_NEAR / 2)
            self.unders.restore(kept["unders"])
        self.proven = set(kept.get("proven", []))
        return True
