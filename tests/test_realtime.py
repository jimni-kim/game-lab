"""A game in which time moves, played by code alone after one question (PLAN §29): the well is read each tick,
the falling piece told from the stack, and keys sent towards the place the code chose. The game here is a toy
drawn for the test: pieces fall a row every few captures whether or not anything is pressed, it turns them its own
way, and it has no key that drops a piece at once."""
from io import BytesIO
import json

from PIL import Image, ImageDraw

from gamelab import realtime
from gamelab.adaptation import Store
from gamelab.agent import play
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult
from gamelab.realtime import Cells, best, fall, normal, piece_in, settle, turns, understood

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


LOST, WON = ("result", {"kind": "done", "end": "lost"}), ("result", {"kind": "done", "end": "won"})
AWAY, BOARD = ("popup", {"kind": "tap", "at": [200, 300]}), ("board", {"kind": "tap", "at": [200, 300]})


class Falling:
    """The toy as a surface. A piece falls a row every `every` captures. It ends when a piece has no room."""
    name = "fake"
    reads_dom = False
    kinds = frozenset({"keys", "tap"})

    def __init__(self, every=3, started=False, shadow=False, held=False, drops=False, turn="ArrowUp",
                 notice_at=None, result_at=None, unseen=0):
        # shadow: the piece is drawn once more, pale, where it would come to lie. held: a key let go at once does
        # nothing. drops: the space bar puts the piece down at once
        self.every, self.playing, self.over = every, False, False
        self.shadow, self.needs_hold, self.drops, self.turn = shadow, held, drops, turn
        self.stack, self.piece, self.colour = {}, None, None
        self.count = self.captures = self.cleared = 0
        self.keys, self.held = [], []
        # notice_at: once so many pieces have come, a dim notice lies over the whole screen and the game waits
        # until it is tapped away. result_at: once so many rows are gone, the level is over and its result is
        # drawn over the well
        self.notice_at, self.result_at, self.notice, self.taps = notice_at, result_at, False, []
        # unseen: a piece is in play for so many captures before it is drawn (it comes in from above the well)
        self.unseen, self.hidden = unseen, 0
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
        self.hidden = self.unseen
        if self.count == self.notice_at:
            self.notice, self.playing = True, False
        if self.result_at is not None and self.cleared >= self.result_at:
            self.piece, self.playing, self.over = None, False, True
            return
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
        if self.playing and self.hidden:
            self.hidden -= 1
        elif self.playing:
            self.captures += 1
            if self.captures % self.every == 0:
                self._down()
        image = Image.new("RGB", SIZE, (240, 240, 240))
        draw = ImageDraw.Draw(image)
        draw.rectangle([BOX[0] - 2, BOX[1] - 2, BOX[2] + 1, BOX[3] + 1], fill=(0, 0, 0))
        draw.rectangle([BOX[0], BOX[1], BOX[2] - 1, BOX[3] - 1], fill=EMPTY)
        shown = {**self.stack, **self._overlay()}
        if self.piece and self.shadow and not self.hidden:
            lies = list(self.piece)
            while self._fits([(r + 1, c) for r, c in lies]):
                lies = [(r + 1, c) for r, c in lies]
            shown |= {cell: (150, 150, 150) for cell in lies}
        shown |= {cell: self.colour for cell in self.piece} if self.piece and not self.hidden else {}
        for (r, c), colour in shown.items():
            if r >= 0:
                draw.rectangle([LEFT + c * CELL + 1, TOP + r * CELL + 1, LEFT + (c + 1) * CELL - 2, TOP + (r + 1) * CELL - 2],
                               fill=colour)
        if self.over and self.result_at is not None:
            draw.rectangle([BOX[0] - 20, BOX[1], BOX[2] + 20, BOX[1] + 8 * CELL], fill=(250, 250, 250))
        if self.notice:
            image = Image.blend(image, Image.new("RGB", SIZE, (0, 0, 0)), 0.6)
        if region is not None:
            image = image.crop(tuple(region))
        out = BytesIO()
        image.save(out, "PNG")
        return out.getvalue()

    def _overlay(self):
        """What the game draws over the well besides its blocks: nothing here."""
        return {}

    async def tap(self, x, y, duration_ms):
        self.taps.append((x, y))
        if self.notice:
            self.notice, self.playing = False, True

    async def key(self, key, hold_ms=0):
        self.keys.append(key)
        self.held.append(hold_ms)
        if self.notice:
            return
        if key == "Space" and not self.playing and not self.over:
            self._start()
            return
        if not self.playing or self.needs_hold and hold_ms < 20:
            return
        if key == "Space" and self.drops:
            count = self.count
            while self.count == count and self.playing:
                self._down()
        elif key in ("ArrowLeft", "ArrowRight"):
            moved = [(r, c + (1 if key == "ArrowRight" else -1)) for r, c in self.piece]
            self.piece = moved if self._fits(moved) else self.piece
        elif key == self.turn:
            # its own way of turning: about the piece's top-left, the other way round from the code's
            top, left = min(r for r, _ in self.piece), min(c for _, c in self.piece)
            high = max(r for r, _ in self.piece) - top
            turned = [(top + (c - left), left + high - (r - top)) for r, c in self.piece]
            self.piece = turned if self._fits(turned) else self.piece
        elif key == "ArrowDown":
            self._down()


class Guide:
    """A model that says the screen is a falling game and answers the one question about it."""

    def __init__(self, answer=ANSWER, falling=True, plays=()):
        # plays: what it answers when it is shown the screen to play it itself (the loop let the game go), in
        # turn and the last again: `(scene, action)`
        self.answer, self.falling, self.asked, self.plays = answer, falling, [], list(plays) or [LOST]
        self.told = []

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        self.asked.append("identify" if "families" in context else "setup" if "inputs" in context else "other")
        if "families" in context:
            return TurnResult(text=json.dumps({"screen": "other", "families": [], "falling": self.falling,
                                               "situation": "a well and a start notice"}))
        if context.get("play"):
            self.asked[-1] = "play"
            self.told.append(context)
            scene, action = self.plays.pop(0) if len(self.plays) > 1 else self.plays[0]
            return TurnResult(text=json.dumps({"situation": "what stands over the well", "scene": scene, "guided": False,
                                               "action": action, "expect": "the game goes on", "notes": ""}))
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
    # (the space bar is pressed once, on a piece in the air, to see whether it drops one; never to start)
    assert fake.keys[0] != "Space" and fake.keys.count("Space") == 1 and made["realtime"]["dropping"] == "none"


async def test_a_game_that_falls_too_fast_ends_as_over_not_as_an_error(monkeypatch):
    monkeypatch.setattr(realtime, "IDLE_S", 0.2)
    fake, guide = Falling(every=1), Guide({**ANSWER, "keys": {**ANSWER["keys"], "rotate": None}})
    # nothing is pressed sideways: every piece lands where it came
    monkeypatch.setattr(realtime, "SHIFTS_MOST", 0)
    run, made = await play(fake, guide, None, goal=GoalSpec("play_until_end"), budget=Budget(3000, 1, 60))
    # the loop does not say what the screen is once nothing falls: the model looks at it once and says the end
    assert run.record["outcome"] == "game_over" and run.record["reason"] == "end_screen", run.record
    assert fake.over and guide.asked.count("play") == 1
    assert made["realtime"]["stretches"][-1]["why"] in ("stopped_falling", "covered")


async def test_an_unusable_answer_leaves_the_game_to_the_usual_ways():
    fake, guide = Falling(), Guide({"board": "somewhere"})
    run, made = await play(fake, guide, None, goal=GoalSpec("play_until_end"), budget=Budget(5, 1, 20), direct=False)
    assert made["realtime"] == {"setup": None}
    assert run.record["outcome"] == "unsupported"


async def test_the_next_game_starts_from_what_was_kept_with_no_question(monkeypatch, tmp_path):
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    store, guide = Store(tmp_path), Guide()
    run, made = await play(Falling(), guide, None, goal=GoalSpec("reach_value", 1), budget=Budget(3000, 1, 60), store=store)
    assert run.record["outcome"] == "goal_reached" and made["realtime"]["source"] == "model"
    kept = store.falling("https://falling.example/")
    assert kept["setup"]["box"] == BOX and len(kept["empty"]) == 3
    again = Guide()
    run, made = await play(Falling(), again, None, goal=GoalSpec("reach_value", 1), budget=Budget(3000, 1, 60), store=store)
    assert run.record["outcome"] == "goal_reached", (run.record, made["realtime"])
    assert again.asked == [] and made["realtime"]["source"] == "kept"
    assert made["identified"]["source"] == "falling_kept"


async def test_what_was_kept_of_another_screen_is_dropped_and_the_model_asked(monkeypatch, tmp_path):
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    store, site = Store(tmp_path), "https://falling.example/"
    setup = understood(ANSWER, list(SIZE), Falling.kinds)
    # the well's cells looked otherwise then: the screen is not the one the words were said of
    store.save_falling(site, {"setup": setup, "empty": [240, 240, 240], "near": 28.0})
    guide = Guide()
    run, made = await play(Falling(), guide, None, goal=GoalSpec("reach_value", 1), budget=Budget(3000, 1, 60), store=store)
    assert run.record["outcome"] == "goal_reached", (run.record, made["realtime"])
    assert guide.asked == ["setup"] and made["falling_kept"] == "dropped" and made["realtime"]["source"] == "model"
    assert store.falling(site)["empty"] != [240, 240, 240]


async def test_kept_words_nothing_is_placed_by_are_dropped_and_the_model_asked(monkeypatch, tmp_path):
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    monkeypatch.setattr(realtime, "FIRST_S", 0.3)
    store, site = Store(tmp_path), "https://falling.example/"
    # the game is started by another key now than the one kept
    setup = understood({**ANSWER, "start": {"kind": "key", "key": "Enter"}}, list(SIZE), Falling.kinds)
    store.save_falling(site, {"setup": setup, "empty": list(EMPTY), "near": 28.0})
    guide = Guide()
    run, made = await play(Falling(), guide, None, goal=GoalSpec("reach_value", 1), budget=Budget(3000, 1, 60), store=store)
    assert run.record["outcome"] == "goal_reached", (run.record, made["realtime"])
    assert guide.asked == ["setup"] and made["falling_kept"] == "dropped"
    assert store.falling(site)["setup"]["start"] == {"kind": "key", "key": "Space"}


def test_a_piece_is_told_from_the_shadow_drawn_of_it():
    stack = Cells((5, c) for c in range(3))
    piece = Cells([(0, 1), (0, 2), (1, 1), (1, 2)])
    shadow = fall(stack, piece, 6, 6)
    assert piece_in(stack, piece | shadow, 6, 6) == piece
    # a piece half above the well is known whole by its shadow
    assert piece_in(stack, Cells([(0, 1), (0, 2)]) | shadow, 6, 6) == Cells([(-1, 1), (-1, 2), (0, 1), (0, 2)])
    # come down into its shadow, the two are one patch: told apart by the shapes it turns to
    near = Cells((r + 1, c) for r, c in piece)
    assert piece_in(stack, near | shadow, 6, 6, turns(piece)) == near
    # cells that hang nowhere a piece's shadow would lie are no piece
    assert piece_in(stack, piece | Cells([(3, 5)]), 6, 6) is None
    assert piece_in(stack, piece, 6, 6) == piece


async def test_a_game_that_draws_shadows_takes_held_keys_and_drops_is_learned_as_it_is_played(monkeypatch, tmp_path):
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    store = Store(tmp_path)
    # (the model names the usual key for turning; this game turns with another, which it gave as one to try)
    fake, guide = Falling(every=8, shadow=True, held=True, drops=True, turn="x"), Guide({**ANSWER, "also": {"rotate": ["x"]}})
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 2), budget=Budget(3000, 1, 60), store=store)
    told = made["realtime"]
    assert run.record["outcome"] == "goal_reached", (run.record, told)
    assert told["shadow"] and told["hold_ms"] == realtime.HOLD_MS and told["keys"]["drop"] == "Space"
    assert told["keys"]["rotate"] == "x" and told["turning"] == "found"
    assert told["resyncs"] == 0 and told["effects"]["unknown"] == 0
    # the next game is played from the start as this one was learned to be
    fake, again = Falling(every=8, shadow=True, held=True, drops=True, turn="x"), Guide()
    run, made = await play(fake, again, None, goal=GoalSpec("reach_value", 2), budget=Budget(3000, 1, 60), store=store)
    assert run.record["outcome"] == "goal_reached" and again.asked == []
    assert all(fake.held[1:]) and "ArrowDown" not in fake.keys and "ArrowUp" not in fake.keys


# a game played by touch alone (as on a phone): a tap turns the piece, a level swipe moves it one column, a swipe
# down drops it at once
TOUCH = frozenset({"tap", "drag", "hold"})
SWIPES = {"left": {"kind": "swipe", "from": [160, 200], "to": [136, 200], "ms": 100},
          "right": {"kind": "swipe", "from": [160, 200], "to": [184, 200], "ms": 100},
          "rotate": {"kind": "tap", "x": 200, "y": 150}, "down": None,
          "drop": {"kind": "swipe", "from": [160, 100], "to": [160, 180], "ms": 80}}
TOUCHED = {**ANSWER, "keys": SWIPES, "start": {"kind": "tap", "x": 160, "y": 150}}


class Touched(Falling):
    kinds = TOUCH

    def __init__(self, **kwargs):
        super().__init__(drops=True, **kwargs)
        self.gestures = []

    async def tap(self, x, y, duration_ms):
        self.gestures.append(("tap", x, y))
        await Falling.key(self, "Space" if not self.playing else self.turn)

    async def drag(self, x0, y0, x1, y1, duration_ms):
        self.gestures.append(("drag", x0, y0, x1, y1, duration_ms))
        if abs(y1 - y0) > abs(x1 - x0):
            await Falling.key(self, "Space")
        else:
            await Falling.key(self, "ArrowRight" if x1 > x0 else "ArrowLeft")

    async def key(self, key, hold_ms=0):
        raise AssertionError("a touch surface sends no keys")


def test_a_game_played_by_touch_is_given_gestures_as_controls():
    said = understood(TOUCHED, list(SIZE), TOUCH)
    assert said["keys"] == {"left": "swipe:160,200,136,200,100", "right": "swipe:160,200,184,200,100",
                            "rotate": "tap:200,150", "down": None, "drop": "swipe:160,100,160,180,80"}
    assert said["start"] == {"kind": "tap", "x": 160, "y": 150}
    # no keys of such games are tried where no key can be sent; gestures the model gave besides are
    assert said["also"] == {"rotate": [], "drop": []}
    assert understood({**TOUCHED, "also": {"rotate": [{"kind": "tap", "x": 120, "y": 150}, "x"]}},
                      list(SIZE), TOUCH)["also"]["rotate"] == ["tap:120,150"]
    # gestures already as the loop keeps them read back the same
    assert understood({**TOUCHED, "keys": said["keys"]}, list(SIZE), TOUCH)["keys"] == said["keys"]
    # key names, where no key can be sent, are no controls
    assert understood(ANSWER, list(SIZE), TOUCH) is None
    assert understood({**TOUCHED, "start": {"kind": "key", "key": "Space"}}, list(SIZE), TOUCH)["start"] is None
    # a point off the screen, a swipe of no length or too long, a swipe where none can be sent
    off = {**SWIPES, "left": {"kind": "swipe", "from": [160, 200], "to": [-5, 200], "ms": 100}}
    assert understood({**TOUCHED, "keys": off}, list(SIZE), TOUCH) is None
    assert understood({**TOUCHED, "keys": {**SWIPES, "rotate": {"kind": "tap", "x": 400, "y": 10}}},
                      list(SIZE), TOUCH)["keys"]["rotate"] is None
    still = {**SWIPES, "left": {"kind": "swipe", "from": [160, 200], "to": [160, 200], "ms": 100}}
    assert understood({**TOUCHED, "keys": still}, list(SIZE), TOUCH) is None
    slow = {**SWIPES, "left": {"kind": "swipe", "from": [160, 200], "to": [136, 200], "ms": 5000}}
    assert understood({**TOUCHED, "keys": slow}, list(SIZE), TOUCH) is None
    assert understood(TOUCHED, list(SIZE), frozenset({"tap"})) is None
    assert understood({**TOUCHED, "keys": {**SWIPES, "left": "swipe:160,200,900,200,100"}}, list(SIZE), TOUCH) is None
    # where keys can be sent, those such games use are tried when the model gave none
    assert understood(ANSWER, list(SIZE), Falling.kinds)["also"] == {"rotate": list(realtime.TURN_TRIED),
                                                                    "drop": list(realtime.DROP_TRIED)}


async def test_a_sent_control_is_carried_out_as_what_it_is():
    class Hand:
        def __init__(self):
            self.done = []

        async def key(self, key, hold_ms=0):
            self.done.append(("key", key, hold_ms))

        async def tap(self, x, y, duration_ms):
            self.done.append(("tap", x, y))

        async def drag(self, x0, y0, x1, y1, duration_ms):
            self.done.append(("drag", x0, y0, x1, y1, duration_ms))

    hand = Hand()
    for token in ("ArrowLeft", "tap:10,20", "swipe:1,2,3,4,90"):
        sent = realtime.gesture(token) or {"kind": "key", "key": token}
        await realtime.tap_or_key(hand, sent)
    await realtime.tap_or_key(hand, {"kind": "key", "key": "x", "hold_ms": 40})
    assert hand.done == [("key", "ArrowLeft", 0), ("tap", 10, 20), ("drag", 1, 2, 3, 4, 90), ("key", "x", 40)]


async def test_lines_are_cleared_in_a_game_played_by_touch(monkeypatch, tmp_path):
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    store = Store(tmp_path)
    fake, guide = Touched(every=8), Guide(TOUCHED)
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 2), budget=Budget(3000, 1, 60), store=store)
    told = made["realtime"]
    assert run.record["outcome"] == "goal_reached", (run.record, told)
    assert told["lines"] >= 2 and told["effects"]["unknown"] == 0 and told["resyncs"] == 0
    kinds = {gesture[0] for gesture in fake.gestures}
    assert kinds == {"tap", "drag"} and told["hold_ms"] == 0
    # the next game is played from the gestures kept, with no question
    fake, again = Touched(every=8), Guide()
    run, made = await play(fake, again, None, goal=GoalSpec("reach_value", 1), budget=Budget(3000, 1, 60), store=store)
    assert run.record["outcome"] == "goal_reached" and again.asked == [] and made["realtime"]["source"] == "kept"



def test_a_piece_may_come_to_lie_anywhere_it_can_be_dropped():
    square = Cells({(0, 0), (0, 1), (1, 0), (1, 1)})
    assert len(realtime.drops(Cells(), square, 6, 4)) == 3
    bar = Cells({(0, 0), (0, 1), (0, 2), (0, 3)})
    assert Cells({(r, 2) for r in range(2, 6)}) in realtime.drops(Cells(), bar, 6, 4)


async def test_a_notice_over_the_well_is_put_away_by_the_model_and_the_loop_takes_the_game_again(monkeypatch):
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    monkeypatch.setattr(realtime, "STILL_S", 0.5)
    fake, guide = Falling(notice_at=3), Guide(plays=[AWAY, BOARD])
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 2), budget=Budget(3000, 1, 60))
    assert run.record["outcome"] == "goal_reached", run.record
    told = made["realtime"]
    # (the well shows through this notice: the piece under it stands still whatever is sent)
    assert [stretch["why"] for stretch in told["stretches"]] == ["piece_still", "done"] and told["lines"] >= 2
    assert fake.cleared >= 2 and fake.taps and guide.asked.count("setup") == 1
    # what the loop saw when it let go is kept to be looked at: the captures, and what was made of each look
    assert told["looks"][-1]["why"] == "piece_still" and told["looks"][-1]["looks"][-1]["event"] == "piece_still"
    assert any(kept["why"].startswith("rt-t") for kept in run.screens)


async def test_a_level_ends_as_the_model_reads_its_result(monkeypatch):
    fake, guide = Falling(result_at=1), Guide(plays=[WON])
    run, made = await play(fake, guide, None, goal=GoalSpec("play_until_end"), budget=Budget(3000, 1, 60))
    assert run.record["outcome"] == "goal_reached" and run.record["reason"] == "won", run.record
    assert fake.cleared >= 1 and guide.asked.count("play") == 1
    # the model is told a level was played while it was not asked: what stands there now is that level's end
    assert "played_by_program" in json.dumps(guide.told[0])


async def test_what_was_said_of_a_well_under_a_notice_is_asked_again_once_the_game_shows(monkeypatch):
    monkeypatch.setattr(realtime, "FIRST_S", 0.3)
    monkeypatch.setattr(realtime, "STILL_S", 0.5)
    fake = Falling(started=True)
    fake.notice, fake.playing = True, False
    guide = Guide({**ANSWER, "start": None}, plays=[AWAY, BOARD])
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 1), budget=Budget(3000, 1, 60))
    assert run.record["outcome"] == "goal_reached", run.record
    assert guide.asked.count("setup") == 2 and made["realtime"]["source"] == "model_again"


async def test_a_falling_game_of_other_rules_is_said_to_be_one_at_once():
    fake, guide = Falling(), Guide({**ANSWER, "clears": "four of one colour touching vanish"})
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 1), budget=Budget(3000, 1, 60))
    assert run.record["outcome"] == "unsupported" and run.record["reason"] == "falling_other_rules", run.record
    assert fake.keys == []


async def test_a_piece_dropped_is_not_dropped_again_while_the_next_is_not_yet_seen(monkeypatch, tmp_path):
    # (2026-11-21, a game on a phone: the piece lay where it was dropped to at the next look, the drop was sent
    # once more and took the piece just come in, unseen, straight down from where it came)
    fake = Falling(drops=True, unseen=2)
    guide = Guide({**ANSWER, "keys": {**ANSWER["keys"], "drop": "Space"}})
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 2), budget=Budget(3000, 1, 60))
    assert run.record["outcome"] == "goal_reached", (run.record, made["realtime"])
    told = made["realtime"]
    assert told["effects"]["confirmed"] == told["pieces"] and told["resyncs"] == 0, told["effects"]
    # one to start the game and one a piece
    assert fake.keys.count("Space") <= told["pieces"] + 2


# ---- PLAN §34 F2: what a gesture did is waited for, how sideways swipes are taken is learned, and a stack other
# than expected is read again and played on

class Followed(Touched):
    """A game played by touch in which the piece goes as far as the finger does: a column every CELL pixels of a
    level swipe past a little slack. `deaf`: a level swipe shorter than so many pixels or quicker than so many
    milliseconds is taken for nothing (and then a swipe moves one column whatever its length). `late`: what a
    gesture does is shown so many captures after it was made."""

    def __init__(self, follows=True, deaf=(0, 0), late=0, **kwargs):
        super().__init__(**kwargs)
        self.follows, self.deaf, self.late, self.queue = follows, deaf, late, []

    async def _do(self, made):
        if made[0] == "tap":
            await Falling.key(self, "Space" if not self.playing else self.turn)
            return
        _, x0, y0, x1, y1, ms = made
        if abs(y1 - y0) > abs(x1 - x0):
            await Falling.key(self, "Space")
            return
        if abs(x1 - x0) < self.deaf[0] or ms < self.deaf[1]:
            return
        for _ in range(int((abs(x1 - x0) - 4) // CELL) if self.follows else 1):
            await Falling.key(self, "ArrowRight" if x1 > x0 else "ArrowLeft")

    async def _made(self, made):
        self.gestures.append(made)
        if self.late and self.playing:
            self.queue.append([self.late, made])
        else:
            await self._do(made)

    async def tap(self, x, y, duration_ms):
        await self._made(("tap", x, y))

    async def drag(self, x0, y0, x1, y1, duration_ms):
        await self._made(("drag", x0, y0, x1, y1, duration_ms))

    async def capture(self, region=None):
        for waiting in self.queue:
            waiting[0] -= 1
        due, self.queue = [made for left, made in self.queue if left <= 0], [w for w in self.queue if w[0] > 0]
        for made in due:
            await self._do(made)
        return await super().capture(region)


def _level_swipes(fake):
    return [made for made in fake.gestures if made[0] == "drag" and abs(made[3] - made[1]) > abs(made[4] - made[2])]


async def test_a_game_in_which_the_piece_follows_the_finger_is_swiped_once_for_all_the_cells_to_go(monkeypatch, tmp_path):
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    store = Store(tmp_path)
    fake, guide = Followed(every=10), Guide(TOUCHED)
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 2), budget=Budget(3000, 1, 60), store=store)
    told = made["realtime"]
    assert run.record["outcome"] == "goal_reached", (run.record, told)
    assert told["reach"]["mode"] == realtime.DRAG and 0.7 * CELL <= told["reach"]["px"] <= 1.5 * CELL, told["reach"]
    assert told["effects"]["unknown"] == 0 and told["resyncs"] == 0
    # swipes of more lengths than the model's one were sent, and they stay inside the screen
    swipes = _level_swipes(fake)
    assert len({abs(made[3] - made[1]) for made in swipes}) > 1
    assert all(0 <= made[1] < SIZE[0] and 0 <= made[3] < SIZE[0] for made in swipes)
    # what was learned is kept with the model's words, and the next game goes by it from its first piece
    kept = store.falling("https://falling.example/")["setup"]["reach"]
    assert kept["mode"] == realtime.DRAG
    fake, again = Followed(every=10), Guide()
    run, made = await play(fake, again, None, goal=GoalSpec("reach_value", 1), budget=Budget(3000, 1, 60), store=store)
    assert run.record["outcome"] == "goal_reached" and again.asked == []
    assert made["realtime"]["reach"]["mode"] == realtime.DRAG
    # about a swipe a piece and no more
    assert len(_level_swipes(fake)) <= 2 * made["realtime"]["pieces"]


async def test_a_game_that_takes_short_quick_swipes_for_nothing_is_swiped_longer_and_slower(monkeypatch):
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    monkeypatch.setattr(realtime, "WAIT_S", 0.1)
    fake, guide = Followed(follows=False, deaf=(30, 140), every=12), Guide(TOUCHED)
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 2), budget=Budget(3000, 1, 60))
    told = made["realtime"]
    assert run.record["outcome"] == "goal_reached", (run.record, told)
    assert told["reach"]["px"] >= 30 and told["reach"]["ms"] >= 140, told["reach"]
    # a long swipe was seen to move one cell: a cell a swipe from then on
    assert told["reach"]["mode"] == realtime.STEP


async def test_a_gesture_shown_late_is_waited_for_and_not_sent_again(monkeypatch):
    # (2026-11-21, a game on a phone: a tap that turns the piece showed half a second later, two looks on; it was
    # sent again at the look between, the piece turned twice, and the drop sent then went to the next piece)
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    fake, guide = Followed(follows=False, late=3, every=14), Guide(TOUCHED)
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 2), budget=Budget(3000, 1, 60))
    told = made["realtime"]
    assert run.record["outcome"] == "goal_reached", (run.record, told)
    assert told["effects"]["confirmed"] == told["pieces"] and told["resyncs"] == 0, told["effects"]
    assert told["wait_ms"] is not None


class Odd(Falling):
    """A game whose rules are a little other than the code's: each piece that comes to lie leaves one block more
    on the lowest column."""

    def _spawn(self):
        if self.count:
            high = {c: min((r for r, col in self.stack if col == c), default=ROWS) for c in range(COLS)}
            c = max(high, key=high.get)
            if high[c] > 4:
                self.stack[(high[c] - 1, c)] = (120, 120, 120)
        super()._spawn()


async def test_a_game_whose_stack_is_never_what_was_expected_is_read_again_each_piece_and_played_on(monkeypatch):
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    fake, guide = Odd(every=4), Guide()
    run, made = await play(fake, guide, None, goal=GoalSpec("actions", 12), budget=Budget(3000, 1, 60))
    told = made["realtime"]
    assert run.record["outcome"] == "goal_reached", (run.record, told)
    # far more readings than the well was once let go after, and not one question besides the first
    assert told["resyncs"] > realtime.RESYNCS_MOST and [s["why"] for s in told["stretches"]] == ["done"]
    assert guide.asked == ["identify", "setup"]


async def test_a_well_in_which_no_piece_can_be_told_is_let_go(monkeypatch):
    class Noise(Falling):
        """Nothing falls: blocks come and go all over the well."""
        async def capture(self, region=None):
            import random
            self.captures += 1
            self.playing, self.piece = False, None
            dice = random.Random(self.captures // 2)
            self.stack = {(dice.randrange(4, ROWS), dice.randrange(COLS)): COLOURS[0] for _ in range(14)}
            return await super().capture(region)

    monkeypatch.setattr(realtime, "SETTLE_S", 0.05)
    fake, guide = Noise(started=True), Guide({**ANSWER, "start": None})
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 1), budget=Budget(3000, 1, 30))
    assert made["realtime"]["stretches"][0]["why"] == "well_unread", made["realtime"]["stretches"]


class Entering(Falling):
    """Each piece comes in from above the well a row at a time: its bottom row is all that shows at first."""

    def __init__(self, **kwargs):
        self.early = 0
        super().__init__(**kwargs)

    def _spawn(self):
        super()._spawn()
        if self.piece:
            high = max(r for r, _ in self.piece)
            self.piece = [(r - high, c) for r, c in self.piece]

    async def key(self, key, hold_ms=0):
        if self.playing and self.piece and key != "Space" and self.count > 1 and min(r for r, _ in self.piece) < 0:
            self.early += 1
        await super().key(key, hold_ms)


async def test_a_piece_still_coming_in_from_above_is_not_played_before_it_is_whole(monkeypatch):
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    fake, guide = Entering(every=6), Guide()
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 2), budget=Budget(3000, 1, 60))
    assert run.record["outcome"] == "goal_reached", (run.record, made["realtime"])
    assert fake.early == 0 and made["realtime"]["effects"]["unknown"] == 0


def test_what_swipes_did_is_held_against_what_was_sent():
    setup = understood(TOUCHED, list(SIZE), TOUCH)
    loop = realtime.Loop(None, setup, send=None, check=None)
    # nothing moved: longer and slower
    loop._reached({"way": 1, "times": 1, "px": 24, "cells": 1}, 0, False)
    assert loop.side["px"] == 36 and loop.side["ms"] == 150 and loop.side["mode"] is None
    # taken for a tap (the piece turned and stayed): the same
    loop._reached({"way": 1, "times": 1, "px": 36, "cells": 1}, 0, True)
    assert loop.side["px"] == 54
    # one long swipe took the piece three cells: so many pixels a cell
    loop._reached({"way": 1, "times": 1, "px": 60, "cells": 3}, 3, False)
    assert loop.side["mode"] == realtime.DRAG and loop.side["px"] == 20
    assert realtime.learned(loop)["reach"] == {"mode": "drag", "px": 20.0, "ms": 225}
    # kept and read back; something else kept there is not taken
    assert realtime._reach({"mode": "drag", "px": 20.0, "ms": 225}) == {"mode": "drag", "px": 20.0, "ms": 225}
    assert realtime._reach({"mode": "fling", "px": 20.0, "ms": 225}) is None and realtime._reach("drag") is None


class Late(Guide):
    """A model that first sees a game but no falling one (a level's reward over the well, as it was read) and,
    asked again in its own play, a falling one."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.first = True

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "families" in context and self.first:
            self.first = False
            self.asked.append("identify")
            return TurnResult(text=json.dumps({"screen": "game", "families": [], "falling": False,
                                               "situation": "a reward over the well"}))
        return await super().complete(request)


async def test_a_falling_game_first_asked_about_where_none_showed_is_taken_by_the_loop_once_it_is_seen(monkeypatch):
    # (PLAN §34, F4) the first asking was on a reward screen; the model played the level by its own taps while the
    # askings in its play said it was a falling game, and lost it
    fake = Falling(started=True)
    fake.notice, fake.playing = True, False
    guide = Late({**ANSWER, "start": None}, plays=[AWAY, BOARD])
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 1), budget=Budget(3000, 1, 60))
    assert run.record["outcome"] == "goal_reached", run.record
    assert made["identified"].get("falling") is True and guide.asked.count("identify") >= 2
    assert made["realtime"]["lines"] >= 1 and fake.cleared >= 1


class Flashing(Falling):
    """A game that shows rows going (F5, as a phone game drew them): the full rows turn white for some looks, the
    row above them glows faintly where it is empty, and only then do they go and the next piece come."""

    def __init__(self, *args, flash=6, **kwargs):
        super().__init__(*args, **kwargs)
        self.flash, self.going, self.left = flash, [], 0

    def _down(self):
        if self.piece is None:
            return
        moved = [(r + 1, c) for r, c in self.piece]
        if self._fits(moved):
            self.piece = moved
            return
        for cell in self.piece:
            self.stack[cell] = self.colour
        full = [r for r in range(ROWS) if all((r, c) in self.stack for c in range(COLS))]
        if not full:
            self._spawn()
            return
        self.piece, self.going, self.left = None, full, self.flash

    def _gone(self):
        full = self.going
        self.cleared += len(full)
        self.stack = {(r + sum(row > r for row in full), c): colour for (r, c), colour in self.stack.items() if r not in full}
        self.going = []
        self._spawn()

    async def capture(self, region=None):
        if self.going:
            self.left -= 1
            if self.left <= 0:
                self._gone()
        return await super().capture(region)

    def _overlay(self):
        if not self.going:
            return {}
        above = min(self.going) - 1
        return {**{(r, c): (250, 250, 250) for r in self.going for c in range(COLS)},
                **{(above, c): (60, 90, 150) for c in range(COLS) if (above, c) not in self.stack}}


async def test_rows_shown_going_are_waited_out_and_the_piece_taken_as_planned(monkeypatch):
    # (PLAN §34, F5) a white band and a glow over the row above were read as cells: the piece was taken as come to
    # lie elsewhere with no row cleared, and the stack read again from the screen
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    fake, guide = Flashing(every=3, flash=8), Guide()
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 3), budget=Budget(3000, 1, 60))
    told = made["realtime"]
    assert run.record["outcome"] == "goal_reached", (run.record, told["effects"])
    assert told["effects"]["confirmed"] == told["pieces"] and told["resyncs"] == 0, (told["effects"], told["resyncs"])
    assert told["lines"] == fake.cleared


def test_a_piece_of_the_same_colour_lying_below_is_no_shadow():
    # (F5) a piece of the same shape that came to lie straight below was taken for the shadow of the one falling
    stack = Cells({(5, c) for c in range(3)})
    piece = Cells({(0, 0), (0, 1), (1, 1), (1, 2)})
    lying = Cells({(3, 0), (3, 1), (4, 1), (4, 2)})
    assert piece_in(stack, piece | lying, 6, 4) == piece
    assert piece_in(stack, piece | lying, 6, 4, unlike=lambda a, b: False) is None
    assert piece_in(stack, piece | lying, 6, 4, unlike=lambda a, b: True) == piece
