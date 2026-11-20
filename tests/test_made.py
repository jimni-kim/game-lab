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
        return TurnResult(text=json.dumps({"screen": "board", "board": BOARD, "rules": {"rows": len(self.fake.grid), "cols": len(self.fake.grid[0])},
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


async def test_a_family_made_for_a_game_is_kept_and_taken_up_again_with_nothing_described_or_written(tmp_path):
    """What was made of a game is kept by the game (plan §12, A3): its description and its rules as written. The
    next game of it is played by them at once; rules that do not hold on its first screen are not used, and the
    game is found, described and written for as if nothing had been kept."""
    from gamelab.adaptation import Store

    store, site = Store(tmp_path), "https://lamps.example/grid"

    async def again(modules):
        fake = Lamps()
        model = Maker(fake, modules)
        played, found = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(30, 1, 120), store=store)
        return fake, model, played, found

    fake, model, played, found = await again([RIGHT])
    assert ending(played) == ("goal_reached", "won") and model.count("write") == 1
    kept = store.made(site)
    assert kept["source"] == RIGHT.strip() and kept["told"]["name"] == "Lamps" and not catalog.MADE
    # the next game: no question of which game, what it is or its rules
    fake, model, played, found = await again([])
    assert ending(played) == ("goal_reached", "won"), (played.record, found)
    assert (model.count("identify"), model.count("describe"), model.count("write"), model.count("play")) == (0, 0, 0, 0)
    assert found["made"]["restored"] and found["identified"]["source"] == "made_kept" and len(fake.taps) == ROWS * COLS
    assert played.record["family"] == found["made"]["family"] and not catalog.MADE
    # what is kept does not hold on the screen (its rules offer no move there): not used, and made anew
    store.save_made(site, {**kept, "source": RIGHT + "\n\ndef legal_actions(state, rules):\n    return []\n"})
    fake, model, played, found = await again([RIGHT])
    assert ending(played) == ("goal_reached", "won"), (played.record, found)
    assert (model.count("identify"), model.count("describe"), model.count("write")) == (1, 1, 1)
    assert found["made_kept"] == "not_taken" and "restored" not in found["made"] and store.made(site)["source"] == RIGHT.strip()
    # nothing kept is a family: a record that is none is left alone
    assert made.Game.restore(site, {"told": {"rows": 2}, "source": 3}) is None and made.Game.restore(site, None) is None


# ── what the written rules say a move leaves, and the cells a reader by samples cannot make out (PLAN §27, S3) ──

FORESEEING = RIGHT + '''
def result(state, action, rules):
    r, c = action["cell"]
    grid = [list(row) for row in state["grid"]]
    grid[r][c] = 1
    return {**state, "grid": grid}
'''


class Rows(Lamps):
    """A 4 by 5 grid of lamps drawn otherwise in each row: a lit lamp has its row's colour, and the dark lamps of
    the row last tapped in are drawn marked, in a shade of that row. `deaf`: taps the game does not take."""

    def __init__(self, deaf=()):
        self.grid, self.taps, self.marked, self.deaf = [[0] * 5 for _ in range(4)], [], None, list(deaf)

    async def capture(self, region=None):
        image = Image.new("RGB", SIZE, (245, 245, 245))
        draw = ImageDraw.Draw(image)
        left, top, width, height = BOARD
        for r, row in enumerate(self.grid):
            for c, cell in enumerate(row):
                x, y = left + c * width / 5, top + r * height / 4
                colour = (250, 60 * r, 0) if cell else (90 + 20 * r, 90, 140) if r == self.marked else (90, 90, 90)
                draw.rectangle((x + 4, y + 4, x + width / 5 - 4, y + height / 4 - 4), fill=colour)
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    async def tap(self, x, y, duration_ms):
        self.taps.append((x, y))
        left, top, width, height = BOARD
        row, col = int((y - top) / (height / 4)), int((x - left) / (width / 5))
        self.marked = row
        if len(self.taps) not in self.deaf:
            self.grid[row][col] = 1


class Vouching:
    """A light model that says of each cell cut out what `sure` is."""

    def __init__(self, sure):
        from gamelab.checker import Spent

        self.sure, self.asked, self.spent = sure, [], Spent()

    async def ask(self, checks, context):
        self.asked.append(checks)
        return {check.key: {"p": self.sure} for check in checks}


async def rows(monkeypatch, module, light, deaf=()):
    monkeypatch.setattr(adaptation, "PLAIN_INSETS", (0.2,))
    fake = Rows(deaf)
    model = Maker(fake, [module], described={**DESCRIBED, "rows": 4, "cols": 5})
    played, found = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(60, 1, 120), light=light)
    assert ending(played)[0] == "goal_reached" and played.record["actions_used"] >= 20, (played.record, found)
    assert model.count("play") == 0
    return fake, model, found


async def test_cells_a_reader_by_samples_cannot_make_out_are_taken_from_what_the_written_rules_say_the_move_left(monkeypatch):
    """The first moves are read by the model and the host's plain adapter reads the rest by their samples. A row
    first played in shows looks no sample has: its dark lamps drawn marked, which the move left as they were, and
    the lamp it lit, which a light model is asked about once. No screen goes back to the reading model for them."""
    light = Vouching(1.0)
    fake, model, found = await rows(monkeypatch, FORESEEING, light)
    # the first screen, the moves before the adapter, and the last screen (the game's end is the model's to see)
    assert model.count("label") <= adaptation.EXPLORE_MOVES + 3 and found["relearned"] <= 1, (model.count("label"), found)
    # rows 1 and 0 were first lit in after the adapter was taken: one question each, of the one lamp
    assert [len(checks) for checks in light.asked] == [1, 1] and "written down as 1" in light.asked[0][0].instructions
    assert found["told_by_rules"] >= 8 and found["light"]["named"] == 2


async def test_a_changed_cell_no_light_model_vouches_for_is_the_reading_models(monkeypatch):
    """Rules written while playing are not taken at their word for the cell a move changed: without a light model's
    yes the screen is read by the model, as before, and the game is played to its end all the same. A look it would
    not vouch for is not asked about twice."""
    light = Vouching(0.2)
    fake, model, found = await rows(monkeypatch, FORESEEING, light)
    assert found["relearned"] >= 2 and found["light"]["named"] == 0 and len(light.asked) == found["light"]["refused"] == 2
    # and rules that do not say what a move leaves play as they did
    fake, model, found = await rows(monkeypatch, RIGHT, None)
    assert found["told_by_rules"] == 0 and found["relearned"] >= 2


async def test_a_move_the_game_did_not_take_is_not_learned_as_its_result(monkeypatch):
    """A tap the game ignores still marks the row: the cell the rules say is lit looks new and is not lit. The light
    model says so, the model reads the screen, and nothing wrong is kept (the game is won with every lamp lit)."""
    class Seeing(Vouching):
        async def ask(self, checks, context):
            self.asked.append(checks)
            answers = {}
            for check in checks:
                image = Image.open(BytesIO(check.image)).convert("RGB")
                answers[check.key] = {"p": 1.0 if image.getpixel((image.width // 2, image.height // 2))[0] == 250 else 0.0}
            return answers
    # the eleventh tap is the first in row 1
    fake, model, found = await rows(monkeypatch, FORESEEING, Seeing(None), deaf=(11,))
    assert all(cell == 1 for row in fake.grid for cell in row) and found["light"]["refused"] >= 1


async def test_a_game_the_family_named_for_it_never_reads_is_described_again_on_its_board_and_played_by_written_rules(monkeypatch):
    """A model names a registered family whose rules the board is none of, and the first screen is described as no
    game written rules could play (a tutorial stands on it). The model plays; when the family is tried again on the
    board and still does not read it, the game is described once more and taken up by rules written for it."""
    monkeypatch.setattr(agent, "RESUME_EVERY", 2)

    class Late(Maker):
        def __init__(self, fake):
            super().__init__(fake, [RIGHT])
            self.descriptions = [{"screen": "game", "fits": False}, DESCRIBED]

        async def complete(self, request):
            context = json.loads(request.messages[0].content[0]["text"])
            if "families" in context:
                self.asked.append(("identify", context))
                return TurnResult(text=json.dumps({"screen": "game", "families": ["slide_merge"], "situation": "lamps"}))
            if context.get("play"):
                self.asked.append(("play", context))
                left, top, width, height = BOARD
                r, c = next((r, c) for r, row in enumerate(self.fake.grid) for c, cell in enumerate(row) if not cell)
                at = [round(left + (c + 0.5) * width / COLS), round(top + (r + 0.5) * height / ROWS)]
                return TurnResult(text=json.dumps({"situation": "lamps", "scene": "board", "guided": False, "score": None,
                                                   "action": {"kind": "tap", "at": at}, "expect": "", "notes": ""}))
            if "family" not in context and "game" not in context:
                self.asked.append(("describe", context))
                return TurnResult(text=json.dumps(self.descriptions.pop(0)))
            return await super().complete(request)

    fake = Lamps()
    model = Late(fake)
    played, found = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(30, 1, 120))
    assert ending(played)[0] == "goal_reached", (played.record, found)
    assert model.count("describe") == 2 and model.count("write") == 1 and 1 <= model.count("play") < ROWS * COLS
    taken = [entry for entry in found["resumes"] if entry["taken"]]
    assert len(taken) == 1 and taken[0]["made"] == found["made"]["family"] == played.record["family"]
    assert sum(move["level"] != "L0" for move in played.record["history"]) >= 1


async def test_cells_that_look_the_same_and_hold_different_things_are_read_as_the_rules_said(monkeypatch):
    """In the two rows played last a lamp lights as one of two kinds, by its column, and both are drawn the same: no
    sample tells them apart, and once the reader has seen both it leaves every such lamp unread on every screen. The
    screen is then what the written rules said the move left, and the reading model is not asked move after move."""
    module = FORESEEING.replace("grid[r][c] = 1\n", "grid[r][c] = 1 + c % 2 if r < 2 else 1\n").replace(
        "want[r][c] = LIT", "want[r][c] = 1 + c % 2 if r < 2 else 1").replace("cell == 1 for", "cell != 0 for")

    class Kinds(Rows):
        async def capture(self, region=None):
            shown, self.grid = self.grid, [[min(cell, 1) for cell in row] for row in self.grid]
            try:
                return await super().capture(region)
            finally:
                self.grid = shown

        async def tap(self, x, y, duration_ms):
            await super().tap(x, y, duration_ms)
            self.grid = [[cell and (1 + c % 2 if r < 2 else 1) for c, cell in enumerate(row)] for r, row in enumerate(self.grid)]

    monkeypatch.setattr(adaptation, "PLAIN_INSETS", (0.2,))
    fake, light = Kinds(), Vouching(1.0)
    model = Maker(fake, [module], described={**DESCRIBED, "rows": 4, "cols": 5})
    played, found = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(60, 1, 120), light=light)
    assert ending(played)[0] == "goal_reached" and played.record["actions_used"] == 20, (played.record, found)
    # the first screen and six moves, then a screen or two once the two kinds first look the same, and the last
    assert model.count("label") <= adaptation.EXPLORE_MOVES + 6 and model.count("play") == 0, (model.count("label"), found)


async def test_a_family_kept_of_a_game_is_taken_up_once_a_notice_on_its_first_screen_is_put_away(tmp_path):
    """The game opens under a start notice: the family kept of it does not read that screen. Once the model's input
    has put the notice away the kept family is tried again, before anything is described or written anew."""
    from gamelab.adaptation import Store

    store = Store(tmp_path)
    fake = Lamps()
    played, found = await play(fake, Maker(fake, [RIGHT]), None, goal=GoalSpec("play_until_end"), budget=Budget(30, 1, 120), store=store)
    assert ending(played) == ("goal_reached", "won") and store.made("https://lamps.example/grid") is not None

    class Covered(Lamps):
        covered = True

        async def capture(self, region=None):
            if not self.covered:
                return await super().capture(region)
            out = BytesIO()
            Image.new("RGB", SIZE, (20, 20, 60)).save(out, format="PNG")
            return out.getvalue()

        async def tap(self, x, y, duration_ms):
            if self.covered:
                self.covered = False
            else:
                await super().tap(x, y, duration_ms)

    class Opening(Maker):
        async def complete(self, request):
            context = json.loads(request.messages[0].content[0]["text"])
            if self.fake.covered and "families" in context:
                self.asked.append(("identify", context))
                return TurnResult(text=json.dumps({"screen": "other", "families": [], "situation": "a start notice"}))
            if self.fake.covered and "family" in context and "reason" not in context:
                self.asked.append(("label", context))
                return TurnResult(text=json.dumps({"screen": "other", "board": None, "rules": None, "state": None}))
            if self.fake.covered:
                self.asked.append(("recover", context))
                return TurnResult(text=json.dumps({"situation": "a start notice", "scene": "popup", "action": {"kind": "tap", "at": [300, 200]}}))
            return await super().complete(request)

    fake = Covered()
    model = Opening(fake, [])
    played, found = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(30, 1, 120), store=store)
    assert ending(played) == ("goal_reached", "won"), (played.record, found)
    assert found["made_kept"] == "taken_past_first_screen" and found["made"]["restored"], ([k for k, _ in model.asked], found)
    assert (model.count("describe"), model.count("write"), model.count("play")) == (0, 0, 0)


async def test_a_family_kept_of_a_game_is_taken_up_when_the_adapter_kept_with_it_does_not_read_the_new_board(tmp_path, monkeypatch):
    """The next game of a site shows its board in looks the kept adapter has no sample of. The family made for the
    game is still its family: the board is read by the model, and nothing is described or written anew."""
    from gamelab.adaptation import Store

    monkeypatch.setattr(adaptation, "PLAIN_INSETS", (0.2,))
    store = Store(tmp_path)

    async def once(fake):
        model = Maker(fake, [FORESEEING], described={**DESCRIBED, "rows": 4, "cols": 5})
        played, found = await play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(60, 1, 120),
                                   light=Vouching(1.0), store=store)
        assert ending(played)[0] == "goal_reached" and played.record["actions_used"] == 20, (played.record, found)
        return model, found

    model, found = await once(Rows())
    assert model.count("write") == 1 and store.load("https://lamps.example/grid", found["made"]["family"]) is not None

    class Tinted(Rows):
        async def capture(self, region=None):
            image = Image.open(BytesIO(await super().capture(region))).convert("RGB")
            out = BytesIO()
            image.point(lambda value: value // 2 + 40).save(out, format="PNG")
            return out.getvalue()

    model, found = await once(Tinted())
    assert found["made"]["restored"] and (model.count("describe"), model.count("write"), model.count("play")) == (0, 0, 0)
