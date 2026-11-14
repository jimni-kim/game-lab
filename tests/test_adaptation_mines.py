"""Making a site adapter at run time for a game played by taps on its cells (adaptive perception plan §8,
stage 5): the model reads the first moves, the solver picks the cells, the harness taps them, and the code
written is adopted only when its taps fall on the same cells. The screen here is a fake drawn from the
family's own rules with a fixed seed; the model is a fake that reads the fake's board and writes a hand-written
adapter."""
from functools import partial
from io import BytesIO
import json

from PIL import Image, ImageDraw
import pytest

from gamelab import adaptation, agent, catalog, recovery
from gamelab.adaptation import Adaptation, AdaptationFailed, Store, adopt, cell_box
from gamelab.agent import play as play_adaptive
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult
from gamelab.recovery import Recovery
from gamelab.rules.minesweeper import COVERED, EXPLODED, FLAG, MINE, is_open

RULES = {"rows": 9, "cols": 9, "mines": 10, "flags": False}
SIZE = (900, 700)
# the cells area: 9 by 9 cells of 48 pixels
BOARD = [150, 100, 432, 432]
PAGE, FRAME, LINE = (240, 240, 240), (120, 120, 120), (200, 200, 200)
COVERED_FACE, OPEN_FACE, EXPLODED_FACE = (170, 170, 170), (232, 232, 232), (220, 40, 40)
INKS = {1: (0, 0, 255), 2: (0, 128, 0), 3: (255, 0, 0), 4: (0, 0, 128), 5: (128, 0, 0), 6: (0, 128, 128),
        7: (0, 0, 0), 8: (128, 128, 128)}
# the marks on a covered cell: a mine shown, a flag
MINE_INK, FLAG_INK = (20, 20, 20), (200, 30, 30)
# the solver's moves on these seeds, by the rules alone (compute 2000): a game won in 11 moves, one lost at its
# fourth move; the centre cell is the solver's first guess
WINNING, LOST_EARLY, COMPUTE = 0, 180, 2000
MINED_CENTRE = [[0, 0], [0, 8], [2, 2], [4, 4], [5, 0], [6, 6], [7, 1], [8, 3], [8, 7], [8, 8]]


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    # the adapters here are the ones a model writes: the host's plain one is tried in test_plain.py
    monkeypatch.setattr(adaptation, "PLAIN_INSETS", ())
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(agent, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    # a short wait for a screen to settle: the fake is still at once
    monkeypatch.setattr(adaptation, "GeneratedAdapter", partial(
        adaptation.GeneratedAdapter, settling={"timeout_s": 0.4, "interval_s": 0.05, "stable_s": 0.1}))


def render(grid, covered=False, button=None):
    """A page with the grid drawn in its frame: each cell's face, a digit as a square of its ink at the middle,
    a mark as a square on a covered face; when `covered`, a notice over the board's upper part with `button`."""
    image = Image.new("RGB", SIZE, PAGE)
    draw = ImageDraw.Draw(image)
    left, top, width, height = BOARD
    draw.rectangle((left - 4, top - 4, left + width + 3, top + height + 3), fill=FRAME)
    across, down = width / 9, height / 9
    for row, cells in enumerate(grid):
        for col, value in enumerate(cells):
            x, y = left + col * across, top + row * down
            face = EXPLODED_FACE if value == EXPLODED else (OPEN_FACE if is_open(value) else COVERED_FACE)
            draw.rectangle((x, y, x + across - 1, y + down - 1), fill=face, outline=LINE)
            ink = INKS.get(value) if is_open(value) else {MINE: MINE_INK, FLAG: FLAG_INK}.get(value)
            if ink is not None:
                draw.rectangle((x + across * 0.3, y + down * 0.3, x + across * 0.7, y + down * 0.7), fill=ink)
    if covered:
        draw.rectangle((left - 20, top - 50, left + width + 20, top + height * 0.6), fill=(250, 250, 250))
        x, y, w, h = button
        draw.rectangle((x, y, x + w - 1, y + h - 1), fill=(40, 120, 220))
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class Mines:
    """A surface showing a minesweeper game played by the family's own rules from a seed."""
    name = "fake"
    reads_dom = False
    kinds = frozenset({"keys", "tap", "drag"})

    def __init__(self, draw=WINNING, mines=None):
        self.family = catalog.family("minesweeper").bind({"rules": RULES})
        self.draw = draw
        self.board = self.family.initial_state(draw)
        if mines is not None:
            self.board["mines"] = mines
        self.taps, self.keys = [], []
        # a notice over the board until its button (above the board) is tapped
        self.covered = False
        # how many taps on cells were played: a test may show the notice after some
        self.played = 0
        self.cover_after = None

    @property
    def grid(self):
        return self.family.shown(self.board)["grid"]

    @property
    def button(self):
        left, top, width, _ = BOARD
        return [left + width // 2 - 60, top - 45, 120, 36]

    def over(self):
        return not self.family.model.legal_actions(self.family.shown(self.board), RULES)

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "https://mines.example/"

    async def elements(self, queries):
        return None

    async def capture(self, region=None):
        return render(self.grid, self.covered, self.button)

    async def key(self, key):
        self.keys.append(key)

    async def tap(self, x, y, duration_ms):
        self.taps.append((x, y))
        left, top, width, height = BOARD
        bx, by, bw, bh = self.button
        if bx <= x < bx + bw and by <= y < by + bh:
            self.covered = False
            return
        if self.covered or not (left <= x < left + width and top <= y < top + height):
            return
        row, col = int((y - top) / (height / 9)), int((x - left) / (width / 9))
        if self.grid[row][col] != COVERED or self.over():
            return
        self.board = self.family.next_state(self.board, {"kind": "open", "cell": [row, col]}, self.draw)
        self.played += 1
        if self.cover_after is not None and self.played >= self.cover_after:
            self.covered, self.cover_after = True, None

    async def drag(self, x0, y0, x1, y1, duration_ms):
        raise AssertionError("a tap game is never dragged")

    async def release(self):
        pass


class Reader:
    """The model: it reads the screen as the fake draws it, writes each of `modules` in turn, and answers each
    of `recovering` when asked what is in the way (then gives up)."""

    def __init__(self, fake, modules=(), recovering=(), mines=lambda look: 10):
        self.fake, self.modules, self.recovering = fake, list(modules), list(recovering)
        self.asked = []
        # the count of mines it says each screen shows, by the number of the reading; None: the screen shows none
        self.mines = mines

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        images = len(request.messages[0].content) - 1
        if "examples" in context:
            self.asked.append(("write", context, images))
            return TurnResult(text=self.modules.pop(0))
        if "tried" in context:
            self.asked.append(("recover", context, images))
            action = self.recovering.pop(0) if self.recovering else {"kind": "give_up"}
            return TurnResult(text=json.dumps({"situation": "as drawn", "action": action}))
        self.asked.append(("label", context, images))
        if self.fake.covered:
            return TurnResult(text=json.dumps({"screen": "other", "board": None, "rules": None, "state": None}))
        left, top, width, height = BOARD
        grid = [list(row) for row in self.fake.grid]
        lost = any(cell == EXPLODED for row in grid for cell in row)
        scene = "over" if lost else ("won" if self.fake.over() else "playing")
        told = self.mines(sum(kind == "label" for kind, _, _ in self.asked) - 1)
        return TurnResult(text=json.dumps({
            "screen": "board", "board": [left - 3, top + 2, width + 4, height - 3],
            "rules": {"rows": 9, "cols": 9, **({} if told is None else {"mines": told})},
            "state": {"grid": grid, "score": None, "scene": scene}}))


ADAPTER = """
from gamelab.sandbox.helpers import distance, nearest

BOARD = {board}
FRAME = {frame}
FACES = {faces}
INKS = {inks}
MARKS = {marks}
NEAR = 8

def measure(picture):
    left, top, width, height = BOARD
    if picture.width < left + width or picture.height < top + height:
        return None
    if distance(picture.pixel(left - 2, top - 2), FRAME) > NEAR:
        return None
    return {{"board": BOARD, "rows": 9, "cols": 9}}

def cell(face, middle):
    kind, far = nearest(face, FACES)
    if far > NEAR:
        return None
    if kind == "exploded":
        return 12
    plain = distance(middle, face) <= NEAR
    if kind == "covered":
        if plain:
            return 9
        mark, far = nearest(middle, MARKS)
        return (11 if mark == "mine" else 10) if far <= NEAR else None
    if plain:
        return 0
    digit, far = nearest(middle, INKS)
    return int(digit) if far <= NEAR else None

def read(picture, layout):
    left, top, width, height = layout["board"]
    across, down = width / layout["cols"], height / layout["rows"]
    grid = []
    for row in range(layout["rows"]):
        cells = []
        for col in range(layout["cols"]):
            x, y = left + col * across, top + row * down
            cells.append(cell(picture.pixel(x + across * 0.15, y + down * 0.15), picture.pixel(x + across / 2, y + down / 2)))
        grid.append(cells)
    return {{"grid": grid, "score": None, "scene": "playing"}}

def target(action, state, layout):
    left, top, width, height = layout["board"]
    across, down = width / layout["cols"], height / layout["rows"]
    row, col = action["cell"]
    return {{"kind": "tap", "x": left + (col + 0.5) * across, "y": top + (row + 0.5) * down}}
"""
TARGET = 'return {"kind": "tap", "x": left + (col + 0.5) * across, "y": top + (row + 0.5) * down}'


def source(target=TARGET):
    text = ADAPTER.format(board=BOARD, frame=FRAME, inks={str(d): ink for d, ink in INKS.items()},
                          faces={"covered": COVERED_FACE, "open": OPEN_FACE, "exploded": EXPLODED_FACE},
                          marks={"mine": MINE_INK, "flag": FLAG_INK})
    assert TARGET in text
    return text.replace(TARGET, target)


def middle(box):
    left, top, width, height = box
    return [left + width / 2, top + height / 2]


def inside(point, box):
    left, top, width, height = box
    return left <= point[0] < left + width and top <= point[1] < top + height


async def test_the_first_moves_of_a_tap_game_are_tapped_on_the_cells_the_solver_picks():
    fake = Mines()
    reader = Reader(fake)
    trace = await Adaptation(reader, "minesweeper", compute=COMPUTE).explore(fake)
    assert len(trace.looks) == adaptation.EXPLORE_MOVES + 1 == len(fake.taps) + 1 and not fake.keys
    assert trace.rules == {"rows": 9, "cols": 9, "mines": 10} and trace.site == "https://mines.example/"
    for look, tapped in zip(trace.looks, fake.taps):
        assert look.action["kind"] == "open" and look.sent["kind"] == "tap" and (look.sent["x"], look.sent["y"]) == tapped
        # the tap went inside the cell the solver picked, on the board as it really lies
        assert inside(tapped, cell_box(BOARD, (9, 9), look.action["cell"]))
    assert trace.looks[-1].state["grid"] == fake.grid and trace.looks[-1].action is None
    assert [images for kind, _, images in reader.asked] == [1] + [2] * adaptation.EXPLORE_MOVES
    # every transition the exploring accepted is one the rules confirm, cells opened in a cascade included
    assert any(sum(is_open(cell) for row in look.state["grid"] for cell in row) > 1 for look in trace.looks)


async def test_a_tap_adapter_is_adopted_when_its_taps_fall_in_the_recorded_cells_and_refused_outside_them():
    fake = Mines()
    trace = await Adaptation(Reader(fake), "minesweeper", compute=COMPUTE).explore(fake)
    good = adopt(source(), trace)
    assert good["passed"], good["problems"]
    assert good["transitions"] == {"checked": adaptation.EXPLORE_MOVES, "confirmed": adaptation.EXPLORE_MOVES}
    # another point of the same cell plays the same move: the pixel need not be the one the harness tapped
    off_centre = source('return {"kind": "tap", "x": left + (col + 0.5) * across + 11, "y": top + (row + 0.5) * down - 9}')
    assert adopt(off_centre, trace)["passed"]
    for target in ('return {"kind": "tap", "x": left + (col + 1.5) * across, "y": top + (row + 0.5) * down}',
                   'return {"kind": "key", "key": "Enter"}',
                   'return {"kind": "tap", "x": 5, "y": 5}'):
        report = adopt(source(target), trace)
        assert not report["passed"] and report["failed_check"] == "target", target
        assert "inside that cell" in report["problems"][0]


async def test_a_tap_game_is_played_by_generated_code_to_its_end(tmp_path):
    fake = Mines()
    reader = Reader(fake, [source()])
    played, made = await play_adaptive(fake, reader, "minesweeper", goal=GoalSpec("play_until_end"),
                                       budget=Budget(60, COMPUTE, 300), store=Store(tmp_path))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "play_until_end"), (played.record, made)
    assert fake.over() and not any(cell == EXPLODED for row in fake.grid for cell in row)
    assert made["generations"] == 1 and made["relearned"] == 0 and not made.get("failed")
    # the first moves, read by the model, are moves of the same game; the rest are read by the code written from them
    assert len(fake.taps) == played.record["confirmed_actions"] and not fake.keys
    assert [move["level"] for move in played.moves[:adaptation.EXPLORE_MOVES + 1]] == ["L2"] * adaptation.EXPLORE_MOVES + ["L3"]
    assert played.record["levels"] == {"L2": adaptation.EXPLORE_MOVES, "L3": len(fake.taps) - adaptation.EXPLORE_MOVES}
    assert [kind for kind, _, _ in reader.asked] == ["label"] * (adaptation.EXPLORE_MOVES + 1) + ["write"]
    assert all(inside(tap, BOARD) for tap in fake.taps)
    assert Store(tmp_path).load("https://mines.example/", "minesweeper")[0] == source().strip()


async def test_a_mine_under_a_first_guess_ends_the_exploring_with_its_own_reason():
    """The first move of the game is a guess; when it opens a mine there is nothing to learn an adapter from,
    and no new game is started unasked: the exploring stops and says why. Played as a game, that is the game's
    own end, not a game that could not be played."""
    fake = Mines(mines=MINED_CENTRE)
    with pytest.raises(AdaptationFailed, match="game_ended"):
        await Adaptation(Reader(fake), "minesweeper", compute=COMPUTE).explore(fake)
    assert len(fake.taps) == 1 and fake.grid[4][4] == EXPLODED
    again = Mines(mines=MINED_CENTRE)
    played, made = await play_adaptive(again, Reader(again), "minesweeper", goal=GoalSpec("play_until_end"),
                                       budget=Budget(60, COMPUTE, 300))
    assert (played.record["outcome"], played.record["reason"]) == ("game_over", "terminal")
    assert played.record["confirmed_actions"] == 1 and len(again.taps) == 1 and made["generations"] == 0 and "failed" not in made


async def test_a_game_lost_after_enough_moves_still_gives_an_adapter_and_ends_as_the_game_ended(tmp_path):
    """A mine opened after the first few moves ends the exploring early with a screen of the lost game as its
    last look. Enough was seen to write the adapter, which must read that screen too; the game ends as the
    game's own end, and the adapter is written then and kept for the next game."""
    fake = Mines(draw=LOST_EARLY)
    trace = await Adaptation(Reader(fake), "minesweeper", compute=COMPUTE).explore(fake)
    assert len(trace.looks) == 5 and len(fake.taps) == 4 and trace.looks[-1].action is None
    assert any(cell == EXPLODED for row in trace.looks[-1].state["grid"] for cell in row) and trace.looks[-1].held
    assert adopt(source(), trace)["passed"]
    again = Mines(draw=LOST_EARLY)
    played, made = await play_adaptive(again, Reader(again, [source()]), "minesweeper", goal=GoalSpec("play_until_end"),
                                       budget=Budget(60, COMPUTE, 300), store=Store(tmp_path))
    assert (played.record["outcome"], played.record["reason"]) == ("game_over", "terminal"), (played.record, made)
    assert played.record["confirmed_actions"] == 4 == len(again.taps) and made["generations"] == 1
    assert played.record["levels"] == {"L2": 4, "L3": 0}
    assert Store(tmp_path).load("https://mines.example/", "minesweeper") is not None
    # the next game on the site is read by that adapter from its first move, and no model is asked
    third = Mines()
    silent = Reader(third)
    played, made = await play_adaptive(third, silent, "minesweeper", goal=GoalSpec("play_until_end"),
                                       budget=Budget(60, COMPUTE, 300), store=Store(tmp_path))
    assert (played.record["outcome"], made["reused"], silent.asked) == ("goal_reached", True, []), (played.record, made)
    assert played.record["levels"]["L2"] == 0


async def test_a_tap_on_the_cells_is_sent_as_the_model_asked_and_told_back_when_it_changed_nothing():
    """Asked what is in the way, the model may answer with a tap anywhere, the board's cells included: the
    prompt tells it such a tap is a move, and nothing in the code refuses one. A tap that changed nothing is told
    back with the next question, and the notice's own button then closes it."""
    fake = Mines()
    fake.covered = True
    reader = Reader(fake, recovering=[{"kind": "tap", "at": middle(BOARD)}, {"kind": "tap", "at": middle(fake.button)}])
    asked = Recovery(reader, "minesweeper")
    assert await asked.attempt(fake, "label_failed", {"state": None, "recent": []})
    assert fake.taps == [tuple(middle(BOARD))] and fake.covered and asked.log[-1]["changed"] is False
    assert await asked.attempt(fake, "label_failed", {"state": None, "recent": []})
    assert reader.asked[-1][1]["tried"] == [{"action": {"kind": "tap", "at": middle(BOARD)}, "screen_changed": False}]
    assert fake.taps[-1] == tuple(middle(fake.button)) and not fake.covered and fake.played == 0


async def test_a_notice_over_the_first_moves_of_a_tap_game_is_closed_and_the_exploring_goes_on():
    """A notice over the first moves is got past by the model's input, and the same game is explored on."""
    again = Mines()
    again.cover_after = 1
    reader = Reader(again, recovering=[{"kind": "tap", "at": middle(again.button)}])
    trace = await Adaptation(reader, "minesweeper", compute=COMPUTE, recovery=Recovery(reader, "minesweeper")).explore(again)
    assert len(trace.looks) == adaptation.EXPLORE_MOVES + 1 and again.taps[1] == tuple(middle(again.button))
    assert len(again.taps) == adaptation.EXPLORE_MOVES + 1 and again.played == adaptation.EXPLORE_MOVES
    assert [kind for kind, _, _ in reader.asked].count("recover") == 1


async def test_a_notice_during_play_of_a_tap_game_is_got_past_and_the_game_played_to_its_end(tmp_path):
    """Play by generated code stops at a notice over the board; a tap on the notice's button, above the board,
    lets the same game go on to its end."""
    fake = Mines()
    fake.cover_after = adaptation.EXPLORE_MOVES + 2
    reader = Reader(fake, [source()], recovering=[{"kind": "tap", "at": middle(fake.button)}])
    played, made = await play_adaptive(fake, reader, "minesweeper", goal=GoalSpec("play_until_end"),
                                       budget=Budget(60, COMPUTE, 300), store=Store(tmp_path))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "play_until_end"), (played.record, made)
    assert fake.over() and not fake.covered and made["recovery"]["restored"] == 1
    # every tap but the one on the button opened a cell; the move whose result the notice hid was played but
    # could not be confirmed
    assert fake.taps.count(tuple(middle(fake.button))) == 1
    assert len(fake.taps) == 1 + fake.played == 1 + played.record["confirmed_actions"] + 1
    # the model is asked once what the screen shows and once what to do about it
    assert [kind for kind, _, _ in reader.asked][adaptation.EXPLORE_MOVES + 2:] == ["label", "recover"]


@pytest.mark.parametrize(("mines", "kept"), [
    (lambda look: None, None),              # the screen shows no count
    (lambda look: look, None),              # a number that changes from screen to screen is a clock, not the count
    (lambda look: 10 if look else None, None),
    (lambda look: 0, None),                 # a value the family does not take
    (lambda look: 12, 12),                  # the same on every screen: taken, right or wrong
])
async def test_the_count_of_mines_is_unknown_unless_every_screen_read_says_the_same(mines, kept):
    """PLAN §10: a rule value the game may be played without knowing starts unknown, and the first moves are
    chosen without it; a value every screen of the exploring agrees on is kept for the play that follows."""
    fake = Mines()
    trace = await Adaptation(Reader(fake, mines=mines), "minesweeper", compute=COMPUTE).explore(fake)
    assert trace.rules == {"rows": 9, "cols": 9, "mines": kept}
    assert len(trace.looks) == adaptation.EXPLORE_MOVES + 1 and adopt(source(), trace)["passed"]


@pytest.mark.parametrize("mines", [lambda look: None, lambda look: 12, lambda look: 8])
async def test_a_tap_game_is_played_to_its_end_with_the_count_of_mines_unknown_or_wrong(mines, tmp_path):
    """The game has ten mines. Told none, two more or two fewer, play by generated code still opens every safe
    cell and ends as the game won, judged from the screen."""
    fake = Mines()
    played, made = await play_adaptive(fake, Reader(fake, [source()], mines=mines), "minesweeper",
                                       goal=GoalSpec("play_until_end"), budget=Budget(60, COMPUTE, 300), store=Store(tmp_path))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "play_until_end"), (played.record, made)
    assert fake.over() and not any(cell == EXPLODED for row in fake.grid for cell in row)
    assert json.loads(next(tmp_path.rglob("adapter.json")).read_text())["rules"]["mines"] == mines(0)
