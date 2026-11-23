"""A game looked up before it is played (PLAN §27 M8, §36 E6): outside it once (its store page and an encyclopedia,
then one question), inside it off its boards (its own help, opened and closed by the model before the first move),
kept by the game and given with every question after, in this work and the next, with the tokens it spent kept apart
from the board's budget. The game here is a toy app: a menu with a help button and a play button, a help page, and
a board of lamps each press lights."""
from io import BytesIO
import json

import pytest
from PIL import Image, ImageDraw

from gamelab import direct, recovery, research, work
from gamelab.adaptation import Store
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult, Usage

SIZE = (600, 400)
HELP, CLOSE, PLAY, LAMP = [60, 40], [540, 40], [300, 200], [300, 325]
OUTSIDE = {"game": "Lamps", "known": True, "controls": "tap the board to light a lamp", "win": "light every lamp",
           "terms": {"lamp": "a light on the board"}, "sources": ["store", "own_knowledge"]}
READ = {"rules": "each tap lights one lamp", "avoid": "the red lamp ends the run"}
OUTSIDE_TOKENS, PLAY_TOKENS = 900, 100


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(direct, "SETTLE_S", 0.0)
    monkeypatch.setattr(direct, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(direct, "CHANGE_LOOK_S", 0.02)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)


@pytest.fixture
def looked_up(monkeypatch):
    """What is fetched of the game: nothing leaves the test; what was asked for is kept."""
    asked = []

    async def gather(address, hint, client=None):
        asked.append((address, hint))
        return {"store": {"title": "Lamps", "description": "Light every lamp."}}

    monkeypatch.setattr(research, "gather", gather)
    return asked


class Lamps:
    name = "fake"
    reads_dom = False
    kinds = frozenset({"tap"})

    def __init__(self):
        self.screen, self.lit, self.helped = "menu", 0, 0

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "android://com.example.lamps"

    async def capture(self, region=None):
        image = Image.new("RGB", SIZE, (245, 245, 245))
        draw = ImageDraw.Draw(image)
        if self.screen == "menu":
            draw.rectangle((40, 20, 80, 60), fill=(0, 160, 0))
            draw.rectangle((250, 180, 350, 220), fill=(40, 120, 220))
        elif self.screen == "help":
            draw.rectangle((100, 100, 500, 300), fill=(200, 200, 200))
            draw.rectangle((520, 20, 560, 60), fill=(200, 0, 0))
        else:
            for lamp in range(self.lit):
                draw.rectangle((20 + lamp * 50, 120, 50 + lamp * 50, 160), fill=(250, 200, 0))
            draw.rectangle((250, 300, 350, 350), fill=(40, 120, 220))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    async def tap(self, x, y, duration_ms):
        near = lambda point: abs(x - point[0]) < 25 and abs(y - point[1]) < 25
        if self.screen == "menu" and near(HELP):
            self.screen, self.helped = "help", self.helped + 1
        elif self.screen == "help" and near(CLOSE):
            self.screen = "menu"
        elif self.screen == "menu" and near(PLAY):
            self.screen = "board"
        elif self.screen == "board":
            self.lit += 1

    async def release(self):
        pass


class Model:
    """Knows no family for the game; looked up, it says what the store page told; asked to look first, it opens
    the help, reads it, closes it and says it is done; then it plays."""

    def __init__(self, fake):
        self.fake = fake
        self.played, self.outside = [], 0

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if request.name == "research_game":
            self.outside += 1
            assert context["store"]["title"] == "Lamps" and "controls" in context["parts"]
            return TurnResult(text=json.dumps(OUTSIDE), usage=Usage(OUTSIDE_TOKENS, 50))
        if "families" in context:
            return TurnResult(text=json.dumps({"screen": "game", "families": [], "situation": "a menu"}))
        if not context.get("play"):
            return TurnResult(text=json.dumps({"screen": "other", "fits": False}))
        self.played.append(context)
        screen = self.fake.screen
        if screen == "menu" and "look_first" in context:
            answer = {"scene": "map", "action": {"kind": "tap", "at": HELP, "research": True}}
        elif screen == "help":
            answer = {"scene": "notice", "found": READ, "research_done": True,
                      "action": {"kind": "tap", "at": CLOSE, "research": True}}
        elif screen == "menu":
            answer = {"scene": "map", "action": {"kind": "tap", "at": PLAY}}
        else:
            answer = {"scene": "board", "action": {"kind": "tap", "at": LAMP}}
        return TurnResult(text=json.dumps({"situation": screen, "expect": "it changes", **answer}),
                          usage=Usage(PLAY_TOKENS, 20))


async def test_a_game_is_looked_up_outside_and_inside_before_its_first_move(tmp_path, looked_up):
    fake, store = Lamps(), Store(tmp_path)
    model = Model(fake)
    # (the play button's press is a confirmed input as any is, not one of looking)
    played, made = await work.play(fake, model, None, goal=GoalSpec("actions", 3), budget=Budget(30, 1, 120),
                                   store=store, research=True)
    assert made["work"]["outcome"] == "goal_reached"
    # only the app's id and the person's word for the game went out, once
    assert looked_up == [("android://com.example.lamps", None)] and model.outside == 1
    # the help was opened once before the first move and the two lamps lit after: inputs of looking count no move
    assert fake.helped == 1 and fake.lit == 2
    first, on_help = model.played[0], model.played[1]
    assert first["research"]["unconfirmed"]["controls"] == OUTSIDE["controls"]
    assert "read_in_game" not in first["research"] and "look_first" in first and "look_first" in on_help
    after = model.played[2:]
    assert after and all("look_first" not in asked for asked in after)
    assert all(asked["research"]["read_in_game"] == READ for asked in after)
    told = made["research"]
    assert told["looked"] == {"questions": 2, "tokens": 2 * PLAY_TOKENS, "inputs": 2, "done": "said_done"}
    assert told["aside_tokens"] == OUTSIDE_TOKENS + 2 * PLAY_TOKENS and told["sources"] == ["store", "own_knowledge"]
    kept = store.research("android://com.example.lamps")
    assert kept["outside"]["win"] == OUTSIDE["win"] and kept["inside"] == READ and kept["looked"] == "said_done"

    # the next work on the game is given what was found and asks nothing of it again
    again, model2 = Lamps(), None
    again.screen = "board"
    model2 = Model(again)
    played, made = await work.play(again, model2, None, goal=GoalSpec("actions", 1), budget=Budget(30, 1, 120),
                                   store=store, research=True)
    assert model2.outside == 0 and len(looked_up) == 1 and again.helped == 0
    assert all("look_first" not in asked and asked["research"]["read_in_game"] == READ for asked in model2.played)


async def test_research_tokens_are_not_of_the_boards_budget(tmp_path, looked_up):
    fake = Lamps()
    model = Model(fake)
    # the look outside alone spends more than the board's budget, which still plays its two moves
    played, made = await work.play(fake, model, None, goal=GoalSpec("actions", 3),
                                   budget=Budget(30, 1, 120, max_input_tokens=OUTSIDE_TOKENS),
                                   store=Store(tmp_path), research=True)
    assert made["work"]["outcome"] == "goal_reached" and fake.lit == 2


async def test_without_research_nothing_is_looked_up(tmp_path, looked_up):
    fake = Lamps()
    fake.screen = "board"
    model = Model(fake)
    played, made = await work.play(fake, model, None, goal=GoalSpec("actions", 1), budget=Budget(30, 1, 120),
                                   store=Store(tmp_path))
    assert looked_up == [] and model.outside == 0 and "research" not in made
    assert all("research" not in asked and "look_first" not in asked for asked in model.played)


def test_a_store_page_and_what_a_model_said_are_kept_short():
    page = ('<meta property="og:title" content="Lamps &amp; More - Apps on Google Play">'
            '<div data-g-id="description" class="x">Light <b>every</b> lamp.<br>Avoid red.</div>')
    assert research.store_page(page) == {"title": "Lamps & More", "description": "Light every lamp. Avoid red."}
    assert research.store_page("<html></html>") is None
    kept = research.parts({"rules": " r " * 500, "win": "", "terms": {"a": "b", "": "c", "d": 3}, "other": "x"})
    assert set(kept) == {"rules", "terms"} and len(kept["rules"]) == research.PART_MOST and kept["terms"] == {"a": "b"}
    assert research.package("android://com.example.lamps") == "com.example.lamps" and research.package("https://x.y/") is None
