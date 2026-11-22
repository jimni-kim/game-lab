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
from gamelab.rules import block_placement, falling, klondike, match3, minesweeper, slide_merge
from gamelab.solvers import (block_placement as block_placement_solver, falling as falling_solver, klondike as klondike_solver,
                          match3 as match3_solver, minesweeper as minesweeper_solver, slide_merge as slide_merge_solver)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/game"
MODELS = {"block_placement": block_placement, "falling": falling, "klondike": klondike, "match3": match3, "minesweeper": minesweeper, "slide_merge": slide_merge}
SOLVERS = {"block_placement": block_placement_solver, "falling": falling_solver, "klondike": klondike_solver, "match3": match3_solver, "minesweeper": minesweeper_solver,
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


# the loop and what it stands on: none of it may lean on the evaluation's callers (`eval web`, `eval android`,
# the demo server), which are the loop's users, not its parts (PLAN §14)
LOOP = ("agent", "adaptation", "direct", "carry", "motion", "recovery", "made", "request", "catalog", "surface",
        "model", "pixels", "samples", "run", "contracts", "execution", "resources", "adapters.generated",
        "environments.web", "environments.android")


def test_nothing_outside_the_evaluation_imports_it():
    source = ROOT / "src/gamelab"
    for path in source.rglob("*.py"):
        if path.relative_to(source).parts[0] == "eval":
            continue
        package = ".".join(("gamelab", *path.relative_to(source).parent.parts))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                base = package.rsplit(".", node.level - 1)[0] if node.level else ""
                names = [".".join(filter(None, (base, node.module or "")))]
                names += [f"{names[0]}.{alias.name}" for alias in node.names]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            assert not any(name == "gamelab.eval" or name.startswith("gamelab.eval.") for name in names), (path, names)


def test_the_loop_runs_without_the_evaluation_loaded():
    import subprocess
    loaded = subprocess.run([sys.executable, "-c", "import importlib, sys\n"
                             f"for name in {LOOP!r}: importlib.import_module('gamelab.' + name)\n"
                             "print(sorted(name for name in sys.modules if name.startswith('gamelab.eval')))"],
                            capture_output=True, text=True, check=True)
    assert loaded.stdout.strip() == "[]", loaded.stdout


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


def test_games_played_with_the_count_of_mines_known_are_the_ones_recorded_before_it_could_be_unknown(monkeypatch):
    """PLAN §10: letting the count be unknown changes nothing where it is known. Whole games recorded before
    the change (three sizes, both policies) are played again cell for cell. The guesses recorded are the safest
    cell's: weighing a guess by what follows it (PLAN §33 session P2) is left out here, it chooses otherwise."""
    monkeypatch.setattr(minesweeper_solver, "GUESS_WEIGHED", 0)
    monkeypatch.setattr(minesweeper_solver, "ENDGAME_LAYOUTS", 0)
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


# ── PLAN §33, session P: the solvers' strength ─────────────────────────

def test_a_game_that_promises_the_first_cell_alone_may_lay_mines_around_it_and_is_begun_in_a_corner():
    """P1/P4: `first_open` is the replay game's promise. "cell" keeps only the opened cell free, so the cells
    around it may hold mines; a solver told so opens a corner first (fewest cells around it)."""
    rules = {**minesweeper.DEFAULT_RULES, "first_open": "cell"}
    beside = 0
    for seed in range(40):
        state = minesweeper.next_state(minesweeper.initial_state(rules, seed), rules, {"kind": "open", "cell": [4, 4]}, seed)
        mines = {tuple(cell) for cell in state["mines"]}
        assert (4, 4) not in mines and len(mines) == rules["mines"]
        beside += any(cell in mines for cell in minesweeper.neighbours(4, 4, 9, 9))
        around = minesweeper.next_state(minesweeper.initial_state(minesweeper.DEFAULT_RULES, seed), minesweeper.DEFAULT_RULES,
                                        {"kind": "open", "cell": [4, 4]}, seed)
        assert not any(tuple(cell) in set(minesweeper.neighbours(4, 4, 9, 9)) for cell in around["mines"])
    assert beside > 10, beside
    fresh = minesweeper.shown(minesweeper.initial_state(rules, 0))
    assert minesweeper_solver.choose(fresh, rules, "solver", 20000)["action"]["cell"] in ([0, 0], [0, 8], [8, 0], [8, 8])
    assert minesweeper_solver.choose(fresh, minesweeper.DEFAULT_RULES, "solver", 20000)["action"]["cell"] == [4, 4]


def test_expectimax_reaches_1024_on_a_small_budget():
    """P2: the line evaluation (free cells, a line rising one way, joins, small tiles over large) with the
    table of grids met reaches 1024 within 900 moves on these seeds at a quarter of the budget the earlier
    evaluation was measured at (where it reached 2048 in two games of three)."""
    for seed in (1, 2, 3):
        end, _ = _play("slide_merge", slide_merge.DEFAULT_RULES, "expectimax", 500, seed, 900)
        assert max(max(row) for row in end["grid"]) >= 1024, (seed, end["grid"])


def _klondike(policy, budget, seed, moves=600):
    """A whole simulated deal with what the solver remembers carried from screen to screen: how it ended."""
    rules = klondike.DEFAULT_RULES
    draws = _draws(seed)
    state, memory = klondike.initial_state(rules, next(draws)), None
    for count in range(moves):
        seen = klondike.shown(state)
        memory = klondike_solver.recall(memory, seen, rules)
        chosen = klondike_solver.choose(seen, rules, policy, budget, (), memory)["action"]
        if chosen is None:
            return state, count
        state = klondike.next_state(state, rules, chosen, next(draws))
    return state, moves


def test_a_klondike_policy_stops_once_the_stock_went_round_for_nothing():
    """P3 (a): with what `recall` keeps, greedy turns the stock once all the way round with nothing else to
    play and then has no move, where it turned it for ever (56% of 1000 deals ran into the cap of moves)."""
    for seed in range(12):
        end, count = _klondike("greedy", 30, seed)
        assert count < 600, seed
        assert end["scene"] == klondike.WON or klondike_solver.choose(klondike.shown(end), klondike.DEFAULT_RULES, "greedy", 30)["action"] is not None


def test_what_is_remembered_of_the_stock_is_the_cards_it_showed_in_their_order():
    rules = klondike.DEFAULT_RULES
    state, memory = klondike.initial_state(rules, 5), None
    turned = []
    memory = klondike_solver.recall(memory, klondike.shown(state), rules)
    assert memory["stock"] == [None] * 24 and memory["waste"] == [] and memory["rounds"] == 0
    for _ in range(24):
        state = klondike.next_state(state, rules, {"kind": "draw"}, 0)
        memory = klondike_solver.recall(memory, klondike.shown(state), rules)
        turned.append(klondike.shown(state)["waste"])
    assert memory["waste"] == turned and memory["stock"] == [] and memory["rounds"] == 0
    state = klondike.next_state(state, rules, {"kind": "recycle"}, 0)
    memory = klondike_solver.recall(memory, klondike.shown(state), rules)
    # put back, the first card turned is the next one again
    assert memory["stock"] == turned[::-1] and memory["waste"] == [] and memory["stock"] == state["stock"]
    assert memory["rounds"] == 1
    # a screen that does not follow from the last one starts again from what it shows alone
    other = klondike.shown(klondike.initial_state(rules, 6))
    assert klondike_solver.recall(memory, other, rules)["stock"] == [None] * 24


def test_hindsight_wins_deals_greedy_does_not_and_plays_the_same_game_twice():
    """P3 (b): the unseen cards dealt at random and each move followed to the end of every such deal. Same
    seeds as greedy; the deals come from a fixed generator, so a game is played the same way twice."""
    won = {policy: [_klondike(policy, 40, seed)[0]["scene"] == klondike.WON for seed in range(24)]
           for policy in ("greedy", "hindsight")}
    assert sum(won["hindsight"]) > sum(won["greedy"]), won
    assert _klondike("hindsight", 40, 3) == _klondike("hindsight", 40, 3)


def _layouts_brute(grid, rules):
    """Every way to lay the rules' mines on the covered cells of a small board that agrees with its numbers."""
    from itertools import combinations
    rows, cols = rules["rows"], rules["cols"]
    covered = [(r, c) for r in range(rows) for c in range(cols) if grid[r][c] == minesweeper.COVERED]
    return [set(laid) for laid in combinations(covered, rules["mines"])
            if all(grid[r][c] == sum((y, x) in laid for y, x in minesweeper.neighbours(r, c, rows, cols))
                   for r in range(rows) for c in range(cols) if minesweeper.is_open(grid[r][c]))], covered


def _win_brute(layouts, covered, rules, cell=None):
    """How often a game is won from these layouts played best to the end (opening `cell` first when given): a
    cell opened safely shows how many mines are around it, and the game is won once one layout is left."""
    if len(layouts) == 1:
        return 1.0
    best = 0.0
    for tried in [cell] if cell is not None else covered:
        shown = {}
        for laid in layouts:
            if tried not in laid:
                near = minesweeper.neighbours(*tried, rules["rows"], rules["cols"])
                shown.setdefault(sum(other in laid for other in near), []).append(laid)
        if not shown or (len(shown) == 1 and len(next(iter(shown.values()))) == len(layouts)):
            continue
        best = max(best, sum(len(kept) * _win_brute(kept, covered, rules) for kept in shown.values()) / len(layouts))
    return best


@pytest.mark.parametrize("rows, mines, shown", [
    (3, 2, ["1....", ".....", "..1.."]),
    (3, 3, ["1..1.", ".....", "....."]),
    (3, 3, ["..1..", ".....", "..1.1"]),
])
def test_a_minesweeper_guess_with_few_layouts_left_is_the_cell_that_wins_most_often_played_to_the_end(rows, mines, shown, monkeypatch):
    """PLAN §33 session P2 (JSMinesweeper's endgame): with few layouts left, the guess is the one that wins most
    often played to the end, counted here over every layout by brute force. On these boards the safest cell
    (what the solver opened before) wins less often."""
    grid = [[minesweeper.COVERED if ch == "." else int(ch) for ch in line] for line in shown]
    rules = {**minesweeper.DEFAULT_RULES, "rows": rows, "cols": len(shown[0]), "mines": mines, "first_open": "cell"}
    layouts, covered = _layouts_brute(grid, rules)
    state = {"grid": grid, "score": None, "scene": "playing"}
    picked = minesweeper_solver.choose(state, rules, "solver", 20000)["action"]["cell"]
    monkeypatch.setattr(minesweeper_solver, "GUESS_WEIGHED", 0)
    monkeypatch.setattr(minesweeper_solver, "ENDGAME_LAYOUTS", 0)
    safest = minesweeper_solver.choose(state, rules, "solver", 20000)["action"]["cell"]
    won = {cell: _win_brute(layouts, covered, rules, cell) for cell in covered}
    assert won[tuple(picked)] == max(won.values()) > won[tuple(safest)], (picked, safest, won)


def test_a_minesweeper_guess_among_many_layouts_is_the_one_most_likely_followed_by_a_second_move_that_survives(monkeypatch):
    """PLAN §33 session P2: with too many layouts to play to the end, a guess is worth how likely it and the
    safest move after what it shows both survive; counted here over every layout by brute force."""
    monkeypatch.setattr(minesweeper_solver, "ENDGAME_LAYOUTS", 0)
    shown = ["1....", ".....", "..1.."]
    grid = [[minesweeper.COVERED if ch == "." else int(ch) for ch in line] for line in shown]
    rules = {**minesweeper.DEFAULT_RULES, "rows": 3, "cols": 5, "mines": 2, "first_open": "cell"}
    layouts, covered = _layouts_brute(grid, rules)

    def survives(cell):
        shown = {}
        for laid in layouts:
            if cell not in laid:
                near = minesweeper.neighbours(*cell, 3, 5)
                shown.setdefault(sum(other in laid for other in near), []).append(laid)
        return sum(max(sum(other not in laid for laid in kept) for other in covered if other != cell)
                   for kept in shown.values()) / len(layouts)

    picked = tuple(minesweeper_solver.choose({"grid": grid, "score": None, "scene": "playing"}, rules, "solver", 20000)["action"]["cell"])
    assert survives(picked) == max(survives(cell) for cell in covered), (picked, {cell: survives(cell) for cell in covered})
