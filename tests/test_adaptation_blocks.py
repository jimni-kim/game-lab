"""A game played by dragging pieces onto a board, read and played with no profile (PLAN §10, G7): the model
reads the first moves and says where each piece waits, the solver picks the placements, and each drag is
carried with a look on the way, because the game draws a held piece away from the pointer. The screen is a
fake drawn from the family's own rules; its held piece hangs above the pointer, enlarged, as such games do."""
from functools import partial
from io import BytesIO
import json

from PIL import Image, ImageDraw
import pytest

from gamelab import adaptation, agent, carry, catalog, recovery
from gamelab.adaptation import Store, adopt
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult
from gamelab.surface import DRAG, HOLD, KEYS, TAP

SIZE = (700, 760)
# the board: 8 by 8 cells of 48 pixels; the tray below it: three slots of 5 by 5 cells of 24 pixels
BOARD, CELL = [150, 60, 384, 384], 48
TRAY, SLOT, SMALL = [150, 480, 384, 128], 128, 24
PAGE, EMPTY, FILLED, GHOST, PIECE = (24, 30, 60), (40, 52, 96), (236, 182, 49), (120, 110, 80), (90, 200, 120)
# how the game draws a held piece: at the board's scale, the point at this share of its box held this many
# cells from the pointer
ANCHOR, OFFSET = (0.5, 0.85), (0.0, -1.7)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(agent, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    monkeypatch.setattr(carry, "LIFT_S", 0.0)
    monkeypatch.setattr(carry, "REST_S", 0.0)
    monkeypatch.setattr(adaptation, "GeneratedAdapter", partial(
        adaptation.GeneratedAdapter, settling={"timeout_s": 0.4, "interval_s": 0.05, "stable_s": 0.1}))


def extent(piece):
    return max(r for r, _ in piece) + 1, max(c for _, c in piece) + 1


class Blocks:
    name = "fake"
    reads_dom = False

    def __init__(self, draw=3, kinds=(KEYS, TAP, DRAG, HOLD)):
        self.kinds = frozenset(kinds)
        self.family = catalog.family("block_placement")
        self.draw = draw
        self.state = self.family.initial_state(draw)
        self.held = None                 # (slot, pointer) while a piece is held
        self.placed, self.missed = [], 0

    def slot_origin(self, slot):
        return TRAY[0] + slot * SLOT + 4, TRAY[1] + 4

    def source(self, slot):
        rows, cols = extent(self.state["tray"][slot])
        x, y = self.slot_origin(slot)
        return [x, y, cols * SMALL, rows * SMALL]

    def _held_box(self):
        slot, (x, y) = self.held
        rows, cols = extent(self.state["tray"][slot])
        return x + OFFSET[0] * CELL - ANCHOR[0] * cols * CELL, y + OFFSET[1] * CELL - ANCHOR[1] * rows * CELL

    def _anchor(self):
        """The cell the held piece would snap to, when all of it lies on empty cells."""
        slot, _ = self.held
        left, top = self._held_box()
        anchor = [round((top - BOARD[1]) / CELL), round((left - BOARD[0]) / CELL)]
        action = {"kind": "place", "slot": slot, "anchor": anchor, "orientation": 0}
        return anchor if action in self.family.model.legal_actions(self.state, self.family.rules) else None

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "https://blocks.example/"

    async def elements(self, queries):
        return None

    async def capture(self, region=None):
        image = Image.new("RGB", SIZE, PAGE)
        draw = ImageDraw.Draw(image)
        ghost = self._anchor() if self.held else None
        for row, cells in enumerate(self.state["board"]):
            for col, value in enumerate(cells):
                x, y = BOARD[0] + col * CELL, BOARD[1] + row * CELL
                draw.rectangle((x + 2, y + 2, x + CELL - 3, y + CELL - 3), fill=FILLED if value else EMPTY)
        for slot, piece in enumerate(self.state["tray"]):
            if not piece or (self.held and self.held[0] == slot):
                continue
            x, y = self.slot_origin(slot)
            for r, c in piece:
                draw.rectangle((x + c * SMALL + 1, y + r * SMALL + 1, x + (c + 1) * SMALL - 2, y + (r + 1) * SMALL - 2), fill=PIECE)
        if self.held:
            piece = self.state["tray"][self.held[0]]
            if ghost:
                for r, c in piece:
                    x, y = BOARD[0] + (ghost[1] + c) * CELL, BOARD[1] + (ghost[0] + r) * CELL
                    draw.rectangle((x + 2, y + 2, x + CELL - 3, y + CELL - 3), fill=GHOST)
            left, top = self._held_box()
            for r, c in piece:
                draw.rectangle((left + c * CELL + 2, top + r * CELL + 2, left + (c + 1) * CELL - 3, top + (r + 1) * CELL - 3), fill=PIECE)
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    async def press(self, x, y):
        for slot, piece in enumerate(self.state["tray"]):
            sx, sy = self.slot_origin(slot)
            if piece and sx <= x < sx + 5 * SMALL and sy <= y < sy + 5 * SMALL:
                self.held = (slot, (x, y))

    async def move(self, x, y, duration_ms):
        if self.held:
            self.held = (self.held[0], (x, y))

    async def lift(self):
        if not self.held:
            return
        anchor, slot = self._anchor(), self.held[0]
        self.held = None
        if anchor is None:
            self.missed += 1
            return
        self.placed.append((slot, anchor))
        self.state = self.family.next_state(self.state, {"kind": "place", "slot": slot, "anchor": anchor, "orientation": 0},
                                            self.draw + len(self.placed))

    async def drag(self, x0, y0, x1, y1, duration_ms):
        await self.press(x0, y0)
        await self.move(x1, y1, duration_ms)
        await self.lift()

    async def tap(self, x, y, duration_ms):
        raise AssertionError("a drag game is never tapped")

    async def key(self, key):
        raise AssertionError("a drag game is given no key")

    async def release(self):
        self.held = None


class Dealer:
    """The model: it reads the fake's board and tray and says where each piece waits, a few pixels off."""

    def __init__(self, fake, modules=()):
        self.fake, self.modules, self.asked = fake, list(modules), []

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "examples" in context:
            self.asked.append(("write", context))
            return TurnResult(text=self.modules.pop(0))
        if "tried" in context:
            self.asked.append(("recover", context))
            return TurnResult(text=json.dumps({"situation": "a board in play", "action": {"kind": "none"}}))
        self.asked.append(("label", context))
        state = self.fake.state
        sources = [None if not piece else [value + shift for value, shift in zip(self.fake.source(slot), (-3, 2, 5, -2))]
                   for slot, piece in enumerate(state["tray"])]
        left, top, width, height = BOARD
        return TurnResult(text=json.dumps({
            "screen": "board", "board": [left - 3, top + 2, width + 4, height - 3], "rules": {"rows": 8, "cols": 8, "tray_size": 3},
            "state": {"board": state["board"], "tray": state["tray"], "score": None, "scene": "playing"}, "sources": sources}))


ADAPTER = """
from gamelab.sandbox.helpers import distance

BOARD, CELL = {board}, {cell}
TRAY, SLOT, SMALL = {tray}, {slot}, {small}
EMPTY, FILLED, PIECE, PAGE = {empty}, {filled}, {piece}, {page}

def measure(picture):
    if picture.width < TRAY[0] + TRAY[2] or picture.height < TRAY[1] + TRAY[3]:
        return None
    return {{"board": BOARD, "rows": 8, "cols": 8, "regions": [TRAY]}}

def origin(slot):
    return TRAY[0] + slot * SLOT + 4, TRAY[1] + 4

def read(picture, layout):
    left, top, width, height = layout["board"]
    board = []
    for row in range(8):
        cells = []
        for col in range(8):
            colour = picture.pixel(left + (col + 0.5) * CELL, top + (row + 0.5) * CELL)
            cells.append(1 if distance(colour, FILLED) <= 10 else (0 if distance(colour, EMPTY) <= 10 else None))
        board.append(cells)
    tray = []
    for slot in range(3):
        x, y = origin(slot)
        piece = [[r, c] for r in range(5) for c in range(5)
                 if distance(picture.pixel(x + (c + 0.5) * SMALL, y + (r + 0.5) * SMALL), PIECE) <= 10]
        tray.append(piece or None)
    return {{"board": board, "tray": tray, "score": None, "scene": "playing"}}

def target(action, state, layout):
    piece = state["tray"][action["slot"]]
    rows, cols = max(r for r, _ in piece) + 1, max(c for _, c in piece) + 1
    x, y = origin(action["slot"])
    left, top, width, height = layout["board"]
    row, col = action["anchor"]
    return {{"kind": "drag", "x0": x + (piece[0][1] + 0.5) * SMALL, "y0": y + (piece[0][0] + 0.5) * SMALL,
            "x1": left + (col + cols / 2 + SHIFT) * CELL, "y1": top + (row + rows / 2) * CELL, "ms": 500}}
"""


def source(shift=0):
    return ADAPTER.format(board=BOARD, cell=CELL, tray=TRAY, slot=SLOT, small=SMALL, empty=EMPTY, filled=FILLED,
                          piece=PIECE, page=PAGE).replace("SHIFT", str(shift))


def test_a_held_piece_is_found_by_what_changed_on_the_screen():
    async def frames():
        fake = Blocks()
        before = await fake.capture()
        box = fake.source(1)
        start = (box[0] + SMALL / 2, box[1] + SMALL / 2)
        await fake.press(*start)
        pointer = (start[0] + 10, start[1] - 30)
        await fake.move(*pointer, 0)
        return fake, before, await fake.capture(), start, pointer
    import asyncio
    fake, before, during, start, pointer = asyncio.run(frames())
    origin, held = carry.find(before, during, start, pointer)
    rows, cols = extent(fake.state["tray"][1])
    left, top = fake._held_box()
    assert all(abs(a - b) <= 3 for a, b in zip(origin, fake.source(1)))
    assert all(abs(a - b) <= 5 for a, b in zip(held, (left, top, cols * CELL, rows * CELL)))
    # a screen on which nothing was picked up shows nothing held
    assert carry.find(before, before, start, pointer) is None


async def test_a_drag_game_is_explored_adapted_and_played_with_every_piece_where_the_solver_put_it(tmp_path):
    fake = Blocks()
    dealer = Dealer(fake, [source()])
    played, made = await agent.play(fake, dealer, "block_placement", goal=GoalSpec("actions", 20), budget=Budget(40, 200, 120),
                                    store=Store(tmp_path))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    assert played.record["levels"] == {"L2": adaptation.EXPLORE_MOVES, "L3": 20 - adaptation.EXPLORE_MOVES}
    assert {move["effect"] for move in played.moves} == {"confirmed"} and len(fake.placed) == 20 and not fake.missed
    # each placement is the one the solver chose, although the piece hung 1.7 cells above the pointer
    assert [(move["action"]["slot"], move["action"]["anchor"]) for move in played.moves] == fake.placed
    assert all(move["carried"]["measured"] for move in played.moves)
    # the model was told which state field lists what is dragged, and the writer where each thing waited
    assert dealer.asked[0][1]["sources"] == "tray"
    write = next(context for kind, context in dealer.asked if kind == "write")
    assert all(len(example["sources"]) == 3 for example in write["examples"]) and write["controls"]["kind"] == "drag"
    kept = Store(tmp_path).load("https://blocks.example/", "block_placement")
    assert kept[0] == source().strip() and kept[2].looks[0].sources is not None


async def test_an_adapter_whose_drag_ends_a_cell_away_is_not_adopted():
    fake = Blocks()
    played, made = await agent.play(fake, Dealer(fake, [source(shift=1)] * adaptation.MAX_GENERATIONS), "block_placement",
                                    goal=GoalSpec("actions", 8), budget=Budget(40, 200, 120))
    assert played.record["outcome"] == "goal_reached" and played.record["levels"]["L3"] == 0, (played.record, made)
    assert made["generation_failures"][0]["reason"] == "no_adapter_passed" and "target" in made["generation_failures"][0]["detail"]
