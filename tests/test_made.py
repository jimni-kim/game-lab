"""A family made while a game is played (PLAN §11, H4): a model describes a game nobody registered, writes
its rules as code, and the one loop plays it by them: the model reads, the code picks, each result is held to
the written rules. The game here is a toy: a 2 by 3 grid of lamps, a tap lights the one tapped, all lit wins."""
from io import BytesIO
import json

import pytest
from PIL import Image, ImageDraw

from gamelab import adaptation, agent, catalog, direct, made, recovery
from gamelab.agent import play
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult

SIZE = (600, 400)
BOARD = [150, 100, 300, 200]
ROWS, COLS = 2, 3
DESCRIBED = {"screen": "game", "fits": True, "name": "Lamps", "summary": "A grid of lamps. A tap lights the lamp tapped; "
             "a lit lamp cannot be tapped. All lamps lit wins.", "rows": ROWS, "cols": COLS, "cells": "1 a lit lamp, 0 a dark one",
             "move": {"kind": "light", "input": "tap", "description": "tap a dark lamp to light it"},
             "won": "every lamp lit", "over": "never"}
RULES = '''
def legal_actions(state, rules):
    return [{"kind": "light", "cell": [r, c]} for r, row in enumerate(state["grid"]) for c, cell in enumerate(row) if cell == 0]

def assess(before, action, after, rules):
    r, c = action["cell"]
    want = [list(row) for row in before["grid"]]
    want[r][c] = LIT
    return before["grid"][r][c] == 0 and after["grid"] == want

def status(state, rules):
    return "won" if all(cell == 1 for row in state["grid"] for cell in row) else "playing"

def choose(state, rules):
    moves = legal_actions(state, rules)
    return moves[-1] if moves else None
'''
RIGHT, MISTAKEN = "LIT = 1\n" + RULES, "LIT = 2\n" + RULES


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    # the adapters here are the ones a model writes: the host's plain one is tried in test_plain.py
    monkeypatch.setattr(adaptation, "PLAIN_INSETS", ())
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(agent, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    monkeypatch.setattr(direct, "SETTLE_S", 0.0)
    monkeypatch.setattr(direct, "STILL_GAP_S", 0.0)


class Lamps:
    name = "fake"
    reads_dom = False
    kinds = frozenset({"keys", "tap", "drag"})

    def __init__(self):
        self.grid, self.taps = [[0] * COLS for _ in range(ROWS)], []

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "https://lamps.example/grid"

    async def capture(self, region=None):
        image = Image.new("RGB", SIZE, (245, 245, 245))
        draw = ImageDraw.Draw(image)
        left, top, width, height = BOARD
        for r, row in enumerate(self.grid):
            for c, cell in enumerate(row):
                x, y = left + c * width / COLS, top + r * height / ROWS
                draw.rectangle((x + 4, y + 4, x + width / COLS - 4, y + height / ROWS - 4), fill=(250, 200, 0) if cell else (90, 90, 90))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    async def tap(self, x, y, duration_ms):
        self.taps.append((x, y))
        left, top, width, height = BOARD
        if left <= x < left + width and top <= y < top + height:
            self.grid[int((y - top) / (height / ROWS))][int((x - left) / (width / COLS))] = 1

    async def key(self, key):
        raise AssertionError("no key is pressed")

    async def drag(self, *args):
        raise AssertionError("nothing is dragged")

    async def release(self):
        pass


class Maker:
    """A model that knows no family for the screen, describes it (`described`), reads it as it is drawn, writes
    each of `modules` in turn as its rules, and plays it with `inputs` when it is left the game."""

    def __init__(self, fake, modules, described=DESCRIBED, inputs=()):
        self.fake, self.modules, self.described, self.inputs = fake, list(modules), described, list(inputs)
        self.asked = []

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        kind = ("identify" if "families" in context else "write" if "game" in context else "play" if context.get("play")
                else "label" if "family" in context else "describe")
        self.asked.append((kind, context))
        if kind == "identify":
            return TurnResult(text=json.dumps({"screen": "game", "families": [], "situation": "a grid of lamps"}))
        if kind == "describe":
            return TurnResult(text=json.dumps(self.described))
        if kind == "write":
            return TurnResult(text=self.modules.pop(0))
        if kind == "play":
            action = self.inputs.pop(0) if self.inputs else {"kind": "done", "end": "won"}
            return TurnResult(text=json.dumps({"situation": "lamps", "score": None, "action": action, "expect": "", "notes": ""}))
        won = all(cell for row in self.fake.grid for cell in row)
        return TurnResult(text=json.dumps({"screen": "board", "board": BOARD, "rules": {"rows": ROWS, "cols": COLS},
                                           "state": {"grid": self.fake.grid, "score": None, "scene": "won" if won else "playing"}}))

    def count(self, kind):
        return sum(asked == kind for asked, _ in self.asked)


def ending(played):
    return played.record["outcome"], played.record["reason"]


async def game(modules, **options):
    fake = Lamps()
    model = Maker(fake, modules, **options)
    played, found = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(30, 1, 120))
    return fake, model, played, found


async def test_a_game_nobody_registered_is_described_and_played_by_rules_written_for_it():
    fake, model, played, found = await game([RIGHT])
    assert ending(played) == ("goal_reached", "won"), (played.record, found)
    # the written solver's moves (its last legal one each time), each sent as a tap on the cell it names
    assert all(cell for row in fake.grid for cell in row) and len(fake.taps) == ROWS * COLS
    assert fake.taps[0] == (BOARD[0] + BOARD[2] * 5 / 6, BOARD[1] + BOARD[3] * 3 / 4)
    assert played.record["levels"] == {"L2": ROWS * COLS, "L3": 0} and played.record["confirmed_actions"] == ROWS * COLS
    assert played.record["family"].startswith("made_") and played.record["policy"] == "generated"
    told = found["made"]
    assert (told["name"], told["input"], told["rows"], told["cols"]) == ("Lamps", "tap", ROWS, COLS)
    assert told["written"] and told["attempts"] == 1 and told["rewrites"] == 0 and told["errors"] == 0
    # one question each for which game, what it is and its rules; the model never chose a move
    assert (model.count("identify"), model.count("describe"), model.count("write"), model.count("play")) == (1, 1, 1, 0)
    # the rules were written from the description and the first screen as it was read
    written = next(context for kind, context in model.asked if kind == "write")
    assert written["game"]["action_form"] == {"kind": "light", "cell": ["row", "column"]}
    assert written["states"] == [{"grid": [[0] * COLS] * ROWS, "score": None, "scene": "playing"}] and written["examples"] == []
    # the family lasts as long as the game
    assert not catalog.MADE


async def test_rules_that_refuse_what_the_screen_really_showed_are_written_again_from_it():
    """The first rules say a lit lamp reads 2. The first move's real result is refused by them, shown to the
    model, and the rules written again; the game goes on by those."""
    fake, model, played, found = await game([MISTAKEN, RIGHT])
    assert ending(played) == ("goal_reached", "won"), (played.record, found)
    assert found["made"]["rewrites"] == 1 and found["made"]["attempts"] == 2 and model.count("play") == 0
    again = [context for kind, context in model.asked if kind == "write"][1]
    assert again["examples"][-1]["action"] == {"kind": "light", "cell": [1, 2]}
    assert again["examples"][-1]["after"]["grid"] == [[0, 0, 0], [0, 0, 1]]
    assert "refuses the result that was really seen" in again["previous"]["problems"][0]
    # the move whose result was refused is not played again
    assert len(fake.taps) == ROWS * COLS


async def test_rules_that_do_not_hold_on_the_first_screen_are_not_played_and_the_model_plays_the_game_itself():
    press = {"kind": "tap", "at": [200, 150]}
    fake, model, played, found = await game(["def legal_actions(state, rules):\n    return []\n"] * made.MAX_ATTEMPTS, inputs=[press])
    assert ending(played) == ("goal_reached", "won"), (played.record, found)
    assert found["made"]["attempts"] == made.MAX_ATTEMPTS and not found["made"]["written"]
    assert fake.taps == [(200, 150)] and played.record["levels"]["L0"] == 1 and played.record["family"] is None
    assert [failure["reason"] for failure in found["generation_failures"]] == ["no_rules_passed"]


@pytest.mark.parametrize("described", [{**DESCRIBED, "fits": False}, {**DESCRIBED, "move": {"kind": "light", "input": "voice", "description": "say it"}},
                                       {**DESCRIBED, "rows": 0}, {"screen": "other"}])
async def test_a_game_that_does_not_fit_written_rules_is_left_to_the_model(described):
    fake, model, played, found = await game([], described=described, inputs=[{"kind": "tap", "at": [200, 150]}])
    assert ending(played) == ("goal_reached", "won") and model.count("write") == 0 and "made" not in found
    assert played.record["levels"]["L0"] == 1


async def test_written_rules_whose_moves_the_game_never_takes_are_dropped_for_the_models_own_play():
    """The rules pass on the first screen but name cells the wrong way round, so no move's result is one they
    allow, and writing them again does not help: the made family is dropped and the model plays."""
    swapped = RIGHT.replace('"cell": [r, c]}', '"cell": [r, c]}').replace("r, c = action[\"cell\"]", "c, r = action[\"cell\"]")
    fake, model, played, found = await game([swapped] * (1 + made.MAX_REWRITES * made.MAX_ATTEMPTS))
    assert ending(played)[0] in ("goal_reached", "game_over"), (played.record, found)
    assert model.count("play") >= 1 and "made_family_dropped" in [failure["reason"] for failure in found["generation_failures"]]


class Picky(Lamps):
    """Lamps that light on the second tap of the same cell (the first only picks it), or, with `keyed`, when the
    cell is tapped and then the key 7 pressed, or, with `paired`, when a lit... dark cell is tapped and then its
    neighbour to the right (both light)."""

    def __init__(self, way):
        super().__init__()
        self.way, self.picked, self.keys = way, None, []

    def _cell(self, x, y):
        left, top, width, height = BOARD
        return (int((y - top) / (height / ROWS)), int((x - left) / (width / COLS)))

    async def tap(self, x, y, duration_ms):
        self.taps.append((x, y))
        cell = self._cell(x, y)
        if self.way == "tap_twice" and self.picked == cell:
            self.grid[cell[0]][cell[1]], self.picked = 1, None
        elif self.way == "tap_to" and self.picked is not None:
            for r, c in (self.picked, cell):
                self.grid[r][c] = 1
            self.picked = None
        else:
            self.picked = cell

    async def key(self, key):
        self.keys.append(key)
        if self.way == "tap_key" and key == "7" and self.picked is not None:
            self.grid[self.picked[0]][self.picked[1]], self.picked = 1, None


@pytest.mark.parametrize(("way", "module", "taps", "keys"), [
    ("tap_twice", RIGHT, 2 * ROWS * COLS, 0),
    ("tap_key", RIGHT.replace('{"kind": "light", "cell": [r, c]}', '{"kind": "light", "cell": [r, c], "key": "7"}'), ROWS * COLS, ROWS * COLS),
])
async def test_a_move_made_by_a_tap_that_picks_and_an_input_that_completes_it_is_played_whole(way, module, taps, keys):
    fake = Picky(way)
    model = Maker(fake, [module], described={**DESCRIBED, "move": {**DESCRIBED["move"], "input": way}})
    played, found = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(30, 1, 120))
    assert ending(played) == ("goal_reached", "won"), (played.record, found)
    assert (len(fake.taps), len(fake.keys)) == (taps, keys) and played.record["actions_used"] == ROWS * COLS
    assert found["made"]["input"] == way and model.count("play") == 0


async def test_a_move_made_by_tapping_one_cell_and_then_another_is_played_whole():
    pairs = '''
def legal_actions(state, rules):
    g = state["grid"]
    return [{"kind": "light", "cell": [r, c], "to": [r, c + 1]} for r in range(len(g)) for c in range(len(g[0]) - 1) if g[r][c] == 0]

def assess(before, action, after, rules):
    want = [list(row) for row in before["grid"]]
    for r, c in (action["cell"], action["to"]):
        want[r][c] = 1
    return after["grid"] == want and after["grid"] != before["grid"]

def status(state, rules):
    return "won" if all(cell == 1 for row in state["grid"] for cell in row) else "playing"

def choose(state, rules):
    moves = legal_actions(state, rules)
    return moves[0] if moves else None
'''
    fake = Picky("tap_to")
    model = Maker(fake, [pairs], described={**DESCRIBED, "move": {**DESCRIBED["move"], "input": "tap_to"}})
    played, found = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(30, 1, 120))
    assert ending(played) == ("goal_reached", "won"), (played.record, found)
    # each move is two taps: the cell, then the one to its right
    left, top, width, height = BOARD
    assert fake.taps[:2] == [(left + width / 6, top + height / 4), (left + width / 2, top + height / 4)]
    assert len(fake.taps) == 2 * played.record["actions_used"]
