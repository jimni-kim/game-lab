"""Registered site captures have independent answers; unread images never become legal plans.

Each capture is read with the board measured on a capture of the same window (`measured_on`), as a
session reads its screens with the geometry it measured at the start."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from gamelab import catalog, geometry, perception, profiles
from gamelab.contracts import ActionPlan, GoalSpec, GoalStatus, NeedObservation, Observation, StateEstimate

SITES = Path(__file__).parent / "fixtures/game/sites"
CASES = [{"measured_on": manifest["measured_on"], **case} for path in sorted(SITES.glob("*.json"))
         for manifest in [json.loads(path.read_text())] for case in manifest["cases"]]


def measured(profile_id, file):
    result = geometry.measure((SITES / file).read_bytes(), profiles.load(profile_id))
    assert isinstance(result, profiles.Profile), result
    return result


def session(file):
    """The profile as measured at the start of the session `file` was captured in."""
    case = next(case for case in CASES if case["file"] == file)
    return measured(case["profile"], case["measured_on"])


def observation():
    return Observation("site-capture", 1, "tab", 1, 0, "surface-clock")


def altered(name, edit):
    with Image.open(SITES / name) as image:
        image = image.convert("RGB")
        image = edit(image) or image
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["file"])
def test_registered_screens_agree_with_independent_answers_or_refuse_planning(case):
    """Website screenshots, including tutorial/held input/terminal screens, cannot invent playable cells.
    No pixel marks an end or win screen: the game's own message leaves what it covers unread, or moved
    the board off its measured place, and neither is played."""
    profile = measured(case["profile"], case["measured_on"])
    result = perception.estimate(observation(), (SITES / case["file"]).read_bytes(), profile)
    if case.get("unreadable") or case["state"]["scene"] != "playing":
        assert isinstance(result, NeedObservation) or result.unknown_fields
        if isinstance(result, StateEstimate):
            family = catalog.family(profile.family_id).bind(profile.data)
            assert isinstance(family.propose(result, GoalSpec("play_until_end"), family.baseline_policy, 50),
                              NeedObservation)
    else:
        assert isinstance(result, StateEstimate)
        assert result.payload == case["state"] and not result.unknown_fields
        family = catalog.family(profile.family_id).bind(profile.data)
        if case.get("terminal"):
            assert family.judge(result, GoalSpec("play_until_end"))[1]
            assert family.model.legal_actions(case["state"], family.rules) == []
            return
        plan = family.propose(result, GoalSpec("play_until_end"), family.baseline_policy, 50)
        assert plan.steps[0] in family.model.legal_actions(case["state"], family.rules)


@pytest.mark.parametrize("case", [case for case in CASES if not case.get("unreadable") and case["state"]["scene"] == "playing"],
                         ids=lambda case: case["file"])
def test_a_playing_screen_measures_its_own_board(case):
    """Code finds the board on any playing screen of a registered site, whatever its window size, and
    the layout it measures there reads the same answer: geometry is measured, never registered."""
    png = (SITES / case["file"]).read_bytes()
    own = geometry.measure(png, profiles.load(case["profile"]))
    assert isinstance(own, profiles.Profile), own
    assert own.layout["board"]["rect"] == measured(case["profile"], case["measured_on"]).layout["board"]["rect"]
    assert perception.estimate(observation(), png, own).payload == case["state"]


@pytest.mark.parametrize("damage", ["hole", "unknown_piece", "stray_pixel", "shifted_tile"])
def test_occluded_or_unregistered_tray_shapes_remain_unknown(damage):
    """A partially readable piece must not shrink, disappear or snap to an invented lattice."""
    def edit(image):
        draw = ImageDraw.Draw(image)
        if damage == "hole":
            draw.rectangle((618, 744, 621, 747), fill=(255, 0, 255))
        elif damage == "unknown_piece":
            draw.rectangle((606, 734, 692, 820), fill=(255, 0, 255))
        elif damage == "stray_pixel":
            draw.point((580, 710), fill=(255, 0, 255))
        else:
            tile = image.crop((606, 734, 635, 763))
            draw.rectangle((606, 734, 634, 762), fill=(57, 85, 148))
            image.paste(tile, (609, 734))
    png = altered("blockblast_initial.png", edit)
    result = perception.estimate(observation(), png, session("blockblast_initial.png"))
    assert result.payload["tray"][1] == []
    assert "tray[1]" in result.unknown_fields
    assert result.payload["tray"][0] and result.payload["tray"][2]


GLOW = (243, 215, 116)          # the site's light around tiles of 128 and above


def cell_box(profile, row, col, fraction=None):
    """A cell's interior, or the 3×3 pixels the reader samples around one of its sample points."""
    board = profile.layout["board"]
    left, top, width, height = board["rect"]
    cell_w, cell_h = width / board["cols"], height / board["rows"]
    if fraction is None:
        return (int(left + (col + 0.12) * cell_w), int(top + (row + 0.12) * cell_h),
                int(left + (col + 0.88) * cell_w), int(top + (row + 0.88) * cell_h))
    x, y = int(left + (col + fraction) * cell_w), int(top + (row + fraction) * cell_h)
    return x - 1, y - 1, x + 1, y + 1


def tinted(box, colour, alpha):
    def edit(image):
        overlay = Image.new("RGB", image.size, colour)
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).rectangle(box, fill=round(alpha * 255))
        return Image.composite(overlay, image, mask)
    return edit


def top_half(profile, row, col):
    left, top, right, bottom = cell_box(profile, row, col)
    return left, top, right, (top + bottom) // 2


@pytest.mark.parametrize(("name", "edit", "cell"), [
    ("halfway between two tiles", lambda p: tinted(cell_box(p, 0, 0, 0.22), (237, 202, 88), 1), (0, 0)),
    ("another tile in one corner", lambda p: tinted(cell_box(p, 1, 2, 0.78), (238, 228, 218), 1), (1, 2)),
    ("a foreign mark", lambda p: tinted(cell_box(p, 1, 2, 0.22), (255, 0, 255), 1), (1, 2)),
    # a 2's near side tinted until it shows a 4's colour, its far side still a 2
    ("a glow that turns half a tile", lambda p: tinted(top_half(p, 2, 0), GLOW, 0.2), (2, 0)),
])
def test_samples_that_split_or_name_nothing_leave_the_cell_unknown(name, edit, cell):
    """Reading the nearest colour by a margin (the tinted capture above) never takes a value some
    samples contradict: a pixel between two tiles, another tile's colour, a foreign mark or a light
    strong enough to turn part of a tile leaves the cell unknown, never a guess."""
    profile = session("ovolve_glow.png")
    case = next(case for case in CASES if case["file"] == "ovolve_glow.png")
    result = perception.estimate(observation(), altered("ovolve_glow.png", edit(profile)), profile)
    grid = deepcopy(case["state"]["grid"])
    grid[cell[0]][cell[1]] = None
    assert result.payload["grid"] == grid and result.unknown_fields


def test_tinted_block_board_cells_keep_their_occupancy():
    """The same rule reads the block board: a light over an empty or a filled cell keeps what it is."""
    profile = session("blockblast_partial.png")
    case = next(case for case in CASES if case["file"] == "blockblast_partial.png")
    filled = next((r, c) for r, row in enumerate(case["state"]["board"]) for c, v in enumerate(row) if v)
    def edit(image):
        for row, col in ((7, 7), filled):
            image = tinted(cell_box(profile, row, col), (255, 255, 255), 0.06)(image)
        return image
    result = perception.estimate(observation(), altered("blockblast_partial.png", edit), profile)
    assert result.payload == case["state"] and not result.unknown_fields


def test_a_piece_colour_no_profile_lists_still_reads():
    """A block profile names its empty and background colours, not the pieces': a piece in a new colour
    reads on the board and in the tray. Regression: an unlisted blue piece stopped live play (2026-11-11)."""
    profile = session("blockblast_partial.png")
    case = next(case for case in CASES if case["file"] == "blockblast_partial.png")
    def recolour(image):
        image.putdata([(20, 200, 180) if pixel == (236, 182, 49) else pixel for pixel in image.getdata()])
    result = perception.estimate(observation(), altered("blockblast_partial.png", recolour), profile)
    assert result.payload == case["state"] and not result.unknown_fields


def test_a_tinted_tray_slot_is_unread_not_consumed():
    """A slot with nothing far from its background but not quite background (a fading banner, a flash)
    is unread, never an empty slot whose piece was used."""
    profile = session("blockblast_partial.png")
    left, top, width, height = profile.layout["tray"][0]["rect"]
    png = altered("blockblast_partial.png", tinted((left, top, left + width - 1, top + height - 1), (255, 255, 255), 0.1))
    result = perception.estimate(observation(), png, profile)
    assert result.payload["tray"][0] == [] and "tray[0]" in result.unknown_fields


@pytest.mark.parametrize("damage", ["board", "geometry"])
def test_changed_site_pixels_cannot_reuse_prior_state(damage):
    """The registered URL/profile is not evidence that a changed or covered screen is playable."""
    def edit(image):
        if damage == "geometry":
            return image.resize((1279, 1000))
        # the board's mark is another tile's colour: a reading that skipped odd samples would miss it
        ImageDraw.Draw(image).rectangle((423, 371, 427, 376), fill=(237, 194, 46))
    profile = session("ovolve_initial.png")
    result = perception.estimate(observation(), altered("ovolve_initial.png", edit), profile)
    assert isinstance(result, NeedObservation) or result.unknown_fields


@pytest.mark.parametrize("damage", ["source", "alias", "piece_on_board", "cell_size"])
def test_registered_profile_cannot_hide_ambiguous_colours_or_invalid_piece_geometry(damage):
    """Malformed configuration must fail before an alternate colour could be read as an empty cell."""
    data = profiles.load("blockblast_org").data
    if damage == "source":
        del data["source"]
    elif damage == "alias":
        # "any other colour" that also names the empty colour would read empty cells as filled
        data["board"]["palette"][1]["alternates"] = [data["board"]["palette"][0]["rgb"]]
    elif damage == "piece_on_board":
        data["board"]["piece"] = deepcopy(data["tray"][0]["piece"])
    else:
        # six such cells could never stand in the band the pieces are read from
        data["tray"][0]["piece"]["cell"] = [1.5, 1.5]
    with pytest.raises(ValueError):
        profiles.measure(profiles.validate(data), [1280, 900], [420, 226, 448, 448])


def test_an_end_banner_over_the_tray_leaves_it_unread_and_the_rules_end_the_game():
    """The "No Space Left" banner spans the tray: no slot is read, none taken for used (a piece the banner
    hides would otherwise vanish), and the board stays readable. The rules then end the game from the move
    before it, as the banner does, when the pieces that move left fit nowhere. The move is made up for
    the test (a one-cell piece just placed, the red 2x3 under the banner left); the screen is real.
    Regression: without the scene sample the banner read as one piece of the middle slot and the other
    two slots as used."""
    case = next(case for case in CASES if case["file"] == "blockblast_no_space.png")
    profile = session("blockblast_no_space.png")
    after = perception.estimate(observation(), (SITES / case["file"]).read_bytes(), profile)
    assert after.payload["board"] == case["state"]["board"] and after.payload["tray"] == [[], [], []]
    family = catalog.family(profile.family_id).bind(profile.data)
    assert isinstance(family.propose(after, GoalSpec("play_until_end"), family.baseline_policy, 50), NeedObservation)
    board = deepcopy(case["state"]["board"])
    board[7][6] = 0
    red = [[0, 0], [0, 1], [0, 2], [1, 0], [1, 1], [1, 2]]
    before = family.estimate(replace(observation(), public_facts={"state": {
        "board": board, "tray": [red, [[0, 0]], None], "score": None, "scene": "playing"}}))
    place = {"kind": "place", "slot": 1, "anchor": [7, 6], "orientation": 0}
    plan = ActionPlan("plan", before.id, before.rule_revision, "test", (place,), 1)
    assert family.ended(before, plan, after)
    # a piece left that fits somewhere: the game goes on, and the unread tray is waited for
    fits = family.estimate(replace(observation(), public_facts={"state": {
        "board": board, "tray": [[[0, 0]], [[0, 0]], None], "score": None, "scene": "playing"}}))
    assert not family.ended(fits, replace(plan, based_on_state=fits.id), after)


@pytest.mark.parametrize("file,goal", [("play2048_over.png", GoalSpec("play_until_end")),
                                       ("play2048_won.png", GoalSpec("reach_value", 2048))])
def test_an_end_screen_that_moves_the_board_is_measured_again_and_judged_by_the_rules(file, goal):
    """The official 2048's end and win screens move the board: it is measured again there, and its
    tiles say what the end message would have, with no mark of that message registered."""
    case = next(case for case in CASES if case["file"] == file)
    profile = measured(case["profile"], file)
    result = perception.estimate(observation(), (SITES / file).read_bytes(), profile)
    assert result.payload == {**case["state"], "scene": "playing"} and not result.unknown_fields
    family = catalog.family(profile.family_id).bind(profile.data)
    reached, terminal = family.judge(result, goal)
    assert terminal if case["state"]["scene"] == "over" else reached is GoalStatus.REACHED


@pytest.mark.parametrize("file,slot_index,shape", [
    ("blockblast_five_cell_tray.png", 1, [[0, col] for col in range(5)]),
    ("blockblast_vertical_tray.png", 2, [[row, 0] for row in range(5)]),
])
def test_five_cell_trays_keep_bevels_and_complete_tile_footprints(file, slot_index, shape):
    """A real five-cell tray has isolated bevel pixels; ignore only specks inside proven tiles."""
    from gamelab.pieces import piece_geometry
    spec = deepcopy(session("blockblast_initial.png").layout["tray"][slot_index])
    spec["rect"] = [0, 0, 150, 160]
    with Image.open(SITES / file) as source:
        image = source.convert("RGB")
    # the crops' background, as the page's gradient showed it over the 160 rows they were cut from
    background = [tuple(round(a + (b - a) * y / 159) for a, b in zip((60, 86, 148), (52, 78, 143))) for y in range(160)]
    assert piece_geometry(image, spec, 4, background)[0] == shape
    image.putpixel((0, 0), (236, 182, 49))
    assert piece_geometry(image, spec, 4, background)[0] == []
