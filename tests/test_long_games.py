"""A game that goes on over fights and stages, played by the model's own inputs (PLAN §36, session E3): an input
that hands the turn over is waited out, what a named thing does is kept once read, where the game stands outlasts
the work, and a game a model once saw to be none of the families is not asked about again. The game here is a toy
duel: the player's button lights a lamp, and the other side answers a moment later by lighting its own."""
from io import BytesIO
import json
import time

import pytest
from PIL import Image, ImageDraw

from gamelab import direct, recovery
from gamelab.adaptation import Store
from gamelab.agent import play
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult

SIZE = (600, 400)
PRESS = {"kind": "tap", "at": [300, 325]}
ANSWERS_AFTER_S = 0.25


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(direct, "SETTLE_S", 0.0)
    monkeypatch.setattr(direct, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(direct, "CHANGE_LOOK_S", 0.02)
    monkeypatch.setattr(direct, "REST_S", 0.4)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)


class Duel:
    """The player's lamps on the left, lit at once by the button; the other side's on the right, each lit
    `ANSWERS_AFTER_S` after the player's."""
    name = "fake"
    reads_dom = False
    kinds = frozenset({"tap"})

    def __init__(self):
        self.pressed: list[float] = []

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "https://duel.example/"

    async def capture(self, region=None):
        image = Image.new("RGB", SIZE, (245, 245, 245))
        draw = ImageDraw.Draw(image)
        answered = sum(time.monotonic() >= at + ANSWERS_AFTER_S for at in self.pressed)
        for lamp in range(len(self.pressed)):
            draw.rectangle((20 + lamp * 60, 120, 60 + lamp * 60, 160), fill=(250, 200, 0))
        for lamp in range(answered):
            draw.rectangle((320 + lamp * 60, 120, 360 + lamp * 60, 160), fill=(200, 40, 40))
        draw.rectangle((250, 300, 350, 350), fill=(40, 120, 220))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    async def tap(self, x, y, duration_ms):
        self.pressed.append(time.monotonic())

    async def release(self):
        pass


class Model:
    """Knows no family for the screen and plays it with each of `answers` in turn (the last again when they run
    out). `identified`: how often it was asked what the game is."""

    def __init__(self, answers):
        self.answers, self.asked, self.identified = list(answers), [], 0

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "families" in context:
            self.identified += 1
            return TurnResult(text=json.dumps({"screen": "game", "families": [], "situation": "a duel of lamps"}))
        if not context.get("play"):
            return TurnResult(text=json.dumps({"screen": "other", "fits": False}))
        self.asked.append(context)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return TurnResult(text=json.dumps({"situation": "lamps", "scene": "board", "expect": "a lamp lights", **answer}))


async def test_an_input_that_hands_the_turn_over_is_waited_out_until_the_screen_rests():
    over = {"action": {**PRESS, "then_wait_ms": 3000}}
    fake, model = Duel(), Model([over, {"action": {"kind": "sequence", "inputs": [PRESS, PRESS], "then_wait_ms": 3000}},
                                 {"action": {"kind": "done", "end": "won"}}])
    played, made = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60))
    assert played.record["outcome"] == "goal_reached" and len(fake.pressed) == 3
    # asked again only once the other side had answered: its lamp is among what changed, and the wait is told
    first = model.asked[1]["history"][-1]
    assert first["action"] == {**PRESS, "then_wait_ms": 3000} and first["repeated"]["waited_ms"] >= ANSWERS_AFTER_S * 1000
    assert any(left >= 300 for left, _, _, _ in first["changed_at"])
    both = model.asked[2]["history"][-1]
    assert both["repeated"]["sent"] == 2 and both["repeated"]["stopped"] == "count" and both["repeated"]["waited_ms"] > 0
    assert sum(left >= 300 for left, _, _, _ in both["changed_at"]) == 2
    assert made["handed_over"]["times"] == 2
    # the wait is kept within its range, and belongs to an input to a place only
    size, kinds = SIZE, frozenset({"tap"})
    assert direct.check({**PRESS, "then_wait_ms": 99000}, size, kinds)["then_wait_ms"] == direct.REST_MS_RANGE[1]
    assert "then_wait_ms" not in direct.check({"kind": "wait", "ms": 500, "then_wait_ms": 900}, size, kinds)
    assert "then_wait_ms" not in direct.check({**PRESS, "repeat": 3, "then_wait_ms": 900}, size, kinds)
    assert "then_wait_ms" not in direct.check({"kind": "sequence", "inputs": [{**PRESS, "then_wait_ms": 900}, PRESS]},
                                              size, kinds)["inputs"][0]


async def test_what_a_named_thing_does_is_kept_once_read_and_where_the_game_stands_outlasts_the_work(tmp_path):
    reads = {"action": PRESS, "learned": {"Fire Bolt": "attack, cost 1: 8 damage", "": "nameless", "Shield": 3},
             "memory": {"rules": "light more lamps than the other side", "run": "stage 2, 30 health", "plan": "press"}}
    fake, model = Duel(), Model([reads, {"action": PRESS, "learned": {"Ward": "skill, cost 1: 5 block"}},
                                 {"action": PRESS}, {"action": PRESS}, {"action": {"kind": "done", "end": "won"}}])
    played, made = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60),
                              store=Store(tmp_path))
    assert played.record["outcome"] == "goal_reached"
    assert "known" not in model.asked[0] and model.asked[1]["known"] == {"Fire Bolt": "attack, cost 1: 8 damage"}
    assert model.asked[2]["known"] == {"Fire Bolt": "attack, cost 1: 8 damage", "Ward": "skill, cost 1: 5 block"}
    assert "run_from_earlier_work" not in model.asked[1] and made["direct"]["learned"] == 2
    kept = Store(tmp_path).memory("https://duel.example/")
    assert kept == {"rules": "light more lamps than the other side", "run": "stage 2, 30 health",
                    "known": {"Fire Bolt": "attack, cost 1: 8 damage", "Ward": "skill, cost 1: 5 block"}}
    # the next work on the game is given both with its first question, the run as one of an earlier work, and
    # is not asked what the game is: a model that saw it in play named no family for it, at the start and once
    # more some moves on (once is not taken for it)
    assert model.identified == 2 and Store(tmp_path).unfamiliar("https://duel.example/") is not None
    again = Model([{"action": PRESS}, {"action": {"kind": "done", "end": "won"}}])
    played, made = await play(Duel(), again, None, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60),
                              store=Store(tmp_path))
    assert again.identified == 0 and made["identified"]["source"] == "kept_none" and made["questions"] == 2
    assert again.asked[0]["known"] == kept["known"] and again.asked[0]["memory"]["run"] == "stage 2, 30 health"
    assert again.asked[0]["run_from_earlier_work"] is True and "run_from_earlier_work" not in again.asked[1]
    # a family there was not to name then: the game is asked about again
    Store(tmp_path).save_unfamiliar("https://duel.example/", {"families": ["slide_merge"]})
    once_more = Model([{"action": {"kind": "done", "end": "won"}}])
    await play(Duel(), once_more, None, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60), store=Store(tmp_path))
    assert once_more.identified == 1
    # and a game seen so only once is not kept as one of no family
    assert Store(tmp_path / "other").unfamiliar("https://duel.example/") is None
    await play(Duel(), Model([{"action": {"kind": "done", "end": "won"}}]), None, goal=GoalSpec("play_until_end"),
               budget=Budget(20, 1, 60), store=Store(tmp_path / "other"))
    assert Store(tmp_path / "other").unfamiliar("https://duel.example/") is None


def test_no_more_named_things_are_kept_than_fit_a_question():
    player = direct.Direct(None, notes={"known": {f"card {index}": "does something" for index in range(direct.KNOWN_MOST)}})
    assert len(player.known) == direct.KNOWN_MOST and player.learned == 0
    # one read otherwise than before takes the newest place, and the oldest goes for a new one
    assert player._learn({"card 0": "does something else", "card new": "x" * 500}) == 2
    assert list(player.known)[-2:] == ["card 0", "card new"] and "card 1" not in player.known
    assert len(player.known) == direct.KNOWN_MOST and len(player.known["card new"]) == direct.KNOWN_TEXT
