"""Games whose cells hold one of several values, read by their family's own reader (PLAN §20): the reader is
taught the first screen a model read, a value not seen before is told by the rules where they can and by a light
model shown the cell alone where they cannot, and no code is written for the game."""
import json

import pytest

from gamelab import adaptation, agent, recovery
from gamelab.adaptation import Store
from gamelab.adapters.kit import KitAdapter
from gamelab.contracts import Budget, GoalSpec
from gamelab.kits import grid
from fake_screen import BOARD, Screen, render
from test_adaptation import Author
import test_adaptation_match3 as gems
import test_adaptation_mines as mines


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(agent, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(agent, "KIT_FIRST_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    settling = {"timeout_s": 0.4, "interval_s": 0.02, "stable_s": 0.05}
    made = KitAdapter.__init__
    monkeypatch.setattr(KitAdapter, "__init__", lambda self, *a, **k: made(self, *a, **{**k, "settling": settling}))


class Light:
    """A light model that names a cell by the colour at its middle (the fake draws each value in one colour)."""

    def __init__(self, names, wrong=()):
        from gamelab.checker import Spent

        self.names, self.wrong, self.asked, self.spent = names, set(wrong), [], Spent()

    async def ask(self, checks, context):
        from io import BytesIO
        from PIL import Image

        self.asked.append(checks)
        answers = {}
        for check in checks:
            image = Image.open(BytesIO(check.image)).convert("RGB")
            value = self.names[image.getpixel((image.width // 2, image.height // 2))]
            answers[check.key] = {"choice": str(value * 2 if value in self.wrong else value), "p": None}
        return answers


def labels(model):
    return [kind for kind, _, _ in model.asked].count("label")


async def test_2048_is_read_by_the_familys_reader_from_its_first_look_and_new_tiles_are_told_by_the_rules(tmp_path):
    fake = Screen()
    author = Author(fake, [])
    played, made = await agent.play(fake, author, "slide_merge", goal=GoalSpec("actions", 60), budget=Budget(80, 20, 120),
                                    store=Store(tmp_path))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    # the model read the first screen only: every tile made after it (4, 8, 16, ...) was named by the rules
    assert labels(author) == 1 and played.record["levels"] == {"L2": 1, "L3": 59}, (author.asked, made)
    assert made["generations"] == 0 and made["kit"]["adopted"] == 1 and made["kit"]["taught"]["rules"] >= 3
    assert {move["effect"] for move in played.moves} == {"confirmed"}
    assert max(max(row) for row in fake.board["grid"]) >= 16
    kept = Store(tmp_path).profile("https://unregistered.example/", "slide_merge")
    assert kept["kit"]["board"] and len(kept["kit"]["looks"]) >= 4
    # the next game on the site asks no model at all
    again = Screen()
    silent = Author(again, [])
    played, made = await agent.play(again, silent, "slide_merge", goal=GoalSpec("actions", 10), budget=Budget(20, 20, 60),
                                    store=Store(tmp_path))
    assert (played.record["outcome"], labels(silent), made["kit"]["restored"]) == ("goal_reached", 0, True), (played.record, made)


def test_a_board_box_a_few_pixels_out_is_fitted_to_the_pictures_own_lines():
    image = grid.open_picture(render({"grid": [[2, 0, 4, 0], [0, 8, 0, 2], [4, 0, 2, 0], [0, 2, 0, 16]]}))
    left, top, width, height = BOARD
    for said in ([left - 6, top + 5, width + 9, height - 8], [left + 7, top - 4, width - 10, height + 6]):
        fitted = grid.fit(image, said, 4, 4)
        assert all(abs(one - other) <= 3 for one, other in zip(fitted, BOARD)), (said, fitted)
    # a picture with no lines to go by leaves the box as it was said
    from PIL import Image

    assert grid.fit(Image.new("RGB", (800, 800), (40, 60, 80)), [100, 100, 400, 400], 4, 4) == [100, 100, 400, 400]


def test_a_cell_is_the_value_whose_look_it_has_and_none_when_it_has_no_look_known_or_two():
    looks = grid.Looks(near=6.0, apart=3.0)
    two, four, eight = bytes([10] * 12), bytes([40] * 12), bytes([44] * 12)
    looks.add(2, two)
    looks.add(4, four)
    assert looks.name(bytes([12] * 12))[0] == 2 and looks.name(bytes([100] * 12))[0] is None
    # a look between two values' is neither
    looks.add(8, eight)
    assert looks.name(bytes([42] * 12))[0] is None
    # what is said of a look now holds over what was said of it before
    looks.add(16, bytes([41] * 12))
    assert looks.name(bytes([41] * 12))[0] == 16 and set(looks.kept) == {2, 16}


MINE_NAMES = {mines.OPEN_FACE: 0, **{ink: count for count, ink in mines.INKS.items()}, mines.COVERED_FACE: 9,
              mines.FLAG_INK: 10, mines.MINE_INK: 11, mines.EXPLODED_FACE: 12}


async def test_minesweeper_is_read_by_the_familys_reader_and_each_new_look_is_named_once_by_a_light_model():
    fake = mines.Mines()
    reader, light = mines.Reader(fake), Light(MINE_NAMES)
    played, made = await agent.play(fake, reader, "minesweeper", goal=GoalSpec("play_until_end"),
                                    budget=Budget(60, mines.COMPUTE, 300), light=light)
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "terminal") or \
        played.record["outcome"] == "goal_reached", (played.record, made)
    assert fake.over() and not any(cell == mines.EXPLODED for row in fake.grid for cell in row)
    # the model read the first screen (every cell covered); each look after it (an opened cell, a 1, a 2, ...)
    # was shown to the light model once, cut out alone, and nobody wrote any code
    assert labels(reader) == 1 and made["generations"] == 0, (reader.asked, made)
    told = made["kit"]["light"]
    assert told["questions"] >= 1 and told["refused"] == 0 and told["named"] == made["kit"]["taught"]["light"] > 0
    assert sum(len(checks) for checks in light.asked) <= 2 * 9 and all(len(checks) <= 2 * 6 for checks in light.asked)


async def test_a_light_models_answer_the_rules_do_not_allow_is_not_kept_and_the_screen_is_the_models():
    fake = mines.Mines()
    # it calls an opened cell with nothing on it covered: no move leaves the cell it opened covered
    reader, light = mines.Reader(fake), Light({**MINE_NAMES, mines.OPEN_FACE: 9})
    played, made = await agent.play(fake, reader, "minesweeper", goal=GoalSpec("play_until_end"),
                                    budget=Budget(60, mines.COMPUTE, 300), light=light)
    assert played.record["outcome"] == "goal_reached" and fake.over(), (played.record, made)
    told = made["kit"]["light"]
    assert told["refused"] >= 1 and labels(reader) > 1 and made["generations"] == 0, (told, made)
    # what the model read taught the reader: the game was not read by the model from there on
    assert played.record["levels"]["L3"] > played.record["levels"]["L2"], played.record["levels"]


async def test_with_no_light_model_a_new_look_is_the_models_to_read_and_still_no_code_is_written():
    fake = mines.Mines()
    reader = mines.Reader(fake)
    played, made = await agent.play(fake, reader, "minesweeper", goal=GoalSpec("play_until_end"),
                                    budget=Budget(60, mines.COMPUTE, 300))
    assert played.record["outcome"] == "goal_reached" and fake.over(), (played.record, made)
    assert 1 < labels(reader) < len(fake.taps) and made["generations"] == 0 and "write" not in [kind for kind, _, _ in reader.asked]


class Muddled(gems.Jeweller):
    """A model that takes some pieces of one colour for another colour nobody else has."""

    async def complete(self, request):
        result = await super().complete(request)
        answer = json.loads(result.text)
        if answer.get("state"):
            answer["state"]["grid"] = [[7 if cell == 3 and col % 3 == 0 else cell for col, cell in enumerate(row)]
                                       for row in answer["state"]["grid"]]
        return type(result)(text=json.dumps(answer))


async def test_match_three_is_read_by_the_familys_reader_by_what_its_pieces_look_like_and_no_code_is_written(tmp_path):
    fake = gems.Gems()
    jeweller = gems.Jeweller(fake)
    played, made = await agent.play(fake, jeweller, "match3", goal=GoalSpec("actions", 20), budget=Budget(40, 200, 120),
                                    store=Store(tmp_path))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    assert played.record["levels"] == {"L2": 1, "L3": 19} and [kind for kind, _ in jeweller.asked] == ["label"], made
    assert not fake.refused and [move["action"] for move in played.moves] == fake.swaps and made["generations"] == 0


async def test_one_kind_of_piece_a_model_calls_two_colours_is_one_kind_by_what_it_looks_like():
    fake = gems.Gems()
    muddled = Muddled(fake)
    played, made = await agent.play(fake, muddled, "match3", goal=GoalSpec("actions", 20), budget=Budget(40, 200, 120))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    # six kinds of piece on the screen are six values, whatever the model called them, and the game is read by
    # the reader from the first screen on
    assert made["kit"]["values"] == len(gems.INKS) and played.record["levels"] == {"L2": 1, "L3": 19}, (played.record, made)
    assert not fake.refused and [kind for kind, _ in muddled.asked] == ["label"]


async def test_a_screen_left_unread_is_looked_at_twice_before_it_is_the_models_however_slow_its_captures(monkeypatch):
    """On a device whose every capture takes longer than a screen is waited on, the first frame after a move was
    already too old: the screen went to the model before the rules were asked what the new tile is."""
    monkeypatch.setattr(agent, "RESTLESS_S", 0.0)
    fake = Screen()
    author = Author(fake, [])
    played, made = await agent.play(fake, author, "slide_merge", goal=GoalSpec("actions", 40), budget=Budget(60, 20, 120))
    assert played.record["outcome"] == "goal_reached" and labels(author) == 1, (played.record, made)
    assert made["kit"]["taught"]["rules"] >= 2


WAYS = {"swap": {"square": True, "swap_power": True, "pair_power": True, "tap_power": False},
        "tap": {"square": False, "swap_power": False, "pair_power": False, "tap_power": True}}


async def test_a_piece_with_a_power_is_learned_from_the_move_that_made_it_and_the_games_ways_from_what_it_took():
    """A game that leaves a piece with a power where four or more lined up, takes four in a square and sets a
    power off when it is swapped: nobody is asked what the new pieces are (the rules say where the move left one
    and of which kind), and each way of moving is tried once and kept as the game took it."""
    fake = gems.Gems(powers=True, rules=WAYS["swap"])
    jeweller = gems.Jeweller(fake)
    played, made = await agent.play(fake, jeweller, "match3", goal=GoalSpec("actions", 40), budget=Budget(60, 200, 120))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    kit = made["kit"]
    assert [kind for kind, _ in jeweller.asked] == ["label"] and not fake.refused, (jeweller.asked, fake.refused)
    assert kit["ways"].items() >= {"square": True, "swap_power": True}.items() and all(kit["ways"].values()), kit["ways"]
    assert kit["taught"]["rules"] >= 3 and kit["values"] > len(gems.INKS)
    # and the pieces with a power were used: none is left standing as if it were a wall
    assert sum(gems.rules.power_of(cell) > 0 for row in fake.state["grid"] for cell in row) <= 2


async def test_a_game_that_sets_a_power_off_by_a_tap_is_found_out_by_trying_and_one_with_no_powers_costs_one_move(tmp_path):
    fake = gems.Gems(powers=True, rules=WAYS["tap"])
    played, made = await agent.play(fake, gems.Jeweller(fake), "match3", goal=GoalSpec("actions", 40), budget=Budget(60, 200, 120),
                                    store=Store(tmp_path))
    assert played.record["outcome"] == "goal_reached", (played.record, made)
    # a swap with a power and a square were each refused once, and after that a power was tapped
    ways = made["kit"]["ways"]
    assert ways.items() >= {"square": False, "swap_power": False, "tap_power": True}.items() and not ways.get("pair_power"), ways
    assert fake.refused == sum(value is False for value in ways.values()) and fake.pops and all(move["effect"] == "confirmed" for move in played.moves if move["kind"] == "pop")
    # the next game on the site begins with what this one found out: nothing is tried again
    kept = Store(tmp_path).profile("https://gems.example/", "match3")
    assert {name: kept["rules"][name] for name in ways} == ways
    again = gems.Gems(draw=7, powers=True, rules=WAYS["tap"])
    played, made = await agent.play(again, gems.Jeweller(again), "match3", goal=GoalSpec("actions", 20), budget=Budget(40, 200, 120),
                                    store=Store(tmp_path))
    assert played.record["outcome"] == "goal_reached" and not again.refused and "ways" not in made["kit"], (again.refused, made["kit"])
    # a game with no powers at all: only the square is tried, once
    plain = gems.Gems()
    played, made = await agent.play(plain, gems.Jeweller(plain), "match3", goal=GoalSpec("actions", 30), budget=Budget(50, 200, 120))
    assert made["kit"].get("ways", {}) in ({}, {"square": False}) and plain.refused <= 1, (made["kit"], plain.refused)


async def test_a_look_not_learned_is_what_a_light_model_calls_it_else_a_power_until_the_game_refuses_it():
    from gamelab import kits
    from gamelab.kits.match3 import UNKNOWN
    from gamelab.rules.match3 import LINE, LOOSE, POWERED

    fake = gems.Gems(powers=True, rules=WAYS["swap"])
    kit = kits.make("match3", {"rows": 8, "cols": 8})
    assert kit.calibrate(await fake.capture(), gems.BOARD, fake.state)
    # two things nobody saw made: a piece with a power, and another kind of thing
    fake.state["grid"][2][3], fake.state["grid"][5][5] = POWERED * LINE + 2, POWERED * 3
    png = await fake.capture()
    assert kit.read(png)[0]["grid"][2][3] == UNKNOWN            # worth using, until something says what it is
    asked = kit.settle(png, None)
    assert sorted(cells for cells, _ in asked) == [[(2, 3)], [(5, 5)]] and all(pictures for _, pictures in asked)
    assert set(kit.asked()) >= {"line", "area", "colour", "seeker", "fixed", "loose", "piece"}
    kit.name(png, [(2, 3)], "line")
    kit.name(png, [(5, 5)], None)
    grid = kit.read(png)[0]["grid"]
    assert (grid[2][3], grid[5][5]) == (POWERED * LINE, UNKNOWN) and kit.settle(png, None) == []
    # the game did not take a move that used the second as a piece: it is something that falls and is not swapped
    assert kit.refused(png, [[5, 5], [5, 6]]) and kit.read(png)[0]["grid"][5][5] == LOOSE
    assert not kit.refused(png, [[2, 3]])                       # what is known to be a piece with a power stays one


class Walled(gems.Gems):
    """A game some of whose cells hold a thing that is no piece, drawn in a colour of its own."""
    WALLS = [(3, 2), (3, 3), (4, 5)]

    def __init__(self, **options):
        super().__init__(**options)
        for r, c in self.WALLS:
            self.state["grid"][r][c] = gems.rules.BLOCK

    async def capture(self, region=None):
        from io import BytesIO
        from PIL import Image, ImageDraw

        image = Image.open(BytesIO(await super().capture())).convert("RGB")
        draw = ImageDraw.Draw(image)
        for r, row in enumerate(self.state["grid"]):
            for c, value in enumerate(row):
                if value == gems.rules.BLOCK:
                    x, y = gems.BOARD[0] + c * gems.CELL, gems.BOARD[1] + r * gems.CELL
                    draw.rectangle((x + 4, y + 4, x + gems.CELL - 5, y + gems.CELL - 5), fill=(245, 240, 235))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


class Fooled(gems.Jeweller):
    """A model that calls the thing that is no piece a white piece."""

    async def complete(self, request):
        result = await super().complete(request)
        answer = json.loads(result.text)
        if answer.get("state"):
            answer["state"]["grid"] = [[8 if cell == gems.rules.BLOCK else cell for cell in row] for row in answer["state"]["grid"]]
        return type(result)(text=json.dumps(answer))


async def test_a_thing_a_model_calls_a_piece_that_the_game_never_lets_be_swapped_is_learned_to_be_none():
    fake = Walled()
    fooled = Fooled(fake)
    played, made = await agent.play(fake, fooled, "match3", goal=GoalSpec("actions", 30), budget=Budget(60, 200, 120))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    # the moves that needed it swapped were refused a few times at most, the reader was not doubted for them
    # (the model read the first screen only), and the game went on with the thing read as something fixed
    assert fake.refused <= 3 and [kind for kind, _ in fooled.asked] == ["label"], (fake.refused, fooled.asked)
    assert made["kit"]["values"] == len(gems.INKS) + 1
