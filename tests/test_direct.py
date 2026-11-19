"""A game no family is registered for, played by the model's own inputs (PLAN §11, H2): the loop sends each
input, tells the model what the code saw change, and ends the game when the model reads its end, when nothing
more can be done, or at a budget. The game here is a toy nobody has rules for: a button that lights one more
lamp each time it is pressed, and a banner once three are lit."""
from io import BytesIO
import json

import pytest
from PIL import Image, ImageDraw

from gamelab import agent, direct, recovery
from gamelab.agent import play
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult

SIZE = (600, 400)
BUTTON = [250, 300, 100, 50]
PRESS = {"kind": "tap", "at": [300, 325]}
BESIDE = {"kind": "tap", "at": [50, 50]}


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(direct, "SETTLE_S", 0.0)
    monkeypatch.setattr(direct, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)


class Lamps:
    """A surface showing the toy: `lit` lamps of three, the button under them, typed letters in a row above."""
    name = "fake"
    reads_dom = False
    kinds = frozenset({"keys", "tap", "drag"})

    def __init__(self):
        self.lit, self.taps, self.keys = 0, [], []

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "https://lamps.example/"

    async def capture(self, region=None):
        image = Image.new("RGB", SIZE, (245, 245, 245))
        draw = ImageDraw.Draw(image)
        for lamp in range(3):
            draw.rectangle((150 + lamp * 110, 120, 230 + lamp * 110, 200), fill=(250, 200, 0) if lamp < self.lit else (90, 90, 90))
        for index, _ in enumerate(self.keys):
            draw.rectangle((20 + index * 30, 20, 40 + index * 30, 40), fill=(0, 0, 0))
        x, y, w, h = BUTTON
        draw.rectangle((x, y, x + w, y + h), fill=(40, 120, 220))
        if self.lit == 3:
            draw.rectangle((100, 40, 500, 90), fill=(30, 160, 60))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    async def tap(self, x, y, duration_ms):
        self.taps.append((x, y))
        left, top, width, height = BUTTON
        if left <= x < left + width and top <= y < top + height and self.lit < 3:
            self.lit += 1

    async def key(self, key):
        self.keys.append(key)

    async def drag(self, *args):
        raise AssertionError("nothing here is dragged")

    async def release(self):
        pass


class Player:
    """A model that knows no family for the screen and plays it with each of `answers` in turn (the last one
    again when they run out)."""

    def __init__(self, answers, families=()):
        self.answers, self.families, self.asked = list(answers), list(families), []

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "families" in context:
            return TurnResult(text=json.dumps({"screen": "game", "families": self.families, "situation": "three lamps"}))
        if not context.get("play"):
            return TurnResult(text=json.dumps({"screen": "other", "board": None, "rules": None, "state": None}))
        self.asked.append(context)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return TurnResult(text=answer if isinstance(answer, str) else json.dumps({
            "situation": "lamps and a button", "score": len(self.asked) - 1, "action": answer,
            "expect": "one more lamp lights", "notes": f"asked {len(self.asked)} times"}))


def ending(played):
    return played.record["outcome"], played.record["reason"]


async def game(answers, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60), **options):
    fake, model = Lamps(), Player(answers, **options)
    played, made = await play(fake, model, None, goal=goal, budget=budget)
    return fake, model, played, made


async def test_a_game_of_no_registered_family_is_played_by_the_model_and_told_what_each_input_changed():
    fake, model, played, made = await game([PRESS, PRESS, PRESS, {"kind": "done", "end": "won"}])
    assert ending(played) == ("goal_reached", "won"), (played.record, made)
    assert fake.lit == 3 and fake.taps == [(300, 325)] * 3
    assert played.record["family"] is None and played.record["policy"] == "model"
    assert played.record["levels"] == {"L2": 0, "L3": 0, "L0": 3} and played.record["confirmed_actions"] == 3
    assert [move["effect"] for move in played.moves] == ["confirmed"] * 3
    # each question after the first carries what the code saw of the input before it, and the model's own notes
    assert model.asked[0]["history"] == [] and model.asked[0]["memory"] == {}
    seen = model.asked[1]["history"][0]
    assert seen["action"] == PRESS and seen["screen_changed"] and seen["expected"] == "one more lamp lights"
    # the first lamp, where it is drawn
    assert any(140 <= left <= 160 and 110 <= top <= 130 for left, top, _, _ in seen["changed_at"])
    # (notes given as one text, as a model once wrote them, are its plan)
    assert model.asked[1]["memory"] == {"plan": "asked 1 times"} and model.asked[0]["goal"]["objective"] == "play_until_end"
    assert made["direct"]["questions"] == 4 and (made["direct"]["score_first"], made["direct"]["score_last"]) == (0, 3)
    # which game it is, whether it can be played by written rules (it is no grid: no), then the four of the play
    assert made["identified"]["candidates"] == [] and made["questions"] == 6 and "made" not in made


async def test_a_family_the_board_cannot_be_read_as_leaves_the_game_to_the_model():
    """The model names a family for the screen; read by that family's description the screen gives no board,
    so nothing is played by it and the model plays the game itself."""
    fake, model, played, made = await game([PRESS, PRESS, PRESS, {"kind": "done", "end": "won"}], families=["minesweeper"])
    assert ending(played) == ("goal_reached", "won") and fake.lit == 3
    assert made["identified"]["tried"] == ["minesweeper"] and made["identified"]["family"] is None


async def test_inputs_that_change_nothing_end_the_game_and_the_model_is_told_they_changed_nothing():
    fake, model, played, made = await game([BESIDE])
    assert ending(played) == ("gave_up", "no_effect"), (played.record, made)
    assert len(fake.taps) == agent.MAX_IDLE and played.record["confirmed_actions"] == 0
    assert not model.asked[1]["history"][0]["screen_changed"] and model.asked[1]["history"][0]["changed_at"] == []


async def test_an_answer_that_is_no_input_is_not_sent_and_three_in_a_row_end_the_game():
    for answer in ({"kind": "tap", "at": [900, 900]}, {"kind": "press"}, "not json"):
        fake, model, played, made = await game([answer])
        assert ending(played) == ("gave_up", "model_no_answer"), (played.record, made)
        assert not fake.taps and len(model.asked) == agent.MAX_SILENT


async def test_a_count_of_inputs_that_changed_the_screen_is_a_goal_and_the_budget_of_inputs_an_end():
    fake, model, played, made = await game([PRESS], goal=GoalSpec("actions", 2))
    assert ending(played) == ("goal_reached", "actions") and fake.lit == 2
    fake, model, played, made = await game([PRESS, PRESS, PRESS, {"kind": "wait", "ms": 1}, BESIDE], budget=Budget(5, 1, 60))
    assert ending(played) == ("budget_exhausted", "actions") and len(fake.taps) == 4
    # what is left is told with each question
    assert model.asked[0]["left"]["inputs"] == 5 and model.asked[4]["left"]["inputs"] == 1


async def test_letters_are_typed_one_key_after_another_and_the_models_end_is_the_games():
    fake, model, played, made = await game([{"kind": "text", "text": "crane"}, {"kind": "key", "key": "Enter"},
                                            {"kind": "done", "end": "lost"}])
    assert fake.keys == ["c", "r", "a", "n", "e", "Enter"]
    assert ending(played) == ("game_over", "end_screen")
    fake, model, played, made = await game([{"kind": "give_up"}])
    assert ending(played) == ("gave_up", "model_gave_up") and not fake.taps


ALL = frozenset({"keys", "tap", "drag", "text", "scroll", "back", "secondary", "relaunch"})


def test_the_new_inputs_are_checked_against_the_screen_and_what_the_surface_can_do():
    size = (600, 400)
    assert direct.check({"kind": "drag", "path": [[10, 10], [50, 10], [50, 90]]}, size, ALL) == {
        "kind": "drag", "path": [[10.0, 10.0], [50.0, 10.0], [50.0, 90.0]], "ms": direct.DRAG_MS, "hold_ms": direct.HOLD_MS}
    assert direct.check({"kind": "drag", "path": [[10, 10], [50, 10]], "ms": 900, "hold_ms": 0}, size, ALL)["ms"] == 900
    for wrong in ({"kind": "drag", "path": [[10, 10]]}, {"kind": "drag", "path": [[10, 10], [700, 10]]},
                  {"kind": "drag", "path": [[10, 10], [20, 10]], "ms": 50},
                  {"kind": "drag", "path": [[10, 10]] * (direct.PATH_MOST + 1)},
                  {"kind": "long_press", "at": [10, 10], "ms": 100}, {"kind": "scroll", "at": [10, 10], "steps": 0},
                  {"kind": "scroll", "at": [10, 10], "steps": 11}, {"kind": "wait", "ms": 6000},
                  {"kind": "wait", "ms": 20000, "until_change": True},
                  {"kind": "sequence", "inputs": [PRESS]},
                  {"kind": "sequence", "inputs": [PRESS, {"kind": "wait", "ms": 10}]},
                  {"kind": "sequence", "inputs": [PRESS, {"kind": "relaunch"}]}):
        assert direct.check(wrong, size, ALL) is None, wrong
    assert direct.check({"kind": "long_press", "at": [10, 10]}, size, ALL)["ms"] == direct.LONG_PRESS_MS
    assert direct.check({"kind": "wait", "ms": 12000, "until_change": True}, size, ALL) == {
        "kind": "wait", "ms": 12000, "until_change": True}
    assert direct.check({"kind": "sequence", "inputs": [PRESS, BESIDE]}, size, ALL) == {
        "kind": "sequence", "inputs": [{"kind": "tap", "at": [300.0, 325.0]}, {"kind": "tap", "at": [50.0, 50.0]}]}
    # what the surface cannot do is no input: a page has no back button, a phone no other mouse button
    phone = frozenset({"tap", "drag", "hold", "text", "scroll", "back", "relaunch"})
    assert direct.check({"kind": "back"}, size, phone) == {"kind": "back"}
    assert direct.check({"kind": "right_tap", "at": [5, 5]}, size, phone) is None
    assert direct.check({"kind": "back"}, size, ALL - {"back"}) is None
    # typed letters go to a surface that types, or one that presses keys
    assert direct.check({"kind": "text", "text": "ab"}, size, frozenset({"text"})) is not None
    assert direct.check({"kind": "text", "text": "ab"}, size, frozenset({"tap"})) is None


def test_every_point_of_an_input_is_brought_from_the_picture_to_the_screen():
    told = {"kind": "sequence", "inputs": [{"kind": "tap", "at": [10, 20]},
                                           {"kind": "drag", "path": [[10, 10], [30, 40]], "ms": 600, "hold_ms": 0}]}
    assert direct.scaled(told, 0.5) == {"kind": "sequence", "inputs": [
        {"kind": "tap", "at": [20.0, 40.0]}, {"kind": "drag", "path": [[20.0, 20.0], [60.0, 80.0]], "ms": 600, "hold_ms": 0}]}


class Hands(Lamps):
    """The toy on a surface that takes every input: a tap on the button and a stroke each light a lamp, and
    every other input is written down. Taps are kept in `pressed`, as `taps` is an input here."""
    kinds = ALL

    def __init__(self):
        super().__init__()
        del self.taps
        self.pressed, self.sent = [], []

    async def tap(self, x, y, duration_ms):
        self.pressed.append((x, y))
        left, top, width, height = BUTTON
        if left <= x < left + width and top <= y < top + height and self.lit < 3:
            self.lit += 1

    async def taps(self, x, y, count, gap_ms):
        self.sent.append(("taps", x, y, count, gap_ms))

    async def stroke(self, points, duration_ms, hold_ms):
        self.sent.append(("stroke", points, duration_ms, hold_ms))
        self.lit = min(3, self.lit + 1)

    async def type(self, text):
        self.sent.append(("type", text))

    async def scroll(self, x, y, steps):
        self.sent.append(("scroll", x, y, steps))

    async def back(self):
        self.sent.append(("back",))

    async def right_tap(self, x, y):
        self.sent.append(("right_tap", x, y))

    async def relaunch(self):
        self.sent.append(("relaunch",))


async def test_the_new_inputs_are_sent_as_the_surface_carries_them_out(monkeypatch):
    monkeypatch.setattr(direct, "SEQUENCE_GAP_S", 0.0)
    fake, model = Hands(), Player([
        {"kind": "sequence", "inputs": [PRESS, PRESS]}, {"kind": "double_tap", "at": [10, 20]},
        {"kind": "long_press", "at": [10, 20], "ms": 500}, {"kind": "drag", "path": [[1, 2], [3, 4]]},
        {"kind": "text", "text": "hi"}, {"kind": "scroll", "at": [5, 6], "steps": -2}, {"kind": "back"},
        {"kind": "right_tap", "at": [7, 8]}, {"kind": "done", "end": "won"}])
    played, made = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60))
    assert ending(played) == ("goal_reached", "won"), (played.record, made)
    assert fake.pressed == [(300, 325), (300, 325), (10, 20)] and fake.lit == 3
    assert fake.sent == [("taps", 10, 20, 2, direct.DOUBLE_TAP_GAP_MS),
                         ("stroke", [[1.0, 2.0], [3.0, 4.0]], direct.DRAG_MS, direct.HOLD_MS), ("type", "hi"),
                         ("scroll", 5, 6, -2), ("back",), ("right_tap", 7, 8)]
    # the inputs the model is told it has are the surface's own
    assert set(model.asked[0]["inputs"]) == set(direct.NEEDS) | {"wait"}
    assert model.asked[0]["inputs"] == sorted(model.asked[0]["inputs"][:-1]) + ["wait"]


async def test_the_game_is_started_again_no_more_than_twice_and_a_wait_may_end_when_the_screen_changes(monkeypatch):
    monkeypatch.setattr(direct, "CHANGE_LOOK_S", 0.0)
    fake, model = Hands(), Player([{"kind": "relaunch"}, {"kind": "relaunch"}, {"kind": "relaunch"}])
    played, made = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60))
    assert fake.sent == [("relaunch",)] * 2 and ending(played) == ("gave_up", "model_no_answer")
    assert "relaunch" in model.asked[1]["inputs"] and "relaunch" not in model.asked[2]["inputs"]

    class Slow(Hands):
        looks = 0

        async def capture(self, region=None):
            self.looks += 1
            self.lit = 1 if self.looks > 3 else 0
            return await super().capture(region)

    surface = Slow()
    await direct.send(surface, {"kind": "wait", "ms": 15000, "until_change": True})
    # the look before the wait, then until the lamp lit: not the whole 15 s at a look each half second
    assert surface.looks == 4
