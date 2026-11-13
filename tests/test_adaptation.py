"""Making a site adapter at run time: the model reads the first moves and writes the code, the harness
checks it (adaptive perception plan §5). The model here is a fake: its readings are the fake screen's own
board and its code is written by hand, wrong on purpose the first time."""
import json

import pytest

from gamelab import adaptation
from gamelab.adaptation import Adaptation, AdaptationFailed, Store, adapter_for, adopt, play_adaptive
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult
from gamelab.play import play
from test_generated import OLD, source
from test_screen import Screen, screen_reading  # noqa: F401


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)


class Author:
    """The model: it reads the screen as the fake draws it, and writes each of `modules` in turn."""

    def __init__(self, fake, modules):
        self.fake, self.modules, self.asked = fake, list(modules), []
        self.misread = 0

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        images = len(request.messages[0].content) - 1
        if "examples" in context:
            self.asked.append(("write", context, images))
            return TurnResult(text=self.modules.pop(0))
        if "tried" in context:
            # play stopped on a screen with no board: the model closes what covers it
            self.asked.append(("recover", context, images))
            left, top, width, height = self.fake.button
            return TurnResult(text=json.dumps({"situation": "a notice", "action": {
                "kind": "tap", "at": [left + width / 2, top + height / 2]}}))
        self.asked.append(("label", context, images))
        if self.fake.covered:
            return TurnResult(text=json.dumps({"screen": "other", "board": None, "rules": None, "state": None}))
        grid = [list(row) for row in self.fake.board["grid"]]
        labels = sum(kind == "label" for kind, _, _ in self.asked)
        if self.misread and labels > 1:       # never the first look: nothing yet says a first reading is wrong
            self.misread -= 1
            grid[0][0] = 3 if self.misread % 2 else (grid[0][0] or 2) * 2
        left, top, width, height = self.fake.measured.layout["board"]["rect"]
        return TurnResult(text=json.dumps({"screen": "board", "board": [left - 3, top + 2, width + 4, height - 3],
                                           "rules": {"size": 4, "target": 2048}, "state": {"grid": grid, "score": 0, "scene": "playing"}}))


def wrong(fake):
    return source(fake, cell="cells.append((int(value) if far <= 12 else None) if (row, col) != (3, 3) else 2)")


async def test_the_first_moves_are_read_by_the_model_checked_by_the_rules_and_chosen_by_the_solver():
    fake = Screen()
    author = Author(fake, [])
    trace = await Adaptation(author, "slide_merge").explore(fake)
    assert len(trace.looks) == adaptation.EXPLORE_MOVES + 1 == len(fake.keys) + 1 and trace.rules == {"size": 4}
    assert [look.sent["key"] for look in trace.looks[:-1]] == fake.keys and trace.looks[-1].action is None
    assert trace.looks[-1].state["grid"] == fake.board["grid"] and trace.site == "https://unregistered.example/"
    # the first reading sees the whole screen alone; later ones also the board with its numbered grid
    assert [images for kind, _, images in author.asked] == [1] + [2] * adaptation.EXPLORE_MOVES
    assert "board" not in author.asked[0][1] and author.asked[1][1]["board"] == trace.looks[0].board


async def test_a_reading_the_rules_refuse_is_asked_again_and_a_screen_never_read_right_stops_before_more_input():
    fake = Screen()
    author = Author(fake, [])
    author.misread = 2                      # a value no tile has, then a board no move leaves
    trace = await Adaptation(author, "slide_merge").explore(fake, moves=4)
    assert len(trace.looks) == 5 and len(author.asked) == 5 + 2
    stuck = Screen()
    author = Author(stuck, [])
    author.misread = 99
    with pytest.raises(AdaptationFailed, match="label_failed"):
        await Adaptation(author, "slide_merge").explore(stuck)
    assert len(stuck.keys) == 1             # the move whose result was never read right is the last one sent


async def test_an_adapter_is_adopted_only_when_it_reads_every_recorded_screen_and_move():
    fake = Screen()
    trace = await Adaptation(Author(fake, []), "slide_merge").explore(fake)
    good = adopt("```python\n" + source(fake) + "\n```", trace)
    assert good["passed"] and good["transitions"] == {"checked": 6, "confirmed": 6} and not good["source"].startswith("```")
    for fault, check in (({"cell": "cells.append(None)"}, "read"), ({"layout": "return None"}, "measure"),
                         ({"key": 'return {"kind": "key", "key": "ArrowUp"}'}, "target"),
                         ({"key": 'return {"kind": "key", "key": "F5"}'}, "target"),
                         ({"read": "    raise RuntimeError('boom')"}, "read")):
        report = adopt(source(fake, **fault), trace)
        assert not report["passed"] and report["failed_check"] == check and report["problems"], fault
    assert adopt("def read(picture, layout):\n    return {}\n", trace)["failed_check"] == "functions"


async def test_a_failure_on_a_held_back_screen_is_not_described_to_the_writer():
    """The last looks are kept back: an adapter wrong only there is refused, and told no more than that."""
    fake = Screen()
    trace = await Adaptation(Author(fake, []), "slide_merge").explore(fake)
    value = trace.looks[-1].state["grid"]
    row, col = next((r, c) for r in range(4) for c in range(4)
                    if value[r][c] and all(look.state["grid"][r][c] == 0 for look in trace.train))
    only_there = source(fake, cell=f"cells.append((int(value) if far <= 12 else None) if (row, col) != ({row}, {col}) else 0)")
    report = adopt(only_there, trace)
    assert not report["passed"] and all("not shown" in problem for problem in report["problems"])
    assert not any(json.dumps(trace.looks[-1].state["grid"]) in problem for problem in report["problems"])


async def test_a_wrong_attempt_is_told_what_was_wrong_and_the_next_one_is_adopted_kept_and_played(tmp_path):
    fake = Screen()
    author = Author(fake, [wrong(fake), source(fake)])
    adapter, made = await adapter_for(fake, author, "slide_merge", store=Store(tmp_path))
    try:
        assert made.generations == 2 and not made.reused and made.questions == adaptation.EXPLORE_MOVES + 1 + 2
        first, second = [context for kind, context, _ in author.asked if kind == "write"]
        assert "previous" not in first and second["previous"]["module"] == wrong(fake).strip()
        assert any(problem.startswith("state on example") for problem in second["previous"]["problems"])
        # the writer sees only the looks not held back, each with its picture
        assert len(first["examples"]) == adaptation.EXPLORE_MOVES + 1 - adaptation.HELD_BACK
        assert [images for kind, _, images in author.asked if kind == "write"] == [len(first["examples"])] * 2
        assert first["examples"][0]["cells"][0]["cell"] == [0, 0] and first["probes"]["rows"] and "def nearest" in first["helpers"]
        played = await play(fake, adapter=adapter, goal=GoalSpec("actions", 4), budget=Budget(8, 20, 60))
        assert played.record["outcome"] == "goal_reached" and played.record["trial"] == 0, (played.record, adapter.last_error)
    finally:
        adapter.close()
    # the next game on the same site asks no model
    silent = Author(fake, [])
    again, kept = await adapter_for(fake, silent, "slide_merge", store=Store(tmp_path))
    again.close()
    assert kept.reused and again.source == source(fake).strip() and not silent.asked
    assert len(list(tmp_path.rglob("look-*.png"))) == adaptation.EXPLORE_MOVES + 1


async def test_attempts_run_out_rather_than_play_by_an_adapter_that_never_passed():
    fake = Screen()
    author = Author(fake, [wrong(fake)] * adaptation.MAX_GENERATIONS)
    with pytest.raises(AdaptationFailed, match="no_adapter_passed"):
        await adapter_for(fake, author, "slide_merge")
    assert len(fake.keys) == adaptation.EXPLORE_MOVES and not author.modules


async def test_a_value_the_game_had_not_shown_is_learned_from_one_more_look_and_play_goes_on(tmp_path, monkeypatch):
    """The first adapter knows only the small tiles (up to 16) its examples showed and leaves any other unread. When
    such a tile appears play stops without guessing; the model reads that one screen, the adapter is written
    again from every look so far, and the same game goes on."""
    from functools import partial
    # a short wait for a screen to settle: the unread screen is looked at three times before play stops
    monkeypatch.setattr(adaptation, "GeneratedAdapter", partial(
        adaptation.GeneratedAdapter, settling={"timeout_s": 0.4, "interval_s": 0.05, "stable_s": 0.1}))
    fake = Screen()
    small = source(fake, cell="cells.append(int(value) if far <= 12 and int(value) <= 16 else None)")
    author = Author(fake, [small, source(fake)])
    played, made = await play_adaptive(fake, author, "slide_merge", goal=GoalSpec("actions", 70), budget=Budget(120, 20, 120),
                                       store=Store(tmp_path))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    assert played.record["confirmed_actions"] == 70 and max(map(max, fake.board["grid"])) >= 32
    assert made["relearned"] == 1 and made["generations"] == 2 and made["looks"] == adaptation.EXPLORE_MOVES + 2
    second = [context for kind, context, _ in author.asked if kind == "write"][1]
    # the look that taught the new value is shown to the writer, with what the old code made of it
    assert any(value == 32 for row in second["examples"][-1]["state"]["grid"] for value in row)
    assert any("left cells unread" in problem for problem in second["previous"]["problems"])
    assert {move["round"] for move in played.moves} == {0, 1}
    # what was learned is kept: the next game starts from the second adapter
    kept = Store(tmp_path).load("https://unregistered.example/", "slide_merge")
    assert kept[0] == source(fake).strip() and len(kept[2].looks) == adaptation.EXPLORE_MOVES + 2 and not kept[2].looks[-1].held
