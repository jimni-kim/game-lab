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
