"""What stands between two levels is the model's to play, by the kinds of screen it names (PLAN §25, R5 and R6;
§27, M1 and M5): a result, a story told a line at a tap, a map with the next level's button and a shop beside
it. The family plays again from the first screen the model names a board; a tap the model asks to be repeated
is sent again unasked while each changes a little of the screen; play that keeps coming back to one screen is
told so and given up; a move the game forces on a level's board is the model's own, and a move of the level.
The level is the fake minesweeper of the adaptation tests."""
from io import BytesIO
import json

import pytest
from PIL import Image, ImageDraw

from gamelab import direct, scenes, work
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult
from test_adaptation_mines import BOARD, COMPUTE, SIZE, WINNING, Reader, middle, quick as mines_quick, source as mines_source  # noqa: F401
from test_work import Levels, ending

LINES = 3
PLAY, SHOP, CLOSE = [60, 500, 120, 40], [260, 500, 120, 40], [400, 80, 40, 40]
CENTRE = [BOARD[0] + BOARD[2] / 2, BOARD[1] + BOARD[3] / 2]


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(direct, "SETTLE_S", 0.0)
    monkeypatch.setattr(direct, "STILL_GAP_S", 0.0)


def inside(x, y, box):
    return box[0] <= x < box[0] + box[2] and box[1] <= y < box[1] + box[3]


def page(colour, boxes=(), bar=None):
    image = Image.new("RGB", SIZE, colour)
    draw = ImageDraw.Draw(image)
    for (x, y, w, h), fill in boxes:
        draw.rectangle((x, y, x + w - 1, y + h - 1), fill=fill)
    if bar is not None:
        draw.rectangle((40, 40 + 24 * bar, 240, 56 + 24 * bar), fill=(20, 20, 20))
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class Garden(Levels):
    """The levels with a way between them: a level's end (the notice and its button), then a story of LINES
    lines, each passed by a tap anywhere, then a map whose one button deals the next level and whose other
    opens a shop, closed by its own button."""

    def __init__(self, draws):
        super().__init__(draws)
        self.stage, self.line, self.shopped = "board", 0, 0

    async def capture(self, region=None):
        if self.stage == "story":
            return page((200, 180, 120), bar=self.line)
        if self.stage == "map":
            return page((120, 190, 140), [(PLAY, (40, 120, 220)), (SHOP, (220, 160, 40))])
        if self.stage == "shop":
            return page((60, 60, 70), [((60, 60, 400, 380), (250, 250, 250)), (CLOSE, (200, 40, 40))])
        return await super().capture(region)

    async def tap(self, x, y, duration_ms):
        if self.stage == "story":
            self.taps.append((x, y))
            self.line += 1
            if self.line >= LINES:
                self.stage, self.line = "map", 0
        elif self.stage == "map":
            self.taps.append((x, y))
            if inside(x, y, PLAY):
                self.covered, self.stage = True, "board"
                await super().tap(*middle(self.button), duration_ms)
                self.taps.pop()
            elif inside(x, y, SHOP):
                self.stage, self.shopped = "shop", self.shopped + 1
        elif self.stage == "shop":
            self.taps.append((x, y))
            if inside(x, y, CLOSE):
                self.stage = "map"
        elif self.covered and inside(x, y, self.button):
            # the level's end is passed: the story begins, and the next level is dealt from the map
            self.taps.append((x, y))
            self.covered, self.stage = False, "story"
        else:
            await super().tap(x, y, duration_ms)


class Guide(Reader):
    """The model: it names each screen and passes it (`repeat`: the taps it asks for at a story's line; `shops`:
    it opens the shop from the map and closes it again, every time; `guided`: it says the first board of a part
    forces a move, and makes it)."""

    def __init__(self, fake, repeat=None, shops=False, guided=False):
        super().__init__(fake, [mines_source()] * 8)
        self.repeat, self.shops, self.guided, self.forced = repeat, shops, guided, 0

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if context.get("name_screen"):
            return TurnResult(text=json.dumps({"scene": "result", "situation": f"level {self.fake.dealt} ended"}))
        if context.get("play"):
            self.asked.append(("play", context, 1))
            stage, guided = self.fake.stage, False
            if stage == "story":
                scene, action = "story", {"kind": "tap", "at": [300, 300], **({"repeat": self.repeat} if self.repeat else {})}
            elif stage == "map":
                scene, action = "map", {"kind": "tap", "at": middle(SHOP if self.shops else PLAY)}
            elif stage == "shop":
                scene, action = "popup", {"kind": "tap", "at": middle(CLOSE)}
            elif self.fake.covered:
                scene, action = "result", {"kind": "tap", "at": middle(self.fake.button)}
            else:
                guided = self.guided and self.forced < self.fake.dealt - 1
                self.forced += guided
                scene, action = "board", {"kind": "tap", "at": CENTRE}
            return TurnResult(text=json.dumps({"situation": stage, "scene": scene, "guided": guided, "score": None,
                                               "action": action, "expect": "the way on",
                                               "notes": f"level {self.fake.dealt}: the map's left button deals the next"}))
        if "tried" in context:
            self.asked.append(("recover", context, 1))
            action = {"kind": "give_up", "end": self.fake.ended} if self.fake.covered else {"kind": "none"}
            return TurnResult(text=json.dumps({"situation": "as drawn", "scene": "result", "action": action}))
        return await super().complete(request)


def plays(model):
    return [context for kind, context, _ in model.asked if kind == "play"]


async def test_the_screens_between_two_levels_are_passed_by_the_model_and_the_family_plays_the_next():
    fake = Garden([WINNING] * 3)
    model = Guide(fake, repeat=5)
    played, made = await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 3), budget=Budget(200, COMPUTE, 600))
    assert ending(played) == ("goal_reached", "levels"), (played.record, made["work"])
    assert fake.ends == ["won"] * 3 and made["work"]["levels"] == {"wanted": 3, "won": 3, "lost": 0}
    # between two levels: the result's button, the story's lines by one answer (the tap sent again until the
    # scene was another), the map's button, and the board, on which the model's own move was not sent
    asked = plays(model)
    assert [context["history"][-1].get("scene") if context["history"] else None for context in asked[:4]] == \
        [None, "result", "story", "map"]
    # (the second time the same screens stood between two levels, code passed them by the steps kept of the
    # first: `test_routine.py`)
    assert len(asked) == 4 and all(context["onward"] for context in asked)
    told = asked[2]["history"][-1]
    assert told["repeated"] == {"sent": LINES, "stopped": "scene_changed"} and told["screen_changed"]
    between = [move for move in played.moves if move["level"] == "L0"]
    assert [move["scene"] for move in between] == ["result", "story", "map"]
    # every tap of the story is an input sent; none of what was sent between the levels is a move of a level
    assert sum(part["actions"] - part["level_moves"] for part in made["work"]["parts"]) >= 2 * (LINES + 2)
    assert made["work"]["parts"][0]["level_moves"] == made["work"]["parts"][0]["confirmed"]
    # what the model noted of the way between the levels went with it to the next part
    assert asked[3]["memory"]["plan"].startswith("level 1:") and asked[0]["memory"] == {}
    assert made["work"]["stopped_at"]["screen"] == "result"


async def test_a_tap_is_repeated_only_as_often_as_asked_and_not_at_all_when_it_changed_nothing():
    fake = Garden([WINNING] * 2)
    model = Guide(fake, repeat=2)
    played, made = await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 2), budget=Budget(200, COMPUTE, 600))
    assert ending(played) == ("goal_reached", "levels")
    # (the third line, a screen like the one the model chose the tap on, is passed by code: the step kept of it)
    stories = [(move["level"], move.get("repeated")) for move in played.moves if move.get("scene") == "story"]
    assert stories == [("L0", {"sent": 2, "stopped": "count"}), ("R", None)]
    # asked for more than may be sent at once, or on a board: no input at all
    assert direct.check({"kind": "tap", "at": [5, 5], "repeat": direct.REPEAT_MOST + 1}, SIZE, fake.kinds) is None
    assert direct.check({"kind": "tap", "at": [5, 5], "repeat": 3}, SIZE, fake.kinds) == {"kind": "tap", "at": [5.0, 5.0], "repeat": 3}


async def test_play_that_keeps_coming_back_to_one_screen_is_told_so_and_given_up():
    fake = Garden([WINNING] * 2)
    model = Guide(fake, shops=True)
    played, made = await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 2), budget=Budget(200, COMPUTE, 600))
    assert ending(played) == ("gave_up", "going_round"), (played.record, made["work"])
    assert made["work"]["levels"]["won"] == 1 and fake.dealt == 1 and fake.shopped == scenes.ROUND_MOST
    # back on the map the second time, the model was told it was there before and what it sent from it
    back = [context["been_here"] for context in plays(model) if "been_here" in context]
    assert back[0] == {"times": 1, "tried": [{"kind": "tap", "at": [float(part) for part in middle(SHOP)]}]}
    assert max(told["times"] for told in back) == scenes.ROUND_MOST - 1
    assert made["work"]["stopped_at"]["screen"] == "result"


async def test_a_move_the_game_forces_on_a_board_is_the_models_and_a_move_of_the_level():
    fake = Garden([WINNING] * 2)
    model = Guide(fake, repeat=5, guided=True)
    played, made = await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 2), budget=Budget(200, COMPUTE, 600))
    assert ending(played) == ("goal_reached", "levels"), (played.record, made["work"])
    forced = [move for move in played.moves if move["level"] == "L0" and move.get("scene") == "board"]
    assert len(forced) == 1 and forced[0]["part"] == 2 and forced[0]["effect"] == "confirmed"
    # once a move of the level was made, the work no longer looks for the next level: the family took the
    # board at the next question, where the game forced nothing
    asked = plays(model)
    assert [bool(context.get("onward")) for context in asked] == [True, True, True, True, False]
    assert made["work"]["parts"][1]["level_moves"] == made["work"]["parts"][1]["confirmed"] - 3


def test_screens_alike_one_after_another_are_one_stay_and_a_screen_left_before_is_a_coming_back():
    trail = scenes.Trail()
    story, town, shop = page((200, 180, 120), bar=0), page((120, 190, 140)), page((60, 60, 70))
    assert trail.met(story) is None and trail.met(page((200, 180, 120), bar=1)) is None and len(trail.stays) == 1
    trail.sent({"kind": "tap", "at": [1, 1]})
    assert trail.met(town) is None
    trail.sent({"kind": "tap", "at": [2, 2]})
    assert trail.met(shop) is None
    assert trail.met(town) == {"times": 1, "tried": [{"kind": "tap", "at": [2, 2]}]}
    assert not trail.round() and trail.inputs == 2
    assert scenes.kind("map") == "map" and scenes.kind("lobby") is None and scenes.kind(None) is None
