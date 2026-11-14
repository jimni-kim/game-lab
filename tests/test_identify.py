"""Finding out which game a screen shows when nobody says (PLAN §10, game identification): a model names the
families it may be, and each is taken only when its board is read from the screen and its first moves do what
its rules say. The screens and the readers are the fakes of the adaptation tests; the model's answer to "which
game is this" is scripted here."""
from functools import partial
import json

import pytest

from gamelab import adaptation, agent, recovery
from gamelab.adaptation import Store, explorable
from gamelab.agent import play as play_adaptive
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult
from test_adaptation import Author
from test_adaptation_mines import COMPUTE, Mines, Reader, middle, source as mines_source
from test_generated import source as slide_source
from test_screen import Screen, screen_reading  # noqa: F401


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(agent, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "IDENTIFY_WAIT_S", 0.0)
    monkeypatch.setattr(adaptation, "GeneratedAdapter", partial(
        adaptation.GeneratedAdapter, settling={"timeout_s": 0.4, "interval_s": 0.05, "stable_s": 0.1}))


class Guide:
    """A model that says which game the screen shows (each of `answers` in turn) and is otherwise `reader`."""

    def __init__(self, reader, answers):
        self.reader, self.answers, self.asked = reader, list(answers), []

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "families" not in context:
            return await self.reader.complete(request)
        self.asked.append(context)
        answer = self.answers.pop(0)
        return TurnResult(text=json.dumps({"screen": "game" if answer is not None else "other",
                                           "families": answer or [], "situation": "as drawn"}))


async def mines(guide_answers, tmp_path, **reader):
    fake = Mines()
    guide = Guide(Reader(fake, [mines_source()], **reader), guide_answers)
    played, made = await play_adaptive(fake, guide, None, goal=GoalSpec("play_until_end"), budget=Budget(60, COMPUTE, 300),
                                       store=Store(tmp_path))
    return fake, guide, played, made


def test_only_families_whose_first_moves_can_be_played_are_offered():
    assert explorable() == ["block_placement", "klondike", "match3", "minesweeper", "slide_merge"]


async def test_a_game_nobody_named_is_played_as_the_family_the_model_names_and_the_screen_bears_out(tmp_path):
    fake, guide, played, made = await mines([["minesweeper"]], tmp_path)
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "play_until_end"), (played.record, made)
    assert played.record["family"] == "minesweeper" and fake.over()
    assert made["identified"] == {"family": "minesweeper", "source": "model", "candidates": ["minesweeper"],
                                  "tried": ["minesweeper"], "questions": 1, "inputs": 0, "situation": "as drawn"}
    # the model is told of each family it may name, and nothing of the site
    assert [family["id"] for family in guide.asked[0]["families"]] == explorable() and set(guide.asked[0]) == {"families"}
    # the reading that bore the family out is the exploring's first: the first screen is read once
    assert [kind for kind, _, _ in guide.reader.asked].count("label") == adaptation.EXPLORE_MOVES + 1


async def test_a_family_the_board_cannot_be_read_as_is_passed_over_with_no_move_played(tmp_path):
    """The model's first guess is wrong. Read by that family's description the screen gives no state its rules
    accept, so nothing is played by it and the next family is tried."""
    fake, guide, played, made = await mines([["slide_merge", "minesweeper", "no_such_family"]], tmp_path)
    assert (played.record["outcome"], played.record["family"]) == ("goal_reached", "minesweeper"), (played.record, made)
    assert made["identified"]["candidates"] == made["identified"]["tried"] == ["slide_merge", "minesweeper"]
    assert not fake.keys and made["identified"]["family"] == "minesweeper"


async def test_a_game_of_no_known_family_is_not_played(tmp_path):
    fake, guide, played, made = await mines([[]], tmp_path)
    assert (played.record["outcome"], played.record["reason"]) == ("unsupported", "game_not_identified")
    assert made["failed"] == "game_not_identified" and not fake.taps and len(guide.asked) == 1
    wrong, guide, played, made = await mines([["slide_merge"]], tmp_path)
    assert (played.record["outcome"], played.record["reason"]) == ("unsupported", "game_not_identified")
    assert made["identified"]["tried"] == ["slide_merge"] and made["identified"]["family"] is None and not wrong.taps


async def test_a_screen_before_the_game_is_got_past_and_asked_about_again(tmp_path):
    """A notice stands over the page when it opens: no game is named for it. The model sends an input of its
    own, told that the game is not known yet, and the screen behind is then identified."""
    fake = Mines()
    fake.covered = True
    reader = Reader(fake, [mines_source()], recovering=[{"kind": "tap", "at": middle(fake.button)}])
    guide = Guide(reader, [None, ["minesweeper"]])
    played, made = await play_adaptive(fake, guide, None, goal=GoalSpec("play_until_end"), budget=Budget(60, COMPUTE, 300),
                                       store=Store(tmp_path))
    assert (played.record["outcome"], played.record["family"]) == ("goal_reached", "minesweeper"), (played.record, made)
    assert made["identified"]["questions"] == 2 and made["identified"]["inputs"] == 1
    asked = next(context for kind, context, _ in reader.asked if kind == "recover")
    assert asked["family"] is None and asked["reason"] == "game_not_shown"
    assert fake.taps[0] == tuple(middle(fake.button))


async def test_a_site_played_before_is_known_by_its_kept_adapter_with_no_question(tmp_path):
    await mines([["minesweeper"]], tmp_path)
    fake, guide, played, made = await mines([], tmp_path)
    assert (played.record["outcome"], played.record["family"]) == ("goal_reached", "minesweeper"), (played.record, made)
    assert made["identified"] == {"family": "minesweeper", "source": "kept", "questions": 0}
    assert not guide.asked and made["reused"] and not guide.reader.asked


async def test_a_game_played_by_keys_is_identified_the_same_way(tmp_path):
    fake = Screen()
    guide = Guide(Author(fake, [slide_source(fake)]), [["minesweeper", "slide_merge"]])
    played, made = await play_adaptive(fake, guide, None, goal=GoalSpec("actions", 20), budget=Budget(60, 20, 120),
                                       store=Store(tmp_path))
    assert (played.record["outcome"], played.record["family"]) == ("goal_reached", "slide_merge"), (played.record, made)
    assert made["identified"]["tried"] == ["minesweeper", "slide_merge"] and not fake.taps
