"""Several things offered of which one is taken (a reward after a fight): the person is asked where someone watches
the work, and with no answer in time, or no one to ask, the model's own pick is sent (2026-11-24, the person)."""
import asyncio
import json

import pytest

from gamelab import agent, direct, recovery
from gamelab.agent import play
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult, Usage

from test_review import Tower


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(direct, "SETTLE_S", 0.0)
    monkeypatch.setattr(direct, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(direct, "CHANGE_LOOK_S", 0.02)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    monkeypatch.setattr(agent, "PICK_WAIT_S", 0.2)


class Model:
    """Every floor offers two doors: it would take the left one, and takes the one the person named when told."""

    def __init__(self, game):
        self.game, self.plays = game, []

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "families" in context:
            return TurnResult(text=json.dumps({"screen": "game", "families": [], "situation": "two doors"}))
        if not context.get("play"):
            return TurnResult(text=json.dumps({"screen": "other", "fits": False}))
        self.plays.append(context)
        if self.game.floor >= 3:
            return TurnResult(text=json.dumps({"situation": "the top", "scene": "result", "action": {"kind": "done", "end": "won"}}))
        right = "right" in (context.get("person_said") or {}).get("answer", "")
        return TurnResult(text=json.dumps({
            "situation": f"floor {self.game.floor}", "scene": "popup", "expect": "a floor up",
            "action": {"kind": "tap", "at": [310, 375] if right else [90, 375]},
            "pick": {"asks": "Which door?", "options": ["left door", "right door"],
                     "picked": "right door" if right else "left door", "why": "it looks sturdier", "taking": True}}),
            usage=Usage(input_tokens=100))


class Watcher:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    async def seen(self, png):
        pass

    async def sending(self, number, action, state, sent, observed_s):
        pass

    async def sent(self):
        pass

    async def pick(self, asked):
        self.asked.append(asked)
        if self.answer is None:
            await asyncio.sleep(30)
        return self.answer


async def test_with_no_one_to_ask_the_models_own_pick_is_sent():
    game = Tower()
    played, made = await play(game, Model(game), None, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60))
    assert played.record["outcome"] == "goal_reached" and game.hp == 20 - 9
    assert [one["picked"] for one in made["picks"]] == ["left door"] and "person_said" not in made["picks"][0]


async def test_the_person_is_asked_once_and_what_they_say_is_taken():
    game, watcher = Tower(), Watcher("the right door please")
    model = Model(game)
    played, made = await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60), watch=watcher)
    # the left door was never pressed: the input given before the answer was not sent
    assert played.record["outcome"] == "goal_reached" and game.hp == 20 - 3
    assert len(watcher.asked) == 1 and watcher.asked[0]["options"] == ["left door", "right door"]
    assert watcher.asked[0]["picked"] == "left door" and watcher.asked[0]["why"] == "it looks sturdier"
    assert made["picks"][0]["person_said"] == "the right door please"
    assert model.plays[1]["person_said"]["answer"] == "the right door please"


async def test_with_no_answer_in_time_the_models_own_pick_is_sent():
    game, watcher = Tower(), Watcher(None)
    played, made = await play(game, Model(game), None, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60), watch=watcher)
    assert played.record["outcome"] == "goal_reached" and game.hp == 20 - 9
    assert len(watcher.asked) == 1 and "person_said" not in made["picks"][0]


class Wayfinder:
    """Every floor is a fork of a map: it says what each way is foreseen to cost and takes the left one on even
    floors, the right one on odd ones. Looks back when asked."""

    def __init__(self, game):
        self.game, self.reviews = game, []

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "families" in context:
            return TurnResult(text=json.dumps({"screen": "game", "families": [], "situation": "a fork"}))
        if request.name == "review_work":
            self.reviews.append(context)
            return TurnResult(text=json.dumps({"went": "climbed", "cost": "the left way", "lessons": ["Go right at a fork"]}))
        if not context.get("play"):
            return TurnResult(text=json.dumps({"screen": "other", "fits": False}))
        if self.game.floor >= 4:
            return TurnResult(text=json.dumps({"situation": "the top", "scene": "result", "action": {"kind": "done", "end": "won"}}))
        left = self.game.floor % 2 == 0
        return TurnResult(text=json.dumps({
            "situation": f"a fork at floor {self.game.floor}", "scene": "map", "expect": "a floor up",
            "memory": {"run": f"floor {self.game.floor}, health {self.game.hp}"},
            "action": {"kind": "tap", "at": [90, 375] if left else [310, 375]},
            "pick": {"kind": "path", "asks": "Which way?", "taking": True,
                     "options": [{"name": "left: an elite", "foresee": "about 3 health lost, a relic"},
                                 {"name": "right: a shop", "foresee": "1 health lost, coins can be spent"}],
                     "picked": "left: an elite" if left else "right: a shop", "why": "health is high" if left else "health is low"}}),
            usage=Usage(input_tokens=100))


async def test_a_way_on_a_map_is_kept_with_what_was_foreseen_and_what_came_after():
    game, watcher = Tower(), Watcher("the right door please")
    model = Wayfinder(game)
    played, made = await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60),
                              watch=watcher, review=True)
    assert played.record["outcome"] == "goal_reached"
    # a way on a map is no offer of rewards: no one is asked, and each fork is kept as it was chosen
    picks = made["picks"]
    assert not watcher.asked and [one["picked"][:5] for one in picks] == ["left:", "right", "left:", "right"]
    first = picks[0]
    assert first["kind"] == "path" and first["options"] == ["left: an elite", "right: a shop"]
    assert first["foreseen"] == ["about 3 health lost, a relic", "1 health lost, coins can be spent"]
    assert first["why"] == "health is high" and first["standing"] == "floor 0, health 20" and first["at_input"] == 1
    # what the screen showed after the last choice
    assert picks[-1]["then"] == ["the top"]
    # the review is given the choices with what was foreseen of each
    told = model.reviews[0]["picks"]
    assert len(told) == 4 and told[1]["foreseen"][1] == "1 health lost, coins can be spent" and "taking" not in told[0]


def test_options_given_as_plain_texts_are_a_reward_with_nothing_foreseen():
    picked = direct._pick({"asks": "Which?", "options": ["a sword", "a shield"], "picked": "a sword", "taking": True})
    assert picked["kind"] == "reward" and "foreseen" not in picked and picked["options"] == ["a sword", "a shield"]
    assert direct._pick({"options": [{"name": "only one", "foresee": "x"}]}) is None
    assert direct._pick({"kind": "shop", "options": [{"name": "a potion", "foresee": "heals 10 for 30 coins"}, "leave"],
                         "picked": "leave"})["foreseen"] == ["heals 10 for 30 coins", ""]


async def test_an_answer_that_is_no_input_is_told_with_the_next_question():
    class Twice(Model):
        async def complete(self, request):
            context = json.loads(request.messages[0].content[0]["text"])
            if context.get("play") and len(self.plays) == 0:
                self.plays.append(context)
                return TurnResult(text=json.dumps({"situation": "floor 0", "scene": "popup", "expect": "a floor up",
                                                   "action": {"kind": "key", "key": "F13"}}))
            return await super().complete(request)

    game = Tower()
    model = Twice(game)
    played, made = await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60))
    assert played.record["outcome"] == "goal_reached"
    told = model.plays[1]["history"][-1]
    assert told["action"] == {"kind": "key"} and told["refused"].startswith("not sent") and told["screen_changed"] is False
