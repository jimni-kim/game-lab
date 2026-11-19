"""The work a person asked for (PLAN §25, R1 to R3): levels cleared one after another on the same screen with
nothing started again between them, a lost level that ends the work unless another try was allowed, stop
conditions a model says are met, things beside the board used only when allowed, one budget for the whole work,
and what is handed back. The screens are the fake minesweeper of the adaptation tests and the lamps of the
direct play tests."""
import json

from gamelab import work
from gamelab.contracts import LOST, Budget, GoalSpec, Scope
from gamelab.model import TurnResult
from gamelab.rules.minesweeper import EXPLODED
from test_adaptation_mines import BOARD, COMPUTE, LOST_EARLY, WINNING, Mines, Reader, middle, quick, source as mines_source  # noqa: F401
from test_direct import PRESS, Lamps, Player


class Levels(Mines):
    """Level after level of the fake game: a level's end puts a notice over the board whose one button deals the
    next level (after a win) or the same level once more (after a loss); `draws` are the levels' boards in turn."""

    def __init__(self, draws):
        super().__init__(draws[0])
        self.draws, self.dealt, self.ended, self.ends = list(draws), 1, None, []

    async def tap(self, x, y, duration_ms):
        covered = self.covered
        await super().tap(x, y, duration_ms)
        if covered and not self.covered:
            # the notice's button: the next board is dealt
            self.draw = self.draws[min(self.dealt, len(self.draws) - 1)]
            self.dealt += 1
            self.board, self.played, self.ended = self.family.initial_state(self.draw), 0, None
            return
        if not self.covered and self.ended is None and self.over():
            self.ended = "lost" if any(cell == EXPLODED for row in self.grid for cell in row) else "won"
            self.ends.append(self.ended)
            self.covered = True


class Passing(Reader):
    """The model, which also gets past a level's end as it is asked: the level played is given up with how it
    ended, and a level that ended before the part began is the model's to pass by its own inputs: the notice's
    button (`uses` what it says the button uses), on a screen it names a result, and then a board it names so."""

    def __init__(self, fake, uses=None, stop=None, **options):
        super().__init__(fake, [mines_source()] * 8, **options)
        self.uses, self.stop = uses, stop

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if context.get("name_screen"):
            self.asked.append(("name", context, 1))
            return TurnResult(text=json.dumps({"scene": "result" if self.fake.covered else "board",
                                               "situation": f"level {self.fake.dealt} {self.fake.ended or 'open'}"}))
        if context.get("play"):
            self.asked.append(("play", context, 1))
            if self.stop is not None and self.stop(self.fake):
                action, scene = {"kind": "stop", "because": "the hearts ran out"}, "result"
            elif self.fake.covered:
                action = {"kind": "tap", "at": middle(self.fake.button), **({"uses": self.uses(self.fake)} if self.uses else {})}
                scene = "result"
            else:
                action, scene = {"kind": "tap", "at": [BOARD[0] + 5, BOARD[1] + 5]}, "board"
            return TurnResult(text=json.dumps({"situation": f"level {self.fake.dealt}", "scene": scene, "guided": False,
                                               "score": None, "action": action, "expect": "the next level",
                                               "notes": "the button above the board deals the next level"}))
        if "tried" not in context:
            return await super().complete(request)
        self.asked.append(("recover", context, 1))
        if self.stop is not None and self.stop(self.fake):
            action = {"kind": "stop", "because": "the hearts ran out"}
        elif context["reason"] == "next_level":
            action = {"kind": "tap", "at": middle(self.fake.button), **({"uses": self.uses(self.fake)} if self.uses else {})}
        elif self.fake.covered:
            action = {"kind": "give_up", "end": self.fake.ended}
        else:
            action = {"kind": "none"}
        return TurnResult(text=json.dumps({"situation": f"level {self.fake.dealt}", "action": action}))


def again(fake):
    return ["retry"] if fake.ended == "lost" else []


def ending(played):
    return played.record["outcome"], played.record["reason"]


async def test_levels_are_cleared_one_after_another_and_the_work_hands_back_what_it_did(tmp_path):
    from gamelab.adaptation import Store

    fake = Levels([WINNING] * 3)
    model = Passing(fake)
    played, made = await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 3), budget=Budget(200, COMPUTE, 600),
                                   store=Store(tmp_path))
    assert ending(played) == ("goal_reached", "levels"), (played.record, made["work"])
    assert fake.ends == ["won"] * 3 and fake.dealt == 3
    handback = made["work"]
    assert handback["levels"] == {"wanted": 3, "won": 3, "lost": 0}
    assert [(part["outcome"], part["reason"]) for part in handback["parts"]] == [("goal_reached", "won")] * 3
    # every move of every part is in the one run, and the record counts them all: the cells, the one input the
    # model sent between two levels, and the same input sent by code the second time
    cells = sum(1 for move in played.moves)
    assert handback["spent"]["inputs"] == played.record["actions_used"] == cells == len(fake.taps)
    assert {move["part"] for move in played.moves} == {1, 2, 3} and played.record["parts"] == 3
    # the second and third parts began on the last level's end, a screen the family reads no board on: the
    # model played on from it by its own inputs, told the work goes on (the button, on a screen it named a
    # result), and the family played again from the screen it named a board, the model's move there not sent.
    # The one input that opened the level was sent again unasked the second time, as a step of the game's routine,
    # and the family tried on the board it opened with no model asked what the screen was
    between = [context for kind, context, _ in model.asked if kind == "play"]
    assert len(between) == 2 and all(context["onward"] and context["scenes"]["board"] for context in between)
    passed = [(move["level"], move["scene"], move["part"]) for move in played.moves if move["level"] in ("L0", "R")]
    assert passed == [("L0", "result", 2), ("R", "result", 3)]
    assert not [context for kind, context, _ in model.asked if kind == "recover" and context["reason"] == "next_level"]
    assert [part["between"]["by_model"] for part in handback["parts"]] == [0, 1, 0]
    assert [part["between"]["by_routine"] for part in handback["parts"]] == [0, 0, 1]
    # the input between two levels is no move of a level
    assert [part["level_moves"] for part in handback["parts"]] == [part["confirmed"] - (number > 0)
                                                                   for number, part in enumerate(handback["parts"])]
    # where it stopped: the last level's end, named once by a model for the person
    assert handback["stopped_at"]["screen"] == "result" and handback["stopped_at"]["situation"] == "level 3 won"
    assert "(result): level 3 won" in work.told(handback)
    assert handback["stopped_at"]["where"] == "https://mines.example/" and handback["resume"]["scope"] == "current_game"
    assert handback["spent"]["used"] == {} and "levels won 3 of 3" in work.told(handback)


async def test_a_lost_level_ends_the_work_unless_another_try_was_allowed():
    fake = Levels([LOST_EARLY, WINNING])
    played, made = await work.play(fake, Passing(fake, uses=again), "minesweeper", goal=GoalSpec("levels", 2),
                                   budget=Budget(200, COMPUTE, 600))
    assert ending(played) == ("game_over", "level_lost") and fake.ends == ["lost"] and fake.dealt == 1
    assert made["work"]["levels"] == {"wanted": 2, "won": 0, "lost": 1}
    # allowed: the lost level is played again, by the input that says it uses another try, and counted so
    fake = Levels([LOST_EARLY, WINNING, WINNING])
    played, made = await work.play(fake, Passing(fake, uses=again), "minesweeper",
                                   goal=GoalSpec("levels", 2, allowed_meta_actions=frozenset({"retry"})),
                                   budget=Budget(200, COMPUTE, 600))
    assert ending(played) == ("goal_reached", "levels") and fake.ends == ["lost", "won", "won"]
    assert made["work"]["levels"] == {"wanted": 2, "won": 2, "lost": 1} and made["work"]["spent"]["used"] == {"retry": 1}
    # allowed, but the person asked to stop at a loss
    fake = Levels([LOST_EARLY, WINNING])
    played, _ = await work.play(fake, Passing(fake, uses=again), "minesweeper",
                                goal=GoalSpec("levels", 2, stop_conditions=(LOST,), allowed_meta_actions=frozenset({"retry"})),
                                budget=Budget(200, COMPUTE, 600))
    assert ending(played) == ("game_over", "level_lost") and fake.dealt == 1


async def test_an_input_that_uses_what_was_not_allowed_is_not_sent_and_the_model_is_told():
    fake = Levels([WINNING, WINNING])
    model = Passing(fake, uses=lambda fake: ["currency"])
    played, made = await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 2), budget=Budget(200, COMPUTE, 600))
    # the second level's button said it spends coins: never pressed, the work handed back with one level won
    assert fake.dealt == 1 and made["work"]["levels"]["won"] == 1 and played.record["outcome"] == "gave_up"
    assert made["work"]["spent"]["refused"]["currency"] >= 1 and made["work"]["spent"]["used"] == {}
    told = [context["history"] for kind, context, _ in model.asked if kind == "play" and context["history"]]
    assert told and "did not allow currency" in told[-1][-1]["refused"]
    assert all(context["goal"]["allowed_meta_actions"] == [] for kind, context, _ in model.asked if kind == "play")


async def test_a_stop_condition_a_model_says_is_met_hands_the_work_back_there():
    fake = Levels([WINNING, WINNING, WINNING])
    model = Passing(fake, stop=lambda fake: fake.covered and fake.dealt == 2)
    played, made = await work.play(fake, model, "minesweeper",
                                   goal=GoalSpec("levels", 3, stop_conditions=("하트가 다 떨어지면",)), budget=Budget(200, COMPUTE, 600))
    # the model says the condition is met at the second level's end: the work stops there, one level counted
    assert ending(played) == ("stopped", "stop_condition") and played.record["detail"] == "the hearts ran out"
    assert made["work"]["levels"] == {"wanted": 3, "won": 1, "lost": 0} and len(made["work"]["parts"]) == 2
    contexts = [context for kind, context, _ in model.asked if kind == "recover"]
    assert contexts and all(context["stop_conditions"] == ["하트가 다 떨어지면"] for context in contexts)
    # a model playing by its own inputs says so too
    lamps = Lamps()
    played, made = await work.play(lamps, Player([PRESS, {"kind": "stop", "because": "the lamp is red"}]), None,
                                   goal=GoalSpec("play_until_end", stop_conditions=("빨간 불이 켜지면",)),
                                   budget=Budget(20, 1, 60))
    assert ending(played) == ("stopped", "stop_condition") and played.record["detail"] == "the lamp is red"
    assert made["work"]["outcome"] == "stopped" and lamps.lit == 1


async def test_a_model_playing_by_its_own_inputs_uses_only_what_was_allowed():
    boost = {**PRESS, "uses": ["boosters"]}
    lamps = Lamps()
    model = Player([boost, boost, PRESS, PRESS, PRESS, {"kind": "done", "end": "won"}])
    played, made = await work.play(lamps, model, None, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60))
    assert ending(played) == ("goal_reached", "won") and lamps.lit == 3 and len(lamps.taps) == 3
    assert made["work"]["spent"]["refused"] == {"boosters": 2} and made["work"]["spent"]["used"] == {}
    assert model.asked[1]["history"][-1]["refused"].endswith("did not allow boosters")
    assert model.asked[0]["meta_actions"]["boosters"] and model.asked[0]["goal"]["allowed_meta_actions"] == []
    lamps = Lamps()
    model = Player([boost, PRESS, PRESS, {"kind": "done", "end": "won"}])
    played, made = await work.play(lamps, model, None, goal=GoalSpec("play_until_end", allowed_meta_actions=frozenset({"boosters"})),
                                   budget=Budget(20, 1, 60))
    assert ending(played) == ("goal_reached", "won") and made["work"]["spent"]["used"] == {"boosters": 1}


async def test_the_works_budget_is_one_for_all_its_levels():
    fake = Levels([WINNING] * 3)
    played, made = await work.play(fake, Passing(fake), "minesweeper", goal=GoalSpec("levels", 3), budget=Budget(15, COMPUTE, 600))
    assert ending(played) == ("budget_exhausted", "actions") and played.record["actions_used"] == 15
    assert made["work"]["levels"]["won"] == 1 and len(made["work"]["parts"]) == 2


async def test_one_game_asked_for_is_one_part_as_it_always_was():
    fake = Mines()
    played, made = await work.play(fake, Reader(fake, [mines_source()]), "minesweeper", goal=GoalSpec("play_until_end"),
                                   budget=Budget(60, COMPUTE, 300))
    assert ending(played) == ("goal_reached", "play_until_end") and made["work"]["levels"] is None
    assert len(made["work"]["parts"]) == 1 and made["work"]["spent"]["inputs"] == len(fake.taps)
    assert not made["work"]["stopped_at"]["in_play"]
    # a game cut short stands on a board in play, for the next request to go on from
    fake = Mines()
    played, made = await work.play(fake, Reader(fake, [mines_source()]), "minesweeper", goal=GoalSpec("play_until_end"),
                                   budget=Budget(3, COMPUTE, 300))
    assert ending(played) == ("budget_exhausted", "actions") and made["work"]["stopped_at"]["in_play"]


def test_a_permission_the_harness_does_not_know_is_no_goal():
    import pytest

    with pytest.raises(ValueError):
        GoalSpec("levels", 3, allowed_meta_actions=frozenset({"buy_gems"}))
    assert GoalSpec("levels", 3).scope == Scope.CURRENT_GAME
