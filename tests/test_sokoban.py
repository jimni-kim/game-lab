"""The sokoban family: a push goes only onto free floor, a level is won once every box stands on a goal, the search
solves levels that need a box moved off and round and never pushes into a dead corner, its plan is kept from move to
move, and every simulated level can be won."""
from gamelab import catalog
from gamelab.rules import sokoban as rules
from gamelab.solvers import sokoban as solver

CELLS = {" ": rules.FLOOR, "#": rules.WALL, ".": rules.GOAL, "$": rules.BOX, "*": rules.BOX_ON_GOAL,
         "@": rules.PLAYER, "+": rules.PLAYER_ON_GOAL}


def level(*lines):
    width = max(map(len, lines))
    grid = [[CELLS[ch] for ch in line.ljust(width, "#")] for line in lines]
    return {"grid": grid, "score": None, "scene": "playing"}, {"rows": len(grid), "cols": width}


def play(state, size, policy="search", budget=20000, moves=500, memory=None):
    """The level played to its end by a policy from what the screen shows; the moves and the last state."""
    played = []
    for _ in range(moves):
        if memory is not None:
            memory = solver.recall(memory, state, size)
            chosen = solver.choose(state, size, policy, budget, [], memory)["action"]
        else:
            chosen = solver.choose(state, size, policy, budget)["action"]
        if chosen is None:
            break
        assert chosen in rules.legal_actions(state, size)
        played.append(chosen)
        state = rules.next_state(state, size, chosen, 0)
    return played, state


def test_a_box_is_pushed_only_onto_free_floor():
    state, size = level("#######",
                        "#@$$ .#",
                        "#   $.#",
                        "# $#  #",
                        "#   ..#",
                        "#######")
    right = rules.move(state["grid"], "right")
    assert right == {"kind": "move", "direction": "right", "cell": [1, 2]}
    # a box against another box stays where it is, and so does the player
    assert rules.step(state["grid"], "right") is None and right not in rules.legal_actions(state, size)
    # against a wall too: the box below the player's next cell
    down, _ = level("#######",
                    "#     #",
                    "# @   #",
                    "# $   #",
                    "#######")
    assert rules.step(down["grid"], "down") is None
    pushed = rules.step(state["grid"], "down")
    assert pushed[1][1] == rules.FLOOR and pushed[2][1] == rules.PLAYER


def test_a_level_is_won_once_every_box_stands_on_a_goal_and_shows_its_message():
    state, size = level("######",
                        "#@$ .#",
                        "######")
    first = rules.next_state(state, size, rules.move(state["grid"], "right"), 0)
    assert first["scene"] == "playing" and not rules.status(first, size, {"objective": "play_until_end"})["terminal"]
    last = rules.next_state(first, size, rules.move(first["grid"], "right"), 0)
    assert last["scene"] == "won" and last["grid"][1][4] == rules.BOX_ON_GOAL
    assert rules.status({**last, "scene": "playing"}, size, {"objective": "play_until_end"}) == {"terminal": True, "goal": "reached"}
    assert rules.legal_actions({**last, "scene": "playing"}, size) == []


# the box beside the player cannot go straight on (a box on a goal stands in its way): it is pushed off its row, the
# other box moved on to the far goal, and it is brought round and back
ROUND = ("#######",
         "#     #",
         "#     #",
         "#.*$ @#",
         "#     #",
         "#     #",
         "#######")
# the first push the rules list sends the box up against the top wall, where no goal is: it never leaves that row
TRAP = ("#######",
        "#     #",
        "# $  .#",
        "# @   #",
        "#######")
# two boxes, one in the other's way
TWO = ("#######",
       "#.    #",
       "#.$$ @#",
       "#     #",
       "#######")


def test_the_search_solves_small_levels_and_its_first_step_is_a_legal_move():
    for lines in (ROUND, TRAP, TWO):
        state, size = level(*lines)
        first = solver.choose(state, size, "search", 20000)["action"]
        assert first in rules.legal_actions(state, size), lines
        played, end = play(state, size)
        assert end["scene"] == "won", lines
    # the box in the way leaves its row and comes back to it
    state, size = level(*ROUND)
    rows = set()
    for action in play(state, size)[0]:
        state = rules.next_state(state, size, action, 0)
        rows |= {r for r, row in enumerate(state["grid"]) for cell in row if cell in rules.BOXES}
    assert rows != {3}


def test_the_search_never_pushes_a_box_onto_a_cell_no_goal_can_be_reached_from():
    state, size = level(*TRAP)
    floor, goals, _, _ = solver._parts(state["grid"])
    live = set().union(*solver.distances(floor, goals).values())
    # the rows along the top and bottom walls hold no goal: dead; the goal's own row is not
    assert not {(1, c) for c in range(1, 6)} & live and not {(3, c) for c in range(1, 6)} & live
    assert {(2, c) for c in range(2, 6)} <= live
    trap = rules.move(state["grid"], "up")
    assert solver.choose(state, size, "baseline", 1)["action"] == trap
    assert solver.choose(state, size, "search", 20000)["action"] != trap
    played, end = play(state, size)
    assert end["scene"] == "won"
    # replayed, no push in the plan leaves a box on a dead cell
    replay = state
    for action in played:
        replay = rules.next_state(replay, size, action, 0)
        boxes = {(r, c) for r, row in enumerate(replay["grid"]) for c, cell in enumerate(row) if cell in rules.BOXES}
        assert boxes <= live


def test_a_box_already_on_a_dead_cell_leaves_no_move_worth_playing():
    state, size = level("######",
                        "#$  .#",
                        "#  @ #",
                        "######")
    assert rules.legal_actions(state, size)
    assert solver.choose(state, size, "search", 20000) == {"action": None, "nodes": 0}


def test_past_its_budget_the_search_still_plays_a_legal_move_towards_a_win():
    state, size = level(*TWO)
    found = solver.plan(state["grid"], 2)
    assert not found["solved"] and found["nodes"] == 2 and found["steps"]
    chosen = solver.choose(state, size, "search", 2)
    assert chosen["action"] in rules.legal_actions(state, size) and chosen["nodes"] == 2
    # one node: nothing weighed beyond the start, the baseline's move
    assert solver.choose(state, size, "search", 1)["action"] == solver.choose(state, size, "baseline", 1)["action"]
    # played on with a tiny budget, the level is still won in the end
    _, end = play(state, size, budget=3, moves=400, memory={"grid": None, "plan": []})
    assert end["scene"] == "won"


def test_the_plan_is_kept_between_moves_and_given_up_when_the_screen_does_not_follow_it():
    state, size = level(*ROUND)
    memory = solver.recall(None, state, size)
    assert memory == {"grid": state["grid"], "plan": []}
    first = solver.choose(state, size, "search", 20000, [], memory)
    assert first["nodes"] > 0 and memory["plan"][0] == first["action"]
    planned = list(memory["plan"])
    after = rules.next_state(state, size, first["action"], 0)
    memory = solver.recall(memory, after, size)
    assert memory["plan"] == planned[1:]
    # the next move comes from the plan, no node searched
    assert solver.choose(after, size, "search", 20000, [], memory) == {"action": planned[1], "nodes": 0}
    # a screen with unread cells keeps it; one the plan does not lead to starts with none
    hidden = rules.hidden_state(after)
    assert solver.recall(memory, hidden, size) is memory
    assert solver.recall(memory, state, size) == {"grid": state["grid"], "plan": []}
    played, end = play(state, size, memory=solver.recall(None, state, size))
    assert end["scene"] == "won" and len(played) == len(planned)


def test_every_simulated_level_can_be_won_and_the_same_seed_gives_the_same_level():
    for rows, cols in ((3, 3), (5, 7), (9, 9), (10, 10), (12, 12)):
        size = {"rows": rows, "cols": cols}
        for seed in range(8):
            state = rules.initial_state(size, seed)
            assert state == rules.initial_state(size, seed)
            assert not rules.validate(state, size) and not rules.solved(state["grid"])
            found = solver.plan(state["grid"], 20000)
            assert found["solved"], (rows, cols, seed)


def test_the_family_loads_and_a_live_game_searches_with_its_own_floor_of_nodes():
    family = catalog.family("sokoban")
    assert family.policies()[:2] == ["baseline", "search"] and family.compute(200) == 20000
    played = family.bind({"rules": {"rows": 10, "cols": 10}})
    state = played.initial_state(4)
    memory = None
    for _ in range(300):
        memory = played.recall(memory, state)
        action = played.choose(state, "search", played.compute(200), memory=memory)
        if action is None:
            break
        state = played.next_state(state, action, 0)
    assert state["scene"] == "won"


def test_the_cell_under_the_player_is_told_by_the_state_before_not_by_the_picture():
    """The figure hides the goal it stands on: read as on plain floor, it is on a goal where the state before had
    one, and with nothing before, where the board has a goal fewer than boxes."""
    before = {"grid": [[1, 1, 1, 1], [1, 5, 2, 1], [1, 3, 2, 1], [1, 3, 0, 1], [1, 1, 1, 1]], "score": None, "scene": "playing"}
    seen = {"grid": [[1, 1, 1, 1], [1, 0, 5, 1], [1, 3, 2, 1], [1, 3, 0, 1], [1, 1, 1, 1]], "score": None, "scene": "playing"}
    told = rules.beneath(seen, before)
    assert told["grid"][1][2] == rules.PLAYER_ON_GOAL
    assert rules.validate(told, {"rows": 5, "cols": 4}) == []
    assert rules.assess(before, {"kind": "move", "direction": "right", "cell": [1, 2]}, told, {"rows": 5, "cols": 4})["effect"] == "confirmed"
    # nothing before: the counts say it
    assert rules.beneath(seen)["grid"][1][2] == rules.PLAYER_ON_GOAL
    # a figure read as on a goal where the level has plain floor
    off = {**before, "grid": [[1, 1, 1, 1], [1, 6, 2, 1], [1, 3, 2, 1], [1, 3, 0, 1], [1, 1, 1, 1]]}
    assert rules.beneath(off, before)["grid"][1][1] == rules.PLAYER
    assert rules.beneath(off)["grid"][1][1] == rules.PLAYER
    # a board with cells unread and nothing before is left as read
    partial = {**seen, "grid": [[1, 1, 1, 1], [1, 0, 5, 1], [1, None, 2, 1], [1, 3, 0, 1], [1, 1, 1, 1]]}
    assert rules.beneath(partial) == partial
    assert rules.beneath(before, before) == before


def test_the_reader_keeps_one_look_for_the_figure_wherever_it_stands():
    from gamelab.kits.sokoban import SokobanKit
    assert SokobanKit._key(rules.PLAYER_ON_GOAL) == rules.PLAYER == SokobanKit._key(rules.PLAYER)
    assert SokobanKit._key(rules.BOX_ON_GOAL) == rules.BOX_ON_GOAL
    assert str(rules.PLAYER_ON_GOAL) not in SokobanKit({"rows": 5, "cols": 4}).options()
