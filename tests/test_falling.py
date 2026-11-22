"""The falling blocks family (PLAN §34, F3): its rules and solver as modules the realtime loop is given, the next piece
read off the game's preview and planned with, and a level's goal from the model's answer, both towards where a piece
goes and against a level's end the model reads from a screen that is no result.

Regression this prevents: a loop whose plan drifts from what it did before the rules and the solver were taken out of
it (the greedy policy is the old `best`, tie for tie); a preview read once wrong and then trusted for a whole game; a
goal kept with the words of one level and played by in the next; and a level said won on a map when the code counted
fewer rows than it asked."""
from io import BytesIO
import json

from PIL import Image, ImageDraw

from gamelab import catalog, realtime
from gamelab.agent import play
from gamelab.contracts import Budget, GoalSpec
from gamelab.eval import policies
from gamelab.rules import falling as rules
from gamelab.solvers import falling as solver
from test_realtime import ANSWER, BOARD, BOX, CELL, COLS, PIECES, SIZE, Falling, Guide

Cells = frozenset
I, O, T = (Cells(piece) for piece in rules.STANDARD_PIECES[:3])


def test_the_family_is_registered_without_a_kit_and_is_not_offered_to_identification():
    family = catalog.family("falling")
    assert family.policies() == ["greedy", "lookahead"] and family.descriptor["default_policy"] == "lookahead"
    from gamelab.adaptation import explorable
    assert "falling" not in explorable()


def test_a_placement_is_one_piece_come_to_lie_and_full_rows_go():
    state = rules.initial_state(rules.DEFAULT_RULES, 0)
    assert state["pieces"] == 0 and len(state["next"]) == 1 and state["piece"] is not None
    # an I piece in an empty well 10 wide: 7 lying, 10 standing
    flat = {**state, "piece": [list(cell) for cell in I]}
    assert len(rules.legal_actions(flat, rules.DEFAULT_RULES)) == 17
    board = [[0] * 10 for _ in range(20)]
    board[19] = [1] * 6 + [0] * 4
    moved = rules.apply({**flat, "board": board}, rules.DEFAULT_RULES,
                        {"kind": "place", "shape": [[0, 0], [0, 1], [0, 2], [0, 3]], "column": 6})
    assert moved["lines"] == 1 and moved["state"]["lines"] == 1 and moved["state"]["board"][19] == [0] * 10
    after = rules.next_state(flat, rules.DEFAULT_RULES, rules.legal_actions(flat, rules.DEFAULT_RULES)[0], 5)
    # the preview's piece comes next, and a new one takes its place
    assert after["piece"] == state["next"][0] and len(after["next"]) == 1 and after["pieces"] == 1


def test_a_level_ends_at_its_goal_or_with_its_last_piece():
    level = {**rules.DEFAULT_RULES, "garbage": 3, "goal_lines": 2, "goal_pieces": 4}
    state = rules.initial_state(level, 7)
    assert all(row.count(0) == 1 for row in state["board"][-3:]) and not any(map(any, state["board"][:-3]))
    assert rules.won(state, level) is False and rules.won(state, rules.DEFAULT_RULES) is None
    spent = {**state, "pieces": 4}
    assert rules.legal_actions(spent, level) == [] and rules.status(spent, level, {})["terminal"]
    done = {**state, "lines": 2}
    assert rules.won(done, level) and rules.status(done, level, {})["goal"] == "reached"
    assert solver.choose(done, level, "lookahead", 2000)["action"] is None


def test_greedy_is_the_loops_first_policy_tie_for_tie():
    # the loop's own weighing before the rules and solver were taken out of it, written out once more
    def old(stack, piece, rows, cols):
        found = None
        for shape in rules.turns(piece):
            for column in range(cols - max(c for _, c in shape)):
                placed = rules.fall(stack, Cells((r, c + column) for r, c in shape), rows, cols)
                if placed is None:
                    continue
                after, lines = rules.settle(stack, placed, rows, cols)
                score = solver.weigh(after, rows, cols, lines)
                if found is None or score > found[2]:
                    found = (shape, column, score)
        return found
    import random
    draw = random.Random(1)
    for _ in range(300):
        rows, cols = draw.choice([(20, 10), (12, 6)])
        stack = Cells((r, c) for r in range(rows // 3, rows) for c in range(cols) if draw.random() < 0.45)
        stack, _ = rules.settle(stack, Cells(), rows, cols)
        piece = Cells(draw.choice(rules.STANDARD_PIECES))
        found, then = solver.best(stack, piece, rows, cols), old(stack, piece, rows, cols)
        assert (found and (found["shape"], found["column"], found["score"])) == then


def test_the_next_piece_changes_where_this_one_goes():
    stack = Cells([(4, 3), (5, 3), (6, 1), (6, 3), (7, 0), (7, 1)])
    alone = solver.place(stack, I, 8, 4)
    ahead = solver.place(stack, I, 8, 4, policy="lookahead", queue=[T])
    assert (alone["column"], len(rules.turns(alone["shape"]))) == (0, 2) and alone["shape"] != ahead["shape"]
    # with nothing shown coming, the look ahead is the greedy choice
    assert solver.place(stack, I, 8, 4, policy="lookahead") == alone


def test_a_goal_met_by_this_piece_is_taken_over_a_better_well():
    # a row short of one cell, and an I piece: the level's last row now, whatever it leaves above
    stack = Cells((7, c) for c in range(3)) | Cells([(6, 0)])
    met = solver.place(stack, I, 8, 4, policy="lookahead", queue=[O], goal={"lines": 1, "pieces": 1})
    assert met["lines"] == 1


def test_the_simulated_levels_are_measured_as_won_or_lost():
    game = policies.play("falling", "greedy", 2000, 0, 50, (), {"goal_lines": 1, "goal_pieces": 9})
    assert isinstance(game["won"], bool) and game["moves"] <= 9


def _drawn(cells, cell=12, gap=2, size=(90, 70), ground=(30, 30, 50), label=False):
    image = Image.new("RGB", size, ground)
    draw = ImageDraw.Draw(image)
    left, top = 9, 11
    for r, c in cells:
        draw.rectangle([left + c * cell + gap // 2, top + r * cell + gap // 2,
                        left + (c + 1) * cell - gap // 2 - 1, top + (r + 1) * cell - gap // 2 - 1], fill=(200, 60, 60))
    out = BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_the_next_piece_is_read_whatever_the_size_of_its_cells():
    preview = realtime.Preview([0, 0, 90, 70])
    for piece in rules.STANDARD_PIECES:
        for cell in (9, 14):
            assert preview.read(_drawn(piece, cell), size=4) == rules.normal(piece), (piece, cell)
    # nothing in the box is no piece
    assert preview.read(_drawn([])) is None


def test_the_answer_gives_the_preview_and_the_goal_and_the_goal_is_not_kept():
    kinds = frozenset({"keys", "tap"})
    said = realtime.understood({**ANSWER, "next": [250, 60, 330, 140], "goal": {"lines": 3, "pieces": None}},
                               list(SIZE), kinds)
    assert said["next"] == [250, 60, 330, 140] and said["goal"] == {"lines": 3, "pieces": None}
    # a preview inside the well, off the screen or too small to tell cells in is none; a goal with no rows is none
    for box in ([110, 60, 200, 100], [250, 60, 900, 140], [250, 60, 256, 66]):
        assert realtime.understood({**ANSWER, "next": box}, list(SIZE), kinds)["next"] is None
    assert realtime.understood({**ANSWER, "goal": {"pieces": 9}}, list(SIZE), kinds)["goal"] is None
    assert realtime.understood(ANSWER, list(SIZE), kinds)["next"] is None
    kept = realtime.record({**said, "goal_at": [0, 0]}, realtime.Well(BOX, 12, 6))["setup"]
    assert "goal" not in kept and "goal_at" not in kept and kept["next"] == [250, 60, 330, 140]


NEXT_BOX = [250, 60, 340, 130]


class Previewed(Falling):
    """The toy with a box beside the well that shows the piece coming next, in smaller cells than the well's. `lies`:
    it shows another piece than the one that comes."""

    def __init__(self, lies=False, **kw):
        self.lies = lies
        super().__init__(**kw)

    async def capture(self, region=None):
        png = await super().capture(None)
        image = Image.open(BytesIO(png)).convert("RGB")
        draw = ImageDraw.Draw(image)
        draw.rectangle(NEXT_BOX, fill=(40, 40, 60))
        if self.playing or self.piece:
            coming = PIECES[(self.count + (2 if self.lies else 0)) % len(PIECES)]
            for r, c in coming:
                x, y = NEXT_BOX[0] + 15 + c * 14, NEXT_BOX[1] + 15 + r * 14
                draw.rectangle([x + 1, y + 1, x + 12, y + 12], fill=(220, 220, 90))
        if region is not None:
            image = image.crop(tuple(region))
        out = BytesIO()
        image.save(out, "PNG")
        return out.getvalue()


async def test_the_next_piece_shown_is_read_each_piece_and_planned_with(monkeypatch):
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    fake, guide = Previewed(), Guide({**ANSWER, "next": NEXT_BOX})
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 3), budget=Budget(3000, 1, 60))
    told = made["realtime"]
    assert run.record["outcome"] == "goal_reached", (run.record, told)
    assert told["policy"] == "lookahead" and told["next"]["matched"] >= 3 and told["next"]["missed"] == 0
    assert not told["next"]["given_up"]
    assert any("next" in look for stretch in told.get("looks", []) for look in stretch["looks"]) or told["next"]["read"]


async def test_a_preview_read_wrong_more_often_than_right_is_not_read_any_more(monkeypatch):
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    fake, guide = Previewed(lies=True), Guide({**ANSWER, "next": NEXT_BOX})
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 2), budget=Budget(3000, 1, 60))
    told = made["realtime"]
    assert run.record["outcome"] == "goal_reached", (run.record, told)
    assert told["next"]["given_up"] and told["next"]["missed"] == realtime.PREVIEW_MISSES


async def test_the_level_s_goal_reaches_the_loop_counted_from_where_it_was_read(monkeypatch):
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    seen = []
    place = solver.place

    def watched(*args, **kw):
        seen.append(kw.get("goal"))
        return place(*args, **kw)
    monkeypatch.setattr(solver, "place", watched)
    fake, guide = Falling(), Guide({**ANSWER, "goal": {"lines": 2, "pieces": 20}})
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 2), budget=Budget(3000, 1, 60))
    assert run.record["outcome"] == "goal_reached", run.record
    assert seen[0] == {"lines": 2, "pieces": 20} and seen[-1]["pieces"] < 20 and made["realtime"]["goal"]["lines"] == 2


MAP_WON = ("map", {"kind": "done", "end": "won"})
MAP_LOST = ("map", {"kind": "done", "end": "lost"})


async def test_a_level_said_won_on_a_map_with_fewer_rows_counted_than_it_asked_is_doubted(monkeypatch):
    # (2026-11-21, PLAN §34 F1: a level that asked 2 rows ended with 1 counted on its map, and the model said won.
    # Here the result covers the well as the row goes: the code counts none)
    fake, guide = Falling(result_at=1), Guide({**ANSWER, "goal": {"lines": 3, "pieces": None}}, plays=[MAP_WON, MAP_LOST])
    run, made = await play(fake, guide, None, goal=GoalSpec("play_until_end"), budget=Budget(3000, 1, 60))
    assert run.record["outcome"] == "game_over" and run.record["reason"] == "end_screen", run.record
    assert guide.asked.count("play") == 2 and "counted 0 rows" in json.dumps(guide.told[1])


async def test_a_level_said_won_again_after_the_doubt_stands_and_is_marked(monkeypatch):
    fake, guide = Falling(result_at=1), Guide({**ANSWER, "goal": {"lines": 3, "pieces": None}}, plays=[MAP_WON])
    run, made = await play(fake, guide, None, goal=GoalSpec("play_until_end"), budget=Budget(3000, 1, 60))
    assert run.record["outcome"] == "goal_reached", run.record
    assert made["end"]["doubted"] == {"lines_counted": 0, "lines_asked": 3, "screen": "map"}


async def test_a_level_said_won_on_its_result_is_not_doubted_whatever_was_counted(monkeypatch):
    # (2026-11-21, PLAN §34 F2: the result drawn over the well hid the level's four rows from the count)
    fake = Falling(result_at=1)
    guide = Guide({**ANSWER, "goal": {"lines": 3, "pieces": None}}, plays=[("result", {"kind": "done", "end": "won"})])
    run, made = await play(fake, guide, None, goal=GoalSpec("play_until_end"), budget=Budget(3000, 1, 60))
    assert run.record["outcome"] == "goal_reached" and guide.asked.count("play") == 1, run.record
    assert "doubted" not in made["end"]


class Asked(Guide):
    """Answers the setup question with each answer in turn, the last again."""

    def __init__(self, answers, **kw):
        super().__init__(answers[0], **kw)
        self.answers = list(answers)

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "inputs" in context and "families" not in context:
            self.answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return await super().complete(request)


async def test_what_only_shows_once_the_game_goes_is_asked_of_it_once_it_is_started(monkeypatch, tmp_path):
    # (2026-11-21, F3 on the web: the one question was answered of a menu over the well, which shows no next piece)
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    fake = Previewed()
    guide = Asked([{**ANSWER, "next": realtime.NEXT_LATER}, {**ANSWER, "start": None, "next": NEXT_BOX, "goal": {"lines": 3, "pieces": None}}])
    run, made = await play(fake, guide, None, goal=GoalSpec("reach_value", 3), budget=Budget(3000, 1, 60))
    told = made["realtime"]
    assert run.record["outcome"] == "goal_reached", (run.record, told)
    assert guide.asked.count("setup") == 2 and told["looked_again"]["next"] == NEXT_BOX
    assert told["next"]["matched"] >= 2 and told["goal"] == {"lines": 3, "pieces": None}
    # an answer that gives no next piece, nor says one shows later, is not asked again (the toy without a preview)
    assert realtime.understood(ANSWER, list(SIZE), frozenset({"keys"})).get("next_later") is None


def test_near_a_levels_end_the_piece_after_the_next_is_weighed_too():
    # (PLAN §34, F5) with a piece limit and few pieces left, the unseen piece after the preview is averaged over
    # the seven; with many left, or no limit, the look ahead is the two pieces as before
    stack = Cells({(r, c) for r in (18, 19) for c in range(10) if c not in (4, 5)})
    piece, coming = Cells({(0, 0), (0, 1), (1, 0), (1, 1)}), Cells({(0, 0), (0, 1), (0, 2), (0, 3)})
    near = solver.place(stack, piece, 20, 10, policy="lookahead", queue=[coming], goal={"lines": 3, "pieces": 5})
    far = solver.place(stack, piece, 20, 10, policy="lookahead", queue=[coming], goal={"lines": 3, "pieces": 30})
    free = solver.place(stack, piece, 20, 10, policy="lookahead", queue=[coming])
    assert near["nodes"] > far["nodes"] == free["nodes"]
    # the square still goes in the gap it fills: two rows cleared now
    assert near["lines"] == 2 and near["column"] == 4


def test_a_preview_box_said_wider_than_the_frame_round_it_is_read():
    # (PLAN §34, F5) the box took in what is drawn round the preview at its edge (a frame, the word NEXT): read as
    # lit, it stretched the piece's extent over the whole box, and no next piece was read in a whole level
    image = Image.new("RGB", (100, 70), (30, 40, 130))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 3, 40], fill=(200, 200, 230))
    draw.rectangle([30, 0, 75, 6], fill=(240, 240, 240))
    for r, c in ((0, 2), (1, 0), (1, 1), (1, 2)):
        draw.rectangle([22 + c * 18, 18 + r * 18, 22 + c * 18 + 16, 18 + r * 18 + 16], fill=(230, 140, 20))
    out = BytesIO()
    image.save(out, "PNG")
    assert realtime.Preview([0, 0, 100, 70]).read(out.getvalue(), None, 4) == rules.Cells({(0, 2), (1, 0), (1, 1), (1, 2)})


# ── pieces of other sizes (PLAN §34, F6) ─────────────────────────────

def test_the_simulated_game_deals_pieces_of_five_cells_or_of_one_to_four():
    # the eighteen one-sided pieces of five, each a kind of its own; the small set has pieces of one to four
    assert len({rules.kind(piece) for piece in rules.PIECE_SETS["pentomino"]}) == 18
    assert {len(piece) for piece in rules.PIECE_SETS["small"]} == {1, 2, 3, 4}
    for name, size in (("pentomino", {5}), ("small", {1, 2, 3, 4})):
        game = policies.play("falling", "lookahead", 2000, 0, 30, (), {"pieces": name})
        assert game["moves"] > 0
        state = rules.initial_state({**rules.DEFAULT_RULES, "pieces": name}, 12345)
        assert len(state["piece"]) in size


def test_the_piece_after_the_next_is_averaged_over_the_pieces_the_game_deals():
    stack = Cells({(r, c) for r in (18, 19) for c in range(10) if c not in (4, 5)})
    piece, coming = Cells({(0, 0), (0, 1), (1, 0), (1, 1)}), Cells({(0, 0), (0, 1), (0, 2), (0, 3)})
    goal = {"lines": 3, "pieces": 5}
    seven = solver.place(stack, piece, 20, 10, policy="lookahead", queue=[coming], goal=goal)
    none = solver.place(stack, piece, 20, 10, policy="lookahead", queue=[coming], goal=goal, dealt=())
    five = solver.place(stack, piece, 20, 10, policy="lookahead", queue=[coming], goal=goal,
                        dealt=rules.PIECE_SETS["pentomino"])
    assert none["nodes"] < seven["nodes"] < five["nodes"]
    assert seven["column"] == five["column"] == 4


def test_a_preview_of_pieces_of_five_is_read_as_such():
    preview = realtime.Preview([0, 0, 90, 70])
    for piece in rules.PIECE_SETS["pentomino"]:
        assert preview.read(_drawn(piece, 10), size={5}) == rules.normal(piece), piece


def test_a_square_in_the_preview_is_one_cell_or_four_by_the_width_of_cells_read_right_before():
    # a game of pieces of one to four cells: a filled square is a piece of one or the square of four, as the
    # preview's cells are wide; that width is learned from the reads that came true
    preview = realtime.Preview([0, 0, 90, 70])
    one = _drawn([(0, 0)], 24)
    assert preview.read(one, size={1, 2, 3, 4}) == O
    assert preview.read(_drawn([(0, 0), (0, 1)], 24), size={1, 2, 3, 4}) == Cells({(0, 0), (0, 1)})
    preview.came_true()
    assert preview.read(one, size={1, 2, 3, 4}) == Cells({(0, 0)})


MIXED = [[(0, 0)], [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0)], [(0, 0), (0, 1)], [(0, 0), (1, 0), (1, 1)],
         [(0, 0), (0, 1), (0, 2)], [(0, 1), (1, 0), (1, 1), (1, 2)], [(0, 0), (0, 1), (1, 0), (1, 1)]]


async def test_a_game_of_pieces_of_one_to_five_cells_is_played_without_waiting_on_each_size(monkeypatch):
    # (PLAN §34, F6) the loop took the size most pieces had for the game's: a piece of five was looked at again
    # as a misread each time it came, and a piece of one or two at the well's top was waited on as one still
    # coming in from above
    import test_realtime
    monkeypatch.setattr(test_realtime, "PIECES", MIXED)
    monkeypatch.setattr(realtime, "IDLE_S", 0.3)
    events = []
    note = realtime.Loop._note

    def noted(self, **what):
        events.append(what.get("event"))
        return note(self, **what)
    monkeypatch.setattr(realtime.Loop, "_note", noted)
    run, made = await play(Falling(every=4), Guide(ANSWER), None, goal=GoalSpec("reach_value", 4),
                           budget=Budget(3000, 1, 60))
    told = made["realtime"]
    # before: the piece of five that came second was doubted until it lay, and the well was let go unread
    assert run.record["outcome"] == "goal_reached", (run.record, told)
    assert told["effects"]["confirmed"] == told["pieces"] and told["resyncs"] == 0
    assert events.count("odd_piece") <= 3
