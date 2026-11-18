"""What a level asks is told to the solver as properties, and a model picks among the solver's moves where the
code cannot weigh the goal (PLAN §31): a layer under the pieces that a line spreads, a piece held in place that
a line sets free, a goal about the layer that asks for more cells than have one, a move the game did not take
though the board read otherwise."""
import json
from types import SimpleNamespace

import test_adaptation_match3 as gems
from gamelab import agent
from gamelab.adapters.kit import KitAdapter
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult
from gamelab.rules import match3 as rules
from gamelab.rules.match3 import HELD, UNDER
from gamelab.solvers import match3 as solver
from test_kit_values import JELLY_GOAL, Jellied, quick  # noqa: F401

R, G, B, Y, P, O = 1, 4, 5, 3, 6, 2
GRASS = (150, 215, 120)
SPREADING = {**rules.DEFAULT_RULES, "rows": 4, "cols": 5, "spread": True}
#   the swap of (1,2) and (2,2) lines three red up along row 1, whose first cell lies on the layer
BOARD = [[G, B, Y, P, O],
         [R, R, B, G, Y],
         [B, O, R, Y, P],
         [Y, P, O, B, G]]
LAYER = [[0, 0, 0, 0, 0],
         [1, 0, 0, 0, 0],
         [0, 0, 0, 0, 0],
         [0, 0, 0, 0, 0]]
MOVE = {"kind": "swap", "cell": [1, 2], "to": [2, 2]}


def test_a_line_with_a_cell_on_a_layer_that_spreads_lays_it_under_its_other_cells():
    result = rules.apply({"grid": BOARD, "under": LAYER}, SPREADING, MOVE)
    assert result["state"]["under"][1] == [1, 1, 1, 0, 0] and result["taken"][UNDER] == 2
    # a line that touches no layer lays none, and a layer that does not spread is taken off by clearing
    bare = rules.apply({"grid": BOARD, "under": [[0] * 5 for _ in range(4)]}, SPREADING, MOVE)
    assert UNDER not in bare["taken"] and not any(map(any, bare["state"]["under"]))
    taken_off = rules.apply({"grid": BOARD, "under": LAYER}, {**SPREADING, "spread": None}, MOVE)
    assert taken_off["state"]["under"][1][0] == 0 and taken_off["taken"][UNDER] == 1


def test_a_piece_held_in_place_lines_up_by_its_colour_and_a_line_sets_it_free_where_it_stands():
    held = [list(row) for row in BOARD]
    held[1][0] = HELD + R
    state = {"grid": held}
    assert MOVE in rules.legal_actions(state, SPREADING)
    # it is not swapped itself
    assert {"kind": "swap", "cell": [0, 0], "to": [1, 0]} not in rules.legal_actions(state, SPREADING)
    result = rules.apply(state, SPREADING, MOVE)
    assert result["state"]["grid"][1][0] == R and result["taken"][HELD] == 1 and result["cleared"] == 2
    # setting one free is worth more to the solver than the same line with none
    assert solver._worth(result["taken"], 2, 0, [], SPREADING) > solver._worth({R: 2}, 2, 0, [], SPREADING)


def test_a_goal_about_the_layer_that_asks_for_more_cells_than_have_one_is_to_spread_it():
    state = {"grid": BOARD, "under": LAYER}
    plain = {**rules.DEFAULT_RULES, "rows": 4, "cols": 5}
    asked = [{"kind": "collect", "target": UNDER, "count": 12}]
    assert rules.meant(asked, state, plain) == {"goals": [{"kind": "cover", "target": UNDER, "count": 12}], "spread": True}
    # one that asks for no more than have a layer may be about taking it off: nothing is settled
    assert rules.meant([{"kind": "collect", "target": UNDER, "count": 1}], state, plain) == {}
    # a goal a model read as one to cover says so itself; goals about pieces say nothing of the layer
    assert rules.meant([{"kind": "cover", "target": UNDER, "count": 1}], state, plain)["spread"] is True
    assert rules.meant([{"kind": "collect", "target": R, "count": 30}], state, plain) == {}
    # a game seen to take its layer off keeps its goal as one to collect
    assert rules.meant(asked, state, {**plain, "spread": False}) == {}


def test_whether_a_layer_spreads_is_settled_by_whether_more_cells_had_one_after_a_move():
    plain = {**rules.DEFAULT_RULES, "rows": 4, "cols": 5}
    more = [[0] * 5, [1, 1, 1, 0, 0], [0] * 5, [0] * 5]
    assert rules.observed({"grid": BOARD, "under": LAYER}, MOVE, {"grid": BOARD, "under": more}, plain)["spread"] is True
    assert rules.observed({"grid": BOARD, "under": more}, MOVE, {"grid": BOARD, "under": LAYER}, plain)["spread"] is False
    # one cell is as often a misreading, and a value settled is not settled again
    one = [[0] * 5, [1, 1, 0, 0, 0], [0] * 5, [0] * 5]
    assert rules.observed({"grid": BOARD, "under": LAYER}, MOVE, {"grid": BOARD, "under": one}, plain)["spread"] is None
    assert rules.observed({"grid": BOARD, "under": more}, MOVE, {"grid": BOARD, "under": LAYER}, SPREADING)["spread"] is True


def test_the_solver_goes_for_the_move_that_spreads_the_layer_and_ranks_its_moves_with_what_each_does():
    wide = [[G, B, Y, P, O, G],
            [R, R, B, G, Y, B],
            [B, O, R, Y, P, P],
            [Y, P, O, B, G, O],
            [G, G, B, G, P, P],
            [O, Y, P, B, Y, O]]
    under = [[0] * 6 for _ in range(6)]
    under[1][0] = 1
    spreading = {**rules.DEFAULT_RULES, "rows": 6, "cols": 6, "spread": True,
                 "goals": [{"kind": "cover", "target": UNDER, "count": 20}]}
    state = {"grid": wide, "under": under}
    # with no goal the solver takes another move; the red line lays the layer under two more cells
    other = solver.choose(state, {**spreading, "goals": None}, "greedy", 200)["action"]
    assert other != MOVE
    assert solver.choose(state, spreading, "greedy", 200)["action"] == MOVE
    ranked = solver.rank(state, spreading, 200)
    assert ranked[0]["action"] == MOVE and ranked[0]["towards_goals"] == 2 and ranked[0]["taken"][UNDER] == 2
    assert any(entry["action"] == other and entry["cleared"] >= 3 and entry["towards_goals"] == 0 for entry in ranked)
    assert len(ranked) <= solver.RANKED and solver.rank(state, spreading, 200, excluded=[MOVE])[0]["action"] != MOVE


class Lawn(Jellied):
    """A game whose layer spreads: a line with a cell on it lays it under its other cells, and the level's goal,
    beside the board, is how many cells still have none."""
    LAYERED = [(r, c) for r in (3, 4) for c in (3, 4)]

    def __init__(self):
        super().__init__(rules={"square": True, "swap_power": False, "pair_power": False, "tap_power": False, "spread": True})

    def bare(self):
        return sum(1 for row in self.state["under"] for cell in row if not cell)

    async def capture(self, region=None):
        from io import BytesIO
        from PIL import Image, ImageDraw

        # the layer lies under the piece and shows around it, as grass does: the piece on it looks as any other
        image = Image.open(BytesIO(await gems.Gems.capture(self))).convert("RGB")
        draw = ImageDraw.Draw(image)
        for r, row in enumerate(self.state["under"]):
            for c, layers in enumerate(row):
                if layers:
                    x, y = gems.BOARD[0] + c * gems.CELL, gems.BOARD[1] + r * gems.CELL
                    draw.rectangle((x, y, x + gems.CELL - 1, y + gems.CELL - 1), fill=GRASS)
                    draw.ellipse((x + 5, y + 5, x + gems.CELL - 6, y + gems.CELL - 6),
                                 fill=gems.INKS[rules.colour_of(self.state["grid"][r][c])])
        left, top, width, height = JELLY_GOAL
        draw.rectangle((left, top, left + width - 1, top + height - 1), fill=GRASS)
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


class Gardener(gems.Jeweller):
    """A model that reads the layer and lists the goal as it shows, with no kind it can put to it."""

    async def complete(self, request):
        result = await super().complete(request)
        answer = json.loads(result.text)
        if answer.get("state"):
            answer["state"]["under"] = self.fake.state["under"]
            answer["panel"] = {"goals": [{"box": JELLY_GOAL, "count": self.fake.bare(), "kind": "other"}]}
        return type(result)(text=json.dumps(answer))


async def test_a_goal_no_kind_was_put_to_on_a_board_with_a_layer_is_about_the_layer_and_the_moves_spread_it():
    fake = Lawn()
    model = Gardener(fake)
    played, made = await agent.play(fake, model, "match3", goal=GoalSpec("actions", 16), budget=Budget(40, 200, 120))
    assert played.record["outcome"] == "goal_reached", (played.record, made)
    goal = made["kit"]["goals"][0]
    # 60 cells to go with 4 laid: the layer spreads, and the goal is to lay it under more
    assert (goal["kind"], goal["target"]) == ("cover", UNDER) and made["kit"]["ways"]["spread"] is True, made["kit"]
    # the moves went for the lawn: far more of it than a game played for the pieces alone lays (3 cells more)
    assert 64 - fake.bare() >= 4 + 12, fake.bare()
    assert goal["count"] <= 60 - 12


class Scored(gems.Jeweller):
    """A model that lists a goal the code cannot weigh (a score) and, asked to pick among the code's moves,
    picks the second every time."""

    def __init__(self, fake, pick=2):
        super().__init__(fake)
        self.pick, self.picked = pick, []

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if context.get("pick"):
            self.picked.append(context)
            return TurnResult(text=json.dumps({"goal": "a score", "pick": self.pick, "because": "it is nearer the goal"}))
        result = await super().complete(request)
        answer = json.loads(result.text)
        if answer.get("state"):
            answer["panel"] = {"goals": [{"box": JELLY_GOAL, "count": 5000, "kind": "other"}]}
        return type(result)(text=json.dumps(answer))


async def test_where_the_code_weighs_none_of_the_goals_a_model_picks_among_its_best_moves_and_never_gives_its_own():
    fake = gems.Gems()
    model = Scored(fake)
    played, made = await agent.play(fake, model, "match3", goal=GoalSpec("actions", 6), budget=Budget(20, 200, 120))
    assert played.record["outcome"] == "goal_reached", (played.record, made)
    asked = model.picked
    assert len(asked) >= 6 and all(context["why"] == "goals_not_weighed" for context in asked)
    assert asked[0]["goals"] == [{"kind": "other", "count": 5000, "weighed": False}]
    # each move told by the middles of its two cells on the screen, with what the rules say it does
    first = asked[0]["moves"][0]
    assert first["id"] == 1 and len(first["from"]) == len(first["to"]) == 2 and first["clears"] >= 3
    assert abs(abs(first["from"][0] - first["to"][0]) + abs(first["from"][1] - first["to"][1]) - gems.CELL) <= 2
    # the move sent was the one picked, a move the game took
    picked = [move for move in played.moves if move.get("picked")]
    assert len(picked) >= 6 and all(move["picked"]["pick"] == 2 and move["effect"] == "confirmed" for move in picked)
    assert made["kit"]["picks"]["questions"] == len(asked) and made["kit"]["picks"]["other"] == len(picked)
    # an answer that picks none leaves the code's own choice
    fake = gems.Gems()
    played, made = await agent.play(fake, Scored(fake, pick=None), "match3", goal=GoalSpec("actions", 3), budget=Budget(20, 200, 120))
    assert played.record["outcome"] == "goal_reached" and made["kit"]["picks"]["other"] == 0
    assert made["kit"]["picks"]["none"] >= 3 and not any(move.get("picked") for move in played.moves)


def test_a_move_read_as_taken_whose_pieces_stand_where_they_stood_and_whose_count_did_not_change_was_not_taken():
    class Reader(KitAdapter):
        def __init__(self, counts):
            self.kit, self._counts = SimpleNamespace(field="grid"), counts

        @property
        def counts(self):
            return self._counts

    def taken_back(counts, ticked, after):
        player = agent.Agent.__new__(agent.Agent)
        player.adapter, player.ticked = Reader(counts), ticked
        return player._taken_back(agent.Reading(b"", {"grid": BOARD}, 0.0, "L3"), MOVE, agent.Reading(b"", {"grid": after}, 0.0, "L3"))

    elsewhere = [list(row) for row in BOARD]
    elsewhere[3][4] = R                     # a cell far from the move reads otherwise: the reading's doing
    moved = [list(row) for row in BOARD]
    moved[1][2] = Y
    assert taken_back(True, False, elsewhere)
    # the pieces moved, the count changed, or the count is not one the reader believes: the move stands
    assert not taken_back(True, False, moved) and not taken_back(True, True, elsewhere)
    assert not taken_back(False, False, elsewhere) and not taken_back(True, None, elsewhere)
