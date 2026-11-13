"""The game plugin's one loop (framework/plugins/game/server/src/game/session.py) against the replay
environment and a stubbed clock: the safety conditions of docs/plans/2026-11-09-generic-game-plugin-
implementation.md §6.3 and the distinct outcomes of §6.7.

Regression this prevents: a move sent twice after its receipt was lost or the worker restarted, input
sent after cancel or a lost lease, a released input reported as released when the release was never
confirmed, an unread cell played as empty, a delivered input reported as a reached goal, a plan with
several steps or a meta action the person did not allow carried out anyway."""
from __future__ import annotations

from copy import deepcopy
import json
from dataclasses import replace

import pytest
from gamelab import catalog
from gamelab.contracts import ActionPlan, Budget, GoalSpec
from gamelab.environments.replay import ReplayEnvironment
from gamelab.session import (ACCEPT_CONSENT, CLOSE_COVER, CONSENT_DECLINED, DECLINE_CONSENT, INPUT_REFUSED, MAX_COVERS,
                          MAX_RECOVERIES, MAX_REFUSED, START_GAME, GameSession)

# a session record is kept in the task's state: small JSON, whatever the game's length
RECORD_LIMIT_BYTES = 16_000
TARGET = "replay-tab"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Guards:
    def __init__(self) -> None:
        self.stopped = False
        self.lease = True

    async def cancelled(self) -> bool:
        return self.stopped

    async def lease_held(self) -> bool:
        return self.lease


class Run:
    """One session's world: the family, the replay game, the saved records and the guards."""

    def __init__(self, family_id="slide_merge", *, seed=3, goal=None, budget=None, policy=None,
                 faults=None, state=None, family=None) -> None:
        self.clock = Clock()
        self.guards = Guards()
        self.family = family or catalog.family(family_id)
        self.env = ReplayEnvironment(self.family, seed=seed, clock=self.clock, target=TARGET,
                                     faults=faults or {}, state=state)
        self.saved: list[dict] = []
        self.goal = goal or GoalSpec(objective="play_until_end")
        self.budget = budget or Budget(max_actions=2000, compute=50)
        self.policy = policy or self.family.baseline_policy

    async def save(self, record: dict) -> None:
        self.saved.append(json.loads(json.dumps(record)))   # what the task state would hold

    def session(self, record=None) -> GameSession:
        if record is not None:
            return GameSession(self.family, self.env, record, clock=self.clock, save=self.save, guards=self.guards)
        return GameSession.start(self.family, self.env, goal=self.goal, budget=self.budget, policy=self.policy,
                                 target=TARGET, clock=self.clock, save=self.save, guards=self.guards)

    async def run(self, record=None) -> dict:
        return await self.session(record).run()


def single(n=1) -> GoalSpec:
    return GoalSpec(objective="actions", target_value=n)


@pytest.mark.parametrize("family_id", ["block_placement", "slide_merge"])
async def test_a_whole_game_runs_to_its_end_in_the_one_loop(family_id):
    run = Run(family_id)
    record = await run.run()
    assert record["outcome"] == "game_over"
    assert record["actions_used"] == record["confirmed_actions"] == len(run.env.executed) > 1
    assert len(set(run.env.executed)) == len(run.env.executed)
    assert record["intent"] is None and record["cleanup"] == "none"
    assert len(json.dumps(record)) < RECORD_LIMIT_BYTES
    assert max(len(json.dumps(saved)) for saved in run.saved) < RECORD_LIMIT_BYTES


@pytest.mark.parametrize("seed", [3, 4])
async def test_a_minesweeper_game_ends_as_its_hidden_mines_say_and_play_never_sees_them(seed):
    """A game with hidden information plays from the screen alone: no observation the session reads holds where
    the mines lie, every opened cell is a confirmed move, and the end is the one the hidden mines make (every
    safe cell open, or a mine opened). Regression: the replay handed its whole state, mines and all, to the
    session as what the screen showed."""
    run = Run("minesweeper", seed=seed, budget=Budget(max_actions=200, compute=20000), policy="solver")
    observe = run.env.observe
    seen = []

    async def watched(target, purpose):
        observation = await observe(target, purpose)
        seen.append(observation.public_facts.get("state", {}))
        return observation

    run.env.observe = watched
    record = await run.run()
    assert seen and not any("mines" in state for state in seen)
    grid, mines = run.env.truth["grid"], {tuple(cell) for cell in run.env.truth["mines"]}
    lost = any(12 in row for row in grid)
    assert (record["outcome"], lost) in (("goal_reached", False), ("game_over", True)), record["outcome"]
    assert record["actions_used"] == record["confirmed_actions"] == len(run.env.executed)
    if not lost:
        assert all((r, c) in mines or cell <= 8 for r, row in enumerate(grid) for c, cell in enumerate(row))


async def test_a_single_move_is_the_same_loop_with_another_stop():
    run = Run(goal=single())
    record = await run.run()
    assert record["outcome"] == "goal_reached" and len(run.env.executed) == 1


async def test_the_intent_is_saved_before_the_input_is_sent():
    run = Run(goal=single(3))
    seen = []
    original = run.env.execute

    async def execute(prepared, context):
        seen.append((prepared.action_id, run.saved[-1]["intent"]["action_id"]))
        return await original(prepared, context)

    run.env.execute = execute
    await run.run()
    assert seen and all(sent == saved for sent, saved in seen)


@pytest.mark.parametrize(("fault", "effect"), [("lost_receipt", "confirmed"), ("not_delivered", "absent")])
async def test_an_unknown_delivery_is_never_sent_again(fault, effect):
    run = Run(goal=single(2), faults={1: fault})
    record = await run.run()
    assert len(set(run.env.executed)) == len(run.env.executed), "an action id was sent twice"
    first = record["history"][0]
    # delivery, effect and goal stay three separate findings
    assert (first["delivery"], first["delivered_steps"], first["effect"]) == ("unknown", None, effect)
    assert first["goal"] == "not_reached"
    assert record["outcome"] == "goal_reached"


class Crash(BaseException):
    """The worker dies mid-input: not an error the session may catch and settle."""


@pytest.mark.parametrize(("remembers", "delivery"), [(True, "delivered"), (False, "unknown")])
async def test_a_restart_with_an_input_in_flight_reconciles_without_sending_it(remembers, delivery):
    run = Run(goal=single(2))
    run.env.remembers = remembers
    original = run.env.execute

    async def dies_after_sending(prepared, context):
        await original(prepared, context)
        raise Crash

    run.env.execute = dies_after_sending
    with pytest.raises(Crash):
        await run.run()
    in_flight = run.saved[-1]
    assert in_flight["intent"] is not None
    sent = in_flight["intent"]["action_id"]
    run.env.execute = original
    record = await run.run(in_flight)
    assert run.env.executed.count(sent) == 1
    first = record["history"][0]
    assert (first["action_id"], first["delivery"], first["effect"]) == (sent, delivery, "confirmed")
    assert run.env.neutralized >= 1, "resuming releases any input left held"
    assert record["outcome"] == "goal_reached"


@pytest.mark.parametrize(("closes", "outcome"), [(1, ("goal_reached", "actions")),
                                                 (MAX_COVERS + 1, ("needs_recovery", "board_covered"))],
                         ids=["closed once", "keeps covering"])
async def test_a_cover_over_the_board_is_closed_as_a_saved_counted_input(closes, outcome):
    """Something that is not the game over the board (a dialog, a notice) is closed by the session
    itself, an input saved before it is sent and counted against the budget like a move; play goes on
    once the board shows. A page that keeps covering it ends the session after MAX_COVERS closes.
    Regression: a popup over the board ended the session as unreadable, waiting for a person."""
    run = Run(goal=single())
    run.env.covered = closes
    execute = run.env.execute
    intents = []

    async def saved_first(prepared, context):
        intents.append(run.saved[-1]["intent"]["plan"]["steps"])
        return await execute(prepared, context)
    run.env.execute = saved_first
    record = await run.run()
    assert (record["outcome"], record["reason"]) == outcome
    closed = min(closes, MAX_COVERS)
    assert intents[:closed] == [[{"kind": CLOSE_COVER}]] * closed
    assert [entry["kind"] for entry in record["history"]][:closed] == [CLOSE_COVER] * closed
    assert record["covers"] == closed and record["actions_used"] == len(run.env.executed)
    assert record["confirmed_actions"] == (1 if closes == 1 else 0)


@pytest.mark.parametrize(("press", "asked"), [(DECLINE_CONSENT, []), (ACCEPT_CONSENT, [ACCEPT_CONSENT]),
                                              (START_GAME, [])])
async def test_what_stands_in_front_of_the_game_is_got_past_and_only_agreeing_is_asked(press, asked):
    """A consent request is refused at its refusing button and the game's start screen started, both named in
    the game's approval; a consent request that offers no refusal is agreed to only once the person, asked
    then, allows it. Regression: a site that asked for consent before its game stopped the session as covered."""
    run = Run(goal=single())
    run.env.covered, run.env.cover_press = 1, press
    record = await run.run()
    assert record["outcome"] == "goal_reached" and run.env.asked == asked
    assert [h["kind"] for h in record["history"]] == [press, "slide"]


async def test_a_cover_input_refused_before_sending_is_not_a_cover_tried():
    """A close the surface refused before sending any of it (an advertisement reloading) never reached the
    cover: it does not use up MAX_COVERS. Regression: on a page with reloading advertisements a refused tap on a
    consent request's refusal counted as one of the session's three covers."""
    run = Run(goal=single(), faults={1: "rejected"})
    run.env.covered = 1
    record = await run.run()
    assert record["outcome"] == "goal_reached" and record["covers"] == 1
    assert [h["kind"] for h in record["history"]] == [CLOSE_COVER, CLOSE_COVER, "slide"]


async def test_the_person_refusing_a_sites_terms_ends_the_game_with_nothing_pressed():
    """Asked to agree to terms the site offers no refusal of, the person may refuse: nothing is pressed and the
    game ends as theirs to stop, saying why."""
    run = Run(goal=single())
    run.env.covered, run.env.cover_press, run.env.refuses = 1, ACCEPT_CONSENT, frozenset({ACCEPT_CONSENT})
    record = await run.run()
    assert (record["outcome"], record["reason"]) == ("user_stopped", CONSENT_DECLINED) and run.env.executed == []


async def test_a_start_screen_over_a_game_already_played_is_not_pressed():
    """The game's start button starts play only before a board was read: over a game played it may start
    another, so the session stops there with nothing pressed."""
    run = Run(goal=single())
    session = run.session()
    session.record["phase"] = "running"
    run.env.covered, run.env.cover_press = 1, START_GAME
    record = await session.run()
    assert (record["outcome"], record["reason"]) == ("needs_recovery", "board_covered") and run.env.executed == []


async def test_a_session_calibrates_until_it_reads_its_board_and_plays_only_after():
    """Until a look reads the board the session is calibrating: what it sends then is its own (closing a
    cover), never a move. The first board it reads makes it running, saved before any move is planned,
    and a resume of either phase goes on from it."""
    run = Run(goal=single())
    run.env.covered = 1
    execute = run.env.execute
    sent_in = []

    async def noted(prepared, context):
        sent_in.append((prepared.steps[0]["kind"], run.saved[-1]["phase"]))
        return await execute(prepared, context)

    run.env.execute = noted
    record = await run.run()
    assert record["outcome"] == "goal_reached"
    assert run.saved[0]["phase"] == "calibrating"
    assert sent_in[0] == (CLOSE_COVER, "calibrating") and sent_in[1][1] == "running"
    assert [kind for kind, _ in sent_in[1:]] != [CLOSE_COVER] * (len(sent_in) - 1)


async def test_a_restart_with_a_close_in_flight_does_not_send_it_again():
    """A close of a cover whose worker died after sending it is looked up, not sent again; the screen
    then says whether the board shows."""
    run = Run(goal=single())
    run.env.covered = 1
    original = run.env.execute

    async def dies_after_sending(prepared, context):
        await original(prepared, context)
        raise Crash

    run.env.execute = dies_after_sending
    with pytest.raises(Crash):
        await run.run()
    in_flight = run.saved[-1]
    sent = in_flight["intent"]["action_id"]
    run.env.execute = original
    record = await run.run(in_flight)
    assert run.env.executed.count(sent) == 1 and record["outcome"] == "goal_reached"
    assert record["history"][0]["kind"] == CLOSE_COVER and record["confirmed_actions"] == 1


async def test_cancel_stops_new_input_and_releases_what_is_held():
    run = Run()
    original = run.env.execute

    async def execute(prepared, context):
        got = await original(prepared, context)
        if len(run.env.executed) == 2:
            run.guards.stopped = True
        return got

    run.env.execute = execute
    record = await run.run()
    assert record["outcome"] == "user_stopped" and len(run.env.executed) == 2
    assert run.env.neutralized == 1 and record["cleanup"] == "confirmed"


async def test_a_release_that_is_not_confirmed_stays_cleanup_pending():
    run = Run()
    run.env.neutralize_result = "unknown"
    run.guards.stopped = True
    record = await run.run()
    assert record["outcome"] == "user_stopped" and run.env.executed == []
    assert record["cleanup"] == "pending"


async def test_a_lost_lease_stops_input():
    run = Run()
    original = run.env.execute

    async def execute(prepared, context):
        got = await original(prepared, context)
        run.guards.lease = False
        return got

    run.env.execute = execute
    record = await run.run()
    assert (record["outcome"], record["reason"]) == ("needs_recovery", "lease_lost")
    assert len(run.env.executed) == 1


@pytest.mark.parametrize("fault", [None, "wrong_effect"])
async def test_a_confirmed_result_is_the_next_moves_only_look(fault):
    """One look per move: a result that is the predicted move backs the next decision and its input
    at once, after the game's animation time and without waiting for it to stay still. A result that
    was not the predicted move is never carried over: the next move reads the screen afresh.
    Regression: each move read the screen three times over (result, decision, input), each waiting
    for two equal frames."""
    from gamelab.session import SettlingPolicy
    run = Run(goal=single(3), budget=Budget(max_actions=10, compute=50, max_seconds=60),
              faults={1: fault} if fault else {})
    session = run.session()
    session.settling = SettlingPolicy(timeout_s=2, interval_s=0.1, stable_s=0.2, animation_s=0.05)
    waits, purposes = [], []

    async def wait(delay):
        waits.append(delay)
        run.clock.now += delay
    observe = run.env.observe

    async def counted(target, purpose):
        purposes.append(purpose)
        return await observe(target, purpose)
    session.wait, run.env.observe = wait, counted
    record = await session.run()
    assert record["outcome"] == "goal_reached" and len(set(run.env.executed)) == len(run.env.executed)
    if fault is None:
        # only the first look waits for the screen to stay still
        assert purposes == ["decide"] * 3 + ["verify"] * 3 and waits == [0.1, 0.1, 0.05, 0.05, 0.05]
    else:
        assert "input" not in purposes and purposes.count("decide") > 2


async def test_a_game_that_must_be_still_takes_a_result_only_once_its_pixels_rest():
    """A game whose moves leave effects fading over the board (a cleared line, a new tray sliding in)
    shows the predicted state while its pixels still change: a result counts only once two looks in a
    row show the same pixels where input is checked, so the next input is not made on moving pixels.
    Regression: live Block Blast refused about one input in four as visual_precondition_failed, each
    costing seconds to read again."""
    from gamelab.session import SettlingPolicy
    run = Run(goal=single(2), budget=Budget(max_actions=10, compute=50, max_seconds=60))
    session = run.session()
    session.settling = SettlingPolicy(timeout_s=2, interval_s=0.1, stable_s=0.2, still=True)
    observe = run.env.observe
    pictures = iter(["fading-1", "fading-2", "rest", "rest"])
    verified = []

    async def wait(delay):
        run.clock.now += delay

    async def fading(target, purpose):
        seen = await observe(target, purpose)
        if purpose == "verify" and run.env.executed == ["a1"]:
            seen = replace(seen, image_ref=next(pictures))
            verified.append(seen)
        return seen
    session.wait, run.env.observe = wait, fading
    record = await session.run()
    assert record["outcome"] == "goal_reached" and record["confirmed_actions"] == 2
    assert [seen.image_ref for seen in verified] == ["fading-1", "fading-2", "rest", "rest"]


async def test_a_game_whose_pixels_never_rest_is_played_once_its_state_holds():
    """A board whose tiles keep animating while the game waits never shows the same pixels twice: its state,
    read the same for RESTLESS_S, is taken as settled. Regression: a site with animated tiles was looked at
    222 times and ended as unreadable before a single move."""
    from gamelab.session import RESTLESS_S, SettlingPolicy
    run = Run(goal=single(2), budget=Budget(max_actions=10, compute=50, max_seconds=60))
    session = run.session()
    session.settling = SettlingPolicy(timeout_s=2, interval_s=0.1, stable_s=0.2, still=True)
    observe = run.env.observe
    looks = []

    async def wait(delay):
        run.clock.now += delay

    async def restless(target, purpose):
        seen = await observe(target, purpose)
        looks.append(purpose)
        return replace(seen, image_ref=f"frame-{len(looks)}")
    session.wait, run.env.observe = wait, restless
    record = await session.run()
    assert record["outcome"] == "goal_reached" and record["confirmed_actions"] == 2
    assert len(looks) <= 3 * (RESTLESS_S / 0.1 + 2)


async def test_trial_moves_time_the_games_animation_and_later_moves_look_once():
    """A profile made in the session starts by waiting for its moves' pixels to rest (`still`), which costs a
    look or more a move; its trial moves time how long that takes, and after them each move waits the
    longest of those times and is checked on one look. A restart keeps the timed settling."""
    from gamelab.session import ANIMATION_MARGIN_S, SettlingPolicy
    run = Run(goal=single(3), budget=Budget(max_actions=10, compute=50, max_seconds=60))
    session = GameSession.start(run.family, run.env, goal=run.goal, budget=run.budget, policy=run.policy,
                                target=TARGET, clock=run.clock, save=run.save, guards=run.guards, trial=2)
    session.settling = SettlingPolicy(timeout_s=2, interval_s=0.1, stable_s=0.2, still=True)
    observe = run.env.observe
    # the verifying looks at each move: the first trial move rests one look in, the second two looks in
    pictures = {"a1": iter(["moving", "rest-1", "rest-1"]), "a2": iter(["moving", "moving-2", "rest-2", "rest-2"])}
    looks = []

    async def wait(delay):
        run.clock.now += delay

    async def animated(target, purpose):
        seen = await observe(target, purpose)
        if purpose == "verify":
            moved = run.env.executed[-1]
            looks.append(moved)
            seen = replace(seen, image_ref=next(pictures[moved]) if moved in pictures else "rest-3")
        return seen
    session.wait, run.env.observe = wait, animated
    record = await session.run()
    assert record["outcome"] == "goal_reached" and record["confirmed_actions"] == 3
    assert record["rested_s"] == [0.1, 0.2]
    assert record["settling"]["still"] is False and record["settling"]["animation_s"] == pytest.approx(0.2 + ANIMATION_MARGIN_S)
    assert looks.count("a3") == 1
    assert run.session(deepcopy(record)).settling == SettlingPolicy(**record["settling"])


async def test_unknown_cells_are_never_played_as_empty():
    """A board never read whole is never played; with nothing sent it ends as a game that cannot be
    played here, not as one waiting on recovery."""
    run = Run("block_placement")
    run.env.hidden_observations = 10_000
    record = await run.run()
    assert run.env.executed == []
    assert (record["outcome"], record["reason"], record["phase"]) == ("unsupported", "unreadable", "ended")


@pytest.mark.parametrize(("left", "outcome"), [([[r, c] for r in range(3) for c in range(3)], ("game_over", "terminal")),
                                               ([[0, 0]], ("needs_recovery", "unreadable"))],
                         ids=["no piece left fits", "a piece left fits"])
async def test_a_result_with_its_tray_covered_ends_the_game_only_when_the_rules_do(left, outcome):
    """An end banner over the tray leaves it unread after a move; the game is over when the pieces that
    move left fit nowhere on the board the screen shows, and otherwise the unread tray is waited for.
    Either way the unread tray never decides a move. Regression: with no end-screen sample a Block Blast
    game that ended this way stopped as needs_recovery."""
    board = [[0 if row == col else 1 for col in range(8)] for row in range(8)]
    run = Run("block_placement", state={"board": board, "tray": [[[0, 0]], left, None], "score": 0, "scene": "playing"})
    observe = run.env.observe

    async def banner(target, purpose):
        seen = await observe(target, purpose)
        if run.env.executed:
            seen.public_facts["state"]["tray"] = [[], [], []]
        return seen

    run.env.observe = banner
    record = await run.run()
    assert (record["outcome"], record["reason"]) == outcome
    assert len(run.env.executed) == 1
    assert record["history"][-1]["terminal"] is (True if outcome[0] == "game_over" else None)


async def test_a_stale_plan_is_dropped_before_input():
    """§6.3-3: the surface changed between deciding and sending, so the plan made on the old one
    is thrown away and a new one is made on what is there now."""
    run = Run(goal=single())
    run.env.refused_ready, run.env.epoch_shifts_before_input = 1, 1
    record = await run.run()
    assert record["outcome"] == "goal_reached" and record["discarded_plans"] == 1
    assert run.env.stale_inputs == 0


async def test_changed_reader_geometry_discards_a_plan_even_when_cells_match():
    """A resize or recalibration cannot reuse a move's old coordinates on an unchanged board."""
    run = Run(goal=single())
    run.env.refused_ready = 10
    observe = run.env.observe

    async def moved(target, purpose):
        seen = await observe(target, purpose)
        return replace(seen, geometry_ref="before" if purpose == "decide" else "moved")

    run.env.observe = moved
    record = await run.run()
    assert (record["outcome"], record["reason"]) == ("needs_recovery", "surface_unsettled")
    assert run.env.executed == []


@pytest.mark.parametrize("changed_at", ["decide", "input", "verify"])
async def test_another_target_is_neither_played_nor_counted_as_effect(changed_at):
    """A misbound adapter cannot play another tab or use its board to confirm this task's move."""
    run = Run(goal=single())
    run.env.refused_ready = 10 if changed_at == "input" else 0
    observe = run.env.observe

    async def switched(target, purpose):
        seen = await observe(target, purpose)
        return replace(seen, target="other-tab") if purpose == changed_at else seen

    run.env.observe = switched
    record = await run.run()
    # another tab on the first look: no board of this task was ever read, so nothing waits on recovery
    expected = "unsupported" if changed_at == "decide" else "needs_recovery"
    assert (record["outcome"], record["reason"]) == (expected, "target_changed")
    assert len(run.env.executed) == (1 if changed_at == "verify" else 0)
    assert record["confirmed_actions"] == 0


async def test_the_budget_ends_the_game_by_actions_and_by_clock():
    run = Run(budget=Budget(max_actions=3, compute=50))
    record = await run.run()
    assert record["outcome"] == "budget_exhausted" and len(run.env.executed) == 3

    run = Run(budget=Budget(max_actions=100, compute=50, max_seconds=2.5))
    original = run.env.execute

    async def slow(prepared, context):
        run.clock.now += 1.0
        return await original(prepared, context)

    run.env.execute = slow
    record = await run.run()
    assert record["outcome"] == "budget_exhausted" and len(run.env.executed) == 3


@pytest.mark.parametrize("family_id", ["block_placement", "minesweeper", "slide_merge"])
async def test_a_move_the_game_ignored_is_not_chosen_again_from_that_state(family_id):
    """A delivered move that changed nothing (the game did not take it) is left out of the next plan
    from the same state, so play goes on with another move. Regression: a near-zero drag the site
    ignored was chosen three times in a row and ended the game as needs_recovery (2026-11-11)."""
    run = Run(family_id, goal=single(2), faults={1: "ignored"})
    record = await run.run()
    assert record["outcome"] == "goal_reached" and record["confirmed_actions"] == 2, record
    effects = [entry["effect"] for entry in record["history"]]
    assert effects == ["absent", "confirmed", "confirmed"]
    assert record["ineffective"] is None
    sent = {saved["intent"]["action_id"]: saved["intent"]["plan"]["steps"][0] for saved in run.saved if saved.get("intent")}
    assert sent["a1"] != sent["a2"] and len(set(run.env.executed)) == 3


@pytest.mark.parametrize("fault", ["wrong_effect", "ignored"])
async def test_a_trial_move_that_does_not_do_what_the_rules_say_ends_play_before_another_input(fault):
    """A profile made in the session from a model's look (a value misread, a key the page ignores) shows
    in its first moves: one of them whose effect is not the predicted one ends the game at once, where a
    registered profile's game goes on and plays from the screen."""
    run = Run(faults={2: fault})
    session = GameSession.start(run.family, run.env, goal=single(4), budget=run.budget, policy=run.policy,
                                target=TARGET, clock=run.clock, save=run.save, guards=run.guards, trial=3)
    record = await session.run()
    assert record["outcome"] == "unsupported" and record["reason"] == "profile_unconfirmed"
    assert len(run.env.executed) == 2
    registered = Run(faults={2: fault}, goal=single(4))
    assert (await registered.run())["outcome"] == "goal_reached" and len(registered.env.executed) > 2


async def test_a_trial_input_the_surface_refused_before_sending_does_not_end_play():
    """An input the browser refused before sending it (the page changed under it: an advertisement) never
    reached the game, so it says nothing about the profile: the trial goes on with the next move.
    Regression: live play ended after its second move, a refused one, as an unconfirmed profile."""
    run = Run(faults={2: "rejected"})
    session = GameSession.start(run.family, run.env, goal=single(4), budget=run.budget, policy=run.policy,
                                target=TARGET, clock=run.clock, save=run.save, guards=run.guards, trial=3)
    record = await session.run()
    assert record["outcome"] == "goal_reached" and record["trial"] == 0


async def test_inputs_refused_before_sending_are_borne_longer_than_moves_without_effect():
    """An input the surface refused before sending any of it never reached the game: the next is planned from a
    fresh look and only a page that refuses MAX_REFUSED in a row ends play. Regression: on a page whose
    advertisements reload beside the board, three refusals in a row ended the game as moves without effect."""
    refusals = MAX_RECOVERIES + 1
    run = Run(goal=single(3), faults={n: "rejected" for n in range(2, 2 + refusals)})
    record = await run.run()
    assert record["outcome"] == "goal_reached" and record["refused"] == 0
    assert len(run.env.executed) == 3 + refusals

    run = Run(faults={n: "rejected" for n in range(1, MAX_REFUSED + 2)})
    record = await run.run()
    assert (record["outcome"], record["reason"]) == ("needs_recovery", INPUT_REFUSED)
    assert len(run.env.executed) == MAX_REFUSED


async def test_an_input_refused_before_sending_is_not_waited_for():
    """An input the surface refused before sending any of it has no result to wait for: the next move is planned
    from a fresh look at once. Regression: on play2048.co each refusal (an advertisement changing the page) was
    followed by the move's animation wait and looks until the settling timeout, about 11 s, for a result that
    could not come."""
    from gamelab.session import SettlingPolicy
    run = Run(goal=single(2), faults={1: "rejected"})
    session = run.session()
    session.settling = SettlingPolicy(timeout_s=10, interval_s=0.15, stable_s=0.25, animation_s=0.45)
    observe, looks = run.env.observe, []

    async def wait(delay):
        run.clock.now += delay

    async def looking(target, purpose):
        looks.append((len(run.env.executed), purpose))
        return await observe(target, purpose)
    session.wait, run.env.observe = wait, looking
    record = await session.run()
    assert record["outcome"] == "goal_reached" and len(run.env.executed) == 3
    assert "verify" not in {purpose for sent, purpose in looks if sent == 1}, looks


@pytest.mark.parametrize("family_id", ["block_placement", "minesweeper", "slide_merge"])
async def test_repeated_inconsistent_effects_end_in_recovery(family_id):
    """Each family's own stray change (its core's `stray_state`: the replay knows no board) is seen as an
    effect the move did not have. Regression this prevents: a family whose stray change the rules take for
    the move, so the replay's recovery checks pass without trying recovery."""
    run = Run(family_id, faults={n: "wrong_effect" for n in range(1, MAX_RECOVERIES + 2)})
    record = await run.run()
    assert record["outcome"] == "needs_recovery" and record["reason"] == "inconsistent"
    assert len(run.env.executed) == MAX_RECOVERIES


class SeveralSteps:
    """A family whose policy answers with a sequence of moves (a later family's long plan)."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def propose(self, state, goal, policy, compute, excluded=()):
        plan = self.inner.propose(state, goal, policy, compute, excluded)
        return ActionPlan(**{**plan.__dict__, "steps": plan.steps * 2})


async def test_a_plan_of_several_steps_is_refused_as_unsupported():
    run = Run(family=SeveralSteps(catalog.family("slide_merge")))
    record = await run.run()
    assert (record["outcome"], record["reason"]) == ("unsupported", "multi_step_plan")
    assert run.env.executed == []


WON = {"grid": [[2048, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 2]], "score": 20000, "scene": "won"}


async def test_a_meta_action_the_goal_does_not_allow_is_asked_for_only_at_its_button():
    """A win message before the goal offers to keep playing: with its button on the screen the person is
    asked (the input approval then names the meta action) and play goes on; with no button seen, play
    stops there with no input, and the person is told why."""
    run = Run(state=WON)
    record = await run.run()
    assert (record["outcome"], record["reason"]) == ("unsupported", "button_unseen")
    assert run.env.executed == [] and run.env.asked == []

    run = Run(state=WON, goal=single(2))
    run.env.buttons = frozenset({"continue_after_win"})
    record = await run.run()
    assert record["outcome"] == "goal_reached" and run.env.asked == ["continue_after_win"]
    assert [h["kind"] for h in record["history"]] == ["continue_after_win", "slide"]
    assert record["allowed_meta"] == ["continue_after_win"]

    allowed = GoalSpec(objective="actions", target_value=2, allowed_meta_actions=frozenset({"continue_after_win"}))
    run = Run(state=WON, goal=allowed)
    record = await run.run()
    assert record["outcome"] == "goal_reached"
    assert [h["kind"] for h in record["history"]] == ["continue_after_win", "slide"]


async def test_a_goal_naming_a_meta_action_the_family_lacks_is_refused():
    run = Run(goal=GoalSpec(objective="play_until_end", allowed_meta_actions=frozenset({"undo"})))
    record = await run.run()
    assert (record["outcome"], record["reason"]) == ("unsupported", "meta_action_unknown")
    assert run.env.executed == []


async def test_a_state_with_no_move_that_is_not_over_ends_in_recovery():
    """A readable screen where the family finds no move yet the game is not over (a block tray
    caught between pieces) must not spin forever re-reading the screen: it ends in recovery after
    the same bounded number of reads as an unreadable screen."""
    between_trays = {"board": [[0] * 8 for _ in range(8)], "tray": [None, None, None], "score": 0}
    run = Run("block_placement", state=between_trays)
    record = await run.run()
    assert run.env.executed == []
    assert (record["outcome"], record["reason"]) == ("needs_recovery", "unreadable")


async def test_settling_retries_transient_reads_and_delayed_effect_without_redelivery():
    """An animation may first show the old board or no board; one input is verified after it settles."""
    from gamelab.session import SettlingPolicy
    run = Run(goal=single(), budget=Budget(max_actions=1, compute=50, max_seconds=10))
    session = run.session()
    session.settling = SettlingPolicy(timeout_s=2, interval_s=0.1, stable_s=0.2)
    async def wait(delay):
        run.clock.now += delay
    session.wait = wait
    observe = run.env.observe
    old, reads = None, []
    async def animated(target, purpose):
        nonlocal old
        seen = await observe(target, purpose)
        if purpose == "decide":
            old = seen
        if purpose == "verify":
            reads.append(run.clock.now)
            if len(reads) <= 2:
                return replace(seen, public_facts={})
            if len(reads) <= 6:
                return replace(seen, public_facts=old.public_facts)
        return seen
    run.env.observe = animated
    record = await session.run()
    assert record["outcome"] == "goal_reached" and record["confirmed_actions"] == 1
    assert len(run.env.executed) == 1 and len(reads) == 7      # the predicted result needs no second frame
    assert all(b > a for a, b in zip(reads, reads[1:]))


@pytest.mark.parametrize("stop", ["cancel", "time", "unstable", "held"])
async def test_settling_is_bounded_and_never_confirms_held_or_unstable_state(stop):
    """Observation waits honor cancellation/time, and an unreleased piece is never a completed move."""
    from gamelab.session import SettlingPolicy
    run = Run(goal=single(), budget=Budget(max_actions=1, compute=50, max_seconds=10))
    session = run.session()
    session.settling = SettlingPolicy(timeout_s=1, interval_s=0.1, stable_s=0.2)
    observe, execute = run.env.observe, run.env.execute
    reads = []
    async def wait(delay):
        run.clock.now += delay
        if run.env.executed:
            if stop == "cancel":
                run.guards.stopped = True
            if stop == "time":
                run.clock.now += 10
    async def observe_after(target, purpose):
        seen = await observe(target, purpose)
        if purpose == "verify":
            reads.append(seen)
            return replace(seen, public_facts={})
        return seen
    async def held(prepared, context):
        receipt = await execute(prepared, context)
        if stop == "held":
            from gamelab.contracts import Neutral
            run.env.neutralize_result = "unknown"
            return replace(receipt, input_neutral=Neutral.UNKNOWN)
        return receipt
    session.wait, run.env.observe, run.env.execute = wait, observe_after, held
    record = await session.run()
    assert record["confirmed_actions"] == 0 and len(run.env.executed) == 1
    assert len(reads) <= 12
    if stop == "held":
        assert not reads and record["cleanup"] == "pending" and record["intent"]
    elif stop == "cancel":
        assert record["outcome"] == "user_stopped"
    else:
        assert record["outcome"] == "budget_exhausted"


async def test_a_win_notice_beside_a_readable_board_is_found_and_kept_playing_after_asking():
    """A game may show its win notice beside a board that stays readable: the board reads as play going on
    and takes no key. The move without effect has the whole screen looked at once; its notice then is the
    win scene, the person is asked, and its button keeps the same game going. A screen showing nothing that
    stops play is looked at once per board, and the moves without effect count as before.
    Regression: live play on the official 2048 site stopped at its win notice as three moves without effect."""
    run = Run(goal=single(2))
    run.env.beside, run.env.buttons = "won", frozenset({"continue_after_win"})
    record = await run.run()
    assert record["outcome"] == "goal_reached" and run.env.asked == ["continue_after_win"]
    assert [h["kind"] for h in record["history"]] == ["slide", "continue_after_win", "slide"]
    assert [h["effect"] for h in record["history"]] == ["absent", "confirmed", "confirmed"]
    assert run.env.stalled_looks == 1

    run = Run(faults={n: "ignored" for n in range(1, MAX_RECOVERIES + 1)})
    record = await run.run()
    assert (record["outcome"], record["reason"]) == ("needs_recovery", "absent")
    assert run.env.stalled_looks == 1 and len(run.env.executed) == MAX_RECOVERIES


async def test_a_move_without_effect_the_screen_accounts_for_is_no_failed_recovery():
    """Moves around a win are often unreadable while its tile and notice animate: a move the notice took counts
    as a third unconfirmed move only when the whole screen shows nothing that stops play.
    Regression: on the official 2048 site the move the win notice blocked was the third unconfirmed one, and
    the game ended right after its keep-playing button was pressed."""
    run = Run(goal=single(2))
    run.env.beside, run.env.buttons = "won", frozenset({"continue_after_win"})
    session = run.session()
    session.record["recoveries"] = MAX_RECOVERIES - 1
    record = await session.run()
    assert record["outcome"] == "goal_reached" and run.env.asked == ["continue_after_win"]
