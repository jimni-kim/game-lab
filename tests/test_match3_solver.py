"""The match-three solver (PLAN §23, Y4): a move is worth meeting the level's goals far above getting on with
them, that above a piece with a power, that above the pieces it clears; and what it leaves is looked at only among
the pieces known, never by what may come in at the top."""
from gamelab.rules import match3 as rules
from gamelab.solvers.match3 import _ahead, _glance, choose

R, O, Y, G, B, P = 1, 2, 3, 4, 5, 6
WAYS = {"square": False, "swap_power": True, "pair_power": True, "tap_power": False}


def rules_for(grid, **more):
    return {"rows": len(grid), "cols": len(grid[0]), "colours": 6, **WAYS, **more}


def pick(grid, **more):
    return choose({"grid": grid, "score": None, "scene": "playing"}, rules_for(grid, **more), "greedy", 400)["action"]


# a swap at the top lines up four reds (a line piece, five cleared at most); one at the bottom lines up three greens
BOARD = [[R, R, B, R, Y, P],
         [O, Y, R, P, O, B],
         [Y, B, P, O, P, Y],
         [B, O, Y, B, Y, O],
         [P, Y, O, P, B, P],
         [G, G, B, G, O, Y]]


def test_with_no_goal_the_move_that_earns_a_piece_with_a_power_is_chosen():
    assert pick(BOARD) == {"kind": "swap", "cell": [0, 2], "to": [1, 2]}


def test_a_goal_about_one_colour_outweighs_a_piece_with_a_power_of_another():
    goals = [{"kind": "collect", "target": G, "count": 10}]
    assert pick(BOARD, goals=goals) == {"kind": "swap", "cell": [5, 2], "to": [5, 3]}
    # a goal met already gains nothing more: the line piece again
    met = [{"kind": "collect", "target": G, "count": 0}]
    assert pick(BOARD, goals=met) == {"kind": "swap", "cell": [0, 2], "to": [1, 2]}


def test_a_move_that_meets_every_goal_is_chosen_over_any_other():
    goals = [{"kind": "collect", "target": G, "count": 3}, {"kind": "collect", "target": R, "count": 40}]
    assert pick(BOARD, goals=goals) == {"kind": "swap", "cell": [0, 2], "to": [1, 2]}      # reds: more of the goals
    goals = [{"kind": "collect", "target": G, "count": 3}, {"kind": "other", "target": None, "count": 4000}]
    assert pick(BOARD, goals=goals) == {"kind": "swap", "cell": [5, 2], "to": [5, 3]}      # the greens meet them


def test_two_pieces_with_a_power_swapped_together_are_worth_more_than_a_line():
    line, area = rules.POWERED * rules.LINE + Y, rules.POWERED * rules.AREA + O
    grid = [row[:] for row in BOARD]
    grid[3][3], grid[3][4] = line, area
    assert pick(grid) == {"kind": "swap", "cell": [3, 3], "to": [3, 4]}


def test_what_a_move_leaves_is_looked_at_among_the_pieces_known_only():
    unknown = None
    # three blues would line up only with the unknown cell's piece: no line
    grid = [[unknown, B, B],
            [B, O, Y],
            [P, Y, O]]
    assert _glance(grid, (1, 0), (0, 0), rules_for(grid)) is None
    assert _ahead(grid, rules_for(grid), []) == 0
    # with a known blue there, it is one
    grid[0][0] = O
    taken, cleared, made = _glance(grid, (1, 0), (0, 0), rules_for(grid))
    assert (taken, cleared, made) == ({B: 3}, 3, 0)


def test_things_fixed_beside_a_line_are_hit_once_a_wave_and_count_towards_a_goal_about_them():
    X = rules.BLOCK
    grid = [[X, P, Y, P],
            [R, R, B, R],
            [P, Y, R, Y],
            [Y, P, X, O]]
    result = rules.apply({"grid": grid, "score": None, "scene": "playing"}, rules_for(grid), {"kind": "swap", "cell": [1, 2], "to": [2, 2]})
    assert result["taken"][X] == 1 and result["hit"] == [[0, 0]]
    assert rules.apply({"grid": grid, "score": None, "scene": "playing"}, rules_for(grid, beside=False),
                       {"kind": "swap", "cell": [1, 2], "to": [2, 2]})["hit"] == []
