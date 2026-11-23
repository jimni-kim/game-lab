"""A board of cells no family knows, told to the model that plays it as text (PLAN §36, session E2): the model
says where the grid is, the code gathers its cells by what they look like and gives each kind a letter, and the
model names the letters, reads the board from them and presses cells by row and column. The game here is a toy
nobody has rules for: a grid of lamps on a phone-sized picture, each tap on one turning it on."""
import base64
from io import BytesIO
import json

import pytest
from PIL import Image, ImageDraw

from gamelab import direct, recovery
from gamelab.agent import play
from gamelab.contracts import Budget, GoalSpec
from gamelab.lattice import Lattice
from gamelab.model import TurnResult

SIZE = (1080, 2400)
BOARD = [140, 800, 800, 800]
ROWS = COLS = 4
OFF, ON, WALL = (90, 90, 90), (250, 200, 0), (20, 20, 120)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(direct, "SETTLE_S", 0.0)
    monkeypatch.setattr(direct, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)


def drawn(cells, notice=False):
    image = Image.new("RGB", SIZE, (235, 235, 235))
    draw = ImageDraw.Draw(image)
    left, top, width, height = BOARD
    for r in range(ROWS):
        for c in range(COLS):
            x, y = left + c * width / COLS, top + r * height / ROWS
            draw.rectangle((x, y, x + width / COLS, y + height / ROWS), fill=cells[r][c], outline=(0, 0, 0), width=4)
    if notice:
        draw.rectangle((100, 700, 980, 1700), fill=(255, 255, 255), outline=(200, 0, 0), width=12)
        draw.ellipse((300, 900, 780, 1500), fill=(0, 160, 90))
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def scenery():
    """A picture with no board on it: its colours run from corner to corner."""
    across = Image.linear_gradient("L").resize(SIZE)
    out = BytesIO()
    Image.merge("RGB", (across, across.rotate(180), Image.radial_gradient("L").resize(SIZE))).save(out, format="PNG")
    return out.getvalue()


class Lamps:
    """A surface that is a picture alone: a 4 by 4 board of lamps, two of them walls, a tap on a lamp lights it."""
    name = "fake"
    reads_dom = False
    kinds = frozenset({"tap", "drag", "back"})

    def __init__(self):
        self.cells = [[OFF] * COLS for _ in range(ROWS)]
        self.cells[0][0] = self.cells[3][3] = WALL
        self.taps = []

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "android://lamps.example"

    async def capture(self, region=None):
        return drawn(self.cells)

    async def tap(self, x, y, duration_ms):
        self.taps.append((x, y))
        left, top, width, height = BOARD
        r, c = int((y - top) // (height / ROWS)), int((x - left) // (width / COLS))
        if 0 <= r < ROWS and 0 <= c < COLS and self.cells[r][c] == OFF:
            self.cells[r][c] = ON

    async def release(self):
        pass


class Reader:
    """A model that plays by the board's text: it says where the grid is, names the letters it is asked to, and
    lights every cell the rows show unlit, by row and column."""

    def __init__(self, scale, fake):
        self.scale, self.fake, self.asked, self.requests = scale, fake, [], []

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "families" in context:
            return TurnResult(text=json.dumps({"screen": "game", "families": [], "situation": "lamps"}))
        if not context.get("play"):
            return TurnResult(text=json.dumps({"screen": "other", "fits": False}))
        self.asked.append(context)
        self.requests.append(request)
        said = {"situation": "a board of lamps", "scene": "board", "expect": "lamps light"}
        cells = context.get("cells")
        if cells is None:
            # the grid, in the pixels of the picture shown, a few off as a model's box is
            box = [round(part * self.scale) for part in BOARD]
            return TurnResult(text=json.dumps({**said, "grid": {"box": [box[0] + 3, box[1] - 2, box[2] - 4, box[3] + 3],
                                                                "rows": ROWS, "cols": COLS},
                                               "action": {"kind": "tap", "at": [box[0] + box[2] * 3 / 8, box[1] + box[3] / 8]}}))
        # (what the cell it is pointed to shows in the picture)
        names = {letter: {OFF: "unlit", ON: "lit", WALL: "wall"}[self.fake.cells[cell[0]][cell[1]]]
                 for letter, cell in (cells.get("unnamed") or {}).items()}
        known = {**cells["names"], **names}
        unlit = [[r, c] for r, row in enumerate(cells["rows"]) for c, letter in enumerate(row) if known.get(letter) == "unlit"]
        if not unlit:
            return TurnResult(text=json.dumps({**said, "scene": "result", "names": names, "action": {"kind": "done", "end": "won"}}))
        action = {"kind": "sequence", "inputs": [{"kind": "tap", "cell": cell} for cell in unlit[:12]]} \
            if len(unlit) > 1 else {"kind": "tap", "cell": unlit[0]}
        return TurnResult(text=json.dumps({**said, "names": names, "action": action}))


def shown(request):
    with Image.open(BytesIO(base64.b64decode(request.messages[0].content[1]["source"]["data"]))) as image:
        return image.size


def test_a_board_is_read_as_letters_by_what_its_cells_look_like():
    cells = [[OFF] * COLS for _ in range(ROWS)]
    cells[0][0], cells[1][2] = WALL, ON
    board = Lattice.said(drawn(cells), {"box": [148, 793, 790, 806], "rows": ROWS, "cols": COLS})
    # the box is fitted to the picture's own lines, and each kind of look is a letter in the order it was met
    assert all(abs(one - other) <= 6 for one, other in zip(board.box, BOARD))
    assert board.read(drawn(cells)) == ["ABBB", "BBCB", "BBBB", "BBBB"]
    assert board.unnamed(["ABBB"]) == {"A": [0, 0], "B": [0, 1]} and board.at([1, 2]) == pytest.approx([640, 1100], abs=3)
    board.name({"A": "wall", "B": "unlit", "Z": "nothing", "C": 3})
    assert board.told(["ABBB", "BBCB"])["names"] == {"A": "wall", "B": "unlit"} and board.told(["AC"])["unnamed"] == {"C": [0, 1]}
    # a letter means the same on the next screen, and what a move changed is told by cells
    cells[2][1] = ON
    assert board.read(drawn(cells)) == ["ABBB", "BBCB", "BCBB", "BBBB"] and board.stands(drawn(cells))
    cells[1][2] = OFF
    assert board.changed(drawn([[OFF] * COLS for _ in range(ROWS)]), drawn(cells)) == {
        "count": 2, "cells": [[0, 0, "B", "A"], [2, 1, "B", "C"]]}
    # something over the board: it does not stand, and nothing is learned from that screen
    assert board.read(drawn(cells, notice=True)) is None and not board.stands(drawn(cells, notice=True))
    assert len(board.kinds) == 3 and board.changed(drawn(cells), drawn(cells, notice=True)) is None
    # a grid said with a column too few, or over no board at all, shows too many kinds and is none
    assert Lattice.said(scenery(), {"box": BOARD, "rows": 8, "cols": 8}) is None
    assert Lattice.said(drawn(cells), {"box": [0, 0, 99999, 10], "rows": 4, "cols": 4}) is None
    assert Lattice.said(drawn(cells), {"box": BOARD, "rows": 1, "cols": 4}) is None


async def test_the_model_is_given_the_board_as_text_and_presses_cells_by_row_and_column():
    fake = Lamps()
    scale = recovery.VIEW_SIDE / SIZE[1]
    model = Reader(scale, fake)
    played, made = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(40, 1, 60), way="direct")
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "won"), (played.record, made)
    assert all(cell != OFF for row in fake.cells for cell in row)
    # the first question has no board as text and asks for the grid; the first tap went by the picture
    first, second, third = model.asked[:3]
    assert "cells" not in first and "The program then reads the cells itself" in model.requests[0].system_stable
    assert shown(model.requests[0])[1] == recovery.VIEW_SIDE
    # the second has the rows, with the letters still to name and the picture at full size
    assert second["cells"]["rows"] == ["ACBB", "BBBB", "BBBB", "BBBA"] and second["cells"]["names"] == {}
    assert second["cells"]["unnamed"] == {"A": [0, 0], "C": [0, 1], "B": [0, 2]}
    assert shown(model.requests[1])[1] == recovery.VIEW_SIDE and "The context has `cells`" in model.requests[1].system_stable
    # the cells it named by row and column were pressed at their middles, twelve at an answer
    left, top, width, height = BOARD
    assert fake.taps[1] == pytest.approx((left + 2.5 * width / COLS, top + 0.5 * height / ROWS), abs=4)
    assert len(fake.taps) == 14 and played.record["confirmed_actions"] == 14
    assert third["history"][-1]["action"]["inputs"][0] == {"kind": "tap", "cell": [0, 2]} and third["history"][-1]["cells_changed"]["count"] == 12
    assert third["history"][-1]["cells_changed"]["cells"][0] == [0, 2, "B", "C"]
    # once every letter has a name the picture is small, and with the board standing as told the instructions
    # leave out what only other screens need
    assert third["cells"]["names"] == {"A": "wall", "B": "unlit", "C": "lit"} and "unnamed" not in third["cells"]
    assert shown(model.requests[2])[1] == direct.SMALL_SIDE and "look" in third["inputs"]
    plain, full = model.requests[2].system_stable, model.requests[1].system_stable
    assert len(plain) < 0.8 * len(full) and "`choice`" in full and "`choice`" not in plain and '"cell": [row, column]' in plain
    assert "[unless" not in plain and "[when" not in plain and "[unless" not in full
    assert made["direct"]["lattice"]["kinds"] == 3 and made["direct"]["lattice"]["told"] == len(model.asked) - 1


async def test_the_board_and_the_names_of_its_kinds_are_kept_for_the_next_work():
    fake = Lamps()
    first = direct.Direct(model := Reader(recovery.VIEW_SIDE / SIZE[1], fake))
    goal, kinds = GoalSpec("play_until_end").to_json(), frozenset({"tap", "drag"})
    await first.ask(await fake.capture(), kinds, goal, {})
    await first.ask(await fake.capture(), kinds, goal, {})
    kept = json.loads(json.dumps(first.lasting()))
    assert kept["grid"]["names"] == {"A": "wall", "B": "unlit"} and "grid" not in first.memory
    # the next work is given the board as text at its first question, small picture and all
    again = direct.Direct(model, notes=kept)
    answer = await again.ask(await fake.capture(), kinds, goal, {})
    assert model.asked[-1]["cells"]["rows"] == ["ABBB", "BBBB", "BBBB", "BBBA"] and "unnamed" not in model.asked[-1]["cells"]
    assert shown(model.requests[-1])[1] == direct.SMALL_SIDE and answer["told"]["inputs"][0]["cell"] == [0, 1]
    # on another screen the board kept does not stand: the picture alone, and a grid is asked for again
    await direct.Direct(model, notes=kept).ask(scenery(), kinds, goal, {})
    assert "cells" not in model.asked[-1] and shown(model.requests[-1])[1] == recovery.VIEW_SIDE
    assert direct.Direct(model, notes={"grid": {"rows": 4}}).lattice is None


async def test_a_grid_that_reads_as_no_board_is_told_and_a_page_that_can_be_read_is_not_asked_for_one():
    class Wrong:
        def __init__(self):
            self.asked = []

        async def complete(self, request):
            self.asked.append(json.loads(request.messages[0].content[0]["text"]))
            return TurnResult(text=json.dumps({"situation": "s", "scene": "board", "expect": "e",
                                               "grid": {"box": [5, 5, 600, 1400], "rows": 9, "cols": 7},
                                               "action": {"kind": "tap", "at": [300, 300]}}))
    model, goal = Wrong(), GoalSpec("play_until_end").to_json()
    player = direct.Direct(model)
    kinds = frozenset({"tap", "drag"})
    assert (await player.ask(scenery(), kinds, goal, {}))["action"]["kind"] == "tap"
    await player.ask(scenery(), kinds, goal, {})
    assert "grid_refused" not in model.asked[0] and "grid_refused" in model.asked[1] and player.lattice is None
    # where the page's own text is given, no grid is asked for and none is taken
    outline = {"size": list(SIZE), "drawn": 0.0, "cut": False,
               "items": [{"at": [640, 400], "size": [100, 50], "tag": "div", "text": "go", "label": "", "classes": [], "bg": "", "flags": []}] * 3}
    other = direct.Direct(model)
    await other.ask(drawn([[OFF] * COLS for _ in range(ROWS)]), kinds, goal, {}, outline=outline)
    assert other.lattice is None and "elements" in model.asked[2]
