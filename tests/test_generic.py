"""The general solver and what a rule model offers it (adaptive perception plan §4.1, §4.2, stage 3)."""
import ast
import json
from pathlib import Path
import random
import sys

import pytest

from gamelab import catalog, rules, solvers
from gamelab.eval.policies import DRAW_BITS
from gamelab.rules import block_placement, minesweeper, slide_merge
from gamelab.solvers import expectimax
from test_core import IO_MODULES

GRID = [[2, 2, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 4]]


def test_a_slide_lists_every_tile_the_game_may_add_with_its_weight():
    state = {"grid": GRID, "score": 10, "scene": "playing"}
    found = slide_merge.outcomes(state, {"kind": "slide", "direction": "left"}, slide_merge.DEFAULT_RULES)
    slid = [[4, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [4, 0, 0, 0]]
    assert len(found) == 14 * 2 and sum(weight for weight, _ in found) == 14 * 10
    assert json.loads(json.dumps(found)) == found           # JSON-shaped: no tuples leak out
    for weight, after in found:
        added = [(r, c, after["grid"][r][c]) for r in range(4) for c in range(4) if after["grid"][r][c] != slid[r][c]]
        assert len(added) == 1 and (added[0][2], weight) in ((2, 9), (4, 1)) and after["score"] == 14
        # each is a result the rule model's own check confirms
        assert slide_merge.assess(state, {"kind": "slide", "direction": "left"}, after, slide_merge.DEFAULT_RULES)["effect"] == "confirmed"
    assert state["grid"] == GRID
    with pytest.raises(ValueError, match="illegal_action"):
        slide_merge.outcomes({"grid": [[2, 0], [0, 0]], "score": 0}, {"kind": "slide", "direction": "left"},
                             {**slide_merge.DEFAULT_RULES, "size": 2})


def test_a_placement_leaves_one_state_and_the_next_tray_is_not_predicted():
    rules_ = block_placement.DEFAULT_RULES
    state = block_placement.initial_state(rules_, 7)
    action = block_placement.legal_actions(state, rules_)[0]
    (weight, after), = block_placement.outcomes(state, action, rules_)
    assert weight == 1 and after == block_placement.apply(state, rules_, action)["state"]
    assert block_placement.assess(state, action, after, rules_)["effect"] == "confirmed"


def test_each_rule_model_says_what_a_general_solver_may_rely_on():
    assert rules.capabilities(slide_merge) == {"chance_enumerable"}
    assert rules.capabilities(block_placement) == {"deterministic", "chance_enumerable"}
    assert rules.capabilities(minesweeper) == {"hidden", "constraints"} and not hasattr(minesweeper, "outcomes")
    assert solvers.generic_policies(slide_merge) == solvers.generic_policies(block_placement) == [expectimax.NAME]
    assert solvers.generic_policies(minesweeper) == []
    assert expectimax.NAME in catalog.family("slide_merge").policies()
    assert expectimax.NAME not in catalog.family("minesweeper").policies()


def test_the_general_solver_is_as_pure_as_the_rule_models():
    tree = ast.parse(Path(expectimax.__file__).read_text(encoding="utf-8"))
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert names - {"__future__"} <= set(sys.stdlib_module_names) - IO_MODULES


def test_it_takes_the_move_whose_outcomes_are_worth_most_and_never_an_excluded_one():
    """A toy rule model: `safe` is worth 5 for certain, `risky` 100 one time in ten and nothing otherwise."""
    class Toy:
        @staticmethod
        def legal_actions(state, rules):
            return [] if state["done"] else [{"kind": "safe"}, {"kind": "risky"}]

        @staticmethod
        def outcomes(state, action, rules):
            if action["kind"] == "safe":
                return [[1, {"worth": 5, "done": True}]]
            return [[1, {"worth": rules["prize"], "done": True}], [9, {"worth": 0, "done": True}]]

        @staticmethod
        def evaluate(state, rules):
            return state["worth"]

        @staticmethod
        def status(state, rules, goal):
            return {"terminal": False, "goal": "not_reached"}

    start = {"worth": 0, "done": False}
    assert expectimax.choose(start, {"prize": 40}, Toy, 100)["action"] == {"kind": "safe"}
    assert expectimax.choose(start, {"prize": 100}, Toy, 100)["action"] == {"kind": "risky"}
    assert expectimax.choose(start, {"prize": 100}, Toy, 100, [{"kind": "risky"}])["action"] == {"kind": "safe"}
    assert expectimax.choose({"worth": 0, "done": True}, {"prize": 1}, Toy, 100) == {"action": None, "depth": 0, "nodes": 0}
    # every line ends after one move: looking deeper finds nothing more and stops
    assert expectimax.choose(start, {"prize": 100}, Toy, 100)["depth"] == 1


def test_it_looks_deeper_with_more_budget_and_avoids_a_lost_line():
    state = {"grid": [[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 8, 8]], "score": 0, "scene": "playing"}
    chosen = expectimax.choose(state, slide_merge.DEFAULT_RULES, slide_merge, 5000)
    assert chosen["action"]["direction"] in ("left", "right") and chosen["depth"] >= 2 and chosen["nodes"] <= 5000
    shallow = expectimax.choose({"grid": GRID, "score": 0, "scene": "playing"}, slide_merge.DEFAULT_RULES, slide_merge, 1)
    assert shallow["depth"] == 1 and shallow["action"] is not None


@pytest.mark.parametrize("family_id", ["slide_merge", "block_placement"])
def test_it_plays_a_whole_game_better_than_the_baseline(family_id):
    """No solver of the game's own is used: the rule model alone, at a small budget, outscores the baseline."""
    family = catalog.family(family_id)

    def play(policy, seed):
        rng = random.Random(seed)
        state = family.initial_state(rng.getrandbits(DRAW_BITS))
        for _ in range(120):
            action = family.choose(family.shown(state), policy, 150)
            if action is None:
                break
            state = family.next_state(state, action, rng.getrandbits(DRAW_BITS))
        return state["score"]
    seeds = range(4)
    assert sum(play(expectimax.NAME, seed) for seed in seeds) > sum(play(family.baseline_policy, seed) for seed in seeds)


def test_a_family_plays_by_the_general_solver_when_its_policy_is_named():
    """The family's `choose` with the general solver's name: each move legal by the rules, and its result one
    the rule model's own check confirms."""
    family = catalog.family("slide_merge")
    rng = random.Random(5)
    state = family.initial_state(rng.getrandbits(DRAW_BITS))
    for _ in range(5):
        action = family.choose(family.shown(state), expectimax.NAME, 200)
        assert action in family.model.legal_actions(state, family.rules)
        after = family.next_state(state, action, rng.getrandbits(DRAW_BITS))
        assert family.model.assess(state, action, after, family.rules)["effect"] == "confirmed"
        state = after
