"""The registered policies: the moves chosen over a rule model (adaptive perception plan §4.2).

Policies are registered, never generated: a strong strategy is the hardest code for a model to write and
has no observed transition to be checked against, while the rule model it runs over is checked every move.
Each solver module is pure like the rule models (plan §3.2) and is ported with them.
"""
from __future__ import annotations

# what every solver offers: choose (a policy's move, with moves to leave out) and its policy names
SOLVER_FUNCTIONS = ("choose",)
SOLVER_VALUES = ("POLICIES",)
