"""Unread cells, changed geometry and model proposals must never become silent legal inputs."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
from io import BytesIO
import json
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from gamelab import catalog, geometry, perception, profiles
from gamelab.contracts import GoalSpec, NeedObservation, Observation, StateEstimate

FIXTURES = Path(__file__).parent / "fixtures/game"
CASES = json.loads((FIXTURES / "screens.json").read_text())["cases"]


def observation():
    return Observation("capture-1", 1, "tab-1", 1, 10.0, "surface-clock", input_ref="input-1")


def image_bytes(name):
    return (FIXTURES / "screens" / name).read_bytes()


def measured(profile_id):
    """The reference profile as measured on its synthetic playing screen, the layout all its screens share."""
    family = profile_id.removeprefix("reference_")
    return geometry.measure(image_bytes(f"{family}_playing.png"), profiles.load(profile_id))


def altered(png, edit):
    with Image.open(BytesIO(png)) as original:
        result = edit(original)
        output = BytesIO()
        result.save(output, format="PNG")
        return output.getvalue()


def cover(png, rect, rgb):
    def draw(image):
        image = image.copy()
        ImageDraw.Draw(image).rectangle(rect, fill=rgb)
        return image
    return altered(png, draw)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_saved_screens_match_known_and_unknown_states(case):
    """A stored screen must reproduce its state without turning an unread tray into a used slot."""
    profile = measured(case["profile"])
    result = perception.estimate(observation(), image_bytes(case["file"]), profile)
    assert isinstance(result, StateEstimate)
    assert result.payload == case["state"]
    assert result.geometry_ref == profile.geometry_ref
    assert result.evidence == observation().id
    assert result.observation_ids == (observation().id,)
    family = catalog.family(profile.family_id).bind(profile.data)
    if "unknown" in case["name"]:
        assert result.unknown_fields
        assert isinstance(family.propose(result, GoalSpec("play_until_end"), family.baseline_policy, 50), NeedObservation)
    else:
        assert not result.unknown_fields


def scrolled(image):
    """The same screen with the page scrolled half a cell up, its window unchanged."""
    moved = Image.new("RGB", image.size, (12, 18, 28))
    moved.paste(image, (0, -40))
    return moved


@pytest.mark.parametrize("failure", ["resize", "moved", "profile", "unmeasured", "malformed", "transparent"])
def test_bad_screen_cannot_borrow_valid_profile_geometry(failure):
    """A profile match is required for this capture, not remembered from a previous screen; a profile
    nobody measured on a screen reads none. A board that moved in a window of the same size is no longer
    framed where it was measured, so its old cells are not read off whatever lies there now."""
    png = image_bytes("slide_playing.png")
    profile = measured("reference_slide")
    if failure == "resize":
        png = altered(png, lambda image: image.resize((800, 1120)))
    elif failure == "moved":
        png = altered(png, scrolled)
    elif failure == "profile":
        profile = measured("reference_block")
    elif failure == "unmeasured":
        profile = profiles.load("reference_slide")
    elif failure == "malformed":
        png = png[:40]
    else:
        def transparent(image):
            image = image.convert("RGBA")
            image.putalpha(128)
            return image
        png = altered(png, transparent)
    assert isinstance(perception.estimate(observation(), png, profile), NeedObservation)


def test_a_foreign_mark_on_one_sample_leaves_the_cell_unknown():
    """An overlay names no registered colour: the cell is unknown, never the value of its other samples."""
    png = cover(image_bytes("slide_playing.png"), (57, 97, 57, 97), (255, 0, 255))
    result = perception.estimate(observation(), png, measured("reference_slide"))
    assert result.payload["grid"][0][0] is None
    assert "grid[0][0]" in result.unknown_fields


def proposal(png, profile, state):
    return {"observation_id": observation().id, "image_sha256": hashlib.sha256(png).hexdigest(),
            "geometry_ref": profile.geometry_ref, "state": deepcopy(state)}


def test_correction_fills_only_unknown_cells_of_the_exact_capture():
    """A correction preserves code-reader evidence and does not change the input observation."""
    png = image_bytes("slide_unknown.png")
    profile = measured("reference_slide")
    before = perception.estimate(observation(), png, profile)
    correction = proposal(png, profile, before.payload)
    correction["state"]["grid"][0][1] = 2
    result = perception.estimate(observation(), png, profile, correction=correction)
    assert result.payload["grid"][0][1] == 2 and not result.unknown_fields
    assert before.payload["grid"][0][1] is None
    assert result.geometry_ref == before.geometry_ref
    correction["state"]["grid"][0][1] = 1024
    assert result.payload["grid"][0][1] == 2


@pytest.mark.parametrize("failure", ["capture", "image", "geometry", "known_cell", "scene", "score", "boolean", "invalid_tile", "extra"])
def test_corrections_cannot_override_code_or_stale_evidence(failure):
    """VLM confidence or a different capture cannot overrule known cells, scene or geometry."""
    png = image_bytes("slide_unknown.png")
    profile = measured("reference_slide")
    before = perception.estimate(observation(), png, profile)
    correction = proposal(png, profile, before.payload)
    if failure in {"capture", "image", "geometry"}:
        correction[{"capture": "observation_id", "image": "image_sha256", "geometry": "geometry_ref"}[failure]] = "other"
    elif failure == "known_cell":
        correction["state"]["grid"][0][0] = 4
    elif failure == "scene":
        correction["state"]["scene"] = "paused"           # no scene of the family
    elif failure == "score":
        correction["state"]["score"] = 100
    elif failure == "extra":
        correction["confidence"] = 1.0
    else:
        correction["state"]["grid"][0][1] = True if failure == "boolean" else 3
    assert perception.estimate(observation(), png, profile, correction=correction) == NeedObservation("correction_conflict")


@pytest.mark.parametrize(("file", "scene", "accepted"), [
    ("slide_unknown.png", "over", True),
    ("block_unknown.png", "over", True),
    ("slide_playing.png", "over", False),
    ("block_unknown.png", "won", False),
])
def test_a_look_may_end_play_only_where_the_code_left_something_unread(file, scene, accepted):
    """No pixel marks an end screen, so a model's look may say a partly unread screen is one of the
    family's scenes that stop play (a banner over the tray), keeping every known cell and leaving what it
    covers unread; it can never say a screen the code read whole has ended, nor name a scene the family
    lacks (a block game cannot be won)."""
    profile = measured(f"reference_{file.split('_')[0]}")
    png = image_bytes(file)
    before = perception.estimate(observation(), png, profile)
    correction = proposal(png, profile, before.payload)
    correction["state"]["scene"] = scene
    result = perception.estimate(observation(), png, profile, correction=correction)
    if not accepted:
        assert result == NeedObservation("correction_conflict")
        return
    assert result.payload == {**before.payload, "scene": scene} and not result.unknown_fields
    family = catalog.family(profile.family_id).bind(profile.data)
    assert family.judge(result, GoalSpec("play_until_end"))[1]
    assert isinstance(family.propose(result, GoalSpec("play_until_end"), family.baseline_policy, 50), NeedObservation)


@pytest.mark.parametrize("piece", [None, [[0, 0], [0, 0]], [[-1, 0]], [[0, 3]], [[True, 0]], [[1, 1]]])
def test_unread_tray_cannot_become_consumed_or_an_invalid_piece(piece):
    """A correction cannot silently discard an unread tray piece or propose duplicate/outside cells."""
    png = image_bytes("block_unknown.png")
    profile = measured("reference_block")
    before = perception.estimate(observation(), png, profile)
    correction = proposal(png, profile, before.payload)
    correction["state"]["tray"][0] = piece
    assert perception.estimate(observation(), png, profile, correction=correction) == NeedObservation("correction_conflict")


def test_known_consumed_tray_is_not_replaced_by_a_correction():
    """A known empty slot is evidence, unlike an unknown board cell."""
    png = image_bytes("block_unknown.png")
    profile = measured("reference_block")
    before = perception.estimate(observation(), png, profile)
    correction = proposal(png, profile, before.payload)
    correction["state"]["tray"][1] = [[0, 0]]
    assert perception.estimate(observation(), png, profile, correction=correction) == NeedObservation("correction_conflict")


def test_correction_can_confirm_an_unknown_piece_without_changing_known_slots():
    """An evidence-bound piece correction is distinct from accepting an invented consumed slot."""
    png = image_bytes("block_unknown.png")
    profile = measured("reference_block")
    before = perception.estimate(observation(), png, profile)
    correction = proposal(png, profile, before.payload)
    correction["state"]["tray"][0] = [[0, 0], [1, 0], [1, 1]]
    correction["state"]["board"][1][1] = 0
    result = perception.estimate(observation(), png, profile, correction=correction)
    assert not result.unknown_fields and result.payload["tray"][1] is None
    correction["state"]["tray"][2] = [[0, 0], [0, True]]
    assert perception.estimate(observation(), png, profile, correction=correction) == NeedObservation("correction_conflict")


def test_code_knowledge_is_not_overridden_by_observation_public_facts():
    """Untrusted page facts cannot skip image reading or make a valid board appear empty."""
    seen = replace(observation(), public_facts={"state": {"grid": [[0] * 4 for row in range(4)]}})
    result = perception.estimate(seen, image_bytes("slide_playing.png"), measured("reference_slide"))
    assert result.payload["grid"][0][0] == 2


def test_profiles_resolve_from_installed_package_resources(tmp_path, monkeypatch):
    """Packaged schema and profiles, not a checkout-only path, must be sufficient for perception."""
    import shutil
    destination = tmp_path / "game/_resources/blueprint"
    shutil.copytree(catalog.FRAGMENT, destination)
    monkeypatch.setattr(catalog, "FRAGMENT", destination)
    profile = measured("reference_slide")
    assert isinstance(perception.estimate(observation(), image_bytes("slide_playing.png"), profile), StateEstimate)


@pytest.mark.parametrize("failure", ["outside", "zero", "overlap", "rules", "unknown_rule", "colour", "boolean", "tiny",
                                     "band"])
def test_invalid_profiles_are_rejected_before_reading(failure):
    """Bad profile data cannot coerce dimensions, invent rules or let ambiguous colours mean empty, and a
    measured board cannot lay the rest out off the screen, over each other or too small to sample."""
    data = profiles.load("reference_block").data
    board = [40, 80, 320, 320]
    if failure == "outside":
        data["tray"][0]["at"][0] = -1
    elif failure == "zero":
        data["tray"][0]["at"][2] = 0
    elif failure == "overlap":
        data["tray"][1]["at"] = data["tray"][0]["at"]
    elif failure == "rules":
        data["rules"]["rows"] = 7
    elif failure == "unknown_rule":
        data["rules"]["gravity"] = True
    elif failure == "colour":
        data["board"]["palette"][1]["rgb"] = data["board"]["palette"][0]["rgb"]
    elif failure == "boolean":
        data["tray"][0]["at"][0] = True
    elif failure == "band":
        # a grid tray is read in place: a band to find pieces in belongs to pieces only
        data["tray_band"] = [0, 1.1, 1, 0.3]
    else:
        board = [40, 80, 64, 64]
    with pytest.raises(ValueError):
        profiles.measure(profiles.validate(data), [400, 560], board)


def test_profile_identity_cannot_change_after_measuring():
    """Changing a profile revision or its measured layout invalidates the previous binding."""
    profile = measured("reference_slide")
    revised = deepcopy(profile.data)
    revised["revision"] += 1
    changed = profiles.measure(profiles.validate(revised), [400, 560], [40, 80, 320, 320])
    assert changed.geometry_ref != profile.geometry_ref
    assert perception.estimate(observation(), image_bytes("slide_playing.png"),
                               replace(profile, data=revised)) == NeedObservation("profile_changed")
    moved = deepcopy(profile.layout)
    moved["board"]["rect"][0] += 1
    assert perception.estimate(observation(), image_bytes("slide_playing.png"),
                               replace(profile, layout=moved)) == NeedObservation("profile_changed")
    with pytest.raises(ValueError, match="unknown_profile"):
        profiles.load("../families/slide_merge")


DOM_GRID = [[2, 0, 0, 0], [0, 4, 0, 0], [0, 0, 8, 0], [0, 0, 0, 2048]]


def page_profile():
    return geometry.measure((FIXTURES / "sites/ovolve_initial.png").read_bytes(), profiles.load("ovolve_2048"))


def board_png(grid, profile):
    """The board region a page-reading observation pictures, its cells painted in their palette colours."""
    board = profile.layout["board"]
    _, _, width, height = board["rect"]
    colours = {entry["value"]: tuple(entry["rgb"]) for entry in board["palette"]}
    image = Image.new("RGB", (width, height), colours[0])
    draw = ImageDraw.Draw(image)
    for row, cells in enumerate(grid):
        for col, value in enumerate(cells):
            draw.rectangle((col * width / 4, row * height / 4, (col + 1) * width / 4 - 1, (row + 1) * height / 4 - 1),
                           fill=colours[value])
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def tile(value, col, row, *extra):
    return {"classes": ["tile", f"tile-{value}", f"tile-position-{col}-{row}", *extra], "text": ""}


def page(grid=DOM_GRID, *, tiles=None, scene=("game-message",), score="1234"):
    items = tiles if tiles is not None else [tile(value, col + 1, row + 1) for row, cells in enumerate(grid)
                                             for col, value in enumerate(cells) if value]
    return {"scene": {"count": 1, "truncated": False, "items": [{"classes": list(scene), "text": ""}]},
            "score": {"count": 1, "truncated": False, "items": [{"classes": ["score-container"], "text": score}]},
            "tiles": {"count": len(items), "truncated": False, "items": items}}


def merged_at_corner():
    """The game keeps a move's two merged tiles under the result until the next move."""
    rest = [tile(v, c + 1, r + 1) for r, row in enumerate(DOM_GRID) for c, v in enumerate(row) if v and (r, c) != (3, 3)]
    return rest + [tile(1024, 4, 4), tile(1024, 4, 4), tile(2048, 4, 4, "tile-merged")]


@pytest.mark.parametrize(("name", "elements", "pixels", "expected"), [
    ("read", page(), DOM_GRID, DOM_GRID),
    ("merge kept in the page", page(tiles=merged_at_corner()), DOM_GRID, DOM_GRID),
    ("two tiles claim a cell", page(tiles=page()["tiles"]["items"] + [tile(4, 1, 1)]), DOM_GRID, (0, 0)),
    ("pixels show another tile", page(), [[4, 0, 0, 0], *DOM_GRID[1:]], (0, 0)),
    ("not a tile value", page(tiles=[tile(3, 1, 1)]), DOM_GRID, "elements_unreadable"),
    ("a cell off the board", page(tiles=[tile(2, 5, 1)]), DOM_GRID, "elements_unreadable"),
    ("a list cut short", {**page(), "tiles": {**page()["tiles"], "truncated": True}}, DOM_GRID, "elements_unreadable"),
    ("two scenes at once", page(scene=("game-message", "game-won", "game-over")), DOM_GRID, "scene_unknown"),
    ("a runner that ignored the read", {"scene": page()["scene"]}, DOM_GRID, "elements_unread"),
])
def test_page_elements_are_checked_like_pixels(name, elements, pixels, expected):
    """A page says what it likes: its tiles must name one value and cell each, a cell several claim is
    known only as the game's declared merge, and the board's pixels in the same observation must not
    show another value. Anything else is unknown, never an empty cell or a guess."""
    profile = page_profile()
    result = perception.estimate_dom(observation(), elements, board_png(pixels, profile), profile)
    if isinstance(expected, str):
        assert isinstance(result, NeedObservation) and result.reason == expected, result
    elif isinstance(expected, tuple):
        assert result.payload["grid"][expected[0]][expected[1]] is None and result.unknown_fields
    else:
        assert result.payload == {"grid": expected, "score": 1234, "scene": "playing"} and not result.unknown_fields


def test_a_page_reading_profile_names_selectors_only():
    """What a profile asks the page for is checked selectors and bounds; a scene it ends on is the game's."""
    profile = page_profile()
    scope = perception.dom_scope(profile)
    assert [query["name"] for query in scope["elements"]] == ["scene", "score", "tiles"]
    left, top, width, height = profile.layout["board"]["rect"]
    assert scope["region"] == [left, top, left + width, top + height]
    # before the board is measured the page is read with the whole screen, to measure it on
    assert "region" not in perception.dom_scope(profiles.load("ovolve_2048"))
    over = perception.estimate_dom(observation(), page(scene=("game-message", "game-over")),
                                   board_png(DOM_GRID, profile), profile)
    assert over.payload["scene"] == "over"
    bad = deepcopy(profile.data)
    bad["reading"]["tiles"]["position"] = "tile-position-([0-9]+)"
    with pytest.raises(ValueError, match="invalid_dom_pattern"):
        profiles.validate(bad)
    block = deepcopy(profiles.load("blockblast_org").data)
    block["reading"] = profile.data["reading"]
    with pytest.raises(ValueError, match="dom_reading_unsupported"):
        profiles.validate(block)
