"""How well play went, as the game shows it (PLAN §27, M9; §36, session E5): the screen a level or a work ends on is
named once and the values the model reads off it are kept with the part and the work and told to the person; and
levels up to one are counted by a number the game shows that no result screen ends (a floor). The game here is a
toy climb: each press lights a lamp, every two lamps are a floor, and each floor costs the climber some health."""
from io import BytesIO
import json

import pytest
from PIL import Image, ImageDraw

from gamelab import direct, recovery, scenes, work
from gamelab.contracts import Budget, GoalSpec
from gamelab.eval import talk
from gamelab.model import TurnResult

SIZE = (600, 400)
PRESS = {"kind": "tap", "at": [300, 325]}
HEALTH = 70


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(direct, "SETTLE_S", 0.0)
    monkeypatch.setattr(direct, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(direct, "CHANGE_LOOK_S", 0.02)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)


class Climb:
    name = "fake"
    reads_dom = False
    kinds = frozenset({"tap"})

    def __init__(self):
        self.pressed = 0

    def floor(self) -> int:
        return self.pressed // 2 + 1

    def health(self) -> int:
        return HEALTH - 10 * (self.floor() - 1)

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "https://climb.example/"

    async def capture(self, region=None):
        image = Image.new("RGB", SIZE, (245, 245, 245))
        draw = ImageDraw.Draw(image)
        for lamp in range(self.pressed):
            draw.rectangle((20 + lamp * 50, 120, 50 + lamp * 50, 160), fill=(250, 200, 0))
        draw.rectangle((250, 300, 350, 350), fill=(40, 120, 220))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    async def tap(self, x, y, duration_ms):
        self.pressed += 1

    async def release(self):
        pass


class Model:
    """Knows no family for the climb, presses on, and names the screen with the floor and the health it shows.
    `progress`: whether it gives the floor when asked for it."""

    def __init__(self, fake, progress=True):
        self.fake, self.progress = fake, progress
        self.played, self.named = [], []

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "families" in context:
            return TurnResult(text=json.dumps({"screen": "game", "families": [], "situation": "a climb"}))
        if context.get("name_screen"):
            self.named.append(self.fake.floor())
            return TurnResult(text=json.dumps({
                "scene": "board", "situation": f"floor {self.fake.floor()}", "level": self.fake.floor(),
                "level_won": False, "counted": "floor",
                "measures": [{"name": "health", "value": self.fake.health(), "most": HEALTH, "better": "higher"},
                             {"name": "", "value": 3}, {"name": "gold", "value": "lots"}]}))
        if not context.get("play"):
            return TurnResult(text=json.dumps({"screen": "other", "fits": False}))
        self.played.append(context)
        return TurnResult(text=json.dumps({
            "situation": "climbing", "scene": "board", "expect": "a lamp lights", "action": PRESS,
            **({"progress": self.fake.floor()} if self.progress else {})}))


async def test_levels_up_to_one_are_counted_by_a_floor_no_result_screen_ends():
    fake = Climb()
    model = Model(fake)
    played, made = await work.play(fake, model, None, goal=GoalSpec("until_level", 2), budget=Budget(30, 1, 120))
    handback = made["work"]
    assert (handback["outcome"], handback["reason"]) == ("goal_reached", "until_level")
    # floor 1 at the start; each part ended once the model read the next floor, before anything more was sent
    assert fake.pressed == 4 and model.named == [1, 2, 3]
    assert [asked["counting"]["at"] for asked in model.played] == [1, 1, 1, 2, 2, 2]
    assert handback["levels"] == {"wanted": 2, "won": 2, "lost": 0, "until": 2, "through": 2, "counted": "floor"}
    assert [part["reason"] for part in handback["parts"]] == ["counted_on", "counted_on"]
    assert handback["parts"][0]["counted_on"] == {"from": 1, "to": 2}
    # how each floor ended, and where the work stopped, as the screen shows it: what is no value is left out
    assert handback["parts"][0]["measures"] == [{"name": "health", "value": 60, "most": 70, "better": "higher"}]
    assert handback["parts"][1]["level"] == 3 and handback["parts"][1]["counted"] == "floor"
    assert handback["measured"] == {"level": 3, "counted": "floor",
                                    "measures": [{"name": "health", "value": 50, "most": 70, "better": "higher"}]}
    # named once after the last part: not again for the handback
    assert handback["spent"]["questions"] == made["questions"]
    assert "the screen shows floor 3, health 50/70 (more is better)" in work.told(handback)
    assert "up to floor 2" in work.told(handback)
    said = talk.handed_back({"work": handback}, ko=True)
    assert "화면의 숫자: floor 3, health 50/70 (많을수록 좋음)." in said


async def test_one_game_is_measured_where_it_stops():
    fake = Climb()
    model = Model(fake, progress=False)
    played, made = await work.play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(3, 1, 60))
    handback = made["work"]
    assert handback["outcome"] == "budget_exhausted" and model.named == [2]
    # no counting was asked of a game played to its end
    assert all("counting" not in asked for asked in model.played)
    assert handback["measured"]["measures"][0]["value"] == 60 and handback["parts"][0].get("measures") is None


def test_values_read_off_a_screen_are_kept_only_as_numbers_with_names():
    kept = scenes.measures([{"name": " steps ", "value": 46, "better": "lower"}, {"name": "best", "value": 43.0, "most": None},
                            {"name": "x", "value": True}, {"name": "y", "value": float("nan")}, "z",
                            {"name": "stars", "value": 2, "most": 3, "better": "more"}] + [{"name": "n", "value": 1}] * 20)
    assert kept[:3] == [{"name": "steps", "value": 46, "better": "lower"}, {"name": "best", "value": 43.0},
                        {"name": "stars", "value": 2, "most": 3}]
    assert len(kept) == scenes.MEASURES_MOST and scenes.measures(None) == []
