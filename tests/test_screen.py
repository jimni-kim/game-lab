"""A game played on a screen through a surface: what the code reads, what a model is asked, what is sent.

The surface here is a fake that draws the registered 2048 site's capture with the game's state painted
over it and plays the keys it is given by the family's own rules; the model is the same fake's canned
answers. No browser, device or paid model is used.
"""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from gamelab import catalog, geometry, perception, profiles
from gamelab.contracts import Budget, GoalSpec, Observation
from gamelab.environments import screen
from gamelab.model import TurnResult
from gamelab.play import TRIAL_MOVES, play
from gamelab.vision import DEFAULT_VISION

SITES = Path(__file__).parent / "fixtures/game/sites"
LOAD = profiles.load


@pytest.fixture(autouse=True)
def screen_reading(monkeypatch):
    """These tests play the registered 2048 site as its screen reads it (the image path and its
    default settling), not through its page's elements."""
    def load(profile_id):
        profile = LOAD(profile_id)
        if "reading" not in profile.data:
            return profile
        return profiles.validate({key: value for key, value in profile.data.items() if key not in ("reading", "settling")})
    monkeypatch.setattr(profiles, "load", load)


def render(state, profile):
    """The site's capture with `state` painted over it, laid out as `profile` was measured on it."""
    block = profile.family_id == "block_placement"
    image = Image.open(SITES / ("blockblast_initial.png" if block else "ovolve_initial.png")).convert("RGB")
    draw = ImageDraw.Draw(image)
    board = profile.layout["board"]
    left, top, width, height = board["rect"]
    # a profile may name no colour for its pieces (any): paint them in one the site uses
    colours = {v["value"]: tuple(v.get("rgb", (236, 182, 49))) for v in board["palette"]}
    for row, cells in enumerate(state["board" if block else "grid"]):
        for col, value in enumerate(cells):
            x, y = left + col * width / board["cols"], top + row * height / board["rows"]
            # Preserve the registered border and scene, painting only tile interiors.
            draw.rectangle((int(x + 10), int(y + 10), int(x + width / board["cols"] - 10), int(y + height / board["rows"] - 10)),
                           fill=colours.get(value, (1, 2, 3)))
    if block:
        band = profile.layout["tray_band"]["rect"]
        for piece, slot in zip(state["tray"], profile.layout["tray"]):
            if piece is not None:
                continue
            # a used slot shows the page's background, as the tray band's edge does on each row
            x, y, w, h = slot["rect"]
            for dy in range(h):
                draw.line((x, y + dy, x + w - 1, y + dy), fill=image.getpixel((band[0], y + dy)))
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class Screen:
    """A surface and the model asked about it."""
    name = "fake"
    reads_dom = False

    def __init__(self, profile_id="ovolve_2048", file="ovolve_initial.png", kinds=(screen.KEYS, screen.TAP, screen.DRAG)):
        self.kinds = frozenset(kinds)
        png = (SITES / file).read_bytes()
        self.profile = profiles.load(profile_id)
        self.measured = geometry.measure(png, self.profile)
        self.family = catalog.family(self.profile.family_id).bind(self.profile.data)
        self.board = perception.estimate(Observation("initial", 0, "tab", 1, 0, "clock"), png, self.measured).payload
        self.keys, self.taps, self.drags, self.model_calls = [], [], [], []
        self.unknown = False
        self.no_effect = False
        # a dialog over the board until its button is tapped, and what the model says such a screen shows
        self.covered = False
        self.screen = "covered"
        # a win notice beside a board that stays readable: keys do nothing until its button is tapped
        self.beside = False
        # the cover is a consent request whose only button agrees
        self.agree_only = False
        # an advertisement on the cover that draws itself anew for every look
        self.animated = False
        self.frames = 0
        # a panel in the frame's colour touching the board, and the box the model says the board is in
        self.joined = False
        self.seen_board = None
        # what the model says a site no profile registers shows (vision.discover)
        self.discovered = None
        self.recovering, self.recover_calls = [], []

    # ── the surface ───────────────────────────────────────────────────

    async def size(self):
        return list(self.measured.layout["viewport"])

    async def capture(self, region=None):
        shown = deepcopy(self.board)
        if self.unknown:
            shown["grid"][0][0] = None
        png = render(shown, self.measured)
        if self.covered:
            png = self._cover(png)
        if self.joined:
            png = self._join(png)
        return png

    async def elements(self, queries):
        return None

    async def address(self):
        return "https://unregistered.example/"

    async def key(self, key):
        self.keys.append(key)
        direction = next(k for k, v in self.profile.data["controls"]["keys"].items() if v == key)
        self._slide(direction)

    def _slide(self, direction):
        if not self.no_effect and not self.beside and not self.covered:
            self.board = self.family.next_state(self.board, {"kind": "slide", "direction": direction}, 0)

    async def tap(self, x, y, duration_ms):
        self.taps.append((x, y))
        left, top, width, height = self.button
        if left <= x < left + width and top <= y < top + height:
            self.covered = self.beside = False

    async def drag(self, x0, y0, x1, y1, duration_ms):
        self.drags.append((x0, y0, x1, y1))
        left, top, width, height = self.measured.layout["board"]["rect"]
        assert all(left <= x < left + width and top <= y < top + height for x, y in ((x0, y0), (x1, y1)))
        across, down = x1 - x0, y1 - y0
        self._slide(("right" if across > 0 else "left") if abs(across) > abs(down) else ("down" if down > 0 else "up"))

    async def release(self):
        pass

    @property
    def button(self):
        left, top, width, _ = self.measured.layout["board"]["rect"]
        return [left + width // 2 - 60, top + 40, 120, 40]

    def _cover(self, png):
        """A dialog over the board's upper part, its frame included, with one button."""
        image = Image.open(BytesIO(png)).convert("RGB")
        draw = ImageDraw.Draw(image)
        left, top, width, height = self.measured.layout["board"]["rect"]
        draw.rectangle((left - 30, top - 30, left + width + 30, top + height // 2), fill=(250, 250, 250))
        x, y, w, h = self.button
        draw.rectangle((x, y, x + w - 1, y + h - 1), fill=(40, 120, 220))
        if self.animated:
            self.frames += 1
            draw.rectangle((left, top - 20, left + 60, top + 20), fill=(self.frames * 37 % 256, 90, 160))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    def outer(self):
        """The measured board's outer edge, the box a model draws around it."""
        left, top, width, height = self.measured.layout["board"]["rect"]
        inset = self.profile.data["board"]["frame"]["inset"]
        across, down = width / (1 - inset[0] - inset[2]), height / (1 - inset[1] - inset[3])
        return [int(left - inset[0] * across), int(top - inset[1] * down), int(across), int(down)]

    def _join(self, png):
        """A score box in the frame's colour touching the board's top, joining its frame."""
        image = Image.open(BytesIO(png)).convert("RGB")
        left, top, width, _ = self.outer()
        ImageDraw.Draw(image).rectangle((left + width // 4, top - 60, left + width * 3 // 4, top + 2),
                                        fill=tuple(self.profile.data["board"]["frame"]["rgb"]))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    # ── the model ─────────────────────────────────────────────────────

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "tried" in context:
            # play stopped on this screen: what the model does about it (recovery), each in turn, then gives up
            self.recover_calls.append(context)
            action = self.recovering.pop(0) if self.recovering else {"kind": "give_up"}
            return TurnResult(text=json.dumps({"situation": "as drawn", "action": action}))
        self.model_calls.append(request)
        evidence = {key: context[key] for key in ("observation_id", "image_sha256")}
        if set(context) == {"observation_id", "image_sha256", "family"}:
            # the look at a game no profile registers: a cover first while there is one
            seen = ({"screen": "covered", "control": self.button, "cover": "notice", "accept": None, "family": None,
                     "board": None, "grid": None, "cells": None} if self.covered else {"cover": None, "accept": None,
                                                                                         **self.discovered})
            return TurnResult(text=json.dumps(evidence | seen))
        if "screens" in context:
            return TurnResult(text=json.dumps(evidence | {
                "screen": self.screen, "board": self.seen_board if self.screen == "board" else None,
                "control": self.button if self.screen == "won" or self.screen == "covered" and not self.agree_only else None,
                "cover": ("consent" if self.agree_only else "notice") if self.screen == "covered" else None,
                "accept": self.button if self.screen == "covered" and self.agree_only else None}))
        return TurnResult(text=json.dumps(evidence | {"geometry_ref": context["geometry_ref"],
                                                      "state": {**self.board, "score": None}}))

    def unregistered(self):
        """The run names the game's family and no profile; the model describes the screen as it is."""
        self.discovered = {"screen": "board", "control": None, "family": "slide_merge", "board": self.outer(),
                           "grid": {"rows": 4, "cols": 4}, "cells": deepcopy(self.board["grid"])}


async def run(fake, *, moves=1, max_actions=5, registered=True, model=True, **options):
    return await play(fake, **({"profile": fake.profile} if registered else {"family_id": "slide_merge"}),
                      goal=GoalSpec("actions", moves), budget=Budget(max_actions, 20, 60),
                      model=fake if model else None, **options)


def ending(played):
    return played.record["outcome"], played.record["reason"]


async def test_a_move_is_sent_as_its_key_and_confirmed_on_the_next_look():
    fake = Screen()
    before = deepcopy(fake.board)
    played = await run(fake, moves=2, model=False)
    assert ending(played) == ("goal_reached", "actions"), played.record
    assert len(fake.keys) == 2 and played.record["confirmed_actions"] == 2 and fake.board != before
    assert [move["effect"] for move in played.moves] == ["confirmed", "confirmed"]
    assert played.geometry["board"] == fake.measured.layout["board"]["rect"]
    assert played.vision["asked"] == 0 and played.looks >= 2 and played.last_screen


async def test_a_surface_without_keys_swipes_a_direction_across_the_board():
    """A phone has no arrow keys: the same profile's move is a swipe inside the measured board."""
    fake = Screen(kinds=(screen.TAP, screen.DRAG))
    played = await run(fake, model=False)
    assert ending(played) == ("goal_reached", "actions"), played.record
    assert not fake.keys and len(fake.drags) == 1


async def test_a_surface_that_cannot_send_the_games_input_is_refused_before_any_look():
    fake = Screen(kinds=(screen.TAP,))
    played = await run(fake, model=False)
    assert ending(played) == ("unsupported", screen.INPUT_KIND_UNSUPPORTED) and not played.looks


async def test_delivered_input_without_visible_effect_is_not_success():
    """A sent input cannot replace the game's observed transition."""
    fake = Screen()
    fake.no_effect = True
    played = await run(fake, model=False)
    assert played.record["outcome"] == "needs_recovery" and played.record["confirmed_actions"] == 0
    assert {move["effect"] for move in played.moves} == {"absent"}


@pytest.mark.parametrize("persistent", [False, True])
async def test_a_model_reads_only_an_unread_state_that_persists(persistent):
    """The model is asked, under the default budget, when the same cells stay unread across two looks, once
    per distinct picture; a frame unread once (an animation) costs no call."""
    fake = Screen()
    fake.unknown = True
    capture = fake.capture

    async def once(region=None):
        png = await capture(region)
        fake.unknown = persistent
        return png
    fake.capture = once
    played = await run(fake)
    assert ending(played) == ("goal_reached", "actions"), played.record
    assert played.vision["budget"] == DEFAULT_VISION
    # one picture before the move and one after it; repeated looks at the same picture are not asked again
    assert len(fake.model_calls) == played.vision["asked"] == (2 if persistent else 0)


@pytest.mark.parametrize(("shown", "outcome", "taps"), [("covered", ("goal_reached", "actions"), 1),
                                                       ("over", ("game_over", "terminal"), 0),
                                                       ("unknown", ("unsupported", "board_not_found"), 0)])
async def test_a_screen_the_code_cannot_read_is_put_to_the_model(shown, outcome, taps):
    """A dialog over the board leaves the code nothing to read; the same pixels on the next look go to
    the bounded model. A cover is closed by the session with one tap on the button the model found, and
    play goes on. The game's own end message ends the game with no input; a screen the model cannot tell
    stays refused."""
    fake = Screen()
    fake.covered, fake.screen = True, shown
    played = await run(fake)
    assert ending(played) == outcome, played.record
    assert len(fake.model_calls) == 1 and len(fake.taps) == taps
    if taps:
        assert played.moves[0]["kind"] == "close_cover" and played.record["actions_used"] == 2
        assert len(fake.keys) == 1 and played.record["confirmed_actions"] == 1
    else:
        assert not fake.keys and played.record["actions_used"] == 0


@pytest.mark.parametrize(("shift", "outcome"), [(0, ("goal_reached", "actions")),
                                               (200, ("unsupported", "board_not_found"))],
                         ids=["the board", "somewhere else"])
async def test_a_board_the_code_cannot_find_is_measured_where_the_model_saw_it(shift, outcome):
    """A score box in the frame's colour touching the board joins its frame, and the code finds no board
    to measure. The same pixels on the next look go to the bounded model, and the board is measured near
    the box it gives and confirmed before it becomes the session's geometry. A box away from the board
    confirms nothing, and the game stops before any input."""
    fake = Screen()
    fake.joined, fake.screen = True, "board"
    left, top, width, height = fake.outer()
    fake.seen_board = [left + 6 + shift, top - 5, width - 8, height + 10]
    played = await run(fake)
    assert ending(played) == outcome, played.record
    assert len(fake.model_calls) == 1
    if shift:
        assert not fake.keys and played.geometry is None
    else:
        assert len(fake.keys) == played.record["confirmed_actions"] == 1
        assert played.geometry["board"] == fake.measured.layout["board"]["rect"]


@pytest.mark.parametrize(("shown", "outcome", "taps"), [("over", ("game_over", "terminal"), 0),
                                                       ("covered", ("goal_reached", "actions"), 1)])
async def test_a_screen_whose_pixels_never_settle_is_still_put_to_the_model(monkeypatch, shown, outcome, taps):
    """An advertisement that animates over an end screen or a cover means no two looks show the same
    pixels. Once the screen has shown no readable board for longer than any move's animation, the model
    is asked all the same."""
    monkeypatch.setattr(screen, "UNREAD_ASK_S", 0.2)
    fake = Screen()
    fake.covered, fake.animated, fake.screen = True, True, shown
    played = await run(fake)
    assert ending(played) == outcome, played.record
    assert len(fake.taps) == taps
    if shown == "over":
        assert len(fake.model_calls) == 1 and not fake.keys


async def test_a_game_no_profile_registers_is_set_up_from_one_look_and_proved_by_trial_moves():
    """A run naming no profile gets one from a single model look the code confirms; its first moves must
    each do what the rules predict."""
    fake = Screen()
    fake.unregistered()
    played = await run(fake, moves=TRIAL_MOVES + 1, max_actions=8, registered=False)
    assert ending(played) == ("goal_reached", "actions"), played.record
    made = played.made_profile["data"]
    assert made["id"].startswith(profiles.SESSION) and made["source"]["url"] == "https://unregistered.example/"
    assert len(fake.model_calls) == 1 and len(fake.keys) == TRIAL_MOVES + 1 and played.record["trial"] == 0


async def test_a_cover_over_a_game_no_profile_registers_is_closed_before_its_board_is_learned():
    fake = Screen()
    fake.unregistered()
    fake.covered = True
    played = await run(fake, registered=False)
    assert ending(played) == ("goal_reached", "actions"), played.record
    assert len(fake.taps) == 1 and played.moves[0]["kind"] == "close_cover" and len(fake.keys) == 1
    assert played.made_profile["data"]["id"].startswith(profiles.SESSION)


@pytest.mark.parametrize(("answer", "reason"), [
    ({"family": "block_placement"}, "family_unknown"),
    ({"screen": "unknown", "family": None, "board": None, "grid": None, "cells": None}, "board_not_found"),
    ({"cells": [[0, 0, 0, 0], [0, 2, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]}, "cells_unconfirmed"),
], ids=["another family", "no board of the game", "cells the screen does not show"])
async def test_a_look_the_screen_does_not_confirm_ends_the_game_before_any_input(answer, reason):
    fake = Screen()
    fake.unregistered()
    fake.discovered.update(answer)
    played = await run(fake, registered=False)
    assert ending(played) == ("unsupported", reason)
    assert not fake.keys and not fake.taps and played.made_profile is None


async def test_a_win_screen_before_the_goal_presses_its_button_and_keeps_playing_the_same_game():
    """The game's win message may come before the goal asked for: its button is pressed once and the same
    game goes on."""
    fake = Screen()
    fake.covered, fake.screen = True, "won"
    played = await run(fake, moves=2)
    assert ending(played) == ("goal_reached", "actions"), played.record
    assert len(fake.taps) == 1 and played.moves[0]["kind"] == "continue_after_win"
    assert {move["kind"] for move in played.moves[1:]} == {"slide"} and fake.keys


async def test_a_win_notice_beside_a_readable_board_is_asked_about_once_and_play_goes_on():
    """A game may show its win notice beside a board that stays readable and stop taking keys. The move
    without effect has the model look at the whole screen once, told the board was read."""
    fake = Screen()
    fake.beside, fake.screen = True, "won"
    played = await run(fake, moves=2)
    assert ending(played) == ("goal_reached", "actions"), played.record
    asked = [json.loads(request.messages[0].content[0]["text"]) for request in fake.model_calls]
    assert [context["board_read"] for context in asked] == [True]
    assert [move["kind"] for move in played.moves] == ["slide", "continue_after_win", "slide"]


@pytest.mark.parametrize("allowed", [False, True])
async def test_a_consent_request_with_no_refusal_is_agreed_to_only_when_the_run_allows_it(allowed):
    fake = Screen()
    fake.covered, fake.agree_only = True, True
    played = await run(fake, accept_consent=allowed)
    if allowed:
        assert ending(played) == ("goal_reached", "actions"), played.record
        assert played.moves[0]["kind"] == "accept_consent" and len(fake.taps) == 1
    else:
        assert ending(played) == ("user_stopped", "consent_declined") and not fake.taps and not fake.keys


async def test_a_win_screen_that_meets_the_goal_ends_the_game_as_won():
    fake = Screen()
    fake.covered, fake.screen = True, "won"
    played = await play(fake, profile=fake.profile, goal=GoalSpec("reach_value", 2048), budget=Budget(5, 20, 60), model=fake)
    assert ending(played) == ("goal_reached", "won") and not fake.keys and not fake.taps


async def test_a_run_is_refused_for_a_goal_or_policy_the_family_cannot_play():
    fake = Screen()
    with pytest.raises(ValueError, match="goal_target_missing"):
        await play(fake, profile=fake.profile, goal=GoalSpec("reach_value"), budget=Budget(5, 20, 60))
    with pytest.raises(ValueError, match="policy_unknown"):
        await play(fake, profile=fake.profile, goal=GoalSpec("actions", 1), budget=Budget(5, 20, 60), policy="no_such")
