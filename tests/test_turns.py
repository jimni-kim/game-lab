"""Waiting for the player's turn by a light judge's yes or no (`turns`, 2026-11-24): a toy game played in turns
whose screen never comes to rest (a timer ticks at every look), where the other side takes a while."""
from io import BytesIO
import json
import time

import pytest
from PIL import Image, ImageDraw

from gamelab import agent, direct, recovery, turns
from gamelab.agent import play
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult, Usage

SIZE = (400, 600)
CARD = [200, 450]


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(direct, "SETTLE_S", 0.0)
    monkeypatch.setattr(direct, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(direct, "CHANGE_LOOK_S", 0.02)
    monkeypatch.setattr(direct, "REST_S", 0.3)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    monkeypatch.setattr(turns, "LOOK_S", 0.02)
    monkeypatch.setattr(turns, "FIRST_S", 0.0)
    monkeypatch.setattr(turns, "ASK_EVERY_S", 0.05)


class Table:
    """A card table: a tap on the card plays it and the other side plays for `OTHER_S`; the player's hand is drawn
    lit (yellow) only at the player's own turn. A timer bar changes at every capture, whoever's turn it is."""
    name = "fake"
    reads_dom = False
    kinds = frozenset({"tap"})
    OTHER_S = 0.4

    def __init__(self):
        self.played, self.ticks, self.until = 0, 0, 0.0

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "https://table.example/"

    def mine(self):
        return time.monotonic() >= self.until

    async def capture(self, region=None):
        self.ticks += 1
        image = Image.new("RGB", SIZE, (20, 90, 40))
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 20 + (self.ticks * 37) % 360, 40), fill=(250, 250, 250))
        draw.rectangle((150, 400, 250, 500), fill=(250, 220, 0) if self.mine() else (90, 90, 90))
        draw.rectangle((20, 80, 20 + self.played * 30, 100), fill=(0, 0, 250))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    async def tap(self, x, y, duration_ms):
        if self.mine() and 150 <= x <= 250 and 400 <= y <= 500:
            self.played += 1
            self.until = time.monotonic() + self.OTHER_S

    async def release(self):
        pass


class Judge:
    """Says yes when the hand in the picture is lit; counts what it was asked."""
    name = "judge"

    def __init__(self, p_yes=0.95):
        self.asked, self.p_yes = 0, p_yes

    async def ask(self, checks, context):
        self.asked += 1
        image = Image.open(BytesIO(checks[0].image)).convert("RGB")
        x, y = image.width // 2, round(image.height * 450 / SIZE[1])
        lit = image.getpixel((x, y))[0] > 200
        assert "hand is lit" in checks[0].instructions
        return {"turn": {"p": self.p_yes if lit else 0.05}}


class Model:
    """Plays a card at its turn and says what its turn looks like; waits when it is asked at the other side's."""

    def __init__(self, game, cue=True, rounds=3, once=False):
        # `once`: the turn is described in the first answer alone, at its top, with no then_wait_ms on any input
        self.game, self.cue, self.rounds, self.once = game, cue, rounds, once
        self.plays = []

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "families" in context:
            return TurnResult(text=json.dumps({"screen": "game", "families": [], "situation": "a card table"}))
        if not context.get("play"):
            return TurnResult(text=json.dumps({"screen": "other", "fits": False}))
        self.plays.append(self.game.mine())
        if self.game.played >= self.rounds:
            return TurnResult(text=json.dumps({"situation": "over", "scene": "result", "action": {"kind": "done", "end": "won"}}))
        if not self.game.mine():
            return TurnResult(text=json.dumps({"situation": "their turn", "scene": "board", "expect": "my turn",
                                               "action": {"kind": "wait", "ms": 100}}), usage=Usage(input_tokens=100))
        if self.once:
            first = len(self.plays) == 1
            return TurnResult(text=json.dumps({"situation": "my turn", "scene": "board", "expect": "a card is played",
                                               "action": {"kind": "tap", "at": CARD},
                                               **({"turn_looks": "your hand is lit yellow", "my_turn": True} if first else {})}),
                              usage=Usage(input_tokens=100))
        action = {"kind": "tap", "at": CARD, "then_wait_ms": 2000,
                  **({"my_turn": "your hand is lit yellow"} if self.cue else {})}
        return TurnResult(text=json.dumps({"situation": "my turn", "scene": "board", "expect": "a card is played",
                                           "action": action}), usage=Usage(input_tokens=100))


async def test_the_model_is_asked_when_the_judge_sees_its_turn_back(monkeypatch):
    game, judge = Table(), Judge()
    model = Model(game)
    played, made = await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(30, 1, 60), turn=[judge])
    assert played.record["outcome"] == "goal_reached" and game.played == 3
    # every question came at the player's own turn: none was spent waiting
    assert all(model.plays) and len(model.plays) == 4
    waited = made["turns"]
    assert waited["waits"] == 3 and waited["yes"] == 3 and waited["noes_out"] == 0 and waited["false_yes"] == 0
    assert judge.asked == waited["asked"] >= 3 and waited["by"] == {"judge": waited["asked"]}


async def test_with_no_cue_or_no_judge_the_screen_is_waited_for_to_rest():
    # the screen never rests: the wait runs to its limit and the model may be asked at the other side's turn
    game = Table()
    game.OTHER_S = 2.5
    model = Model(game, cue=False, rounds=1)
    played, made = await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(30, 1, 60), turn=[Judge()])
    assert played.record["outcome"] == "goal_reached" and "turns" not in made
    assert False in model.plays


async def test_a_judge_unsure_hands_the_picture_to_the_next_and_a_wrong_yes_is_counted(monkeypatch):
    game = Table()
    unsure, careful = Judge(p_yes=0.5), Judge()
    played, made = await play(game, Model(game, rounds=1), None, goal=GoalSpec("play_until_end"), budget=Budget(30, 1, 60),
                              turn=[unsure, careful])
    assert made["turns"]["yes"] == 1 and careful.asked >= 1 and unsure.asked >= careful.asked
    # a yes the model then only waits at is counted as wrong
    always = Judge()

    async def yes(checks, context):
        always.asked += 1
        return {"turn": {"p": 0.99}}

    always.ask = yes
    game = Table()
    game.OTHER_S = 1.0
    played, made = await play(game, Model(game, rounds=2), None, goal=GoalSpec("play_until_end"), budget=Budget(30, 1, 60),
                              turn=[always])
    assert made["turns"]["false_yes"] >= 1


def test_the_cue_is_kept_with_an_input_that_hands_the_turn_over():
    action = {"kind": "tap", "at": [5, 5], "then_wait_ms": 4000, "my_turn": "  the hand is lit  "}
    assert direct.check(action, SIZE, frozenset({"tap"}))["my_turn"] == "the hand is lit"
    assert "my_turn" not in direct.check({"kind": "tap", "at": [5, 5], "my_turn": "x"}, SIZE, frozenset({"tap"}))
    assert turns.cue("") is None and len(turns.cue("x" * 500)) == turns.CUE_MOST


async def test_a_turn_described_once_is_kept_and_used_after_every_move_and_by_the_next_work(monkeypatch, tmp_path):
    from gamelab.adaptation import Store
    game, judge = Table(), Judge()
    model = Model(game, once=True)
    played, made = await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(30, 1, 60), turn=[judge],
                              store=Store(tmp_path))
    assert played.record["outcome"] == "goal_reached" and all(model.plays)
    assert made["turns"]["waits"] == 3 and made["turns"]["yes"] == 3
    assert Store(tmp_path).memory("https://table.example/")["turn_cue"] == "your hand is lit yellow"
    # the next work never says it: the kept words are used, and given back to the model
    game = Table()
    model = Model(game, once=True)
    model.plays.append(True)
    played, made = await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(30, 1, 60), turn=[Judge()],
                              store=Store(tmp_path))
    assert played.record["outcome"] == "goal_reached" and all(model.plays) and made["turns"]["yes"] == 3


async def test_judges_that_never_see_the_turn_hand_it_to_the_model_after_a_few_noes():
    class Blind(Judge):
        async def ask(self, checks, context):
            self.asked += 1
            return {"turn": {"p": 0.01}}

    game, blind = Table(), Blind()
    started = time.monotonic()
    played, made = await play(game, Model(game, rounds=2), None, goal=GoalSpec("play_until_end"), budget=Budget(30, 1, 60),
                              turn=[blind])
    waited = made["turns"]
    assert played.record["outcome"] == "goal_reached"
    # no time of its own: each wait ended on the noes, and the model played the turn the judge missed
    assert waited["noes_out"] >= 1 and waited["yes"] == 0 and waited["false_no"] >= 1
    assert blind.asked >= turns.NOES_MOST and time.monotonic() - started < 10


async def test_a_window_waiting_for_the_player_ends_the_wait_and_its_screen_is_asked_about(monkeypatch):
    class Popup(Table):
        """After the first card the game puts up a window over the table (the turn played for the player) until it
        is tapped away; the turn itself comes back only once it is gone."""
        def __init__(self):
            super().__init__()
            self.window = False

        async def capture(self, region=None):
            png = await super().capture(region)
            if not self.window:
                return png
            image = Image.open(BytesIO(png)).convert("RGB")
            ImageDraw.Draw(image).rectangle((100, 250, 300, 350), fill=(250, 0, 250))
            out = BytesIO()
            image.save(out, format="PNG")
            return out.getvalue()

        async def tap(self, x, y, duration_ms):
            if self.window and 100 <= x <= 300 and 250 <= y <= 350:
                self.window = False
                return
            before = self.played
            await super().tap(x, y, duration_ms)
            if self.played == 1 and before == 0:
                self.window = True

    class Seer(Judge):
        async def ask(self, checks, context):
            self.asked += 1
            image = Image.open(BytesIO(checks[0].image)).convert("RGB")
            w = image.getpixel((image.width // 2, round(image.height * 300 / SIZE[1])))
            lit = image.getpixel((image.width // 2, round(image.height * 450 / SIZE[1])))[0] > 200
            assert [check.key for check in checks] == ["turn", "notice"]
            return {"turn": {"p": 0.95 if lit else 0.05}, "notice": {"p": 0.95 if w == (250, 0, 250) else 0.02}}

    class Closer(Model):
        async def complete(self, request):
            context = json.loads(request.messages[0].content[0]["text"])
            if context.get("play") and self.game.window:
                self.plays.append("window")
                return TurnResult(text=json.dumps({"situation": "a window", "scene": "popup", "expect": "it closes",
                                                   "action": {"kind": "tap", "at": [200, 300]}}))
            return await super().complete(request)

    game, judge = Popup(), Seer()
    game.OTHER_S = 5.0
    started = time.monotonic()
    model = Closer(game, once=True, rounds=2)
    played, made = await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(30, 1, 60), turn=[judge])
    assert played.record["outcome"] == "goal_reached"
    # the window was seen at once, not after the other side's 5 s, and the model closed it
    assert made["turns"]["notice"] >= 1 and "window" in model.plays and model.plays.index("window") == 1
