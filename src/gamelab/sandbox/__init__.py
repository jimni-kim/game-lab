"""Running code a model wrote, apart from the harness (adaptive perception plan §7).

Generated code (a site adapter) is written from what an outside page shows, so nothing it does is trusted:
it runs in its own process with no environment variables, in a temporary folder, with no network, no
writes outside that folder, no reads of the home folder beyond the Python it runs on, no child processes,
and bounds on each call's time, the process's memory and an answer's size. It is handed pictures and JSON
and gives JSON back; the harness checks every answer (a state by the rule model, an input against the
board's region) and never gives it a browser, a key or a file of its own.

On macOS the file and network rules are `sandbox-exec`'s; without it (another system) only the process
limits hold, and `Sandbox.confined` says which.
"""
from __future__ import annotations

from .host import Sandbox, SandboxError

__all__ = ["Sandbox", "SandboxError"]
