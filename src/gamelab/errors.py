"""The few errors the harness tells apart from a game's own failures."""
from __future__ import annotations


class Denied(Exception):
    """The person (or the run's settings standing in for them) refused what an input needed."""


class Interrupted(Exception):
    """The run was stopped from outside the game; the session lets it through instead of recording a failure."""
