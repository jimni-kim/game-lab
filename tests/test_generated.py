"""A site adapter whose reading is generated code: run in the sandbox, and refused when it is wrong (adaptive
perception plan §4.3, §6, stage 4). The code here is written by hand as a model would write it."""
import pytest

from gamelab.adapters.generated import ADAPTER_FAILED, GeneratedAdapter, check_layout
from gamelab.contracts import CELLS_UNREAD, NeedObservation, Observation
from fake_screen import PALETTE, Screen

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
    palette = {str(value): list(rgb) for value, rgb in PALETTE.items()}
    text = ADAPTER.format(board=fake.rect, palette=palette)
    for old, new in changed.items():
        assert OLD[old] in text
        text = text.replace(OLD[old], new)
    return text


# the lines a faulty adapter differs in
OLD = {"cell": "cells.append(int(value) if far <= 12 else None)",
       "key": 'return {"kind": "key", "key": KEYS[action["direction"]]}',
       "layout": 'return {"board": BOARD, "size": 4}',
       "read": "    left, top, width, height = layout[\"board\"]\n    size = layout[\"size\"]"}


@pytest.mark.parametrize(("fault", "call", "reason"), [
    ({"cell": "cells.append(3)"}, "read", "unreadable"),
    ({"read": "    return [1, 2, 3]"}, "read", ADAPTER_FAILED),
    ({"cell": "cells.append(None if (row, col) == (0, 0) else (int(value) if far <= 12 else None))"}, "read", CELLS_UNREAD),
    ({"read": "    while True:\n        pass"}, "read", ADAPTER_FAILED),
    ({"read": "    import os\n    os._exit(1)"}, "read", ADAPTER_FAILED),
    ({"read": "    open('/etc/hosts', 'a').write('x')\n" + OLD["read"]}, "read", ADAPTER_FAILED),
    ({"layout": 'return {"board": [0, 0, 99999, 10], "size": 4}'}, "measure", "board_not_found"),
    ({"layout": "return None"}, "measure", "board_not_found"),
    ({"key": 'return {"kind": "key", "key": "F5"}'}, "target", "input_refused"),
    ({"key": 'return {"kind": "tap", "x": 5, "y": 5}'}, "target", "input_refused"),
    ({"key": 'return {"kind": "key", "key": "ArrowLeft", "then": "Enter"}'}, "target", "input_refused"),
], ids=["a state the rules do not allow", "a state that is no state", "a cell it cannot read", "an endless loop",
        "a crash", "a write outside its folder", "a layout off the screen", "no layout",
        "a key no game is played with", "another kind of input than the game's", "more than one input"])
async def test_wrong_generated_code_cannot_play_wrongly(fault, call, reason):
    """Whatever is wrong with the code, it yields no layout, no state or no input: never a state the rules do
    not account for or an input the layout does not. The right code yields each on the same screen."""
    fake = Screen()
    png = await fake.capture()
    seen = Observation("look", 0, "fake", 1, 0.0, "clock")
    right = GeneratedAdapter("slide_merge", source(fake), call_timeout_s=1.0)
    wrong = GeneratedAdapter("slide_merge", source(fake, **fault), call_timeout_s=1.0)
    try:
        assert right.measure(png) is True and wrong.measure(png) in (True, NeedObservation("board_not_found"))
        if call == "measure":
            assert wrong.measure(png) == NeedObservation(reason) and not wrong.measured
            return
        state = right.read(seen, png)
        assert state.payload == fake.board and not state.unknown_fields
        assert right.target({"kind": "slide", "direction": "left"}, state, seen, png).arguments == {"keys": ["ArrowLeft"]}
        read = wrong.read(seen, png)
        if call == "read":
            assert read == NeedObservation(reason), wrong.last_error
            return
        with pytest.raises(ValueError, match=reason):
            wrong.target({"kind": "slide", "direction": "left"}, read, seen, png)
    finally:
        right.close()
        wrong.close()


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
