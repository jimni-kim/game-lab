"""The board is measured on the screen, never registered: a layout the screen does not confirm reads and
plays nothing, and every input follows the board where it was measured."""
from pathlib import Path

import pytest

from gamelab import catalog, geometry, perception, pixels, profiles
from gamelab.contracts import ActionPlan, NeedObservation, Observation
from gamelab.adapters.reference import ReferenceAdapter
from gamelab.execution import map_action
from gamelab.pieces import read_tray

SITES = Path(__file__).parent / "fixtures/game/sites"
SEEN = Observation("capture", 1, "tab", 2, 10, "surface-clock")


def png(name):
    return (SITES / name).read_bytes()


@pytest.mark.parametrize(("profile_id", "file"), [
    ("blockblast_org", "ovolve_initial.png"),
    ("blockblast_org", "play2048_progress.png"),
    ("ovolve_2048", "blockblast_initial.png"),
    ("ovolve_2048", "play2048_progress.png"),
    ("play2048_co", "ovolve_initial.png"),
])
def test_another_games_board_is_refused_before_any_input(profile_id, file):
    """A profile finds no board of its own on another game's screen, even where a colour of that game is
    close to its frame's: nothing is measured, so nothing is read or played."""
    assert geometry.measure(png(file), profiles.load(profile_id)) == NeedObservation("board_not_found")


def test_a_board_half_covered_by_a_dialog_is_not_measured_as_a_smaller_board():
    """A dialog over a board's upper half leaves its lower half framed by a gutter that looks like the
    frame's edge: that part is not measured as the whole board with squashed cells, which would read two
    rows as four and play on them."""
    from io import BytesIO
    from PIL import Image, ImageDraw
    profile, file = profiles.load("ovolve_2048"), "ovolve_progress.png"
    left, top, width, height = geometry.measure(png(file), profile).layout["board"]["rect"]
    image = Image.open(SITES / file).convert("RGB")
    ImageDraw.Draw(image).rectangle((left - 30, top - 30, left + width + 30, top + height // 2), fill=(250, 250, 250))
    out = BytesIO()
    image.save(out, format="PNG")
    assert geometry.measure(out.getvalue(), profile) == NeedObservation("board_not_found")


@pytest.mark.parametrize("board", [[476, 226, 448, 448], [420, 254, 448, 448], [420, 226, 392, 392],
                                   [1000, 226, 448, 448]], ids=["a cell right", "half a cell down", "smaller",
                                                                "off the screen"])
def test_a_proposed_board_is_confirmed_only_by_the_screen(board):
    """Whoever proposes where the board is (code or a model), the layout it implies is checked on
    this very image: a board a cell off, half a cell off, of another size or off the screen is refused."""
    profile = profiles.load("blockblast_org")
    with pixels.decode(png("blockblast_partial.png"), None) as image:
        assert isinstance(geometry.confirm(image, profile, [420, 226, 448, 448]), profiles.Profile)
        assert isinstance(geometry.confirm(image, profile, board), NeedObservation)


def outer(measured):
    """The outer edge of a measured board's frame (left, top, width, height), from the profile's inset."""
    left, top, width, height = measured.layout["board"]["rect"]
    inset = measured.data["board"]["frame"]["inset"]
    across, down = width / (1 - inset[0] - inset[2]), height / (1 - inset[1] - inset[3])
    return left - inset[0] * across, top - inset[1] * down, across, down


@pytest.mark.parametrize(("profile_id", "file"), [("play2048_co", "play2048_sized_1280x1000.png"),
                                                   ("blockblast_org", "blockblast_sized_1100x780.png")])
@pytest.mark.parametrize(("shift", "scale", "found"), [((0, 0), (1, 1), True), ((0.04, -0.03), (0.97, 1.05), True),
                                                       ((-0.06, 0.05), (1.1, 0.93), True), ((0.3, 0.3), (1, 1), False),
                                                       ((0, 0), (0.6, 0.6), False)],
                         ids=["on the board", "a little off", "a little larger", "far off", "around part of it"])
def test_a_board_the_code_misses_is_measured_where_a_model_saw_it(profile_id, file, shift, scale, found):
    """A panel in the frame's colour touching the board (a score box) joins its frame, and the code finds
    no board. A model's box about the board's outer edge, drawn by eye, is where the code looks again: it
    takes the edges from the frame on the image, so a box a little off reads the same board the code
    measures on the clean screen, and a box far off or around part of the board finds nothing to confirm."""
    from io import BytesIO
    from PIL import Image, ImageDraw
    profile = profiles.load(profile_id)
    clean = geometry.measure(png(file), profile)
    expected = perception.estimate(SEEN, png(file), clean).payload
    left, top, across, down = outer(clean)
    image = Image.open(SITES / file).convert("RGB")
    ImageDraw.Draw(image).rectangle((int(left + across / 4), int(top - 60), int(left + across * 3 / 4), int(top) + 2),
                                    fill=tuple(profile.data["board"]["frame"]["rgb"]))
    out = BytesIO()
    image.save(out, format="PNG")
    touched = out.getvalue()
    assert geometry.measure(touched, profile) == NeedObservation("board_not_found")
    box = [int(left + shift[0] * across), int(top + shift[1] * down), int(across * scale[0]), int(down * scale[1])]
    with pixels.decode(touched, None) as decoded:
        located = geometry.locate(decoded, profile, box)
    if found:
        assert isinstance(located, profiles.Profile), located
        assert perception.estimate(SEEN, touched, located).payload == expected
    else:
        assert isinstance(located, NeedObservation)


def test_a_drag_lands_on_the_same_cell_at_every_window_size():
    """The same move in the same game, its window resized between captures, drops the piece on the same
    board cell and grabs it on its own tiles: input coordinates follow the board measured on each screen."""
    profile = profiles.load("blockblast_org")
    landed = []
    for file in ("blockblast_sized_1280x900.png", "blockblast_sized_1100x780.png", "blockblast_sized_1440x1000.png"):
        measured = geometry.measure(png(file), profile)
        state = perception.estimate(SEEN, png(file), measured)
        family = catalog.family(profile.family_id).bind(profile.data)
        action = family.model.legal_actions(state.payload, family.rules)[0]
        mapped = map_action(ActionPlan("plan", state.id, state.rule_revision, "test", (action,), 1),
                            state, SEEN, png(file), ReferenceAdapter.measured_as(measured)).arguments
        left, top, width, height = measured.layout["board"]["rect"]
        landed.append(((mapped["end_x"] - left) / width * 8, (mapped["end_y"] - top) / height * 8))
        with pixels.decode(png(file), None) as image:
            _, centres = read_tray(image, measured.layout)[action["slot"]]
        assert (mapped["start_x"], mapped["start_y"]) in centres
    # in board cells: the same point of the board at every size, to a pixel's rounding
    assert all(abs(a - b) < 0.05 for point in landed[1:] for a, b in zip(point, landed[0]))


def test_a_piece_wider_than_its_slot_reads_and_is_played_from_its_own_tiles():
    """A piece is read from the tray band, however far past its slot it reaches, and grabbed on its tiles.
    Regression: a one-by-six piece spilled past its fixed slot box, the slot read unknown and both Block
    Blast games of 2026-11-11 stopped as unreadable."""
    profile = geometry.measure(png("blockblast_six_cell.png"), profiles.load("blockblast_org"))
    state = perception.estimate(SEEN, png("blockblast_six_cell.png"), profile)
    assert state.payload["tray"][2] == [[0, col] for col in range(6)] and not state.unknown_fields
    family = catalog.family(profile.family_id).bind(profile.data)
    action = next(action for action in family.model.legal_actions(state.payload, family.rules) if action["slot"] == 2)
    mapped = map_action(ActionPlan("plan", state.id, state.rule_revision, "test", (action,), 1),
                        state, SEEN, png("blockblast_six_cell.png"), ReferenceAdapter.measured_as(profile)).arguments
    with pixels.decode(png("blockblast_six_cell.png"), None) as image:
        _, centres = read_tray(image, profile.layout)[2]
    assert (mapped["start_x"], mapped["start_y"]) in centres
    # its tiles reach past both sides of the slot's box
    slot, half = profile.layout["tray"][2]["rect"], profile.layout["tray"][2]["piece"]["cell"][0] / 2
    assert min(x for x, _ in centres) - half < slot[0] and max(x for x, _ in centres) + half > slot[0] + slot[2]


def test_a_page_reading_board_is_measured_whatever_its_tiles_look_like():
    """A profile that reads its tiles from the page does not need their colours to find its board: a
    tile whose picture splits (a light, a colour the profile does not list) cannot stop the game starting."""
    from io import BytesIO
    from PIL import Image, ImageDraw

    page_reading = profiles.load("ovolve_2048")
    left, top, width, _ = geometry.measure(png("ovolve_initial.png"), page_reading).layout["board"]["rect"]
    with Image.open(SITES / "ovolve_initial.png") as source:
        image = source.convert("RGB")
    # the first cell's top half in an unlisted colour, its bottom half still empty
    ImageDraw.Draw(image).rectangle((left + 10, top + 10, left + width // 4 - 10, top + width // 8), fill=(90, 30, 160))
    output = BytesIO()
    image.save(output, format="PNG")
    assert isinstance(geometry.measure(output.getvalue(), page_reading), profiles.Profile)
    screen_reading = profiles.validate({key: value for key, value in page_reading.data.items() if key != "reading"})
    assert geometry.measure(output.getvalue(), screen_reading) == NeedObservation("board_not_found")
