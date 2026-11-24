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
