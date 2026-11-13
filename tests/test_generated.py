"""A site adapter whose reading is generated code: played through the sandbox, and refused when it is wrong
(adaptive perception plan §4.3, §6, stage 4). The code here is written by hand as a model would write it."""
import pytest

from gamelab.adapters.generated import GeneratedAdapter, check_layout
from gamelab.contracts import Budget, GoalSpec
from gamelab.play import TRIAL_MOVES, play
from test_screen import Screen, screen_reading  # noqa: F401

ADAPTER = """
from gamelab.sandbox.helpers import nearest

BOARD = {board}
PALETTE = {palette}
KEYS = {{"up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight"}}

def measure(picture):
    if picture.width < BOARD[0] + BOARD[2] or picture.height < BOARD[1] + BOARD[3]:
        return None
    return {{"board": BOARD, "size": 4}}

def read(picture, layout):
    left, top, width, height = layout["board"]
    size = layout["size"]
    grid = []
    for row in range(size):
        cells = []
        for col in range(size):
            x, y = left + (col + 0.5) * width / size, top + (row + 0.3) * height / size
            value, far = nearest(picture.pixel(x, y), PALETTE)
            cells.append(int(value) if far <= 12 else None)
        grid.append(cells)
    return {{"grid": grid, "score": None, "scene": "playing"}}

def target(action, state, layout):
    return {{"kind": "key", "key": KEYS[action["direction"]]}}
"""


def source(fake, **changed):
    board = fake.measured.layout["board"]
    palette = {str(entry["value"]): entry["rgb"] for entry in board["palette"]}
    text = ADAPTER.format(board=board["rect"], palette=palette)
    for old, new in changed.items():
        assert OLD[old] in text
        text = text.replace(OLD[old], new)
    return text


# the lines a faulty adapter differs in
OLD = {"cell": "cells.append(int(value) if far <= 12 else None)",
       "key": 'return {"kind": "key", "key": KEYS[action["direction"]]}',
       "layout": 'return {"board": BOARD, "size": 4}',
       "read": "    left, top, width, height = layout[\"board\"]\n    size = layout[\"size\"]"}


async def run(fake, text, moves=TRIAL_MOVES + 1):
    adapter = GeneratedAdapter("slide_merge", text, site="https://example.test/", call_timeout_s=1.0,
                               settling={"timeout_s": 0.6, "interval_s": 0.05, "stable_s": 0.1})   # a short wait for a screen to settle
    try:
        played = await play(fake, adapter=adapter, goal=GoalSpec("actions", moves), budget=Budget(8, 20, 60))
    finally:
        adapter.close()
    return played, adapter


async def test_a_game_is_read_and_played_by_generated_code_in_the_sandbox():
    fake = Screen()
    played, adapter = await run(fake, source(fake))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, adapter.last_error)
    assert len(fake.keys) == TRIAL_MOVES + 1 and played.record["trial"] == 0 and played.vision["asked"] == 0
    assert adapter.calls["failed"] == 0 and adapter.sandbox.restarts == 1
    assert played.geometry["adapter"] == adapter.digest and played.geometry["board"] == fake.measured.layout["board"]["rect"]


@pytest.mark.parametrize(("fault", "sent", "ending"), [
    ({"cell": "cells.append(3)"}, 0, ("unsupported", "unreadable")),
    ({"read": "    return [1, 2, 3]"}, 0, ("unsupported", "unreadable")),
    ({"cell": "cells.append(None if (row, col) == (0, 0) else (int(value) if far <= 12 else None))"}, 0, ("unsupported", "unreadable")),
    ({"cell": "cells.append((int(value) if far <= 12 else None) if (row, col) != (0, 0) else 2)"}, 1, ("unsupported", "profile_unconfirmed")),
    ({"read": "    while True:\n        pass"}, 0, ("unsupported", "unreadable")),
    ({"read": "    import os\n    os._exit(1)"}, 0, ("unsupported", "unreadable")),
    ({"read": "    open('/etc/hosts', 'a').write('x')\n" + OLD["read"]}, 0, ("unsupported", "unreadable")),
    ({"layout": 'return {"board": [0, 0, 99999, 10], "size": 4}'}, 0, ("unsupported", "board_not_found")),
    ({"layout": "return None"}, 0, ("unsupported", "board_not_found")),
    ({"key": 'return {"kind": "key", "key": "F5"}'}, 0, ("needs_recovery", "input_refused")),
    ({"key": 'return {"kind": "tap", "x": 5, "y": 5}'}, 0, ("needs_recovery", "input_refused")),
    ({"key": 'return {"kind": "key", "key": "ArrowLeft", "then": "Enter"}'}, 0, ("needs_recovery", "input_refused")),
], ids=["a state the rules do not allow", "a state that is no state", "a cell it cannot read", "a cell it reads wrongly",
        "an endless loop", "a crash", "a write outside its folder", "a layout off the screen", "no layout",
        "a key no game is played with", "another kind of input than the game's", "more than one input"])
async def test_wrong_generated_code_cannot_play_wrongly(fault, sent, ending):
    """Whatever is wrong with the code, the result is a game not played: no input the rules and the layout
    do not account for is ever sent."""
    fake = Screen()
    played, adapter = await run(fake, source(fake, **fault))
    assert (played.record["outcome"], played.record["reason"]) == ending, (played.record, adapter.last_error)
    assert len(fake.keys) == sent and not fake.taps and not fake.drags


def test_a_pointer_input_must_land_inside_the_layouts_regions():
    adapter = GeneratedAdapter("minesweeper", "")
    try:
        adapter.layout, adapter.size = {"board": [100, 100, 200, 200], "regions": [[400, 100, 50, 50]]}, [800, 600]
        assert adapter.checked_input({"kind": "tap", "x": 150, "y": 150}).arguments["start_x"] == 150
        assert adapter.checked_input({"kind": "tap", "x": 410.5, "y": 120}).tool == "browser_drag"
        for wrong in ({"kind": "tap", "x": 350, "y": 150}, {"kind": "tap", "x": float("nan"), "y": 1},
                      {"kind": "tap", "x": "150", "y": 150}, {"kind": "drag", "x0": 150, "y0": 150, "x1": 160, "y1": 160, "ms": 100},
                      {"kind": "key", "key": "ArrowLeft"}, None, "tap"):
            with pytest.raises(ValueError):
                adapter.checked_input(wrong)
    finally:
        adapter.close()


def test_a_layout_is_boxes_of_whole_pixels_on_the_screen():
    size = [800, 600]
    assert check_layout({"board": [0, 0, 800, 600], "cells": "anything"}, size)
    for wrong in (None, [], {"board": [0, 0, 801, 600]}, {"board": [1.5, 0, 10, 10]}, {"board": [0, 0, 0, 10]},
                  {"board": [-1, 0, 10, 10]}, {"board": [0, 0, 10, 10], "regions": [[0, 0, 900, 10]]},
                  {"board": [0, 0, 10, 10], "regions": "all"}):
        assert check_layout(wrong, size) is None
