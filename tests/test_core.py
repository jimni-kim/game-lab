"""The game families' pure rule models and policies (src/gamelab/rules/*.py and
solvers/*.py), driven by the shared fixtures in tests/fixtures/game/<family>/: input JSON -> expected output JSON.

The same fixtures become the parity reference when the rule models and solvers move to Rust (docs/plans/
2026-11-09-generic-game-plugin-implementation.md §3.2, §11.4), so each case pins a rule a player would
see broken: a merge counted twice, a line left uncleared, an unknown cell played as empty, a spawn
accepted that the game never makes.

Regression this prevents: a rule, legality or transition check that drifts from the game, found only
when a real board is played wrong; and a core that starts depending on IO, set order or an unseeded
random source, so the two hosts would choose different moves from the same board."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest
from gamelab.rules import block_placement, klondike, match3, minesweeper, slide_merge
from gamelab.solvers import (block_placement as block_placement_solver, klondike as klondike_solver, match3 as match3_solver,
                          minesweeper as minesweeper_solver, slide_merge as slide_merge_solver)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/game"
MODELS = {"block_placement": block_placement, "klondike": klondike, "match3": match3, "minesweeper": minesweeper, "slide_merge": slide_merge}
SOLVERS = {"block_placement": block_placement_solver, "klondike": klondike_solver, "match3": match3_solver, "minesweeper": minesweeper_solver,
           "slide_merge": slide_merge_solver}
# modules a pure rule model or solver may not import even from the standard library: IO, time, randomness, logging
IO_MODULES = {"os", "io", "time", "random", "logging", "pathlib", "socket", "datetime", "asyncio",
              "subprocess", "threading", "secrets", "uuid", "shutil", "tempfile"}


def _cases():
    for family in sorted(MODELS):
        for path in sorted((FIXTURES / family).glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for case in data["cases"]:
                yield pytest.param(family, data["op"], case, id=f"{family}/{path.stem}/{case['name']}")


@pytest.mark.parametrize(("family", "op", "case"), list(_cases()))
def test_core_matches_the_shared_fixture(family, op, case):
    # a fixture names its operation; a policy's is the solver's, every other the rule model's
    run = getattr(MODELS[family], op, None) or getattr(SOLVERS[family], op)
    if "error" in case:
        with pytest.raises(ValueError, match=case["error"]):
            run(**case["input"])
        return
    got = json.loads(json.dumps(run(**case["input"])))   # JSON-shaped: no tuples or objects leak out
    expected = case["expected"]
    if isinstance(expected, dict) and isinstance(got, dict):
        got = {k: got.get(k) for k in expected}           # policies may say more (depth, nodes)
    assert got == expected


@pytest.mark.parametrize("module", [*MODELS.values(), *SOLVERS.values()], ids=lambda module: module.__name__)
def test_a_rule_model_or_solver_imports_only_pure_standard_modules(module):
    """§3.2: rule models and solvers are the part ported to Rust; IO, clocks and unseeded randomness stay
    in the session and readers, so the port is mechanical and both hosts choose the same move. A solver's
    relative import is its rule model, pure by the same test."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {(n.module or "").split(".")[0] for n in ast.walk(tree)
              if isinstance(n, ast.ImportFrom) and not n.level}
    names -= {"__future__"}
    assert names <= set(sys.stdlib_module_names), names - set(sys.stdlib_module_names)
    assert not names & IO_MODULES, names & IO_MODULES


def _play(family, rules, policy, budget, seed, moves):
    """A whole simulated game from a seed: the policy's moves, chosen from what the screen shows, the game's
    own draws."""
    model, solver = MODELS[family], SOLVERS[family]
    draws = _draws(seed)
    state = model.initial_state(rules, next(draws))
    played = []
    for _ in range(moves):
        chosen = solver.choose(model.shown(state), rules, policy, budget)["action"]
        if chosen is None:
            break
        played.append(chosen)
        state = model.next_state(state, rules, chosen, next(draws))
    return state, played


def _draws(seed):
    # a fixed linear congruential stream: the test needs repeatable draws, not good randomness
    value = seed
    while True:
        value = (value * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        yield value >> 33


def test_the_same_board_and_seed_play_the_same_game():
    """Determinism (§3.2-3): same input and seed, same output, for both policies of both families."""
    for family in sorted(MODELS):
        rules = MODELS[family].DEFAULT_RULES
        for policy in SOLVERS[family].POLICIES:
            assert _play(family, rules, policy, 200, 7, 20) == _play(family, rules, policy, 200, 7, 20)


def test_the_block_search_outscores_the_greedy_baseline_on_the_same_budget():
    """§12 stage 6: the search over the remaining pieces' order is compared with the 1-move greedy
    baseline on the same starting boards, piece streams and compute budget."""
    rules = block_placement.DEFAULT_RULES
    totals = {}
    for policy in ("greedy", "search"):
        totals[policy] = sum(_play("block_placement", rules, policy, 1500, seed, 60)[0]["score"] for seed in (2, 6, 7))
    assert totals["search"] > totals["greedy"], totals


def test_expectimax_reaches_a_higher_tile_than_the_baseline_on_the_same_budget():
    """§12 stage 6: expectimax with depth from the compute budget against the fixed-order baseline,
    same seeds and the same number of moves."""
    rules = slide_merge.DEFAULT_RULES
    best = {}
    for policy in ("baseline", "expectimax"):
        best[policy] = sum(max(max(row) for row in _play("slide_merge", rules, policy, 100, seed, 150)[0]["grid"])
                           for seed in (1, 2))
    assert best["expectimax"] > best["baseline"], best


def test_the_minesweeper_solver_wins_more_games_than_the_baseline_on_the_same_budget():
    """Family expansion plan §7.1: counting every layout that agrees with the screen against each number
    alone and a guess, same seeds, same budget. A solver that guesses where the numbers prove a cell safe,
    or opens a cell they prove a mine, loses games the baseline does not."""
    rules = minesweeper.DEFAULT_RULES
    wins = {}
    for policy in ("baseline", "solver"):
        ends = [_play("minesweeper", rules, policy, 20000, seed, 100)[0] for seed in range(40)]
        wins[policy] = sum(minesweeper.status(minesweeper.shown(end), rules, {"objective": "play_until_end"})["goal"]
                           == "reached" for end in ends)
    assert wins["solver"] > wins["baseline"] and wins["solver"] >= 36, wins


def test_games_played_with_the_count_of_mines_known_are_the_ones_recorded_before_it_could_be_unknown():
    """PLAN §10: letting the count be unknown changes nothing where it is known. Whole games recorded before
    the change (three sizes, both policies) are played again cell for cell."""
    recorded = json.loads((FIXTURES / "minesweeper_games.json").read_text(encoding="utf-8"))["games"]
    for game in recorded:
        _, played = _play("minesweeper", game["rules"], game["policy"], game["budget"], game["seed"], 500)
        assert [action["cell"] for action in played] == game["cells"], (game["rules"], game["policy"], game["seed"])


def _play_believing(rules, believed, seed):
    """A replay game by `rules` whose solver is told `believed`: how it ended, as the game itself judges it."""
    draws = _draws(seed)
    state = minesweeper.initial_state(rules, next(draws))
    goal = {"objective": "play_until_end"}
    for _ in range(rules["rows"] * rules["cols"]):
        if minesweeper.status(minesweeper.shown(state), rules, goal)["terminal"]:
            break                   # a real game takes no move once it is won or lost
        chosen = SOLVERS["minesweeper"].choose(minesweeper.shown(state), believed, "solver", 20000)["action"]
        if chosen is None:
            return "stuck"
        state = minesweeper.next_state(state, rules, chosen, next(draws))
    seen = minesweeper.status(minesweeper.shown(state), rules, goal)
    judged = minesweeper.status(minesweeper.shown(state), believed, goal)
    if seen["goal"] != "reached":
        return "lost" if seen["terminal"] else "stuck"
    return "won" if judged["goal"] == "reached" else "won_unjudged"


def test_the_minesweeper_solver_wins_about_as_often_without_the_count_of_mines():
    """PLAN §10: a screen need not say how many mines there are. The numbers alone win nearly as many games
    (measured 380 of 400 against 379 with the count), and a game won is judged won from the screen."""
    rules = minesweeper.DEFAULT_RULES
    ends = [_play_believing(rules, {**rules, "mines": None}, seed) for seed in range(40)]
    assert ends.count("won") >= 35 and "stuck" not in ends, ends


@pytest.mark.parametrize("believed", [8, 12])
def test_a_wrong_count_of_mines_does_not_stop_the_game(believed):
    """A count read wrongly used to leave nearly every game stuck or called won too early (no cell the count
    allowed, or as many cells open as it expected). The move is now chosen without a count that leaves none,
    and a game is not won while a number leaves a cell undecided."""
    rules = minesweeper.DEFAULT_RULES
    ends = [_play_believing(rules, {**rules, "mines": believed}, seed) for seed in range(40)]
    assert ends.count("won") >= 30 and "stuck" not in ends, ends


def test_the_replay_game_needs_its_count_of_mines():
    rules = {**minesweeper.DEFAULT_RULES, "mines": None}
    with pytest.raises(ValueError, match="mines_unknown"):
        minesweeper.next_state(minesweeper.initial_state(rules, 1), rules, {"kind": "open", "cell": [4, 4]}, 1)


def test_a_block_score_goal_without_a_target_is_not_judged_reached_or_missed():
    """A score goal that names no value cannot be decided from the board: the goal is unknown,
    rather than the judgement failing and ending the game as an error."""
    state = {"board": [[0] * 8 for _ in range(8)], "tray": [[[0, 0]], None, None], "score": 40}
    seen = block_placement.status(state, block_placement.DEFAULT_RULES, {"objective": "reach_value"})
    assert seen["goal"] == "unknown"
