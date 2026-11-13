"""Regenerate synthetic PNG fixtures, never captures from an actual website or game."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "tools"))
from source_roots import roots  # noqa: E402

sys.path[:0] = [str(root) for root in roots()]
from game import profiles  # noqa: E402

BACKGROUND = (12, 18, 28)
UNKNOWN = (255, 0, 255)
CELL_GUTTER = 2
# where the synthetic screens put the board: the profiles say only where the rest goes relative to it
VIEWPORT = [400, 560]
BOARD = [40, 80, 320, 320]
# the board's frame around its cells, in the profile's frame colour
FRAME_BORDER = 8


def rectangle(draw, rect, colour):
    left, top, width, height = rect
    draw.rectangle((left, top, left + width - 1, top + height - 1), fill=tuple(colour))


def grid(draw, spec, values):
    left, top, width, height = spec["rect"]
    palette = {entry["value"]: entry["rgb"] for entry in spec["palette"]}
    for row in range(spec["rows"]):
        for col in range(spec["cols"]):
            bounds = (int(left + col * width / spec["cols"]) + CELL_GUTTER,
                      int(top + row * height / spec["rows"]) + CELL_GUTTER,
                      int(left + (col + 1) * width / spec["cols"]) - CELL_GUTTER,
                      int(top + (row + 1) * height / spec["rows"]) - CELL_GUTTER)
            draw.rectangle(bounds, fill=tuple(palette.get(values[row][col], UNKNOWN)))


def render(profile, state):
    image = Image.new("RGB", tuple(profile["viewport"]), BACKGROUND)
    draw = ImageDraw.Draw(image)
    left, top, width, height = profile["board"]["rect"]
    rectangle(draw, [left - FRAME_BORDER, top - FRAME_BORDER, width + 2 * FRAME_BORDER, height + 2 * FRAME_BORDER],
              profile["board"]["frame"]["rgb"])
    grid(draw, profile["board"], state.get("grid", state.get("board")))
    for index, spec in enumerate(profile["tray"]):
        piece = state["tray"][index]
        values = [[0 for col in range(spec["cols"])] for row in range(spec["rows"])]
        if piece == []:
            values[0][0] = None
        elif piece:
            for row, col in piece:
                values[row][col] = 1
        grid(draw, spec, values)
    return image


def main():
    slide = {"grid": [[2, 2, 0, 0], [4, 8, 16, 32], [64, 128, 256, 512], [1024, 0, 0, 0]],
             "scene": "playing", "score": None}
    block = {"board": [[0] * 8 for row in range(8)], "tray": [[[0, 0], [1, 0], [1, 1]], None, [[0, 0], [0, 1]]],
             "scene": "playing", "score": None}
    block["board"][0] = [1, 1, 1, 1, 1, 1, 0, 0]
    block["board"][5][3] = 1
    cases = []
    for family, initial in (("slide", slide), ("block", block)):
        profile_id = f"reference_{family}"
        profile = profiles.measure(profiles.load(profile_id), VIEWPORT, BOARD).layout
        # "over" is a board with no move left: no end message is drawn, the rules tell the end
        variants = {"playing": initial, "unknown": deepcopy(initial), "over": deepcopy(initial)}
        if family == "slide":
            variants["unknown"]["grid"][0][1] = None
            variants["over"]["grid"] = [[2 if (row + col) % 2 else 4 for col in range(4)] for row in range(4)]
        else:
            variants["unknown"]["board"][1][1] = None
            variants["unknown"]["tray"][0] = []
            variants["over"]["board"] = [[1] * 8 for row in range(8)]
        for name, state in variants.items():
            filename = f"{family}_{name}.png"
            with render(profile, state) as image:
                image.save(HERE / "screens" / filename)
            cases.append({"name": f"{family}_{name}", "profile": profile_id,
                          "file": filename, "state": state})
    (HERE / "screens.json").write_text(json.dumps({"source": "synthetic", "cases": cases}, indent=2) + "\n")


if __name__ == "__main__":
    (HERE / "screens").mkdir(exist_ok=True)
    main()
