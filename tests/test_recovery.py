"""A screen the code cannot play on is put to a model before the game ends (PLAN §8): one input of the
model's own choosing, the screen read again by the code, and play going on by the code once it reads a board.

Regression this prevents: a game ended as unreadable or covered by a notice the code had no button for (a
card shown whenever a new tile first appears), although one tap anywhere would have closed it.
"""
import json

import pytest

from gamelab import adaptation, agent, recovery
from gamelab.adaptation import Adaptation, Store
from gamelab.agent import play as play_adaptive
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult
from gamelab.recovery import Recovery, check
from fake_screen import Screen
from test_adaptation import Author, source

KINDS = frozenset({"keys", "tap", "drag"})


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(agent, "STILL_GAP_S", 0.0)


def middle(box):
    left, top, width, height = box
    return [left + width / 2, top + height / 2]


def ending(played):
    return played.record["outcome"], played.record["reason"]


class Scripted:
    """The model: `author` reads and writes, and what it does about a screen play stopped on is each of
    `recovering` in turn, then giving up."""

    def __init__(self, author, recovering):
        self.author, self.recovering, self.recover_calls = author, list(recovering), []

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "tried" not in context:
            return await self.author.complete(request)
        self.recover_calls.append(context)
        action = self.recovering.pop(0) if self.recovering else {"kind": "give_up"}
        return TurnResult(text=json.dumps({"situation": "as drawn", "action": action}))


async def covered_game(recovering, moves=1):
    """A game whose first screen is a notice over the board, played by the agent's loop."""
    fake = Screen()
    fake.covered = True
    model = Scripted(Author(fake, [source(fake)]), recovering(fake))
    played, _ = await play_adaptive(fake, model, "slide_merge", goal=GoalSpec("actions", moves), budget=Budget(20, 20, 120))
    return fake, model, played


def covering(fake, after):
    """The fake shows its cover again each time the keys sent reach one of `after`."""
    key = fake.key

    async def pressed(name):
        await key(name)
        if len(fake.keys) in after:
            fake.covered = True
    fake.key = pressed


@pytest.mark.parametrize(("action", "kept"), [
    ({"kind": "tap", "at": [10, 20]}, True), ({"kind": "tap", "at": [100, 20]}, False),
    ({"kind": "tap", "at": [10]}, False), ({"kind": "key", "key": "Escape"}, True),
    ({"kind": "key", "key": "F5"}, False), ({"kind": "swipe", "from": [1, 1], "to": [50, 50]}, True),
    ({"kind": "swipe", "from": [1, 1], "to": [50, 500]}, False), ({"kind": "wait", "ms": 500}, True),
    ({"kind": "wait", "ms": 60000}, False), ({"kind": "none"}, True), ({"kind": "give_up", "why": "x"}, True),
    ({"kind": "click"}, False), ("tap", False)])
def test_only_an_input_on_the_screen_a_listed_key_or_a_bounded_wait_is_taken(action, kept):
    assert (check(action, (100, 100), KINDS) is not None) == kept


def test_an_input_the_surface_cannot_carry_out_is_not_taken():
    assert check({"kind": "key", "key": "Enter"}, (100, 100), frozenset({"tap", "drag"})) is None


async def test_a_cover_nothing_reads_past_is_got_past_by_the_models_own_input_and_play_goes_on():
    """No board can be read under the notice, so the game cannot begin. The model is asked what to do instead,
    taps, and the game goes on."""
    fake, model, played = await covered_game(lambda fake: [{"kind": "tap", "at": [5, 5]}, {"kind": "tap", "at": middle(fake.button)}],
                                             moves=10)
    assert ending(played) == ("goal_reached", "actions"), played.record
    assert len(fake.keys) == 10 and fake.taps == [(5, 5), tuple(middle(fake.button))]
    # the second question is told what the first input did
    assert [call["tried"] for call in model.recover_calls] == [
        [], [{"action": {"kind": "tap", "at": [5.0, 5.0]}, "screen_changed": False}]]
    assert {call["reason"] for call in model.recover_calls} == {"label_failed"}
    assert [(r["source"], r["changed"], r.get("restored", False)) for r in played.recoveries] == [
        ("model", False, False), ("model", True, True)]
    # recovery inputs are not moves: they spend none of the game's actions
    assert played.record["actions_used"] == 10


async def test_a_model_that_gives_up_or_sees_nothing_in_the_way_ends_the_game_with_no_input():
    for action in ({"kind": "give_up"}, {"kind": "none"}, {"kind": "tap", "at": [-5, 0]}):
        fake, model, played = await covered_game(lambda fake: [action])
        assert ending(played) == ("gave_up", "label_failed"), played.record
        assert model.recover_calls and not fake.taps and not fake.keys
        if action["kind"] != "none":
            assert len(model.recover_calls) == 1


async def test_inputs_that_never_bring_play_back_run_out():
    fake, model, played = await covered_game(lambda fake: [{"kind": "tap", "at": [5 + n, 5]} for n in range(recovery.MAX_TRIES + 3)])
    assert ending(played) == ("gave_up", "label_failed"), played.record
    assert len(fake.taps) == len(model.recover_calls) == recovery.MAX_TRIES and not fake.keys


async def test_a_notice_over_the_first_moves_is_got_past_while_the_model_still_reads_every_screen():
    """While the adapter is not written yet the model reads each screen; one that shows no board is put to it
    again as a screen to get past, and the first moves go on. Regression: a card shown for a new tile ended
    the game before any adapter was made (`label_failed`)."""
    fake = Screen()
    covering(fake, after={3})
    author = Author(fake, [])
    trace = await Adaptation(author, "slide_merge", recovery=Recovery(author, "slide_merge")).explore(fake)
    assert len(trace.looks) == adaptation.EXPLORE_MOVES + 1 and len(fake.taps) == 1
    assert [kind for kind, _, _ in author.asked].count("recover") == 1


async def test_what_got_past_a_sites_screens_is_kept_with_its_adapter(tmp_path):
    fake = Screen()
    covering(fake, after={3})
    author = Author(fake, [source(fake)])
    played, made = await play_adaptive(fake, author, "slide_merge", goal=GoalSpec("actions", 4), budget=Budget(20, 20, 120),
                                       store=Store(tmp_path))
    assert ending(played) == ("goal_reached", "actions"), (played.record, made)
    assert made["recovery"]["restored"] == 1 and len(played.recoveries) == 1
    kept = Store(tmp_path).recoveries("https://unregistered.example/", "slide_merge")
    assert [known["action"] for known in kept] == [{"kind": "tap", "at": middle(fake.button)}]


async def test_a_value_the_adapter_takes_for_another_is_learned_again_when_its_moves_stop_making_sense(tmp_path, monkeypatch):
    """The first adapter reads every tile above 16 as 16: nothing is left unread, but the moves stop doing what
    the rules predict. Nothing is in the way on the screen, so the model reads that screen and the adapter is
    written again. Regression: a two-digit tile read as its first digit ended the game as inconsistent."""
    from functools import partial
    monkeypatch.setattr(adaptation, "GeneratedAdapter", partial(
        adaptation.GeneratedAdapter, settling={"timeout_s": 0.4, "interval_s": 0.05, "stable_s": 0.1}))
    fake = Screen()
    capped = source(fake, cell="cells.append(min(int(value), 16) if far <= 12 else None)")
    author = Author(fake, [capped, source(fake)])
    author.unblocked = True
    played, made = await play_adaptive(fake, author, "slide_merge", goal=GoalSpec("actions", 70), budget=Budget(140, 20, 120),
                                       store=Store(tmp_path))
    assert ending(played) == ("goal_reached", "actions"), (played.record, made)
    assert made["relearned"] >= 1 and made["generations"] == 2 and max(map(max, fake.board["grid"])) >= 32


async def test_a_kept_input_is_sent_again_only_where_no_board_showed_and_is_forgotten_once_it_fails():
    """An input that got past a notice is replayed only when play stops for want of a board: where the board was
    read whole and the moves made no sense, the screen may be the board itself, and a tap there a move. A replay
    after which play is stuck again is forgotten. Regression: a kept tap was replayed on a board read whole."""
    fake = Screen()
    tap = {"kind": "tap", "at": middle(fake.button)}
    kept = [{"action": tap, "signature": recovery.signature(await fake.capture())}]
    asked = Recovery(fake, "slide_merge", known=kept)
    fake.recovering = [{"kind": "none"}]
    assert not await asked.attempt(fake, "inconsistent", {"state": None, "recent": []})
    assert asked.log[-1]["source"] == "model" and not fake.taps
    replayed = Recovery(fake, "slide_merge", known=kept)
    fake.recovering = [{"kind": "give_up"}]
    assert await replayed.attempt(fake, "unreadable", {"state": None, "recent": []})
    assert replayed.log[-1]["source"] == "replay" and len(fake.taps) == 1
    # stuck again with nothing confirmed between: the kept input did not work here, and the model is asked
    assert not await replayed.attempt(fake, "unreadable", {"state": None, "recent": []})
    assert replayed.log[-1]["source"] == "model" and replayed.known == []


async def test_a_large_screen_is_shown_smaller_and_what_the_model_points_at_is_sent_in_the_screens_pixels():
    """A model's service shrinks a large picture, and the model answers in the pixels it saw. Regression: on a
    phone's screen (1080 by 2400) the board box and every swipe landed about a seventh too near the corner."""
    import json
    from io import BytesIO
    from PIL import Image
    from gamelab.model import TurnResult

    class Phone:
        kinds, taps = KINDS, []

        async def capture(self, region=None):
            out = BytesIO()
            Image.new("RGB", (1080, 2400), (20, 30, 40)).save(out, format="PNG")
            return out.getvalue()

        async def tap(self, x, y, duration_ms):
            self.taps.append((round(x), round(y)))

    class Pointing:
        async def complete(self, request):
            self.screen = json.loads(request.messages[0].content[0]["text"])["screen"]
            return TurnResult(text=json.dumps({"situation": "a notice", "action": {"kind": "tap", "at": [self.screen[0] / 2, self.screen[1] / 4]}}))
    model = Pointing()
    assert await Recovery(model, "slide_merge").attempt(Phone(), "board_not_found", {"state": None, "recent": []})
    scale = recovery.VIEW_SIDE / 2400
    assert model.screen == [round(1080 * scale), recovery.VIEW_SIDE] and Phone.taps == [(540, 600)]
    small, same = recovery.view(await Screen().capture())
    assert same == 1.0
