"""The one loop a game is played by (docs/design/2026-11-14-agent-loop.md): the model reads until an adapter
is written, the adapter reads until it is doubted, and the game goes on whichever of them reads. The screens
and the models are the fakes of the adaptation tests."""
import asyncio
from functools import partial

import pytest

from gamelab import adaptation, agent, recovery
from gamelab.adaptation import Adaptation, Made, Store
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult, Usage
from gamelab.rules.minesweeper import EXPLODED
from test_adaptation import Author, wrong
from test_adaptation_mines import COMPUTE, Mines, Reader, middle, source as mines_source
from test_generated import source
from test_screen import Screen, screen_reading  # noqa: F401

SETTLING = {"timeout_s": 0.4, "interval_s": 0.05, "stable_s": 0.1}


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(agent, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "GeneratedAdapter", partial(adaptation.GeneratedAdapter, settling=SETTLING))


def ending(played):
    return played.record["outcome"], played.record["reason"]


class Costly:
    """A model whose every question costs `tokens` to ask."""

    def __init__(self, model, tokens):
        self.model, self.tokens = model, tokens

    async def complete(self, request):
        result = await self.model.complete(request)
        return TurnResult(text=result.text, usage=Usage(self.tokens, 10))


class Watched:
    def __init__(self, cancel_after=None):
        self.cancel, self.cancel_after = asyncio.Event(), cancel_after
        self.shots, self.inputs, self.open = 0, [], None

    async def seen(self, png):
        self.shots += 1

    async def sending(self, number, action, state, sent, observed_s):
        assert self.open is None
        self.open = (number, action, state, sent)

    async def sent(self):
        self.inputs.append(self.open)
        self.open = None
        if len(self.inputs) == self.cancel_after:
            self.cancel.set()


async def test_a_game_no_adapter_passes_for_goes_on_read_by_the_model_to_its_end():
    """Every adapter written is wrong. The game is not ended for it: the model reads each screen, the solver
    picks each move, and the game is won."""
    fake = Mines()
    reader = Reader(fake, ["def measure(picture):\n    return None\n"] * adaptation.MAX_GENERATIONS)
    played, made = await agent.play(fake, reader, "minesweeper", goal=GoalSpec("play_until_end"), budget=Budget(60, COMPUTE, 300))
    # the model read the last screen, the game's own win message with it
    assert ending(played) == ("goal_reached", "won"), (played.record, made)
    assert fake.over() and not any(cell == EXPLODED for row in fake.grid for cell in row)
    assert played.record["levels"] == {"L2": len(fake.taps), "L3": 0} and made["digest"] is None
    assert [failure["reason"] for failure in made["generation_failures"]] == ["no_adapter_passed"]
    assert made["generations"] == adaptation.MAX_GENERATIONS and not reader.modules and "failed" not in made
    # one reading for each screen: the first, and each move's result
    assert [kind for kind, _, _ in reader.asked].count("label") == len(fake.taps) + 1


async def test_an_adapter_is_tried_again_after_more_moves_read_by_the_model(tmp_path):
    fake = Screen()
    author = Author(fake, [wrong(fake)] * adaptation.MAX_GENERATIONS + [source(fake)])
    played, made = await agent.play(fake, author, "slide_merge", goal=GoalSpec("actions", 30), budget=Budget(60, 20, 120),
                                    store=Store(tmp_path))
    assert ending(played) == ("goal_reached", "actions"), (played.record, made)
    again = adaptation.EXPLORE_MOVES + agent.RETRY_LOOKS
    assert [move["level"] for move in played.moves] == ["L2"] * again + ["L3"] * (30 - again)
    assert made["generations"] == adaptation.MAX_GENERATIONS + 1 and len(made["generation_failures"]) == 1
    # the second writer is shown the moves played meanwhile
    writes = [context for kind, context, _ in author.asked if kind == "write"]
    assert len(writes[-1]["examples"]) == len(writes[0]["examples"]) + agent.RETRY_LOOKS
    assert Store(tmp_path).load("https://unregistered.example/", "slide_merge")[0] == source(fake).strip()


async def test_a_game_ends_when_its_questions_have_used_their_tokens():
    fake = Mines()
    reader = Costly(Reader(fake, [mines_source()]), 1000)
    played, made = await agent.play(fake, reader, "minesweeper", goal=GoalSpec("play_until_end"),
                                    budget=Budget(60, COMPUTE, 300, max_input_tokens=3000))
    assert ending(played) == ("budget_exhausted", "tokens"), (played.record, made)
    # the question that crossed the limit was the last: three readings, so two moves
    assert made["tokens"] == {"input": 3000, "output": 30} and made["questions"] == 3 and len(fake.taps) == 2
    # a game read by code asks nothing, and is not ended for tokens it does not spend
    enough = Mines()
    reader = Costly(Reader(enough, [mines_source()]), 1000)
    played, made = await agent.play(enough, reader, "minesweeper", goal=GoalSpec("play_until_end"),
                                    budget=Budget(60, COMPUTE, 300, max_input_tokens=8000))
    assert ending(played) == ("goal_reached", "play_until_end") and made["tokens"]["input"] == 8000


async def test_a_trial_move_the_rules_do_not_bear_out_sends_the_screen_to_the_model(tmp_path):
    """The adapter kept for the site reads one cell wrongly whatever it shows. Its first move does not do what
    the rules predict of the board it read: the model reads the next screens, the adapter fails on them and is
    written again, and the same game goes on by the new one."""
    fake = Screen()
    earlier = Screen()
    trace = await Adaptation(Author(earlier, []), "slide_merge").explore(earlier)
    Store(tmp_path).save(trace, Made(wrong(fake).strip(), 1, False, {"passed": True}))
    author = Author(fake, [source(fake)])
    played, made = await agent.play(fake, author, "slide_merge", goal=GoalSpec("actions", 12), budget=Budget(40, 20, 120),
                                    store=Store(tmp_path))
    assert ending(played) == ("goal_reached", "actions"), (played.record, made)
    levels = [(move["level"], move["effect"]) for move in played.moves]
    assert levels[0][0] == "L3" and ("L2", "confirmed") in levels and levels[-1] == ("L3", "confirmed")
    assert made["relearned"] >= 1 and made["generations"] == 2
    assert Store(tmp_path).load("https://unregistered.example/", "slide_merge")[0] == source(fake).strip()
    # no input was sent on a board the adapter misread without the screen being read again afterwards
    assert sum(effect != "confirmed" for _, effect in levels) <= agent.TRIAL_MOVES


async def test_the_games_own_end_screen_over_the_board_ends_the_game_as_the_model_saw_it():
    fake = Mines()
    fake.cover_after = 3
    reader = Reader(fake, recovering=[{"kind": "give_up", "end": "lost"}])
    played, made = await agent.play(fake, reader, "minesweeper", goal=GoalSpec("play_until_end"), budget=Budget(60, COMPUTE, 300))
    assert ending(played) == ("game_over", "end_screen") and played.record["detail"] == "as drawn", played.record
    assert len(fake.taps) == 3
    # a win message over the board is the goal reached when the family's own win is what was asked for
    won = Mines()
    won.cover_after = 3
    played, _ = await agent.play(won, Reader(won, recovering=[{"kind": "give_up", "end": "won"}]), "minesweeper",
                                 goal=GoalSpec("play_until_end"), budget=Budget(60, COMPUTE, 300))
    assert ending(played) == ("goal_reached", "won"), played.record
    # the rule model says whether a win is the goal: a tile asked for beyond the game's own is not reached by it
    playing = agent.Agent(Screen(), None, "slide_merge", goal=GoalSpec("reach_value", 4096), budget=Budget(1, 1, 1))
    playing.family = agent.catalog.family("slide_merge")
    assert not playing._won_meets_goal()
    playing.goal = GoalSpec("reach_value", 2048)
    assert playing._won_meets_goal()
    stuck = Mines()
    stuck.cover_after = 3
    played, made = await agent.play(stuck, Reader(stuck), "minesweeper", goal=GoalSpec("play_until_end"), budget=Budget(60, COMPUTE, 300))
    assert ending(played) == ("gave_up", "label_failed") and len(stuck.taps) == 3


async def test_moves_the_game_does_not_take_are_each_tried_once_and_the_game_given_up():
    fake = Screen()
    fake.no_effect = True
    author = Author(fake, [])
    author.unblocked = True
    played, made = await agent.play(fake, author, "slide_merge", goal=GoalSpec("actions", 5), budget=Budget(40, 20, 120))
    assert ending(played) == ("gave_up", "absent"), played.record
    assert len(fake.keys) == len(set(fake.keys)) >= 2 and {move["effect"] for move in played.moves} == {"absent"}
    assert [kind for kind, _, _ in author.asked].count("recover") == 2


async def test_each_input_is_told_to_the_watcher_and_a_cancelled_game_stops_before_the_next():
    fake = Mines()
    watch = Watched(cancel_after=9)
    played, made = await agent.play(fake, Reader(fake, [mines_source()]), "minesweeper", goal=GoalSpec("play_until_end"),
                                    budget=Budget(60, COMPUTE, 300), watch=watch, cancel=watch.cancel)
    assert ending(played) == ("budget_exhausted", "cancelled") and len(fake.taps) == 9 == played.record["actions_used"]
    assert [number for number, _, _, _ in watch.inputs] == list(range(1, 10)) and watch.shots == played.looks > 0
    assert [(sent["x"], sent["y"]) for _, _, _, sent in watch.inputs] == fake.taps
    assert all(state["grid"][action["cell"][0]][action["cell"][1]] == 9 for _, action, state, _ in watch.inputs)


async def test_a_budget_of_inputs_or_time_ends_the_game_with_its_reason():
    fake = Mines()
    played, _ = await agent.play(fake, Reader(fake, [mines_source()]), "minesweeper", goal=GoalSpec("play_until_end"),
                                 budget=Budget(4, COMPUTE, 300))
    assert ending(played) == ("budget_exhausted", "actions") and len(fake.taps) == 4
    slow = Mines()
    played, _ = await agent.play(slow, Reader(slow, [mines_source()]), "minesweeper", goal=GoalSpec("play_until_end"),
                                 budget=Budget(60, COMPUTE, 1e-6))
    assert ending(played) == ("budget_exhausted", "time") and not slow.taps


async def test_a_board_whose_pixels_never_rest_is_played_once_its_state_reads_the_same(monkeypatch):
    """A game that must be still before its result counts, on a site that animates while it waits: the state
    reading the same for a while is its rest. After the trial moves the game waits the time they took and
    looks once."""
    monkeypatch.setattr(agent, "RESTLESS_S", 0.15)
    monkeypatch.setattr(adaptation, "GeneratedAdapter", partial(
        adaptation.GeneratedAdapter, settling={**SETTLING, "timeout_s": 3.0, "still": True}))
    fake = Screen()
    capture, count = fake.capture, [0]

    async def shimmering(region=None):
        from io import BytesIO
        from PIL import Image
        count[0] += 1
        image = Image.open(BytesIO(await capture(region))).convert("RGB")
        left, top, _, _ = fake.measured.layout["board"]["rect"]
        image.putpixel((left + 1, top + 1), (count[0] % 250, 0, 0))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()
    fake.capture = shimmering
    author = Author(fake, [source(fake)])
    playing = agent.Agent(fake, author, "slide_merge", goal=GoalSpec("actions", 14), budget=Budget(40, 20, 120))
    played, made = await playing.run()
    assert ending(played) == ("goal_reached", "actions"), (played.record, made)
    assert played.record["levels"] == {"L2": adaptation.EXPLORE_MOVES, "L3": 14 - adaptation.EXPLORE_MOVES}
    assert not playing.settling.still and 0 < playing.settling.animation_s <= agent.ANIMATION_MAX_S


async def test_a_notice_before_the_first_board_of_a_site_played_before_is_got_past_and_the_kept_adapter_plays(tmp_path):
    first = Mines()
    await agent.play(first, Reader(first, [mines_source()]), "minesweeper", goal=GoalSpec("play_until_end"),
                     budget=Budget(60, COMPUTE, 300), store=Store(tmp_path))
    fake = Mines()
    fake.covered = True
    reader = Reader(fake, recovering=[{"kind": "tap", "at": middle(fake.button)}])
    played, made = await agent.play(fake, reader, "minesweeper", goal=GoalSpec("play_until_end"),
                                    budget=Budget(60, COMPUTE, 300), store=Store(tmp_path))
    assert ending(played) == ("goal_reached", "play_until_end"), (played.record, made)
    assert made["reused"] and played.record["levels"]["L2"] == 0
    # the kept input is sent again at the same notice with no question (the first game met none: one question here)
    assert [kind for kind, _, _ in reader.asked] == ["label", "recover"]


def test_the_agents_loop_loads_nothing_of_the_baseline():
    """The session, its screen environment, the registered profiles and the hand-written readers are the
    baseline's (PLAN §10, G6): the agent's loop on a browser page or a phone runs without any of them."""
    import subprocess
    import sys
    listed = subprocess.run([sys.executable, "-c", "import sys, gamelab.agent, gamelab.environments.web, "
                             "gamelab.environments.android; print(' '.join(sorted(sys.modules)))"],
                            capture_output=True, text=True, check=True).stdout.split()
    baseline = ("session", "play", "baseline", "vision", "perception", "geometry", "profiles", "discovery", "registered",
                "environments.screen", "adapters.reference")
    ours = [name.removeprefix("gamelab.") for name in listed if name.startswith("gamelab.")]
    assert ours and not [name for name in ours if name in baseline or name.endswith(".reader")]
