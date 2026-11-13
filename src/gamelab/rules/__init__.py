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
RULE_VALUES = ("DEFAULT_RULES",)
