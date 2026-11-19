"""A game asked for from its start is not the one the first screen shows played (PLAN §32, session Q): an app
opened again shows the game it was left at, and before 2026-11-19 a finished Mines game was handed back as won,
and a 2048 game holding 1024 as the goal reached, with no move made.

- a game still in play that already meets the goal is left as it is and the person is told: a new game would
  throw it away, which is never pressed unasked (the person's choice);
- a game that ended before the work is not its end: a new game of the same kind is started and played;
- when none can be started, the work says so rather than claim the old game's end.

The screens are the fake 2048 of the adaptation tests and the fake minesweeper of the work tests."""
import json

from gamelab import work
from gamelab.contracts import Budget, GoalSpec, Scope
from gamelab.model import TurnResult
from fake_screen import Screen
from test_adaptation import Author, quick as quick_2048  # noqa: F401
from test_adaptation_mines import COMPUTE, WINNING, middle, quick  # noqa: F401
from test_work import Levels, Passing, ending

AT_GOAL = [[2, 4, 8, 16], [4, 1024, 2, 4], [0, 2, 0, 0], [0, 0, 0, 2]]


async def test_a_game_in_play_that_already_meets_the_goal_is_left_and_told_of_not_thrown_away():
    fake = Screen()
    fake.board["grid"] = [row[:] for row in AT_GOAL]
    played, made = await work.play(fake, Author(fake, []), "slide_merge",
                                   goal=GoalSpec("reach_value", 1024, scope=Scope.NEW_GAME), budget=Budget(40, 20, 120))
    assert ending(played) == ("stopped", "met_before"), played.record
    assert "already met the goal" in played.record["detail"]
    assert played.record["actions_used"] == 0 and fake.keys == [] and fake.taps == []
    # going on with the game on the screen, as asked: the goal is the one the screen shows met
    fake = Screen()
    fake.board["grid"] = [row[:] for row in AT_GOAL]
    played, _ = await work.play(fake, Author(fake, []), "slide_merge",
                                goal=GoalSpec("reach_value", 1024, scope=Scope.CURRENT_GAME), budget=Budget(40, 20, 120))
    assert ending(played) == ("goal_reached", "reach_value")


class Anew(Passing):
    """The model of the work tests, which on a screen whose game ended before the work presses its button (a
    new game) when told the game is to be one from its start, or else (`gives_up`) says how the old one ended."""

    def __init__(self, fake, gives_up=False, **options):
        super().__init__(fake, **options)
        self.gives_up = gives_up

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if context.get("anew") and self.fake.covered:
            asked = "play" if context.get("play") else "recover"
            self.asked.append((asked, context, 1))
            action = {"kind": "done" if asked == "play" else "give_up", "end": self.fake.ended} if self.gives_up \
                else {"kind": "tap", "at": middle(self.fake.button)}
            return TurnResult(text=json.dumps({"situation": f"a game ended: {self.fake.ended}", "scene": "result",
                                               "guided": False, "score": None, "action": action,
                                               "expect": "a new game", "notes": ""}))
        return await super().complete(request)


def ended_before(fake):
    fake.covered, fake.ended, fake.ends = True, "won", ["won"]
    return fake


async def test_a_game_that_ended_before_the_work_is_started_anew_and_played_to_its_own_end():
    fake = ended_before(Levels([WINNING, WINNING]))
    model = Anew(fake)
    played, made = await work.play(fake, model, "minesweeper", goal=GoalSpec("play_until_end", scope=Scope.NEW_GAME),
                                   budget=Budget(200, COMPUTE, 600))
    assert ending(played) == ("goal_reached", "won"), (played.record, made["work"])
    # the new game was dealt and played to its end by the family, and its end is not passed for another: the
    # input that started it was kept for a game ended before a work, not for the end of the work's own
    assert fake.dealt == 2 and fake.ends == ["won", "won"]
    assert played.record["level_moves"] > 0
    told = [context for kind, context, _ in model.asked if kind in ("play", "recover") and context.get("anew")]
    assert told, model.asked


async def test_a_game_that_ended_before_the_work_is_not_handed_back_as_its_end_when_no_new_one_is_started():
    fake = ended_before(Levels([WINNING, WINNING]))
    played, made = await work.play(fake, Anew(fake, gives_up=True), "minesweeper",
                                   goal=GoalSpec("play_until_end", scope=Scope.NEW_GAME), budget=Budget(200, COMPUTE, 600))
    assert ending(played) == ("gave_up", "new_game_not_found"), (played.record, made["work"])
    assert played.record["actions_used"] == 0 and fake.dealt == 1
