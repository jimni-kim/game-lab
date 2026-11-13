"""The rule models: each game's pure rules, legal moves and transition check (adaptive perception plan §3, §4.1).

A rule model is what the session trusts: its checks decide whether a reading is a state and whether a
move did what the rules allow, so its policies (`solvers/`) and its screen reading (`families/<id>/`) stay
apart from it. Each one is standard library only, JSON in and out, no IO, no clock, no unseeded
randomness (plan §3.2), and its cases are the shared fixtures in tests/fixtures/game/<id>/.
"""
from __future__ import annotations

# what every rule model offers: its rules and checks, the replay game (initial_state, next_state, and
# shown: its state as a screen shows it, without what the game hides) and the replay's faults
# (hidden_state, stray_state), then its standard rule values
RULE_FUNCTIONS = ("unknown_fields", "validate", "legal_actions", "status", "assess", "explain", "ended",
                  "unread_state", "initial_state", "next_state", "shown", "hidden_state", "stray_state")
RULE_VALUES = ("DEFAULT_RULES", "CAPABILITIES")
# what a rule model says of itself for the general solvers (adaptive perception plan §4.1): a move leaves one
# state, every state a move may leave can be listed, they can only be sampled, the shown values constrain
# what is hidden, part of the state is hidden
DETERMINISTIC, CHANCE_ENUMERABLE, CHANCE_SAMPLED, CONSTRAINTS, HIDDEN = (
    "deterministic", "chance_enumerable", "chance_sampled", "constraints", "hidden")
CAPABILITY_NAMES = (DETERMINISTIC, CHANCE_ENUMERABLE, CHANCE_SAMPLED, CONSTRAINTS, HIDDEN)
# a model whose moves' results can be listed offers `outcomes(state, action, rules) -> [[weight, state]]`;
# `evaluate(state, rules) -> int`, a search's heuristic, is offered by those that have one
ENUMERATING = (DETERMINISTIC, CHANCE_ENUMERABLE)


def capabilities(model) -> frozenset[str]:
    """What `model` offers, with what follows from it: one certain result is a list of results."""
    declared = frozenset(model.CAPABILITIES)
    return declared | ({CHANCE_ENUMERABLE} if DETERMINISTIC in declared else frozenset())
