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

# the reader as it is made, before a test gives it the settling it plays by
KIT_INIT = KitAdapter.__init__


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
    # the model read the first screen once; the reader, taught by that reading, read it itself, and every move was
    # played from the reader's reading (PLAN §26, C3)
    assert played.record["levels"] == {"L2": 0, "L3": 20} and [kind for kind, _ in jeweller.asked] == ["label"], made
    assert not fake.refused and [move["action"] for move in played.moves] == fake.swaps and made["generations"] == 0


async def test_one_kind_of_piece_a_model_calls_two_colours_is_one_kind_by_what_it_looks_like():
    fake = gems.Gems()
    muddled = Muddled(fake)
    played, made = await agent.play(fake, muddled, "match3", goal=GoalSpec("actions", 20), budget=Budget(40, 200, 120))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    # six kinds of piece on the screen are six values, whatever the model called them, and the game is read by
    # the reader from the first screen on
    assert made["kit"]["values"] == len(gems.INKS) and played.record["levels"] == {"L2": 0, "L3": 20}, (played.record, made)
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
    assert sum(gems.rules.power_of(cell) > 0 for row in fake.state["grid"] for cell in row) <= 3


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
    assert made["kit"].get("ways", {}) in ({}, {"square": True}) and not plain.refused, (made["kit"], plain.refused)


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


COUNTER = [20, 20, 90, 40]


def settle_slowly(monkeypatch):
    """A settling whose time is over long after a refused move is let go."""
    settling = {"timeout_s": 4.0, "interval_s": 0.02, "stable_s": 0.05}
    monkeypatch.setattr(KitAdapter, "__init__", lambda self, *a, **k: KIT_INIT(self, *a, **{**k, "settling": settling}))


class Counted(gems.Gems):
    """A game that shows beside its board how many moves are left: a number drawn as a shade of its own."""

    def __init__(self, **options):
        super().__init__(**options)
        self.left = 40

    def _play(self, action):
        taken = len(self.swaps) + len(self.pops)
        super()._play(action)
        self.left -= len(self.swaps) + len(self.pops) - taken

    async def capture(self, region=None):
        from io import BytesIO
        from PIL import Image, ImageDraw

        image = Image.open(BytesIO(await super().capture())).convert("RGB")
        left, top, width, height = COUNTER
        shade = 60 + self.left * 97 % 190
        ImageDraw.Draw(image).rectangle((left, top, left + width, top + height), fill=(shade, shade, shade))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


class Counting(gems.Jeweller):
    """A model that also says where the game shows the moves left."""

    async def complete(self, request):
        result = await super().complete(request)
        answer = json.loads(result.text)
        if answer.get("state"):
            answer["panel"] = {"moves": COUNTER}
        return type(result)(text=json.dumps(answer))


async def test_a_move_whose_board_and_count_of_moves_left_stay_as_they_were_is_refused_without_waiting_out_the_settling(monkeypatch):
    settle_slowly(monkeypatch)
    monkeypatch.setattr(agent, "REFUSED_S", 0.2)
    fake = Counted(powers=True, rules=WAYS["tap"])
    model = Counting(fake)
    played, made = await agent.play(fake, model, "match3", goal=GoalSpec("actions", 30), budget=Budget(50, 200, 120))
    assert played.record["outcome"] == "goal_reached", (played.record, made)
    # the model was asked where the count is, and it was seen to change with the moves the game took
    assert "panel" in model.asked[0][1] and made["kit"]["counter"]["seen"] >= 1 and not made["kit"]["counter"]["missed"]
    refused = [move for move in played.moves if move["effect"] == "absent"]
    assert refused and fake.refused == len(refused), (fake.refused, played.moves)
    # a refused move the reader read once the count was seen to change was let go soon after it was sent, not
    # when the settling's four seconds were over
    first = next(index for index, move in enumerate(played.moves) if move["effect"] == "confirmed" and move["level"] == "L3")
    later = [move for move in played.moves[first + 1:] if move["effect"] == "absent" and move["read_after_by"] == "L3"]
    assert later and all(move["timing"]["read"]["refused_s"] < 1.0 for move in later), refused


async def test_with_no_count_of_moves_left_known_a_refused_move_is_let_go_after_the_longer_wait(monkeypatch):
    settle_slowly(monkeypatch)
    monkeypatch.setattr(agent, "ABSENT_S", 0.5)
    fake = gems.Gems(powers=True, rules=WAYS["tap"])
    played, made = await agent.play(fake, gems.Jeweller(fake), "match3", goal=GoalSpec("actions", 20), budget=Budget(40, 200, 120))
    refused = [move for move in played.moves if move["effect"] == "absent"]
    assert played.record["outcome"] == "goal_reached" and refused and "counter" not in made["kit"], (played.record, made)
    assert all(0.5 <= move["timing"]["read"]["refused_s"] < 1.5 for move in refused), refused


class Wide(gems.Gems):
    """A board nine cells wide with no cell at the ends of its two top rows, and no lines between its cells: what
    shows where it has no cell is the page."""
    HOLES = [(0, 0), (1, 0), (0, 8), (1, 8)]

    def __init__(self, **options):
        super().__init__(rules={"square": False, "swap_power": False, "pair_power": False, "tap_power": False, "rows": 8, "cols": 9}, **options)
        for r, c in self.HOLES:
            self.state["grid"][r][c] = gems.rules.EMPTY

    async def capture(self, region=None):
        from io import BytesIO
        from PIL import Image, ImageDraw

        image = Image.open(BytesIO(await super().capture())).convert("RGB")
        draw = ImageDraw.Draw(image)
        for r, c in self.HOLES:
            x, y = gems.BOARD[0] + c * gems.CELL, gems.BOARD[1] + r * gems.CELL
            draw.rectangle((x, y, x + gems.CELL - 1, y + gems.CELL - 1), fill=gems.PAGE)
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


class Narrow(gems.Jeweller):
    """A model that reads the wide board as its seven middle columns, box and all, unless shown a grid to read by."""

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        self.asked.append(("label", context))
        grid = self.fake.state["grid"]
        left, top, width, height = gems.BOARD[0], gems.BOARD[1], 9 * gems.CELL, 8 * gems.CELL
        if "board" not in context:
            grid = [row[1:8] for row in grid]
            left, width = left + gems.CELL, 7 * gems.CELL
        return gems.TurnResult(text=json.dumps({
            "screen": "board", "board": [left + 2, top - 1, width - 3, height + 2], "rules": {"rows": len(grid), "cols": len(grid[0])},
            "state": {"grid": grid, "score": None, "scene": "playing"}}))


async def test_a_board_read_narrower_than_it_is_is_counted_by_the_looks_of_its_pieces_and_read_again_by_that_grid():
    fake = Wide()
    model = Narrow(fake)
    played, made = await agent.play(fake, model, "match3", goal=GoalSpec("actions", 20), budget=Budget(40, 200, 120))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    # asked twice: once with no grid (it read seven columns), once with the nine the pieces lay out drawn
    assert [kind for kind, _ in model.asked] == ["label", "label"] and "board" in model.asked[1][1], model.asked
    # the only move refused tried four in a square, which this game does not take
    assert fake.refused <= 1 and made["kit"].get("ways", {}) in ({}, {"square": False}), (fake.refused, made["kit"])
    assert [move["effect"] for move in played.moves].count("confirmed") >= 19


def test_a_reader_counts_a_board_by_the_looks_it_knows_and_grows_it_by_the_lines_beside_that_hold_them():
    import asyncio
    from gamelab import kits

    fake = Wide()
    png = asyncio.run(fake.capture())
    narrow = {"grid": [row[1:8] for row in fake.state["grid"]], "score": None, "scene": "playing"}
    left, top = gems.BOARD[:2]
    box, rows, cols = kits.survey("match3", {}, png, [left + gems.CELL, top, 7 * gems.CELL, 8 * gems.CELL], narrow)
    assert (rows, cols) == (8, 9) and all(abs(one - other) <= gems.CELL // 4 for one, other in zip(box, [left, top, 9 * gems.CELL, 8 * gems.CELL])), box
    # a reading of the right count is left as it is
    whole = {"grid": fake.state["grid"], "score": None, "scene": "playing"}
    assert kits.survey("match3", {}, png, [left, top, 9 * gems.CELL, 8 * gems.CELL], whole)[1:] == (8, 9)
    assert kits.survey("slide_merge", {}, png, [left, top, 9 * gems.CELL, 8 * gems.CELL], whole) is None


def test_where_the_board_has_no_cell_is_learned_from_a_light_model_and_a_piece_shown_there_makes_it_a_cell():
    import asyncio
    from gamelab import kits

    fake = Wide()
    png = asyncio.run(fake.capture())
    kit = kits.make("match3", {"rows": 8, "cols": 9})
    said = [list(row) for row in fake.state["grid"]]
    said[7][4] = gems.rules.EMPTY                # a reading that called a cell at the bottom none
    left, top = gems.BOARD[:2]
    assert kit.calibrate(png, [left, top, 9 * gems.CELL, 8 * gems.CELL], {"grid": said, "score": None, "scene": "playing"})
    assert kit.holes == {*Wide.HOLES, (7, 4)}
    kit.holes.discard((0, 8))                    # and one that left a place with no cell out
    asked = kit.settle(png, None)
    # the piece at the bottom is a cell after all, and the place at the end of the top row is asked about
    assert (7, 4) not in kit.holes and [cells for cells, _ in asked] == [[(0, 8)]], (kit.holes, asked)
    assert kit.name(png, [(0, 8)], "none") == gems.rules.EMPTY and (0, 8) in kit.holes
    assert kit.read(png)[0]["grid"][0][8] == gems.rules.EMPTY and kit.settle(png, None) == []


GOAL_BOX, OTHER_BOX = [560, 100, 48, 48], [560, 170, 48, 48]


class Goaled(gems.Gems):
    """A game that shows its level's goals beside the board: collect yellow pieces (drawn as one is on the board,
    a little larger), and a score."""

    async def capture(self, region=None):
        from io import BytesIO
        from PIL import Image, ImageDraw

        image = Image.open(BytesIO(await super().capture())).convert("RGB")
        draw = ImageDraw.Draw(image)
        left, top, width, height = GOAL_BOX
        draw.rectangle((left, top, left + width - 1, top + height - 1), fill=gems.WELL)
        draw.ellipse((left + 4, top + 4, left + width - 5, top + height - 5), fill=gems.INKS[3])
        left, top, width, height = OTHER_BOX
        draw.polygon([(left, top + height), (left + width // 2, top), (left + width, top + height)], fill=(250, 200, 0))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


class Aiming(gems.Jeweller):
    """A model that also lists the level's goals, calling the yellow piece by another colour's name."""

    async def complete(self, request):
        result = await super().complete(request)
        answer = json.loads(result.text)
        if answer.get("state"):
            answer["panel"] = {"goals": [{"box": GOAL_BOX, "count": 20, "kind": "collect", "name": "green gem"},
                                         {"box": OTHER_BOX, "count": 5000, "kind": "other"},
                                         {"box": [9000, 0, 5, 5], "count": 3, "kind": "collect"}]}
        return type(result)(text=json.dumps(answer))


async def test_a_levels_goals_are_about_the_cells_their_pictures_look_like_and_count_down_with_the_moves_taken():
    fake = Goaled()
    model = Aiming(fake)
    played, made = await agent.play(fake, model, "match3", goal=GoalSpec("actions", 12), budget=Budget(30, 200, 120))
    assert played.record["outcome"] == "goal_reached", (played.record, made)
    assert "goals" in model.asked[0][1]["panel"]
    goals = made["kit"]["goals"]
    # the yellow piece is what the first goal is about, whatever the model called it; a goal off the screen is left out
    assert [(goal["kind"], goal["target"]) for goal in goals] == [("collect", 3), ("other", None)], goals
    assert goals[0]["count"] < 20 and goals[1]["count"] == 5000, goals


def test_a_moves_progress_is_what_it_takes_of_what_the_goals_are_about_up_to_what_each_still_needs():
    from gamelab.rules import match3 as rules

    goals = [{"kind": "collect", "target": 3, "count": 2}, {"kind": "collect", "target": 5, "count": 10},
             {"kind": "bring_down", "target": 12, "count": 1}, {"kind": "other", "target": None, "count": 900}]
    assert rules.progress(goals, {3: 4, 5: 3, 1: 6}) == 2 + 3
    assert rules.advance(goals, {3: 4, 5: 3}) == [{**goals[0], "count": 0}, {**goals[1], "count": 7}, goals[2], goals[3]]
    assert rules.progress(None, {3: 1}) == 0


class Boxed(Walled):
    """A game whose things fixed are boxes of two layers: a line cleared beside one cracks it (drawn darker), and
    a second breaks it, leaving a piece in its place."""
    WALLS = [(3, 2), (3, 3), (4, 5), (6, 1), (2, 6)]

    def __init__(self, **options):
        super().__init__(**options)
        self.layers = {cell: 2 for cell in self.WALLS}
        self.broken = 0

    def _play(self, action):
        before = self.state
        hit = []
        if action in self.family.model.legal_actions(self.state, self.family.rules):
            hit = gems.rules.apply(before, {**self.family.rules, "beside": True}, action)["hit"]
        super()._play(action)
        for r, c in hit:
            self.layers[(r, c)] -= 1
            if not self.layers[(r, c)]:
                del self.layers[(r, c)]
                self.broken += 1
                grid = self.state["grid"]
                for colour in gems.INKS:
                    grid[r][c] = colour
                    if not gems.rules._lines_through(grid, (r, c)):
                        break

    async def capture(self, region=None):
        from io import BytesIO
        from PIL import Image, ImageDraw

        image = Image.open(BytesIO(await super().capture())).convert("RGB")
        draw = ImageDraw.Draw(image)
        for (r, c), left in self.layers.items():
            if left == 1:
                x, y = gems.BOARD[0] + c * gems.CELL, gems.BOARD[1] + r * gems.CELL
                draw.rectangle((x + 4, y + 4, x + gems.CELL - 5, y + gems.CELL - 5), fill=(150, 110, 70))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


async def test_things_fixed_that_a_line_beside_them_breaks_are_learned_so_and_each_new_layer_as_the_same_thing():
    fake = Boxed(rules={"square": True, "swap_power": False, "pair_power": False, "tap_power": False})
    model = gems.Jeweller(fake)
    played, made = await agent.play(fake, model, "match3", goal=GoalSpec("actions", 40), budget=Budget(60, 200, 120))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    kit = made["kit"]
    # the game showed that they break; the cracked box was learned as the same thing by the rules, with nobody asked
    assert kit["ways"].get("beside") is True and kit["taught"]["rules"] >= 1, kit
    assert [kind for kind, _ in model.asked] == ["label"] and fake.broken >= 1, (model.asked, fake.broken)


def test_a_count_of_moves_left_seen_to_change_is_not_moved_by_one_reading_that_puts_it_elsewhere():
    from gamelab import kits

    kit = kits.make("match3", {"rows": 8, "cols": 8})
    kit.place_counter([800, 300, 120, 90])
    kit.tally(True)
    kit.place_counter([890, 320, 20, 41])
    assert kit.counter == [800, 300, 120, 90] and kit.counts
    kit.place_counter([802, 298, 118, 92])             # the same place, read a little otherwise
    assert kit.counter == [802, 298, 118, 92] and kit.counts


STEADY, FLICKER = (245, 240, 235), ((255, 0, 255), (0, 200, 200))


class Flickering(gems.Gems):
    """A game in which, after a few moves, two things that are no piece show at the top: one drawn the same on
    every screen, one in another colour on each (an effect that keeps playing)."""

    def __init__(self, **options):
        super().__init__(**options)
        self.shots = 0

    def _play(self, action):
        super()._play(action)
        if len(self.swaps) == 3:
            self.state["grid"][0][0] = self.state["grid"][0][7] = gems.rules.BLOCK

    async def capture(self, region=None):
        from io import BytesIO
        from PIL import Image, ImageDraw

        self.shots += 1
        image = Image.open(BytesIO(await super().capture())).convert("RGB")
        draw = ImageDraw.Draw(image)
        for col, ink in ((0, STEADY), (7, FLICKER[self.shots % 2])):
            if self.state["grid"][0][col] == gems.rules.BLOCK:
                x, y = gems.BOARD[0] + col * gems.CELL, gems.BOARD[1]
                draw.rectangle((x + 4, y + 4, x + gems.CELL - 5, y + gems.CELL - 5), fill=ink)
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


async def test_a_look_caught_changing_on_a_board_at_rest_is_not_put_to_the_light_model_nor_learned(monkeypatch):
    from io import BytesIO
    from PIL import Image

    monkeypatch.setattr(agent, "UNSTABLE_GAP_S", 0.0)
    fake = Flickering()
    light = Light({STEADY: gems.rules.BLOCK})
    played, made = await agent.play(fake, gems.Jeweller(fake), "match3", goal=GoalSpec("actions", 12),
                                    budget=Budget(30, 200, 120), light=light)
    assert played.record["outcome"] == "goal_reached", (played.record, made)
    shown = [Image.open(BytesIO(check.image)).convert("RGB") for checks in light.asked for check in checks]
    middles = {image.getpixel((image.width // 2, image.height // 2)) for image in shown}
    # the steady one was asked about and named; the one that keeps changing never was
    assert STEADY in middles and not middles & set(FLICKER), middles
    assert made["kit"]["light"]["unstable"] >= 1 and made["kit"]["light"]["named"] >= 1, made["kit"]["light"]


JELLY, JELLY_GOAL = (120, 200, 250), [560, 100, 48, 48]


class Jellied(gems.Gems):
    """A game some of whose cells have a layer laid under their piece, seen through over the whole cell (the piece
    on it looks otherwise than one with none), cleared by clearing the piece on it; its goal, beside the board, is
    to clear them all."""
    LAYERED = [(r, c) for r in (5, 6, 7) for c in range(2, 6)]

    def __init__(self, **options):
        super().__init__(**options)
        self.state["under"] = [[1 if (r, c) in self.LAYERED else 0 for c in range(8)] for r in range(8)]

    async def capture(self, region=None):
        from io import BytesIO
        from PIL import Image, ImageDraw

        image = Image.open(BytesIO(await super().capture())).convert("RGB")
        draw = ImageDraw.Draw(image)
        for r, row in enumerate(self.state["under"]):
            for c, layers in enumerate(row):
                if layers:
                    # the layer is seen through: over the whole cell, the piece on it too
                    x, y = gems.BOARD[0] + c * gems.CELL, gems.BOARD[1] + r * gems.CELL
                    cell = image.crop((x, y, x + gems.CELL, y + gems.CELL))
                    image.paste(Image.blend(cell, Image.new("RGB", cell.size, JELLY), 0.4), (x, y))
        left, top, width, height = JELLY_GOAL
        draw.rectangle((left, top, left + width - 1, top + height - 1), fill=JELLY)
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


class Layering(gems.Jeweller):
    """A model that reads the layers under the pieces and the goal to clear them."""

    async def complete(self, request):
        result = await super().complete(request)
        answer = json.loads(result.text)
        if answer.get("state"):
            answer["state"]["under"] = self.fake.state["under"]
            left = sum(map(sum, self.fake.state["under"]))
            answer["panel"] = {"goals": [{"box": JELLY_GOAL, "count": left, "kind": "collect"}]}
        return type(result)(text=json.dumps(answer))


async def test_layers_under_pieces_are_read_by_the_cells_corners_and_a_goal_to_clear_them_is_got_on_with():
    from gamelab.rules.match3 import UNDER

    fake = Jellied()
    model = Layering(fake)
    played, made = await agent.play(fake, model, "match3", goal=GoalSpec("actions", 20), budget=Budget(40, 200, 120))
    assert played.record["outcome"] == "goal_reached", (played.record, made)
    goal = made["kit"]["goals"][0]
    assert (goal["kind"], goal["target"]) == ("collect", UNDER), made["kit"]["goals"]
    assert made["kit"]["goals_seen"][0]["goals"][0]["target"] == UNDER
    left = sum(map(sum, fake.state["under"]))
    # the moves went for the layers: most are cleared, and the count kept is what the screen shows
    assert left <= 1 and goal["count"] <= left, (left, goal)        # with no goal about them, 3 were left
    # read by a model once; when the moves the code chose stopped gaining on the goal, a model was asked to pick
    # among them (PLAN §31, L3), and with no pick the code's own choice stood
    assert [kind for kind, context in model.asked if not context.get("pick")] == ["label"], model.asked
    picks = [context for _, context in model.asked if context.get("pick")]
    assert all(context["why"] == "no_progress" and context["goals"][0]["weighed"] and len(context["moves"]) >= 2
               for context in picks)
    assert made["kit"].get("picks", {"questions": 0})["questions"] == len(picks)


def test_a_goals_picture_drawn_shaded_is_still_the_value_its_looks_are_nearest_by_far_and_one_like_none_is_none():
    import asyncio
    from io import BytesIO
    from PIL import Image, ImageDraw
    from gamelab import kits
    from gamelab.kits import grid
    from gamelab.kits.match3 import GOAL_NEAR

    fake = gems.Gems()
    png = asyncio.run(fake.capture())
    kit = kits.make("match3", {"rows": 8, "cols": 8})
    assert kit.calibrate(png, gems.BOARD, fake.state)
    image = Image.open(BytesIO(png)).convert("RGB")
    draw = ImageDraw.Draw(image)
    shaded, box = tuple(round(part * 0.8) for part in gems.INKS[4]), [560, 100, 60, 60]       # a green piece, darker
    draw.rectangle((560, 100, 619, 159), fill=gems.WELL)
    draw.ellipse((566, 106, 613, 153), fill=shaded)
    draw.rectangle((560, 200, 619, 259), fill=(150, 100, 50))                                 # nothing on the board
    picture = grid.open_picture((lambda out: (image.save(out, format="PNG"), out.getvalue())[1])(BytesIO()))
    seen = grid.look(picture, box, 0.3, kit.side)
    assert kit.looks.far(seen, 4) > GOAL_NEAR * kit.near           # too far for the plain test
    assert kit._pictured(picture, box) == 4 and kit._pictured(picture, [560, 200, 60, 60]) is None


async def test_a_power_the_reading_model_put_no_kind_to_is_named_by_the_light_model_not_kept_as_no_kind():
    """A level that begins with pieces with a power (paper planes): the reading model says only that they have a
    power. The family's reader does not learn them as that: their look is put to the light model, which names
    the kind, and a later reading that again says only "a power" agrees with the kind learned."""
    from gamelab import kits
    from gamelab.kits.match3 import UNKNOWN
    from gamelab.rules.match3 import POWERED, SEEKER

    fake = gems.Gems(powers=True)
    fake.state["grid"][2][3] = UNKNOWN
    png = await fake.capture()
    kit = kits.make("match3", {"rows": 8, "cols": 8})
    assert kit.calibrate(png, gems.BOARD, fake.state)
    assert kit.read(png)[0]["grid"][2][3] == UNKNOWN            # a power, until it is named
    asked = kit.settle(png, None)
    assert [cells for cells, _ in asked] == [[(2, 3)]]
    kit.name(png, [(2, 3)], "seeker")
    state = kit.read(png)[0]
    assert state["grid"][2][3] == POWERED * SEEKER and kit.settle(png, None) == []
    assert kit.same(state, fake.state)                          # the model's "a power" is the seeker learned here
    other = json.loads(json.dumps(fake.state))
    other["grid"][2][3] = 1
    assert not kit.same(state, other)

