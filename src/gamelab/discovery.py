"""A profile for a game no profile registers, made from one look at its screen (plan §4.5).

A model says which known family the screen shows, about where its board is, how many cells it has and
what each one holds. None of that is used as it was said: the family must be one whose games can be set
up so (`registered.DISCOVERY`) and the board's size one its code plays; the code finds the board's frame
around the box from its lines (`geometry.frame`), takes each value's colours from the cells said to hold
it, and the profile this makes is then checked, measured and read like a registered one. It is kept only
when it reads the very cells the model read. A model that read cells consistently wrong (two values
swapped everywhere) is caught by the first moves, whose results then differ from the rules' prediction.
"""
from __future__ import annotations

from typing import Any

from . import catalog, geometry, perception, pixels, profiles
from . import registered
from .contracts import NeedObservation


def propose(png: bytes, answer: Any, url: str) -> profiles.Profile | NeedObservation:
    """The measured session profile `answer` (`{"family", "board", "grid": {"rows", "cols"}, "cells"}`)
    describes on this screen of the page at `url`, or why there is none."""
    if not isinstance(answer, dict) or set(answer) != {"family", "board", "grid", "cells"}:
        return NeedObservation("invalid_answer")
    family_id, box, grid, cells = answer["family"], answer["board"], answer["grid"], answer["cells"]
    if family_id not in registered.DISCOVERY:
        return NeedObservation("family_unknown")
    if (not isinstance(grid, dict) or set(grid) != {"rows", "cols"}
            or any(type(value) is not int or value < 1 for value in grid.values())):
        return NeedObservation("invalid_answer")
    rows, cols = grid["rows"], grid["cols"]
    if (not isinstance(box, list) or len(box) != 4 or any(type(value) is not int for value in box)
            or not isinstance(cells, list) or len(cells) != rows
            or any(not isinstance(values, list) or len(values) != cols for values in cells)):
        return NeedObservation("invalid_answer")
    found = catalog.descriptor(family_id)["discovery"]
    learn = registered.DISCOVERY[family_id]
    rules = learn.rules(rows, cols, found["rules"])
    if rules is None:
        return NeedObservation("rules_unsupported")
    try:
        image = pixels.decode(png, None)
    except ValueError as error:
        return NeedObservation(str(error))
    with image:
        framed = geometry.frame(image, box, rows, cols)
        if framed is None:
            return NeedObservation("board_not_found")
        frame, rect = framed
        palette = learn.palette(image, {"rect": rect, "rows": rows, "cols": cols}, cells, found["tolerance"])
        if isinstance(palette, str):
            return NeedObservation(palette)
        data = {"revision": 1, "family_id": family_id, "fixture_only": False, "controls": found["controls"],
                "settling": found["settling"], "tolerance": found["tolerance"],
                "board": {"frame": frame, "rows": rows, "cols": cols, "palette": palette, "exact": True}, "tray": [],
                "rules": rules}
        try:
            profile = profiles.session(data, url)
        except ValueError as error:
            return NeedObservation(str(error))
        measured = geometry.confirm(image, profile, rect)
        if isinstance(measured, NeedObservation):
            return measured
        payload = perception.read(image, measured)
    if isinstance(payload, NeedObservation):
        return payload
    if payload[registered.READERS[family_id].BOARD] != cells:
        return NeedObservation("cells_unconfirmed")
    return measured
