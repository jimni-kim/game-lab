"""What keeps moving on a screen, left out of what is read of it (PLAN §13): a tutorial's hand circling over a
board, a drag aimed by where the held thing is drawn in the model's own play, a press long enough for the game
to pick a thing up, and a family gone back to once a tutorial has given way to the game."""
from functools import partial
from io import BytesIO
import json

from PIL import Image, ImageChops, ImageDraw
import pytest

from gamelab import adaptation, agent, carry, direct, motion, recovery
from gamelab.agent import play
from gamelab.carry import Carrier
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult
from gamelab.motion import Steady
from test_adaptation_blocks import BOARD as BLOCKS_BOARD, CELL, PIECE as BLOCKS_PIECE, Blocks, extent
from test_adaptation_mines import COMPUTE, SIZE as MINES_SIZE, Mines, Reader
from test_direct import BESIDE, Lamps, Player


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "PLAIN_INSETS", ())
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(agent, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    monkeypatch.setattr(direct, "SETTLE_S", 0.0)
    monkeypatch.setattr(direct, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(carry, "LIFT_S", 0.0)
    monkeypatch.setattr(carry, "REST_S", 0.0)
    monkeypatch.setattr(adaptation, "GeneratedAdapter", partial(
        adaptation.GeneratedAdapter, settling={"timeout_s": 0.4, "interval_s": 0.05, "stable_s": 0.1}))


def with_hand(png, at, colour=(255, 255, 255)):
    """A hand-sized blot over a capture, as a tutorial's pointer is drawn."""
    image = Image.open(BytesIO(png)).convert("RGB")
    x, y = at
    ImageDraw.Draw(image).ellipse((x, y, x + 40, y + 40), fill=colour)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def same(one, other):
    with Image.open(BytesIO(one)) as a, Image.open(BytesIO(other)) as b:
        return ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is None


class Circling:
    """A screen with a hand that moves on at every capture, along `path` and round again."""

    def __init__(self, path, colour=(255, 255, 255)):
        self.path, self.colour, self.shown, self.captures = list(path), colour, 0, 0

    def hand(self, png):
        self.captures += 1
        at = self.path[self.shown % len(self.path)]
        self.shown += 1
        return with_hand(png, at, self.colour)


class HandBlocks(Blocks):
    """The block game under a tutorial's hand that circles over its board for ever."""

    def __init__(self, **options):
        super().__init__(**options)
        left, top = BLOCKS_BOARD[0], BLOCKS_BOARD[1]
        self.circling = Circling([(left + 10 + 52 * step, top + 150) for step in range(7)])

    async def capture(self, region=None):
        return self.circling.hand(await super().capture(region))


async def test_a_hand_that_keeps_circling_over_a_still_board_is_left_out_and_where_it_moved_is_kept():
    fake = HandBlocks()
    clean = await Blocks.capture(fake)
    seen = Steady(fake)
    png = await seen.capture()
    assert same(png, clean)
    assert seen.animating and fake.circling.captures == motion.FRAMES
    # what moved is where the hand went, all of it (a step a capture)
    left = min(box[0] for box in seen.moving)
    right = max(box[0] + box[2] for box in seen.moving)
    assert left <= BLOCKS_BOARD[0] + 10 and right >= BLOCKS_BOARD[0] + 10 + 52 * (motion.FRAMES - 1) + 40


async def test_a_still_screen_is_looked_at_twice_and_given_as_it_is():
    fake = Blocks()
    looks = []
    capture = fake.capture

    async def counted(region=None):
        looks.append(1)
        return await capture(region)
    fake.capture = counted
    seen = Steady(fake)
    png = await seen.capture()
    assert same(png, await capture()) and len(looks) == 2 and not seen.animating


async def test_a_screen_that_changes_almost_everywhere_is_a_new_scene_and_given_as_it_is_last():
    colours = iter([(40 * step, 0, 0) for step in range(20)])

    class Fading:
        async def capture(self, region=None):
            out = BytesIO()
            Image.new("RGB", (200, 100), next(colours)).save(out, format="PNG")
            return out.getvalue()
    seen = Steady(Fading())
    png = await seen.capture()
    with Image.open(BytesIO(png)) as image:
        assert image.getpixel((5, 5)) == (40 * (motion.FRAMES - 1), 0, 0)
    assert not seen.animating


def goal_at(fake, slot, anchor):
    """Where the middle of the piece in `slot` lies when it covers the cells from `anchor`."""
    rows, cols = extent(fake.state["tray"][slot])
    return [BLOCKS_BOARD[0] + (anchor[1] + cols / 2) * CELL, BLOCKS_BOARD[1] + (anchor[0] + rows / 2) * CELL]


def slot_middle(fake, slot):
    left, top, width, height = fake.source(slot)
    return (left + width / 2, top + height / 2)


async def test_a_carried_drag_under_a_circling_hand_puts_the_piece_where_it_was_meant_to_go():
    fake = HandBlocks()
    seen = Steady(fake)
    await seen.capture()
    told = await Carrier(seen).carry(slot_middle(fake, 0), goal_at(fake, 0, [2, 2]), 400)
    assert told["measured"] and fake.placed == [(0, [2, 2])], (told, fake.missed)


class Stiff(Blocks):
    """A block game that picks a piece up only when it was pressed a while before the pointer moved."""
    NEEDS_S = 0.05

    async def press(self, x, y):
        import time
        self.pressed_at = time.monotonic()
        await super().press(x, y)

    async def move(self, x, y, duration_ms):
        import time
        if self.held and time.monotonic() - self.pressed_at < self.NEEDS_S and self.held[1] != (x, y):
            if not getattr(self, "moved", False):
                self.held = None        # moved too soon: nothing was picked up
                return
        self.moved = True
        await super().move(x, y, duration_ms)

    async def lift(self):
        self.moved = False
        await super().lift()


async def test_a_carry_that_finds_nothing_held_lets_go_and_presses_longer_at_once(monkeypatch):
    monkeypatch.setattr(carry, "HOLDS", (0.0, 0.08, 0.16))
    fake = Stiff()
    carrier = Carrier(fake)
    told = await carrier.carry(slot_middle(fake, 0), goal_at(fake, 0, [2, 2]), 400)
    assert told["measured"] and (told["hold_s"], told["tries"]) == (0.08, 2) and fake.placed == [(0, [2, 2])]
    # the press that picked it up is the one from now on
    second = await carrier.carry(slot_middle(fake, 1), goal_at(fake, 1, [5, 4]), 400)
    assert (second["hold_s"], second["tries"]) == (0.08, 1) and second["measured"]


async def test_a_drag_the_model_marks_carried_is_aimed_by_where_the_held_piece_is_drawn():
    """In the model's own play, a carried drag's end is where the piece's middle goes; the code finds where the
    game draws it held (above the finger, larger) and tells the model."""
    fake = Blocks()
    goal = goal_at(fake, 0, [3, 1])
    start = list(slot_middle(fake, 0))
    model = Player([{"kind": "drag", "path": [start, goal], "ms": 400, "hold_ms": 0, "carry": True},
                    {"kind": "done", "end": "won"}])
    played, made = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(10, 1, 60))
    assert fake.placed == [(0, [3, 1])], (played.record, fake.missed)
    seen = model.asked[1]["history"][0]
    assert seen["screen_changed"] and seen["picked_up"] and max(map(abs, seen["landed_off_by"])) <= 12, seen
    assert played.moves[0]["carried"]["picked"]


class Springy(Lamps):
    """The lamp game played by dragging: a lamp lights for a drag pressed at least 400 ms before it moved."""
    kinds = frozenset({"tap", "drag"})

    def __init__(self):
        super().__init__()
        self.holds = []

    async def stroke(self, points, duration_ms, hold_ms):
        self.holds.append(hold_ms)
        if hold_ms >= 400 and self.lit < 3:
            self.lit += 1


async def test_a_drag_that_missed_says_nothing_of_its_press_when_nothing_showed_whether_it_was_picked_up():
    """A plain drag that did nothing may have missed its place as well as its press: no press is set from it."""
    fake = Springy()
    drag = {"kind": "drag", "path": [[300, 325], [300, 150]], "ms": 300}
    model = Player([{**drag, "hold_ms": 150}, {**drag, "hold_ms": 500}, {**drag, "hold_ms": 150},
                    {"kind": "done", "end": "won"}])
    played, made = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(10, 1, 60))
    assert fake.holds == [150, 500, 150], played.record
    assert all("drag_hold_ms_at_least" not in asked for asked in model.asked)


async def test_in_the_models_own_play_a_carried_drag_that_picked_nothing_up_is_pressed_longer_without_asking(monkeypatch):
    monkeypatch.setattr(carry, "HOLDS", (0.0, 0.08, 0.16))
    fake = Stiff()
    start = list(slot_middle(fake, 0))
    drag = {"kind": "drag", "path": [start, goal_at(fake, 0, [2, 2])], "ms": 400, "hold_ms": 0, "carry": True}
    model = Player([drag, {"kind": "done", "end": "won"}])
    played, made = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(10, 1, 60))
    assert fake.placed == [(0, [2, 2])], (played.record, fake.missed)
    seen = model.asked[1]["history"][0]
    assert (seen["picked_up"], seen["held_ms"], seen["screen_changed"]) == (True, 80, True)


async def test_a_carried_drag_that_picked_the_piece_up_and_missed_sets_no_press():
    fake = Blocks()
    start = list(slot_middle(fake, 0))
    missed = {"kind": "drag", "path": [start, [5, 5]], "ms": 400, "hold_ms": 0, "carry": True}
    placed = {**missed, "path": [start, goal_at(fake, 0, [2, 2])], "hold_ms": 500}
    model = Player([missed, placed, {"kind": "done", "end": "won"}])
    played, made = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(10, 1, 60))
    assert fake.placed == [(0, [2, 2])], (played.record, fake.missed)
    assert model.asked[1]["history"][0]["picked_up"] and "drag_hold_ms_at_least" not in model.asked[2]


class HandLamps(Lamps):
    """The lamp game under a hand that keeps moving beside the lamps."""

    def __init__(self):
        super().__init__()
        # drawn dark: the page is nearly white
        self.circling = Circling([(30 + 60 * step, 230) for step in range(7)], colour=(40, 40, 40))

    async def capture(self, region=None):
        return self.circling.hand(await super().capture(region))


async def test_in_the_models_own_play_a_hand_that_keeps_moving_is_no_change_and_the_model_is_told_where_it_moves():
    fake = HandLamps()
    model = Player([BESIDE])
    played, made = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60))
    assert (played.record["outcome"], played.record["reason"]) == ("gave_up", "no_effect"), played.record
    assert not any(entry["screen_changed"] for entry in model.asked[-1]["history"])
    assert model.asked[0]["moving"]


class Tutorial(Mines):
    """A minesweeper game behind a tutorial screen that covers it whole until it is tapped."""

    def __init__(self, **options):
        self.tutorial = True
        super().__init__(**options)

    @property
    def covered(self):
        return self.tutorial

    @covered.setter
    def covered(self, value):
        pass

    async def capture(self, region=None):
        if not self.tutorial:
            return await super().capture(region)
        image = Image.new("RGB", MINES_SIZE, (30, 60, 150))
        ImageDraw.Draw(image).rectangle((380, 300, 520, 380), fill=(250, 200, 0))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    async def tap(self, x, y, duration_ms):
        if self.tutorial:
            self.taps.append((x, y))
            self.tutorial = False
            return
        await super().tap(x, y, duration_ms)


class Guide(Reader):
    """The model: it names minesweeper for the game, plays the tutorial itself with a tap, and reads the board."""

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "families" in context:
            return TurnResult(text=json.dumps({"screen": "game", "families": ["minesweeper"], "situation": "a tutorial"}))
        if context.get("play"):
            self.asked.append(("play", context, 1))
            return TurnResult(text=json.dumps({"situation": "a tutorial", "score": None,
                                               "action": {"kind": "tap", "at": [450, 340]},
                                               "expect": "the tutorial closes", "notes": ""}))
        return await super().complete(request)


async def test_a_family_whose_board_a_tutorial_hid_is_gone_back_to_once_the_models_own_play_got_past_it():
    fake = Tutorial()
    model = Guide(fake)
    played, made = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(6, COMPUTE, 60))
    assert made["resumes"] == [{"family": "minesweeper", "at_action": 1, "taken": True, "read_by": "model"}], (played.record, made)
    assert played.record["family"] == "minesweeper" and played.record["levels"]["L0"] == 1
    assert played.record["levels"]["L2"] >= 1 and fake.played >= 1


class Slow(Reader):
    """The model: it names minesweeper, cannot read the board at first (as under a tutorial's last step, which
    changes the screen only a little), and plays four safe cells itself; after that it reads the board."""
    TAPS = [(6, 0), (6, 2), (6, 3), (6, 4), (6, 5)]

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "families" in context:
            return TurnResult(text=json.dumps({"screen": "game", "families": ["minesweeper"], "situation": "a board"}))
        if context.get("play"):
            self.asked.append(("play", context, 1))
            row, col = self.TAPS[min(self.fake.played, len(self.TAPS) - 1)]
            return TurnResult(text=json.dumps({"situation": "a board", "score": None,
                                               "action": {"kind": "tap", "at": [150 + col * 48 + 24, 100 + row * 48 + 24]},
                                               "expect": "a cell opens", "notes": ""}))
        if self.fake.played < 4:
            return TurnResult(text=json.dumps({"screen": "other", "board": None, "rules": None, "state": None}))
        return await super().complete(request)


async def test_the_family_is_tried_again_after_some_of_the_models_own_moves_even_when_the_screen_changed_little():
    fake = Mines(draw=0)
    model = Slow(fake)
    played, made = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(8, COMPUTE, 60))
    assert made["resumes"] == [{"family": "minesweeper", "at_action": 4, "taken": True, "read_by": "model"}], (played.record, made)
    assert played.record["levels"]["L0"] == 4 and played.record["levels"]["L2"] >= 1


class Decoy(Blocks):
    """A block game that, while a piece is held, also draws a thing of the piece's own shape and colours right by
    the pointer, moving on at every capture whatever the pointer does (a tutorial's ghost piece)."""

    def __init__(self, **options):
        super().__init__(**options)
        self.shown = 0

    async def capture(self, region=None):
        png = await super().capture(region)
        if not self.held:
            return png
        from test_adaptation_blocks import PIECE
        self.shown += 1
        image = Image.open(BytesIO(png)).convert("RGB")
        x, y = self.held[1]
        # always nearer the pointer than the piece held, which hangs above it
        left = x - 70 + 30 * (self.shown % 3)
        ImageDraw.Draw(image).rectangle((left, y + 5, left + 139, y + 48), fill=PIECE)
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


async def test_what_moves_by_the_pointer_but_not_with_it_is_not_taken_for_the_piece_held():
    fake = Decoy()
    told = await Carrier(fake).carry(slot_middle(fake, 0), goal_at(fake, 0, [2, 2]), 400)
    assert told["measured"] and fake.placed == [(0, [2, 2])], (told, fake.placed)


class Frozen(Mines):
    """A minesweeper board that takes no tap on its cells (as a tutorial that waits for one move only)."""

    async def tap(self, x, y, duration_ms):
        self.taps.append((x, y))


class Watching(Reader):
    """The model: it reads the board, and when it plays the game itself it says the game is over."""

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if context.get("play"):
            self.asked.append(("play", context, 1))
            return TurnResult(text=json.dumps({"situation": "nothing moves", "score": None,
                                               "action": {"kind": "done", "end": "lost"}, "expect": "", "notes": ""}))
        return await super().complete(request)


async def test_moves_the_game_keeps_refusing_leave_the_family_for_the_models_own_play():
    """Many cells are open to a tap: leaving out each refused one alone would go on for ever."""
    fake = Frozen()
    model = Watching(fake)
    played, made = await play(fake, model, "minesweeper", goal=GoalSpec("play_until_end"), budget=Budget(40, COMPUTE, 60))
    assert (played.record["outcome"], played.record["reason"]) == ("game_over", "end_screen"), (played.record, made)
    assert played.record["levels"]["L2"] == agent.FRUITLESS_MOST
    assert {"reason": "family_left", "detail": "fruitless"} in made["generation_failures"]
    assert sum(kind == "play" for kind, _, _ in model.asked) == 1


class Following(Blocks):
    """A block game whose held piece moves `gain` times the finger's way from where it was pressed, and at each
    capture catches up only `catch` of what it is still behind (as a game that eases it after the finger)."""

    def __init__(self, gain=1.0, catch=1.0, **options):
        super().__init__(**options)
        self.gain, self.catch, self.shown, self.start = gain, catch, None, None

    async def press(self, x, y):
        await super().press(x, y)
        self.start = self.shown = (x, y)

    async def capture(self, region=None):
        if self.held:
            (sx, sy), (x, y) = self.start, self.held[1]
            target = (sx + self.gain * (x - sx), sy + self.gain * (y - sy))
            self.shown = (self.shown[0] + self.catch * (target[0] - self.shown[0]),
                          self.shown[1] + self.catch * (target[1] - self.shown[1]))
        return await super().capture(region)

    def _held_box(self):
        kept = self.held
        self.held = (kept[0], self.shown)
        try:
            return Blocks._held_box(self)
        finally:
            self.held = kept


@pytest.mark.parametrize("gain", [0.8, 1.0, 1.45])
@pytest.mark.parametrize("anchor", [[2, 2], [0, 3], [5, 0]])
async def test_a_held_piece_drawn_further_than_the_finger_and_late_is_put_where_it_was_meant_to_go(gain, anchor):
    """The drawing runs ahead of the finger (or behind it) and catches up a moment late: the carry looks once it
    rests, learns how it follows the pointer from two places, and lands it in one press."""
    fake = Following(gain=gain, catch=0.5)
    told = await Carrier(fake).carry(slot_middle(fake, 0), goal_at(fake, 0, anchor), 400)
    assert fake.placed == [(0, anchor)] and fake.missed == 0, (told, fake.placed)
    assert told["measured"] and told["picked"] and told["tries"] == 1 and abs(told["gain"] - gain) <= 0.08, told


async def test_a_carry_that_could_not_tell_how_the_drawing_follows_aims_it_by_the_carries_before():
    fake = Following(gain=1.45)
    carrier = Carrier(fake)
    first = await carrier.carry(slot_middle(fake, 0), goal_at(fake, 0, [2, 2]), 400)
    assert first["measured"] and fake.placed == [(0, [2, 2])]
    # the next one is looked at once only on the way: the gain the first measured stands in
    carry_step = carry.STEP_PX
    try:
        carry.STEP_PX = carry.STEP_LEAST = 0.0
        second = await carrier.carry(slot_middle(fake, 1), goal_at(fake, 1, [5, 4]), 400)
    finally:
        carry.STEP_PX, carry.STEP_LEAST = carry_step, 32.0
    assert second["picked"] and not second["measured"] and fake.placed[-1] == (1, [5, 4]), (second, fake.placed)


class Glinting:
    """A still screen with a glint that blinks in one small place at every capture, and a change elsewhere
    when told to."""

    def __init__(self):
        self.captures, self.shown, self.changed = 0, 0, False

    async def capture(self, region=None):
        self.captures += 1
        self.shown += 1
        image = Image.new("RGB", (400, 600), (30, 30, 60))
        draw = ImageDraw.Draw(image)
        draw.rectangle((100, 100, 300, 300), fill=(200, 120, 40))
        if self.shown % 2:
            draw.ellipse((150, 150, 170, 170), fill=(255, 255, 255))
        if self.changed:
            draw.rectangle((100, 400, 300, 450), fill=(40, 200, 120))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


async def test_a_small_glint_found_once_is_not_drawn_through_again_until_something_else_changes_or_a_read_fails():
    fake = Glinting()
    seen = Steady(fake)
    await seen.capture()
    assert seen.animating and fake.captures == motion.FRAMES
    # nothing changed but the glint: one capture, given as it is with where the glint is
    before = fake.captures
    await seen.capture()
    assert fake.captures == before + 1 and seen.animating and seen.quick == 1
    # something changed elsewhere and then held still: two captures tell it, no drawing through
    fake.changed = True
    before = fake.captures
    png = await seen.capture()
    with Image.open(BytesIO(png)) as image:
        assert image.getpixel((200, 420)) == (40, 200, 120)
    assert fake.captures == before + 2 and seen.animating
    # a reading that failed: the next capture draws through again
    seen.forget()
    before = fake.captures
    await seen.capture()
    assert fake.captures == before + motion.FRAMES


async def test_once_carries_agree_on_how_the_drawing_follows_a_carry_looks_once_and_a_miss_sends_it_back_to_measuring():
    fake = Following(gain=1.45, catch=0.5)
    carrier = Carrier(fake)
    goals = [(0, [2, 2]), (1, [5, 4]), (2, [0, 0])]
    told = []
    for slot, anchor in goals:
        told.append(await carrier.carry(slot_middle(fake, slot), goal_at(fake, slot, anchor), 400))
    assert fake.placed == goals and not fake.missed, (told, fake.placed)
    assert told[0]["measured"] and told[1]["measured"] and told[2].get("quick") and len(told[2]["looks"]) == 2
    carrier.doubt()
    fake.state = fake.family.initial_state(3)
    again = await carrier.carry(slot_middle(fake, 0), goal_at(fake, 0, [3, 3]), 400)
    assert again["measured"] and not again.get("quick")


async def test_a_doubted_carry_that_cannot_measure_is_still_aimed_by_what_was_measured_before():
    """A miss sends the carries back to measuring, but one whose looks cannot tell how the drawing follows is
    aimed by the gain measured before, not as if the drawing kept to the finger (2026-11-16: two cells off)."""
    fake = Following(gain=1.45)
    carrier = Carrier(fake)
    first = await carrier.carry(slot_middle(fake, 0), goal_at(fake, 0, [2, 2]), 400)
    assert first["measured"] and fake.placed == [(0, [2, 2])]
    carrier.doubt()
    carry_step = carry.STEP_PX
    try:
        carry.STEP_PX = carry.STEP_LEAST = 0.0
        second = await carrier.carry(slot_middle(fake, 1), goal_at(fake, 1, [5, 4]), 400)
    finally:
        carry.STEP_PX, carry.STEP_LEAST = carry_step, 32.0
    assert not second["measured"] and not second.get("quick") and abs(second["gain"] - 1.45) <= 0.08, second
    assert fake.placed[-1] == (1, [5, 4]), (second, fake.placed)


class Twinkling(Following):
    """A block game whose board twinkles over one place when told to (`field`): dots in the pieces' own colour that
    change at every capture, close enough together to be one change the size of the place (as gems of a piece's
    colour glinting over a board, 2026-11-16)."""

    def __init__(self, **options):
        super().__init__(**options)
        self.field, self.shown_twinkles = None, 0

    async def capture(self, region=None):
        png = await super().capture(region)
        if self.field is None or region is not None:
            return png
        self.shown_twinkles += 1
        image = Image.open(BytesIO(png)).convert("RGB")
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = self.field
        for y in range(top, bottom, 9):
            for x in range(left, right, 9):
                if (x // 9 + y // 9 + self.shown_twinkles) % 2:
                    draw.rectangle((x, y, x + 3, y + 3), fill=BLOCKS_PIECE)
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


async def test_a_twinkling_board_is_not_taken_for_the_held_piece_once_its_size_is_known():
    """A carry that could see the held piece once only, over a board that twinkles round where the pointer stops
    first, in the piece's colour and proportions: the twinkling place is no piece of the size the carries before
    measured, and the piece lands where it was meant to (2026-11-16: taken for the piece, it hung the piece from the
    board's middle and let go off the board)."""
    fake = Twinkling(gain=1.45, catch=0.5)
    carrier = Carrier(fake)
    first = await carrier.carry(slot_middle(fake, 1), goal_at(fake, 1, [0, 0]), 400)
    assert first["measured"] and fake.placed == [(1, [0, 0])], first
    # the third piece (a single block) to the top of the board: the pointer's first stop is in the middle of a
    # twinkling square three pieces wide, well clear of where the piece was pressed
    start, goal = slot_middle(fake, 2), goal_at(fake, 2, [0, 6])
    stop = (start[0] + 0.2 * (goal[0] - start[0]), start[1] + 0.2 * (goal[1] - start[1]))
    side = 3 * 2 * CELL
    fake.field = (round(stop[0] - side / 2), round(start[1] - 24 - side), round(stop[0] + side / 2), round(start[1] - 24))
    carry_step = carry.STEP_PX
    try:
        carry.STEP_PX = carry.STEP_LEAST = 0.0
        second = await carrier.carry(start, goal, 400)
    finally:
        carry.STEP_PX, carry.STEP_LEAST = carry_step, 32.0
    assert fake.placed[-1] == (2, [0, 6]), (second, fake.placed)


def test_changes_where_small_things_keep_moving_are_left_out_and_a_place_of_another_size_is_no_held_piece():
    image = Image.new("RGB", (200, 200), (20, 20, 60))
    before = BytesIO()
    image.save(before, format="PNG")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 17, 17), fill=(255, 255, 255))        # a glint
    draw.rectangle((100, 100, 147, 147), fill=(200, 80, 40))      # a drawing 48 a side
    after = BytesIO()
    image.save(after, format="PNG")
    assert len(carry._boxes(before.getvalue(), after.getvalue())) == 2
    assert carry._boxes(before.getvalue(), after.getvalue(), ignore=[[8, 8, 12, 12]]) == [[100, 100, 48, 48]]
    assert carry._scaled([100, 100, 48, 48], [0, 0, 24, 24], 2.0)
    assert not carry._scaled([0, 0, 200, 200], [0, 0, 24, 24], 2.0)


def test_a_carry_is_trusted_when_it_picked_the_piece_up_and_knew_how_it_hung():
    assert carry.trusted({"picked": True, "measured": True})
    assert carry.trusted({"picked": True, "quick": True})
    assert not carry.trusted({"picked": True, "measured": False, "why": "nothing_followed"})
    assert not carry.trusted({"picked": False, "measured": True})
    assert not carry.trusted(None)
