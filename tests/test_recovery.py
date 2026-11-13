"""A screen the code cannot play on is put to a model before the game ends (PLAN §8): one input of the
model's own choosing, the screen read again by the code, and play going on by the code once it reads a board.

Regression this prevents: a game ended as unreadable or covered by a notice the code had no button for (a
card shown whenever a new tile first appears), although one tap anywhere would have closed it.
"""
from copy import deepcopy

import pytest

from gamelab import adaptation, recovery
from gamelab.adaptation import Adaptation, Store, play_adaptive
from gamelab.contracts import Budget, GoalSpec
from gamelab.recovery import Recovery, check
from gamelab.session import MAX_COVERS
from test_adaptation import Author, source
from test_screen import Screen, ending, run, screen_reading  # noqa: F401 (the fixture: the site read by its screen)
from test_session import Run, single

KINDS = frozenset({"keys", "tap", "drag"})


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)


def middle(box):
    left, top, width, height = box
    return [left + width / 2, top + height / 2]


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


async def test_a_cover_the_code_has_no_button_for_is_got_past_by_the_models_own_input_and_play_goes_on():
    """The bounded reader cannot tell what the screen shows, so the code has nothing to press and would end
    the game as unreadable. The model is asked what to do instead, taps, and the code plays on."""
    fake = Screen()
    fake.covered, fake.screen = True, "unknown"
    fake.recovering = [{"kind": "tap", "at": [5, 5]}, {"kind": "tap", "at": middle(fake.button)}]
    played = await run(fake, moves=3)
    assert ending(played) == ("goal_reached", "actions"), played.record
    assert len(fake.keys) == 3 and fake.taps == [(5, 5), tuple(middle(fake.button))]
    # the second question is told what the first input did
    assert [call["tried"] for call in fake.recover_calls] == [
        [], [{"action": {"kind": "tap", "at": [5.0, 5.0]}, "screen_changed": False}]]
    assert fake.recover_calls[0]["reason"] == "board_not_found" and fake.recover_calls[0]["state"] is None
    assert [(r["source"], r["changed"], r.get("restored", False)) for r in played.recoveries] == [
        ("model", False, False), ("model", True, True)]
    # recovery inputs are not moves: they spend none of the game's actions
    assert played.record["actions_used"] == 3 and played.record["recovery"] == {"tries": 0, "total": 2}


async def test_the_input_that_got_past_a_screen_is_sent_again_without_asking_when_the_screen_comes_back():
    fake = Screen()
    fake.screen = "unknown"
    fake.recovering = [{"kind": "tap", "at": middle(fake.button)}]
    covering(fake, after={2, 4})
    played = await run(fake, moves=6, max_actions=10)
    assert ending(played) == ("goal_reached", "actions"), played.record
    assert len(fake.recover_calls) == 1 and len(fake.taps) == 2
    assert [r["source"] for r in played.recoveries] == ["model", "replay"]
    assert played.recoveries[0]["reason"] == "unreadable" and fake.recover_calls[0]["state"] is not None


async def test_a_model_that_gives_up_or_sees_nothing_in_the_way_ends_the_game_as_it_would_have_ended():
    for action in ({"kind": "give_up"}, {"kind": "none"}, {"kind": "tap", "at": [-5, 0]}):
        fake = Screen()
        fake.covered, fake.screen = True, "unknown"
        fake.recovering = [action]
        played = await run(fake)
        assert ending(played) == ("unsupported", "board_not_found"), played.record
        assert len(fake.recover_calls) == 1 and not fake.taps and not fake.keys


async def test_inputs_that_never_bring_play_back_run_out():
    fake = Screen()
    fake.covered, fake.screen = True, "unknown"
    fake.recovering = [{"kind": "tap", "at": [5 + n, 5]} for n in range(recovery.MAX_TRIES + 3)]
    played = await run(fake)
    assert ending(played) == ("unsupported", "board_not_found"), played.record
    assert len(fake.taps) == recovery.MAX_TRIES and played.record["recovery"]["tries"] == recovery.MAX_TRIES


async def test_without_a_model_a_game_ends_as_before():
    fake = Screen()
    fake.covered = True
    played = await run(fake, model=False)
    assert ending(played) == ("unsupported", "board_not_found") and played.recoveries == []


async def test_covers_are_counted_in_a_row_so_a_game_that_shows_one_now_and_then_is_played_on():
    """A page that keeps covering its board ends the game after MAX_COVERS closes in a row; a cover closed
    between confirmed moves does not add up over a game. Regression: the fourth notice of a long game ended it."""
    game = Run(goal=single(MAX_COVERS + 2))
    ready, moved = game.env.ready, [0]

    async def covered_again(observation):
        # a notice comes up after each move, seen when the next one is about to be sent
        if game.saved and game.saved[-1]["confirmed_actions"] > moved[0]:
            moved[0] = game.saved[-1]["confirmed_actions"]
            game.env.covered = 1
            return False
        return await ready(observation)
    game.env.ready = covered_again
    game.env.covered = 1
    record = await game.run()
    assert (record["outcome"], record["reason"]) == ("goal_reached", "actions"), record
    assert record["covers"] > MAX_COVERS and record["covered_run"] == 0


async def test_the_session_asks_its_environment_before_ending_on_a_screen_and_only_then():
    """The loop itself: a game about to end as covered is carried on when the environment got past the screen,
    and ends as it would have when it did not, or when the end is the game's own."""
    game = Run(goal=single())
    game.env.covered = MAX_COVERS + 2
    asked = []

    async def recover(reason, context):
        asked.append((reason, deepcopy(context)))
        return len(asked) == 1
    game.env.recover = recover
    record = await game.run()
    assert (record["outcome"], record["reason"]) == ("goal_reached", "actions"), record
    assert [reason for reason, _ in asked] == ["board_covered"] and record["recovery"] == {"tries": 0, "total": 1}
    assert asked[0][1]["state"] is None and [m["kind"] for m in asked[0][1]["recent"]] == ["close_cover"] * MAX_COVERS

    over = Run()
    over.env.recover = recover
    record = await over.run()
    assert record["outcome"] == "game_over" and len(asked) == 1


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


async def test_a_cover_the_reader_sees_no_button_on_goes_to_the_model_at_once():
    """The bounded reader says a cover hides the board and shows no button to close it: there is nothing to
    wait for, so the model is asked what to do after that one look, not after three unread ones. Regression:
    each card of a site cost nine questions and half a minute before anything was tried."""
    fake = Screen()
    fake.covered, fake.buttonless = True, True
    fake.recovering = [{"kind": "tap", "at": middle(fake.button)}]
    played = await run(fake)
    assert ending(played) == ("goal_reached", "actions"), played.record
    assert len(fake.model_calls) == 1 and fake.recover_calls[0]["reason"] == "board_covered" and len(fake.taps) == 1


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
