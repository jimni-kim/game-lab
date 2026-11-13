"""The registered policies: the moves chosen over a rule model (adaptive perception plan §4.2).

Policies are registered, never generated: a strong strategy is the hardest code for a model to write and
has no observed transition to be checked against, while the rule model it runs over is checked every move.
Each solver module is pure like the rule models (plan §3.2) and is ported with them.
"""
from __future__ import annotations

# what every solver offers: choose (a policy's move, with moves to leave out) and its policy names
SOLVER_FUNCTIONS = ("choose",)
SOLVER_VALUES = ("POLICIES",)

from .. import rules  # noqa: E402
from . import expectimax  # noqa: E402

# the general solvers, by the policy name a run asks for them with: each plays any rule model that offers
# what its REQUIRES names (`fits`), through `choose(state, rules, model, budget, excluded)`
GENERIC = {expectimax.NAME: expectimax}


def fits(solver, model) -> bool:
    """Whether a general solver can play `model`: every capability it requires, and a heuristic to search by."""
    return set(solver.REQUIRES) <= rules.capabilities(model) and callable(getattr(model, "evaluate", None))


def generic_policies(model) -> list[str]:
    """The general solvers' policy names `model` can be played with."""
    return [name for name, solver in GENERIC.items() if fits(solver, model)]
