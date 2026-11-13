"""A game no profile registers is set up from a model's look only where the screen agrees with every part of
it (plan §4.5): a wrong family, size, cell value or box leaves no profile, so nothing is read or played."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from gamelab import discovery, geometry, perception, profiles
from gamelab.contracts import NeedObservation, Observation

SITES = Path(__file__).parent / "fixtures/game/sites"
SEEN = Observation("capture", 1, "tab", 2, 10, "surface-clock")
URL = "https://unregistered.example/"
STATES = {case["file"]: case.get("state") for name in ("play2048", "ovolve")
          for case in json.loads((SITES / f"{name}.json").read_text())["cases"]}
REGISTERED = {"play2048": "play2048_co", "ovolve": "ovolve_2048"}


def png(name):
    return (SITES / name).read_bytes()


def look(file, *, shift=(0.0, 0.0), scale=(1.0, 1.0), **changes):
    """A model's answer about `file`: the family, a box about the board's outer edge drawn by eye (here
    from the registered profile's measurement, off by `shift` and `scale` of its size), the grid's size
    and the cells the fixture's independent answer holds."""
    measured = geometry.measure(png(file), profiles.load(REGISTERED[file.split("_")[0]]))
    left, top, width, height = measured.layout["board"]["rect"]
    inset = measured.data["board"]["frame"]["inset"]
    across, down = width / (1 - inset[0] - inset[2]), height / (1 - inset[1] - inset[3])
    box = [int(left - inset[0] * across + shift[0] * across), int(top - inset[1] * down + shift[1] * down),
           int(across * scale[0]), int(down * scale[1])]
    answer = {"family": "slide_merge", "board": box, "grid": {"rows": 4, "cols": 4},
              "cells": deepcopy(STATES[file]["grid"])}
    return {**answer, **changes}


@pytest.mark.parametrize("file", ["play2048_sized_1280x1000.png", "play2048_initial.png", "ovolve_initial.png",
                                  "ovolve_progress.png"])
@pytest.mark.parametrize(("shift", "scale"), [((0, 0), (1, 1)), ((0.04, -0.03), (0.97, 1.05))],
                         ids=["on the board", "a little off"])
def test_an_unregistered_board_is_read_as_the_registered_profile_reads_it(file, shift, scale):
    """With no profile of the site, a model's look at it makes one from the screen (frame, size, colours)
    that reads the same state the registered profile and the independent answer do, from pixels alone
    (the page-reading profile's site included)."""
    made = discovery.propose(png(file), look(file, shift=shift, scale=scale), URL)
    assert isinstance(made, profiles.Profile), made
    assert made.id.startswith(profiles.SESSION) and made.data["source"] == {"url": URL}
    assert "reading" not in made.data
    assert perception.estimate(SEEN, png(file), made).payload == STATES[file]


def test_a_session_profile_measures_the_board_again_at_another_window_size():
    """What a session profile keeps is the game's, not the window's: made on one window size, it finds and
    reads the board at another with no new look, as a registered profile does after a resize."""
    made = discovery.propose(png("play2048_sized_1280x1000.png"), look("play2048_sized_1280x1000.png"), URL)
    resized = geometry.measure(png("play2048_sized_1440x1100.png"), made)
    assert isinstance(resized, profiles.Profile), resized
    assert resized.id == made.id and resized.layout["board"]["rect"] != made.layout["board"]["rect"]
    assert perception.estimate(SEEN, png("play2048_sized_1440x1100.png"), resized).payload == STATES[
        "play2048_sized_1440x1100.png"]


def changed_cell(row, col, value):
    cells = deepcopy(STATES["play2048_initial.png"]["grid"])
    cells[row][col] = value
    return cells


@pytest.mark.parametrize(("changes", "reason"), [
    ({"family": "block_placement"}, "family_unknown"),
    ({"grid": {"rows": 5, "cols": 5}, "cells": [[0] * 5 for _ in range(5)]}, "board_not_found"),
    ({"grid": {"rows": 2, "cols": 2}, "cells": [[0, 0], [0, 2]]}, "rules_unsupported"),
    ({"cells": changed_cell(0, 3, 4)}, "cells_unconfirmed"),
    ({"cells": changed_cell(1, 1, 2)}, "cells_unconfirmed"),
    ({"cells": changed_cell(1, 1, None)}, "cells_unconfirmed"),
    ({"board": [20, 20, 300, 120]}, "board_not_found"),
    ({"score": 0}, "invalid_answer"),
    ({"cells": [[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 2]]}, "board_full"),
], ids=["a family it cannot set up", "too many cells", "a size the rules do not play",
        "one of two equal tiles called another value", "an empty cell called a tile", "a cell left unread",
        "a box around something else", "an answer of another shape", "no empty cell to learn"])
def test_a_wrong_look_makes_no_profile(changes, reason):
    """Every part of a model's answer is checked on the screen before anything is read or played from it:
    the family and size against what the code plays, the box against the frame's lines, each value against
    the colours of the cells said to hold it, and the profile against the very cells the model read."""
    assert discovery.propose(png("play2048_initial.png"), look("play2048_initial.png", **changes), URL) == \
        NeedObservation(reason)


def test_a_tile_value_not_yet_learned_reads_unknown_never_as_a_learned_one_of_a_near_colour():
    """A session profile learns its tile colours as the values appear; until it has, a new value's colour
    may lie a few units from one it knows (play2048.co's 256 beside its 128). It reads unknown then, so the
    rules can teach it, never as the near value: a board read with a 256 as a 128 planned moves on it.
    Regression: live play stopped after four moves made on a 256 read as 128."""
    from gamelab import pixels
    made = discovery.propose(png("play2048_sized_1280x1000.png"), look("play2048_sized_1280x1000.png"), URL)
    image = pixels.decode(png("play2048_progress.png"), None)
    board = STATES["play2048_progress.png"]["grid"]
    known = [{"cell": [r, c], "value": v} for r, row in enumerate(board) for c, v in enumerate(row) if 4 <= v <= 128]
    learned = profiles.learn(made, perception.colours(image, made, known))
    assert perception.estimate(SEEN, png("play2048_progress.png"), learned).payload["grid"][3][0] is None
    loose = profiles.learn(profiles.measure(profiles.validate({**made.data, "board": {
        **made.data["board"], "exact": False}}), made.layout["viewport"], made.layout["board"]["rect"]),
        learned.data["board"]["palette"][len(made.data["board"]["palette"]):])
    assert perception.estimate(SEEN, png("play2048_progress.png"), loose).payload["grid"][3][0] == 128
    taught = profiles.learn(learned, perception.colours(image, learned, [{"cell": [3, 0], "value": 256}]))
    assert perception.estimate(SEEN, png("play2048_progress.png"), taught).payload["grid"][3][0] == 256


def test_a_frame_tinted_by_glowing_tiles_still_frames_the_board_and_a_moved_board_does_not():
    """A profile made on a fresh board takes the frame's own colour; big tiles along an edge later tint that
    side of the frame past it. The board still counts as where it was measured, while a board a cell off,
    whose frame samples land on cells, does not.
    Regression: every read failed once glowing tiles lined the bottom edge (`board_unframed`)."""
    from gamelab import pixels
    made = discovery.propose(png("play2048_sized_1280x1000.png"), look("play2048_sized_1280x1000.png"), URL)
    with pixels.decode(png("play2048_progress.png"), None) as image:
        assert perception.framed(image, made.layout)
        left, top, width, height = made.layout["board"]["rect"]
        shifted = profiles.measure(made, made.layout["viewport"], [left + width // 4, top, width, height])
        assert not perception.framed(image, shifted.layout)
