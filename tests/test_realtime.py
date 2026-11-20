"""A game in which time moves, played by code alone after one question (PLAN §29): the well is read each tick,
the falling piece told from the stack, and keys sent towards the place the code chose. The game here is a toy
drawn for the test: pieces fall a row every few captures whether or not anything is pressed, it turns them its own
way, and it has no key that drops a piece at once."""
from io import BytesIO
import json

from PIL import Image, ImageDraw

from gamelab import realtime
from gamelab.agent import play
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult
from gamelab.realtime import Cells, best, fall, normal, settle, turns, understood

ROWS, COLS, CELL = 12, 6, 20
LEFT, TOP = 100, 40
SIZE = (400, 320)
BOX = [LEFT, TOP, LEFT + COLS * CELL, TOP + ROWS * CELL]
# the pieces as they come, each as the game first shows it
PIECES = [[(0, 0), (0, 1), (1, 0), (1, 1)], [(0, 0), (0, 1), (0, 2), (0, 3)], [(0, 0), (1, 0), (1, 1), (1, 2)],
          [(0, 1), (1, 0), (1, 1), (1, 2)], [(0, 0), (0, 1), (1, 1), (1, 2)]]
COLOURS = [(230, 200, 20), (20, 200, 220), (30, 60, 220), (160, 30, 160), (220, 40, 40)]
EMPTY = (20, 24, 40)
ANSWER = {"board": BOX, "columns": COLS, "rows": ROWS,
          "keys": {"left": "ArrowLeft", "right": "ArrowRight", "rotate": "ArrowUp", "down": "ArrowDown", "drop": None},
          "start": {"kind": "key", "key": "Space"}, "situation": "an empty well"}


class Falling:
    """The toy as a surface. A piece falls a row every `every` captures. It ends when a piece has no room."""
    name = "fake"
    reads_dom = False
    kinds = frozenset({"keys", "tap"})

    def __init__(self, every=3, started=False):
        self.every, self.playing, self.over = every, False, False
        self.stack, self.piece, self.colour = {}, None, None
        self.count = self.captures = self.cleared = 0
        self.keys = []
        if started:
            self._start()

    def _start(self):
        self.playing = True
        self._spawn()

    def _spawn(self):
        cells = PIECES[self.count % len(PIECES)]
        self.colour = COLOURS[self.count % len(COLOURS)]
        self.count += 1
        self.piece = [(r, c + 1) for r, c in cells]
        if not self._fits(self.piece):
            self.piece, self.playing, self.over = None, False, True

    def _fits(self, cells):
        return all(0 <= c < COLS and r < ROWS and (r, c) not in self.stack for r, c in cells)

    def _down(self):
        moved = [(r + 1, c) for r, c in self.piece]
        if self._fits(moved):
            self.piece = moved
            return
        for cell in self.piece:
            self.stack[cell] = self.colour
        full = [r for r in range(ROWS) if all((r, c) in self.stack for c in range(COLS))]
        self.cleared += len(full)
        self.stack = {(r + sum(row > r for row in full), c): colour for (r, c), colour in self.stack.items() if r not in full}
        self._spawn()

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "https://falling.example/"

    async def elements(self, queries):
        return None

    async def release(self):
        pass

    async def capture(self, region=None):
        if self.playing:
            self.captures += 1
            if self.captures % self.every == 0:
                self._down()
        image = Image.new("RGB", SIZE, (240, 240, 240))
        draw = ImageDraw.Draw(image)
        draw.rectangle([BOX[0] - 2, BOX[1] - 2, BOX[2] + 1, BOX[3] + 1], fill=(0, 0, 0))
        draw.rectangle([BOX[0], BOX[1], BOX[2] - 1, BOX[3] - 1], fill=EMPTY)
        shown = dict(self.stack) | ({cell: self.colour for cell in self.piece} if self.piece else {})
        for (r, c), colour in shown.items():
            if r >= 0:
                draw.rectangle([LEFT + c * CELL + 1, TOP + r * CELL + 1, LEFT + (c + 1) * CELL - 2, TOP + (r + 1) * CELL - 2],
                               fill=colour)
        if region is not None:
            image = image.crop(tuple(region))
        out = BytesIO()
        image.save(out, "PNG")
        return out.getvalue()

    async def tap(self, x, y, duration_ms):
        pass

    async def key(self, key):
        self.keys.append(key)
        if key == "Space" and not self.playing and not self.over:
            self._start()
        if not self.playing:
            return
        if key in ("ArrowLeft", "ArrowRight"):
            moved = [(r, c + (1 if key == "ArrowRight" else -1)) for r, c in self.piece]
            self.piece = moved if self._fits(moved) else self.piece
        elif key == "ArrowUp":
            # its own way of turning: about the piece's top-left, the other way round from the code's
            top, left = min(r for r, _ in self.piece), min(c for _, c in self.piece)
            high = max(r for r, _ in self.piece) - top
            turned = [(top + (c - left), left + high - (r - top)) for r, c in self.piece]
            self.piece = turned if self._fits(turned) else self.piece
        elif key == "ArrowDown":
            self._down()


class Guide:
    """A model that says the screen is a falling game and answers the one question about it."""

    def __init__(self, answer=ANSWER, falling=True):
        self.answer, self.falling, self.asked = answer, falling, []

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        self.asked.append("identify" if "families" in context else "setup" if "inputs" in context else "other")
        if "families" in context:
            return TurnResult(text=json.dumps({"screen": "other", "families": [], "falling": self.falling,
                                               "situation": "a well and a start notice"}))
        return TurnResult(text=json.dumps(self.answer))


def test_a_piece_turns_to_each_of_its_shapes_once():
    assert len(turns(Cells(PIECES[0]))) == 1
    assert len(turns(Cells(PIECES[1]))) == 2
    assert len(turns(Cells(PIECES[2]))) == 4
    assert normal([(3, 4), (3, 5)]) == Cells([(0, 0), (0, 1)])


def test_a_piece_falls_to_the_stack_and_full_rows_go():
    stack = Cells((3, c) for c in range(1, 4))
    lies = fall(stack, Cells([(0, 0), (1, 0)]), 4, 4)
    assert lies == Cells([(2, 0), (3, 0)])
    after, lines = settle(stack | Cells([(2, 1)]), lies, 4, 4)
    assert lines == 1 and after == Cells([(3, 0), (3, 1)])
    assert fall(stack, Cells([(3, 1)]), 4, 4) is None


def test_the_place_chosen_clears_a_line_when_one_can_be_cleared():
    stack = Cells((5, c) for c in range(4)) | Cells((4, c) for c in range(4))
    found = best(stack, Cells([(0, 0), (0, 1), (1, 0), (1, 1)]), 6, 6)
    assert found["column"] == 4 and found["lines"] == 2 and found["after"] == Cells()


def test_an_answer_the_loop_cannot_use_is_none():
    kinds = frozenset({"keys", "tap"})
    assert understood(ANSWER, list(SIZE), kinds)["box"] == BOX
    assert understood({**ANSWER, "board": [0, 0, 900, 100]}, list(SIZE), kinds) is None
    assert understood({**ANSWER, "keys": {**ANSWER["keys"], "down": None}}, list(SIZE), kinds) is None
    assert understood({**ANSWER, "keys": {**ANSWER["keys"], "left": "alert(1)"}}, list(SIZE), kinds) is None
    assert understood(ANSWER, list(SIZE), frozenset({"tap"})) is None
    # a start it cannot send is no start
    assert understood({**ANSWER, "start": {"kind": "run", "code": "x"}}, list(SIZE), kinds)["start"] is None


async def test_lines_are_cleared_with_one_question_about_the_well(monkeypatch):
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    fake, guide = Falling(), Guide()
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 2), budget=Budget(3000, 1, 60))
    told = made["realtime"]
    assert run.record["outcome"] == "goal_reached", (run.record, told)
    assert guide.asked == ["identify", "setup"]
    assert told["lines"] >= 2 and fake.cleared >= 2
    # the stack the code kept is the game's own, and no piece came to lie where the code did not expect it
    assert told["effects"]["unknown"] == 0 and told["resyncs"] == 0
    assert run.record["policy"] == "realtime" and run.record["levels"]["RT"] == told["pieces"]
    assert all(move["level"] == "RT" for move in run.moves)


async def test_a_game_already_going_is_taken_up_from_its_stack(monkeypatch):
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    fake = Falling(started=True)
    fake.stack = {(ROWS - 1, c): COLOURS[0] for c in range(COLS - 2)}
    guide = Guide({**ANSWER, "start": None})
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 1), budget=Budget(3000, 1, 60))
    assert run.record["outcome"] == "goal_reached", (run.record, made["realtime"])
    assert "Space" not in fake.keys


async def test_a_game_that_falls_too_fast_ends_as_over_not_as_an_error(monkeypatch):
    monkeypatch.setattr(realtime, "IDLE_S", 0.2)
    fake, guide = Falling(every=1), Guide({**ANSWER, "keys": {**ANSWER["keys"], "rotate": None}})
    # nothing is pressed sideways: every piece lands where it came
    monkeypatch.setattr(realtime, "SHIFTS_MOST", 0)
    run, made = await play(fake, guide, None, goal=GoalSpec("play_until_end"), budget=Budget(3000, 1, 60))
    assert run.record["outcome"] == "game_over" and run.record["reason"] == "stopped_falling", run.record
    assert fake.over


async def test_an_unusable_answer_leaves_the_game_to_the_usual_ways():
    fake, guide = Falling(), Guide({"board": "somewhere"})
    run, made = await play(fake, guide, None, goal=GoalSpec("play_until_end"), budget=Budget(5, 1, 20), direct=False)
    assert made["realtime"] == {"setup": None}
    assert run.record["outcome"] == "unsupported"
