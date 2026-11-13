"""Perception from a screenshot or from the page's elements, never an input grant.

A profile reads a screen only once measured on one (`geometry.py`), and then only while this screen
still has the measured size and the board's frame still surrounds the measured cells. No pixel marks
the scene: a screen read from its pixels is playing until
the rules find no move left, and what an end screen covers simply reads unknown. No model is called
here. The bounded vision reader may propose corrections from the same screenshot: they fill only
unknown cells, and may say that a screen left partly unread is one of the family's scenes that stop
play, never change known cells, rules, geometry or score. Unread geometry is not correctable.
A profile reading the page (`reading.source: dom`) names selectors only; what the page says is
checked like pixels are, and against the board's pixels in the same observation.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import re
from typing import Any, Callable

from . import catalog, pixels, profiles
from .contracts import NeedObservation, Observation, StateEstimate

# a page's score as its own text: digits only, anything else is not a score
SCORE_TEXT = re.compile(r"[0-9]{1,12}")
# what a screen with no readable board may show besides the family's scenes that stop play: the board
# where the code did not find it, something that is not the game over the board, or nothing the model
# can tell (perception.screen)
BOARD, COVERED, UNKNOWN_SCREEN = "board", "covered", "unknown"
# what may cover the board: something to close (a dialog, a notice, an advertisement), a request to agree
# to the site's privacy or cookie terms, or the game's own start screen before play
NOTICE, CONSENT, START = "notice", "consent", "start"
COVERS = (NOTICE, CONSENT, START)
# the largest control a model may point at, as a share of the screen's width and height: a button, not
# a banner that would let a click land anywhere on it
CONTROL_SHARE = (0.5, 0.25)


def reads_dom(profile: profiles.Profile) -> bool:
    return profile.data.get("reading", {}).get("source") == "dom"


def dom_scope(profile: profiles.Profile) -> dict[str, Any]:
    """The browser_shot arguments (kernel.observation_scope) a page-reading profile observes with:
    its elements, and, once measured, a picture of the board alone as the input's evidence (until
    then the whole screen, to measure the board on)."""
    reading = profile.data["reading"]
    elements = [{"name": "scene", "selector": reading["scene"]["selector"], "max": 1},
                {"name": "score", "selector": reading["score"]["selector"], "max": 1},
                *catalog.READERS[profile.family_id].dom_queries(reading)]
    if profile.layout is None:
        return {"elements": elements}
    left, top, width, height = profile.layout["board"]["rect"]
    return {"elements": elements, "region": [left, top, left + width, top + height]}


def _checked(profile: profiles.Profile) -> profiles.Profile | NeedObservation:
    try:
        checked = profiles.check(profile)
    except ValueError:
        return NeedObservation("profile_changed")
    if checked != profile:
        return NeedObservation("profile_changed")
    if checked.layout is None:
        return NeedObservation("geometry_unmeasured")
    return checked


def estimate_dom(observation: Observation, elements: Any, png: bytes, profile: profiles.Profile
                 ) -> StateEstimate | NeedObservation:
    """The page's elements, with `png` the measured board's picture from the same observation."""
    checked = _checked(profile)
    if isinstance(checked, NeedObservation):
        return checked
    if not reads_dom(checked):
        return NeedObservation("dom_unregistered")
    names = {query["name"] for query in dom_scope(checked)["elements"]}
    if (not isinstance(elements, dict) or set(elements) != names
            or any(not isinstance(found, dict) or not isinstance(found.get("items"), list)
                   or type(found.get("count")) is not int for found in elements.values())):
        return NeedObservation("elements_unread")
    reading = checked.data["reading"]
    scene = _dom_scene(elements["scene"], reading["scene"]["classes"])
    if scene is None:
        return NeedObservation("scene_unknown")
    score = _dom_score(elements["score"])
    _, _, width, height = checked.layout["board"]["rect"]
    try:
        image = pixels.decode(png, [width, height])
    except ValueError as error:
        return NeedObservation(str(error))
    with image:
        payload = catalog.READERS[checked.family_id].read_dom(elements, checked.layout, scene, score, image)
    if payload is None:
        return NeedObservation("elements_unreadable")
    bound = replace(observation, geometry_ref=checked.geometry_ref, public_facts={"state": payload})
    return catalog.family(checked.family_id).bind(checked.data).estimate(bound)


def _dom_scene(found: dict, classes: dict[str, str]) -> str | None:
    """One scene element; the scene its classes name, playing when they name none, unknown for two."""
    if found["count"] != 1 or found["items"][0].get("protected") or found["items"][0].get("cut"):
        return None
    named = {classes[name] for name in found["items"][0]["classes"] if name in classes}
    return None if len(named) > 1 else named.pop() if named else "playing"


def _dom_score(found: dict) -> int | None:
    if found["count"] != 1 or not isinstance(text := found["items"][0].get("text"), str):
        return None
    return int(text) if SCORE_TEXT.fullmatch(text) else None


def estimate(observation: Observation, png: bytes, profile: profiles.Profile, *,
             correction: dict[str, Any] | None = None) -> StateEstimate | NeedObservation:
    """The state a measured layout reads on an image of the measured size."""
    checked = _checked(profile)
    if isinstance(checked, NeedObservation):
        return checked
    try:
        image = pixels.decode(png, checked.layout["viewport"])
    except ValueError as error:
        return NeedObservation(str(error))
    with image:
        payload = read(image, checked)
    if isinstance(payload, NeedObservation):
        return payload
    if correction is not None:
        payload = _correct(payload, correction, observation, png, checked)
        if payload is None:
            return NeedObservation("correction_conflict")
    bound = replace(observation, geometry_ref=checked.geometry_ref,
                    public_facts={"state": payload})
    return catalog.family(checked.family_id).bind(checked.data).estimate(bound)


def read(image: Any, profile: profiles.Profile) -> dict | NeedObservation:
    """The family's state on a decoded screen of a measured profile's size, while the board is where it
    was measured."""
    if not framed(image, profile.layout):
        return NeedObservation("board_unframed")
    return catalog.READERS[profile.family_id].read(image, profile.layout)


def framed(image: Any, layout: dict) -> bool:
    """Whether the board's frame surrounds its measured cells on every side, as the profile registers
    it (`board.frame`: its colour, the right and bottom sides' own colour for a frame drawn sunk or raised
    (`opposite`), and the cells' inset from its edges). It is sampled halfway across the border beside
    each cell's sample points; most samples of each side must show the frame, since a tile's light may
    tint a few. A board that moved, or one proposed a cell off, shows the page or a cell there instead."""
    board = layout["board"]
    left, top, width, height = board["rect"]
    inset = board["frame"]["inset"]
    # each border's width in pixels, from the frame's whole size around the cells
    across, down = width / (1 - inset[0] - inset[2]), height / (1 - inset[1] - inset[3])
    reach = [max(1, inset[0] * across / 2), max(1, inset[1] * down / 2),
             max(1, inset[2] * across / 2), max(1, inset[3] * down / 2)]
    cell_w, cell_h = width / board["cols"], height / board["rows"]
    rows = [top + (row + fraction) * cell_h for row in range(board["rows"]) for fraction in pixels.SAMPLE_FRACTIONS]
    cols = [left + (col + fraction) * cell_w for col in range(board["cols"]) for fraction in pixels.SAMPLE_FRACTIONS]
    sides = [[(left - reach[0], y) for y in rows], [(x, top - reach[1]) for x in cols],
             [(left + width - 1 + reach[2], y) for y in rows], [(x, top + height - 1 + reach[3]) for x in cols]]
    near_side = board["frame"]["rgb"]
    far_side = board["frame"].get("opposite", near_side)
    cells = [rgb for entry in board["palette"] if "rgb" in entry for rgb in [entry["rgb"], *entry.get("alternates", [])]]
    for side, frame in zip(sides, (near_side, near_side, far_side, far_side)):
        points = [(int(x), int(y)) for x, y in side]
        if any(not (0 <= x < image.width and 0 <= y < image.height) for x, y in points):
            return False
        near = sum(_frame_pixel(image.getpixel(point), frame, cells) for point in points)
        if 2 * near <= len(points):
            return False
    return True


def _frame_pixel(pixel: tuple[int, ...], frame: list[int], cells: list[list[int]]) -> bool:
    """Whether a pixel shows the frame: its colour, or that colour tinted by a tile's light (within
    MAX_REACH) yet clearly nearer the frame than any cell's colour. A board that moved puts the page or a
    cell where the frame was, which is far from the frame or nearer a cell."""
    distance = max(abs(a - b) for a, b in zip(pixel, frame))
    if distance <= pixels.FRAME_REACH:
        return True
    return distance <= pixels.MAX_REACH and all(
        max(abs(a - b) for a, b in zip(pixel, rgb)) - distance >= pixels.DECISION_MARGIN for rgb in cells)


def colours(image: Any, profile: profiles.Profile, cells: list[dict]) -> list[dict] | None:
    """The palette entries a measured board's cells teach, given the value each one holds
    (`[{"cell": [r, c], "value": v}]`, from the rules): each sample point's own colour, a gradient
    across its few pixels but not an edge or a numeral, the same for every cell of one value. None
    when any cell is not such a tile or a colour lies too near a registered one to be told from it by a
    clear margin. An `exact` board tells a cell by all of its faces (`pixels.face_value`): there a value is
    learned when its faces and every listed value's do not match each other."""
    layout = profile.layout
    palette = layout["board"]["palette"]
    exact = layout["board"].get("exact", False)
    learned: dict[int, list[tuple]] = {}
    for item in cells:
        found = pixels.faces(image, layout["board"], *item["cell"], layout["tolerance"])
        if found is None:
            return None
        listed = [[entry["rgb"], *entry.get("alternates", [])] for entry in palette if "rgb" in entry]
        if exact:
            if any(pixels.covers(colours, found) or pixels.covers(found, colours) for colours in listed):
                return None
        # far enough from every registered colour that each reads as its own value by a clear margin
        elif any(min(max(abs(a - b) for a, b in zip(colour, rgb)) for rgb in sum(listed, []))
                 < 2 * pixels.DECISION_MARGIN for colour in found):
            return None
        if learned.setdefault(item["value"], found) != found:
            return None
    return [{"value": value, "rgb": list(found[0]), **({"alternates": [list(c) for c in found[1:]]} if found[1:] else {})}
            for value, found in learned.items()]


def screens(family_id: str) -> list[str]:
    """What a model may say a screen with no readable board shows: never that play goes on, only where
    a board is for the code to measure and read."""
    return [scene["id"] for scene in catalog.descriptor(family_id)["scenes"] if scene["id"] != "playing"] + [
        BOARD, COVERED, UNKNOWN_SCREEN]


def screen(answer: Any, observation: Observation, png: bytes, family_id: str) -> dict[str, Any] | None:
    """A model's answer about this very capture (`vision.CorrectionReader.classify`): `screen`, one of
    `screens`; for a board `board`, the box of its outer edge inside the screen (only where the code
    looks, `geometry.locate`); for a cover, what it is (`cover`, one of COVERS) and `control`, the box of
    the one button to press on it (a notice's close, a consent request's refusal, a start screen's start),
    and for a consent request `accept`, the box of its agreeing button, pressed only once the person allows
    it; for a scene whose message shows a button the family presses (`button_on`) that button's box; each
    box inside the screen and the size of a button; None for anything else."""
    if not isinstance(answer, dict) or set(answer) != {"observation_id", "image_sha256", "screen", "board", "control",
                                                       "cover", "accept"}:
        return None
    if (answer["observation_id"], answer["image_sha256"]) != (observation.id, hashlib.sha256(png).hexdigest()):
        return None
    if not isinstance(answer["screen"], str) or answer["screen"] not in screens(family_id):
        return None
    board, control, cover, accept = answer["board"], answer["control"], answer["cover"], answer["accept"]
    pressed = {scene for scene in catalog.family(family_id).buttons.values()}
    if (board is not None) != (answer["screen"] == BOARD) or (
            control is not None and answer["screen"] != COVERED and answer["screen"] not in pressed):
        return None
    if (cover is not None) != (answer["screen"] == COVERED) or (cover is not None and cover not in COVERS) \
            or (accept is not None and cover != CONSENT):
        return None
    try:
        with pixels.decode(png, None) as image:
            width, height = image.size
    except ValueError:
        return None
    for box, button in ((board, False), (control, True), (accept, True)):
        if box is None:
            continue
        if not isinstance(box, list) or len(box) != 4 or any(type(value) is not int for value in box):
            return None
        left, top, wide, high = box
        if left < 0 or top < 0 or wide <= 0 or high <= 0 or left + wide > width or top + high > height:
            return None
        if button and (wide > width * CONTROL_SHARE[0] or high > height * CONTROL_SHARE[1]):
            return None
    return {"screen": answer["screen"], "board": board and list(board), "control": control and list(control),
            "cover": cover, "accept": accept and list(accept)}


def _correct(payload: dict, proposal: dict, observation: Observation, png: bytes,
             profile: profiles.Profile) -> dict | None:
    if not isinstance(proposal, dict) or set(proposal) != {"observation_id", "image_sha256", "geometry_ref", "state"}:
        return None
    if (proposal["observation_id"], proposal["image_sha256"], proposal["geometry_ref"]) != (
            observation.id, hashlib.sha256(png).hexdigest(), profile.geometry_ref):
        return None
    revised = proposal["state"]
    if not isinstance(revised, dict) or set(revised) != set(payload):
        return None
    if revised["score"] is not None:
        return None
    # a look may say that a screen the code left partly unread is the game's own message stopping play
    # (one of the family's scenes): it can stop play, never start it again or say a readable screen ended
    scenes = {scene["id"] for scene in catalog.descriptor(profile.family_id)["scenes"]}
    if revised["scene"] != payload["scene"] and (
            payload["scene"] != "playing" or not isinstance(revised["scene"], str) or revised["scene"] not in scenes
            or not catalog.family(profile.family_id).model.unknown_fields(payload)):
        return None
    reader = catalog.READERS[profile.family_id]
    if not _grid_correction(payload[reader.BOARD], revised[reader.BOARD], reader.tile):
        return None
    if not reader.check_correction(payload, revised, profile.data):
        return None
    return deepcopy(revised)


def _grid_correction(before: list, after: Any, tile: Callable[[int], bool]) -> bool:
    if not isinstance(after, list) or len(before) != len(after):
        return False
    for old_row, new_row in zip(before, after):
        if not isinstance(new_row, list) or len(old_row) != len(new_row):
            return False
        for old, new in zip(old_row, new_row):
            if new is not None:
                if type(new) is not int or new < 0 or not tile(new):
                    return False
            if old is not None and old != new:
                return False
    return True

