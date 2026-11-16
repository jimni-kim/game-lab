"""The plain adapter (PLAN §11, H5): a game whose board is one grid needs no code written for it. The host
divides the box the model gave into cells and names what each holds by samples cut from the screens the model
read; the model writes nothing, and reads a screen again only when it shows something not seen before."""
from functools import partial

import pytest

from gamelab import adaptation, agent, recovery
from gamelab.adaptation import Store, plain
from gamelab.contracts import Budget, GoalSpec
from fake_screen import Screen
from test_adaptation import Author
from test_adaptation_mines import COMPUTE, Mines, Reader


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(agent, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "GeneratedAdapter", partial(
        adaptation.GeneratedAdapter, settling={"timeout_s": 0.4, "interval_s": 0.05, "stable_s": 0.1}))


async def test_a_tap_game_on_a_grid_is_read_by_the_plain_adapter_with_no_code_written(tmp_path):
    fake = Mines()
    reader = Reader(fake, [])
    played, made = await agent.play(fake, reader, "minesweeper", goal=GoalSpec("play_until_end"),
                                    budget=Budget(60, COMPUTE, 300), store=Store(tmp_path))
    assert (played.record["outcome"], played.record["reason"]) in (("goal_reached", "won"), ("goal_reached", "play_until_end")), (played.record, made)
    # no adapter was asked for, and the one in use is the host's own
    assert made["generations"] == 0 and not [kind for kind, _, _ in reader.asked if kind == "write"]
    assert played.record["levels"]["L3"] > 0 and fake.over()
    kept = next(tmp_path.glob("*/minesweeper/adapter.py")).read_text()
    assert kept.startswith("# The plain adapter") and '"look"' in kept
    # every tap opened a cell: the cells are where the model's box put them
    assert fake.played == len(fake.taps)


async def test_a_key_game_on_a_grid_is_read_by_the_plain_adapter_and_new_values_only_add_samples(tmp_path):
    fake = Screen()
    author = Author(fake, [])
    author.unblocked = True
    played, made = await agent.play(fake, author, "slide_merge", goal=GoalSpec("actions", 60), budget=Budget(120, 20, 120),
                                    store=Store(tmp_path))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    assert made["generations"] == 0 and played.record["levels"]["L3"] > played.record["levels"]["L2"]
    # a tile value not seen before, made by two tiles joining, was told by the rules: its sample was kept
    # without sending the screen to the model (PLAN §17, F5)
    assert made["told_by_rules"] >= 1 and made["relearned"] == 0 and max(map(max, fake.board["grid"])) >= 16


def test_only_a_game_whose_state_is_one_grid_has_a_plain_adapter():
    from gamelab.adaptation import Look, Trace
    grid = Trace("slide_merge", {}, "x", [Look(b"", {"grid": [[0, 2], [0, 0]], "score": 0, "scene": "playing"}, [10, 20, 100, 100])])
    source = plain(grid, {"kind": "keys", "keys": {"up": "ArrowUp"}})
    assert "BOARD, ROWS, COLS, FIELD, OTHERS, INSET = [10, 20, 100, 100], 2, 2, 'grid'" in source
    tray = Trace("block_placement", {}, "x", [Look(b"", {"board": [[0]], "tray": [[[0, 0]]], "score": 0, "scene": "playing"}, [0, 0, 8, 8])])
    assert plain(tray, {"kind": "drag", "source": "tray"}) is None
    piles = Trace("klondike", {}, "x", [Look(b"", {"stock": 1, "waste": "0", "foundations": [], "tableau": [], "scene": "playing"}, [0, 0, 8, 8])])
    assert plain(piles, {"kind": "places"}) is None


def test_a_thing_drawn_a_few_pixels_off_in_its_box_is_still_named_by_its_sample():
    """A grid divided evenly over a board with gutters cuts the same tile at another offset in every column.
    The drawing itself is what is held against the samples, wherever it lies in the box."""
    from PIL import Image, ImageDraw
    from gamelab import samples

    def tile(dx, dy, mark, ground=(238, 228, 218)):
        image = Image.new("RGB", (60, 60), ground)
        draw = ImageDraw.Draw(image)
        if mark == "bar":
            draw.rectangle((20 + dx, 15 + dy, 26 + dx, 45 + dy), fill=(60, 50, 40))
        elif mark == "cross":
            draw.rectangle((20 + dx, 15 + dy, 26 + dx, 45 + dy), fill=(60, 50, 40))
            draw.rectangle((10 + dx, 27 + dy, 36 + dx, 33 + dy), fill=(60, 50, 40))
        return samples.cut(image, [0, 0, 60, 60])
    bank = samples.Bank()
    bank.add(2, tile(0, 0, "bar"))
    bank.add(4, tile(0, 0, "cross"))
    bank.add(0, tile(0, 0, None, ground=(204, 192, 180)))
    assert bank.name(tile(7, -4, "bar")) == (True, 2) and bank.name(tile(-5, 6, "cross")) == (True, 4)
    assert bank.name(tile(0, 0, None, ground=(204, 192, 180))) == (True, 0)
    # another ground, or another drawing, is still a thing nobody read
    assert bank.name(tile(3, 3, "bar", ground=(237, 194, 46))) == (False, None)
    assert bank.name(tile(0, 0, None)) == (False, None)
