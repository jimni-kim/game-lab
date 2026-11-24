"""A work played by the model's own inputs looked back on once it ends (PLAN §27 M11, §36 E8): the lessons it
writes are kept by the game, given to every question of the next work, and counted when an input follows one."""
from io import BytesIO
import json

import pytest
from PIL import Image, ImageDraw

from gamelab import direct, recovery, review, work
from gamelab.adaptation import Store
from gamelab.agent import play
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult, Usage

SIZE = (400, 600)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(direct, "SETTLE_S", 0.0)
    monkeypatch.setattr(direct, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(direct, "CHANGE_LOOK_S", 0.02)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)


class Tower:
    """A toy climb: each tap on the left door climbs a floor and costs 3 health, on the right one 1 health."""
    name = "fake"
    reads_dom = False
    kinds = frozenset({"tap"})

    def __init__(self):
        self.floor, self.hp = 0, 20

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "https://tower.example/"

    async def capture(self, region=None):
        image = Image.new("RGB", SIZE, (40, 40, 80))
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 40, 20 + self.floor * 30, 60), fill=(250, 250, 0))
        draw.rectangle((20, 80, 20 + self.hp * 10, 100), fill=(250, 0, 0))
        draw.rectangle((40, 300, 140, 450), fill=(120, 60, 20))
        draw.rectangle((260, 300, 360, 450), fill=(20, 120, 60))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    async def tap(self, x, y, duration_ms):
        if 300 <= y <= 450 and (40 <= x <= 140 or 260 <= x <= 360):
            self.floor += 1
            self.hp -= 3 if x < 200 else 1

    async def release(self):
        pass


class Model:
    """Plays the toy climb: the left door unless a lesson says the right one; ends after `floors` floors. Looks
    back once asked, with the lesson it learned."""

    def __init__(self, game, floors=5):
        self.game, self.floors = game, floors
        self.asked: list[str] = []
        self.contexts: list[dict] = []
        self.reviews: list[dict] = []

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        self.asked.append(request.name)
        if "families" in context:
            return TurnResult(text=json.dumps({"screen": "game", "families": [], "situation": "a tower"}))
        if request.name == "review_work":
            self.reviews.append(context)
            assert request.messages[0].content[1]["type"] == "image"
            kept = [one["lesson"] for one in context["lessons_before"]]
            return TurnResult(text=json.dumps({
                "went": f"climbed {self.game.floor} floors, health {self.game.hp}",
                "cost": "the left door costs 3 health a floor",
                "lessons": kept or ["Take the right door: it costs 1 health a floor, the left one 3"]}),
                usage=Usage(input_tokens=2000))
        if not context.get("play"):
            return TurnResult(text=json.dumps({"screen": "other", "fits": False}))
        self.contexts.append(context)
        game = self.game
        if game.floor >= self.floors:
            return TurnResult(text=json.dumps({"situation": f"floor {game.floor}", "scene": "result",
                                               "action": {"kind": "done", "end": "won"}}))
        lessons = context.get("lessons") or []
        right = any("right door" in one["lesson"] for one in lessons)
        return TurnResult(text=json.dumps({
            "situation": f"floor {game.floor}, health {game.hp}", "scene": "board", "expect": "a floor up",
            "action": {"kind": "tap", "at": [310, 375] if right else [90, 375]},
            **({"lesson": 1} if right else {})}), usage=Usage(input_tokens=3000))


async def test_a_work_is_looked_back_on_and_its_lessons_are_followed_in_the_next(tmp_path):
    game = Tower()
    model = Model(game)
    played, made = await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(40, 1, 60),
                              store=Store(tmp_path), review=True)
    assert played.record["outcome"] == "goal_reached" and game.hp == 5
    # one question over the whole work, after its last, told its inputs and how it ended
    assert model.asked[-1] == "review_work" and model.asked.count("review_work") == 1
    told = model.reviews[0]
    assert told["outcome"] == "goal_reached" and len(told["inputs"]) >= 5 and told["situations"][0] == "floor 0, health 20"
    assert made["review"]["lessons"] == ["Take the right door: it costs 1 health a floor, the left one 3"]
    assert made["review"]["tokens"] == 2000
    kept = Store(tmp_path).memory("https://tower.example/")
    assert kept["lessons"] == made["review"]["lessons"] and kept["reviews"][0]["outcome"] == "goal_reached"

    # the next work is given the lesson with every question, goes by it, and says so
    again = Tower()
    model = Model(again)
    played, made = await play(again, model, None, goal=GoalSpec("play_until_end"), budget=Budget(40, 1, 60),
                              store=Store(tmp_path), review=True)
    assert all(context["lessons"][0]["number"] == 1 for context in model.contexts)
    assert again.hp == 15 and made["direct"]["lessons"]["followed"] == {"1": 5}
    # its review is told how often each lesson was followed, and the inputs that did carry it
    told = model.reviews[0]
    assert told["lessons_before"] == [{"number": 1, "lesson": kept["lessons"][0], "followed": 5}]
    assert sum(entry.get("lesson") == [1] for entry in told["inputs"]) == 5
    assert len(Store(tmp_path).memory("https://tower.example/")["reviews"]) == 2


async def test_a_short_work_or_one_not_asked_to_is_not_looked_back_on(tmp_path):
    game = Tower()
    model = Model(game, floors=2)
    await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(40, 1, 60), store=Store(tmp_path),
               review=True)
    assert "review_work" not in model.asked
    game = Tower()
    model = Model(game)
    await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(40, 1, 60), store=Store(tmp_path))
    assert "review_work" not in model.asked and "lessons" not in (Store(tmp_path).memory("https://tower.example/") or {})


async def test_what_looking_back_spent_is_not_of_the_works_budget(tmp_path):
    game = Tower()
    model = Model(game)
    played, made = await work.play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(40, 1, 60),
                                   store=Store(tmp_path), review=True)
    assert made["review"]["tokens"] == 2000 and made["work"]["spent"]["tokens"]["input"] >= 2000


def test_lessons_are_kept_short_and_few():
    said = ["  a  ", 3, "", "b" * 500] + [f"l{n}" for n in range(20)]
    kept = review.lessons(said)
    assert kept[0] == "a" and len(kept[1]) == review.LESSON_CHARS and len(kept) == review.LESSONS_MOST
    assert review.lessons("not a list") == []
