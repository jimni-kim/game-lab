"""What C showed of a family that lets a level's board go, and of the end of a work (PLAN §26, session C2):

- why play by a family was handed to the model is kept, part by part (C2-1);
- a family taken again on a screen the model named a board and let go before its first move is tried again only
  some inputs on (C2-2), so the tries a game has are not all used on the level's first screen;
- a board the family's reader cannot read is played from the model's reading of it (C2-3);
- a game that opens no level until time gives a life back ends the work, told how long until the next (C2-4);
- a name or profile window the game forces is passed and told of (C2-5);
- each part's seconds, tokens, questions and the moves a level was left with (C2-6).

The levels are the fake minesweeper between whose levels a story and a map stand (`test_between`); the rules are
match3's, on a board like the one level 21 of a garden game showed (2026-11-18)."""
import json

from gamelab import agent, direct, recovery, work
from gamelab.contracts import Budget, GoalSpec
from gamelab.families import GridFamily
from gamelab.model import TurnResult
from gamelab.rules import match3
from gamelab.solvers import match3 as match3_solver
from test_adaptation_mines import COMPUTE, WINNING
from test_between import Garden, Guide, plays, quick  # noqa: F401
from test_work import ending

# a sparse board: every power stands among empty places and pieces held in place, and no swap lines anything up
PLANE, HELD = 500, match3.HELD
SPARSE = [[0, 0, 3, 0, 0, 0, 0],
          [0, PLANE, 0, 0, 1, 3, 5],
          [3, 0, 0, 3, 5, 4, 4],
          [0, 0, HELD + 4, HELD + 1, 3, 1, 3],
          [0, 0, PLANE, HELD + 5, 4, 5, 4],
          [0, 0, HELD + 4, HELD + 5, 1, 3, 1]]
RULES = {**match3.DEFAULT_RULES, "rows": 6, "cols": 7}


def test_a_power_nothing_can_be_swapped_with_is_tapped_while_the_game_is_not_known_to_refuse_taps():
    state = {"grid": SPARSE, "score": None, "scene": "playing"}
    moves = match3.legal_actions(state, RULES)
    assert {"kind": "pop", "cell": [1, 1]} in moves and {"kind": "pop", "cell": [4, 2]} in moves
    assert all(move["kind"] == "pop" for move in moves)
    assert match3_solver.choose(state, RULES, "greedy", 200)["action"]["kind"] == "pop"
    # a game seen not to take a tap: no move, as before
    assert match3.legal_actions(state, {**RULES, "tap_power": False}) == []
    # a power with a piece beside it is set off by a swap while taps are not known: it is not tapped
    open_ = [row[:] for row in SPARSE]
    open_[1][2] = 2
    moves = match3.legal_actions({**state, "grid": open_}, RULES)
    assert {"kind": "pop", "cell": [1, 1]} not in moves and {"kind": "swap", "cell": [1, 1], "to": [1, 2]} in moves
    # what a tapped power leaves is the screen's to show: the move is one the rules can play
    assert match3.apply(state, RULES, {"kind": "pop", "cell": [1, 1]})["state"]["grid"][1][1] is None


class Board(Guide):
    """The model of `test_between`, which says on the map of the second part that no life is left (`lives`), marks
    its tap on the story's first line as making a name (`profile`), and tells the moves a level was left with."""

    def __init__(self, fake, lives=None, profile=None, **options):
        super().__init__(fake, **options)
        self.lives, self.profile = lives, profile

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if context.get("play") and self.fake.stage == "map" and self.lives is not None and self.fake.dealt == 2:
            self.asked.append(("play", context, 1))
            return TurnResult(text=json.dumps({"situation": "the map: no lives, the next in 25 minutes", "scene": "map",
                                               "guided": False, "score": None,
                                               "action": {"kind": "out_of_lives", "minutes": self.lives},
                                               "expect": "", "notes": ""}))
        if context.get("play") and self.fake.stage == "story" and self.profile and self.fake.line == 0:
            self.asked.append(("play", context, 1))
            return TurnResult(text=json.dumps({"situation": "a name to make", "scene": "popup", "guided": True,
                                               "score": None, "action": {"kind": "tap", "at": [300, 300],
                                                                         "profile": self.profile},
                                               "expect": "the name is kept", "notes": ""}))
        if "tried" in context and self.fake.covered:
            self.asked.append(("recover", context, 1))
            return TurnResult(text=json.dumps({"situation": "the level's end", "scene": "result",
                                               "action": {"kind": "give_up", "end": self.fake.ended, "moves_left": 4}}))
        return await super().complete(request)


def refusing(monkeypatch, fake, times):
    """The family finds no move on the second level's board the first `times` it is asked."""
    choose, refused = GridFamily.choose, []

    def chosen(self, payload, policy, compute, excluded=()):
        if fake.dealt == 2 and len(refused) < times:
            refused.append(payload)
            return None
        return choose(self, payload, policy, compute, excluded)

    monkeypatch.setattr(GridFamily, "choose", chosen)
    return refused


async def test_a_family_let_go_before_its_first_move_is_kept_with_why_and_tried_again_only_some_inputs_on(monkeypatch):
    fake = Garden([WINNING] * 2)
    refused = refusing(monkeypatch, fake, 1)
    model = Board(fake, repeat=5)
    played, made = await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 2), budget=Budget(200, COMPUTE, 600))
    assert ending(played) == ("goal_reached", "levels"), (played.record, made["work"])
    assert len(refused) == 1
    second = made["work"]["parts"][1]
    # the family was let go on the board the model named one, before a move of its own, for want of a move
    let_go = [entry for entry in second["let_go"] if entry["stage"] != "read"]
    assert let_go[0]["stage"] == "no_move" and let_go[0]["why"] == "absent" and let_go[0]["moves"] == 0
    assert let_go[0]["resumed"] and let_go[0]["on_board"] and "tap_power" not in let_go[0].get("ways", {})
    taken = [entry for entry in second["resumes"] if entry["taken"]]
    assert taken[0]["on_board"] and taken[0]["let_go"]["stage"] == "no_move"
    # tried again only once the model's own inputs had gone on as long as after a board the family did not read
    assert taken[1]["at_action"] - taken[0]["at_action"] >= agent.RESUME_EVERY
    on_board = [move for move in played.moves if move["part"] == 2 and move["level"] == "L0" and move.get("scene") == "board"]
    assert len(on_board) >= 1
    # the first part was let go only on the screens before its level, by the reader that read none there
    assert all(entry["stage"] == "read" for entry in made["work"]["parts"][0].get("let_go", []))


async def test_a_board_the_familys_reader_cannot_read_is_played_by_the_family_from_the_models_reading(monkeypatch, tmp_path):
    from gamelab.adaptation import Store

    fake = Garden([WINNING] * 2)
    model = Board(fake, repeat=5)
    read_code, failed = agent.Agent._read_code, []

    async def unread(self, expect):
        if fake.dealt == 2 and fake.stage == "board" and not fake.covered and not failed:
            failed.append(self.used)
            self.why = "board_not_found"
            return None
        return await read_code(self, expect)

    monkeypatch.setattr(agent.Agent, "_read_code", unread)
    played, made = await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 2), budget=Budget(200, COMPUTE, 600),
                                   store=Store(tmp_path))
    assert ending(played) == ("goal_reached", "levels"), (played.record, made["work"])
    second = made["work"]["parts"][1]
    taken = [entry for entry in second["resumes"] if entry["taken"]]
    assert taken[0]["kept_unread"] == "board_not_found" and taken[0]["read_by"] == "model"
    # the level's first move was the family's, from the model's reading (L2), and nothing was let go on its board
    level = [move for move in played.moves if move["part"] == 2 and move["level"] != "L0"]
    assert level and level[0]["level"] == "L2"
    assert not [entry for entry in second.get("let_go", []) if entry["on_board"]]


async def test_a_level_the_game_opens_only_once_a_life_comes_back_ends_the_work_told_how_long(monkeypatch):
    fake = Garden([WINNING] * 3)
    model = Board(fake, lives=25, repeat=5)
    played, made = await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 3), budget=Budget(200, COMPUTE, 600))
    assert ending(played) == ("stopped", "out_of_lives"), (played.record, made["work"])
    handback = made["work"]
    # the map after the second level: the third is not opened
    assert handback["levels"]["won"] == 2 and fake.dealt == 2 and fake.stage == "map"
    assert handback["out_of_lives"]["minutes"] == 25
    assert "out of lives, next life in 25 min" in work.told(handback)
    # nothing was sent at that answer
    assert not [move for move in played.moves if move["part"] == 3 and move.get("scene") == "map"]


async def test_a_name_the_game_made_the_player_make_is_told_and_each_part_tells_what_it_spent():
    fake = Garden([WINNING] * 2)
    model = Board(fake, profile="Player", repeat=5)
    played, made = await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 2), budget=Budget(200, COMPUTE, 600))
    assert ending(played) == ("goal_reached", "levels"), (played.record, made["work"])
    handback = made["work"]
    assert [entry["made"] for entry in handback["profiles"]] == ["Player"]
    assert "a name or profile window came up and was passed as 'Player'" in work.told(handback)
    for part in handback["parts"]:
        assert part["seconds"] >= 0 and part["input_tokens"] >= 0 and part["questions"] >= 1
    assert sum(part["questions"] for part in handback["parts"]) <= handback["spent"]["questions"]


def test_the_answers_of_an_end_of_lives_a_level_s_moves_left_and_a_name_made_are_read():
    size, kinds = (900, 700), frozenset({"tap", "keys", "drag", "back", "text"})
    assert direct.check({"kind": "out_of_lives", "minutes": 25}, size, kinds) == {"kind": "out_of_lives", "minutes": 25}
    assert direct.check({"kind": "out_of_lives", "minutes": "soon"}, size, kinds) == {"kind": "out_of_lives", "minutes": None}
    assert recovery.check({"kind": "out_of_lives", "minutes": 6}, size, kinds) == {"kind": "out_of_lives", "minutes": 6}
    assert direct.check({"kind": "done", "end": "won", "moves_left": 3}, size, kinds) == {"kind": "done", "end": "won", "moves_left": 3}
    assert recovery.check({"kind": "give_up", "end": "won", "moves_left": -1}, size, kinds) == {"kind": "give_up", "end": "won"}
    assert recovery.profile({"kind": "tap", "at": [1, 1], "profile": " Player "}) == "Player"
    assert recovery.profile({"kind": "tap", "at": [1, 1]}) is None


def test_a_tap_the_game_took_on_a_power_of_no_known_kind_or_colour_says_it_takes_taps():
    state = {"grid": SPARSE, "score": None, "scene": "playing"}
    pop = {"kind": "pop", "cell": [1, 1]}
    assert match3.learned(state, pop, "confirmed", RULES)["tap_power"] is True
    # not taken: a thing nobody knew the kind of may be no power at all, so nothing is learned
    assert match3.learned(state, pop, "absent", RULES)["tap_power"] is None
    lined = [row[:] for row in SPARSE]
    lined[1][1] = match3.POWERED * match3.LINE + 2
    assert match3.learned({**state, "grid": lined}, pop, "absent", RULES)["tap_power"] is False
    assert match3.learned(state, pop, "confirmed", {**RULES, "tap_power": False})["tap_power"] is False
