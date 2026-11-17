"""A swap-and-match game read and played with no profile (PLAN §10, G8): the model reads the first moves, the
solver picks the swaps, each is dragged from one cell onto its neighbour, and the code written is adopted only
when its drags run between the same cells. The screen is a fake drawn from the family's own rules."""
from functools import partial
from io import BytesIO
import json

from PIL import Image, ImageDraw
import pytest

from gamelab import adaptation, agent, catalog, recovery
from gamelab.adaptation import Store
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult
from gamelab.rules import match3 as rules
from gamelab.surface import DRAG, KEYS, TAP

SIZE = (700, 600)
BOARD, CELL = [150, 80, 384, 384], 48
PAGE, WELL = (30, 30, 46), (52, 52, 70)
INKS = {1: (220, 50, 50), 2: (240, 150, 40), 3: (240, 220, 60), 4: (60, 190, 90), 5: (60, 110, 230), 6: (150, 80, 210)}


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    # the adapters here are the ones a model writes: the host's plain one is tried in test_plain.py
    monkeypatch.setattr(adaptation, "PLAIN_INSETS", ())
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(agent, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "GeneratedAdapter", partial(
        adaptation.GeneratedAdapter, settling={"timeout_s": 0.4, "interval_s": 0.05, "stable_s": 0.1}))


class Gems:
    name = "fake"
    reads_dom = False
    kinds = frozenset({KEYS, TAP, DRAG})

    def __init__(self, draw=5, powers=False, rules=None):
        # `powers`: whether the game leaves a piece with a power where four or more lined up (drawn with a white
        # mark, larger for each kind); a game without them removes such a line whole. `rules`: the ways of
        # moving this game takes (the family's rule values)
        self.family = catalog.family("match3").bind({"rules": rules or {"square": False, "swap_power": False, "pair_power": False, "tap_power": False}})
        self.draw, self.powers = draw, powers
        self.state = self.family.initial_state(draw)
        self.swaps, self.refused, self.pops = [], 0, []

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "https://gems.example/"

    async def elements(self, queries):
        return None

    async def capture(self, region=None):
        image = Image.new("RGB", SIZE, PAGE)
        draw = ImageDraw.Draw(image)
        for row, cells in enumerate(self.state["grid"]):
            for col, value in enumerate(cells):
                x, y = BOARD[0] + col * CELL, BOARD[1] + row * CELL
                draw.rectangle((x, y, x + CELL - 1, y + CELL - 1), fill=WELL)
                draw.ellipse((x + 5, y + 5, x + CELL - 6, y + CELL - 6), fill=INKS.get(rules.colour_of(value), (120, 120, 120)))
                if kind := rules.power_of(value):
                    draw.rectangle((x + 14, y + 14, x + 16 + 4 * kind, y + 34), fill=(255, 255, 255))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    def _cell(self, x, y):
        return [int((y - BOARD[1]) // CELL), int((x - BOARD[0]) // CELL)]

    async def drag(self, x0, y0, x1, y1, duration_ms):
        one, other = sorted([self._cell(x0, y0), self._cell(x1, y1)])
        self._play({"kind": "swap", "cell": one, "to": other})

    def _play(self, action):
        if action not in self.family.model.legal_actions(self.state, self.family.rules):
            self.refused += 1                # the game swaps the two back
            return
        (self.swaps if action["kind"] == "swap" else self.pops).append(action)
        self.state = self.family.next_state(self.state, action, self.draw + len(self.swaps) + len(self.pops))
        if not self.powers:
            self.state = {**self.state, "grid": self._plain(self.state["grid"])}

    def _plain(self, grid):
        """A game with no powers: where the rules leave one, a piece of a colour that lines nothing up."""
        grid = [list(row) for row in grid]
        for r, row in enumerate(grid):
            for c, value in enumerate(row):
                if rules.power_of(value):
                    for colour in INKS:
                        grid[r][c] = colour
                        if not rules._lines_through(grid, (r, c)):
                            break
        return grid

    async def tap(self, x, y, duration_ms):
        if not self.powers:
            raise AssertionError("a swap is dragged, not tapped")
        self._play({"kind": "pop", "cell": self._cell(x, y)})

    async def key(self, key):
        raise AssertionError("a swap game is given no key")

    async def release(self):
        pass


class Jeweller:
    """The model: it reads the fake's grid by colour."""

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
        left, top, width, height = BOARD
        return TurnResult(text=json.dumps({
            "screen": "board", "board": [left - 3, top + 2, width + 4, height - 3], "rules": {"rows": 8, "cols": 8},
            "state": {"grid": self.fake.state["grid"], "score": None, "scene": "playing"}}))


ADAPTER = """
from gamelab.sandbox.helpers import nearest

BOARD, CELL, INKS = {board}, {cell}, {inks}

def measure(picture):
    if picture.width < BOARD[0] + BOARD[2] or picture.height < BOARD[1] + BOARD[3]:
        return None
    return {{"board": BOARD}}

def read(picture, layout):
    left, top, width, height = layout["board"]
    grid = []
    for row in range(8):
        cells = []
        for col in range(8):
            colour, far = nearest(picture.pixel(left + (col + 0.5) * CELL, top + (row + 0.5) * CELL), INKS)
            cells.append(int(colour) if far <= 12 else None)
        grid.append(cells)
    return {{"grid": grid, "score": None, "scene": "playing"}}

def target(action, state, layout):
    left, top, width, height = layout["board"]
    (row, col), (to_row, to_col) = action["cell"], action[TO]
    return {{"kind": "drag", "x0": left + (col + 0.5) * CELL, "y0": top + (row + 0.5) * CELL,
            "x1": left + (to_col + 0.5) * CELL, "y1": top + (to_row + 0.5) * CELL, "ms": 250}}
"""


def source(to='"to"'):
    return ADAPTER.format(board=BOARD, cell=CELL, inks={str(value): ink for value, ink in INKS.items()}).replace("TO", to)


async def test_a_swap_game_is_explored_adapted_and_played_by_drags_between_cells(tmp_path):
    fake = Gems()
    jeweller = Jeweller(fake, [source()])
    played, made = await agent.play(fake, jeweller, "match3", goal=GoalSpec("actions", 20), budget=Budget(40, 200, 120),
                                    store=Store(tmp_path))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    assert played.record["levels"] == {"L2": adaptation.EXPLORE_MOVES, "L3": 20 - adaptation.EXPLORE_MOVES}
    # every drag was a swap the rules allow, the one the solver chose
    assert not fake.refused and [move["action"] for move in played.moves] == fake.swaps
    assert "sources" not in jeweller.asked[0][1] and all("carried" not in move for move in played.moves)
    assert Store(tmp_path).load("https://gems.example/", "match3")[0] == source().strip()


async def test_an_adapter_whose_drag_does_not_reach_the_neighbour_is_not_adopted():
    fake = Gems()
    played, made = await agent.play(fake, Jeweller(fake, [source('"cell"')] * adaptation.MAX_GENERATIONS), "match3",
                                    goal=GoalSpec("actions", 8), budget=Budget(40, 200, 120))
    assert played.record["outcome"] == "goal_reached" and played.record["levels"]["L3"] == 0, (played.record, made)
    assert "target" in made["generation_failures"][0]["detail"]
