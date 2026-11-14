"""The game families this plugin knows: each one's rule model, solver and descriptor (plan §4; adaptive
perception plan §3).

A family is listed here by code, not by a manifest or setting: adding one is a rule model under `rules/`,
a solver under `solvers/` and a descriptor in
`blueprint/families/<id>.json`, checked against `blueprint/family.schema.json` when it is loaded. A
descriptor names the rule parameters its rule model implements, with the values a game may be bound to,
and the policies its solver offers; a mismatch, or a module without a function the harness calls, stops
the plugin rather than playing a game by rules nobody wrote.
"""
from __future__ import annotations

import json
from functools import cache
from types import ModuleType
from typing import Any

from . import resources, rules, solvers
from .families import GridFamily
from .rules import block_placement, klondike, match3, minesweeper, slide_merge
from .solvers import (block_placement as block_placement_solver, klondike as klondike_solver, match3 as match3_solver,
                      minesweeper as minesweeper_solver, slide_merge as slide_merge_solver)

# the package's own blueprint (src/gamelab/blueprint)
FRAGMENT = resources.FRAGMENT
SCHEMA = FRAGMENT / "family.schema.json"
# each family's rule model, the one the harness trusts, and its solver, the registered policies over it
MODELS: dict[str, ModuleType] = {"block_placement": block_placement, "klondike": klondike, "match3": match3,
                                 "minesweeper": minesweeper,
                                 "slide_merge": slide_merge}
SOLVERS: dict[str, ModuleType] = {"block_placement": block_placement_solver, "klondike": klondike_solver,
                                  "match3": match3_solver,
                                  "minesweeper": minesweeper_solver, "slide_merge": slide_merge_solver}

def descriptor_ids() -> list[str]:
    return sorted(p.name.removesuffix(".json") for p in (FRAGMENT / "families").iterdir()
                  if p.is_file() and p.name.endswith(".json"))


def descriptor(family_id: str) -> dict[str, Any]:
    data = json.loads((FRAGMENT / "families" / f"{family_id}.json").read_text(encoding="utf-8"))
    problems = resources.validate(data, json.loads(SCHEMA.read_text(encoding="utf-8")))
    model, solver = MODELS[family_id], SOLVERS[family_id]
    # what the harness calls is checked here, not when a game first reaches the missing function
    for kind, module, functions, values in (("rule model", model, rules.RULE_FUNCTIONS, rules.RULE_VALUES),
                                            ("solver", solver, solvers.SOLVER_FUNCTIONS, solvers.SOLVER_VALUES)):
        if missing := [name for name in functions if not callable(getattr(module, name, None))] \
                + [name for name in values if not hasattr(module, name)]:
            raise ValueError(f"game family {family_id}: the {kind} lacks {', '.join(missing)}")
    if unknown := set(model.CAPABILITIES) - set(rules.CAPABILITY_NAMES):
        raise ValueError(f"game family {family_id}: unknown capabilities {sorted(unknown)}")
    if set(model.CAPABILITIES) & set(rules.ENUMERATING) and not callable(getattr(model, "outcomes", None)):
        raise ValueError(f"game family {family_id}: the rule model lists no outcomes")
    if data["id"] != family_id:
        problems.append(f"id {data['id']!r} is not the file's name")
    # a model reads the state's fields by this description, so it names exactly the fields the rule model writes
    if set(data["state_fields"]) != set(model.unread_state(model.DEFAULT_RULES, data["scenes"][0]["id"])):
        problems.append("state_fields differ from the rule model's state")
    if set(data["rule_parameters"]) != set(model.DEFAULT_RULES):
        problems.append("rule_parameters differ from the rule model's rules")
    elif resources.validate(model.DEFAULT_RULES, rules_schema(data)):
        problems.append("the rule model's standard rules are outside the descriptor's values")
    if set(data["policies"]) != set(solver.POLICIES) \
            or not {data["baseline_policy"], data["default_policy"]} <= set(solver.POLICIES):
        problems.append("policies differ from the solver's")
    if not set(data.get("discovery", {}).get("rules", {})) <= set(model.DEFAULT_RULES):
        problems.append("discovery sets rules the rule model does not implement")
    if problems:
        raise ValueError(f"game family {family_id}: " + "; ".join(problems))
    return data


def rules_schema(data: dict[str, Any]) -> dict[str, Any]:
    """The rule values a game of this family may be bound to, from its descriptor: a new family needs no
    schema of its own."""
    return {"type": "object", "additionalProperties": False,
            "properties": {name: parameter["values"] for name, parameter in data["rule_parameters"].items()}}


@cache
def family(family_id: str) -> GridFamily:
    if family_id not in MODELS:
        raise KeyError(family_id)
    return GridFamily(descriptor(family_id), MODELS[family_id], SOLVERS[family_id])
