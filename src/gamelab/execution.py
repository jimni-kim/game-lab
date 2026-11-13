"""Pure logical-action mapping. A mapping is a proposal, never input authority."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import ActionPlan, Observation, StateEstimate

# the input kind a press and release in place is approved as (input session `tap`), never as a drag
TAP = "tap"
# how long a tap holds the pointer down: within the authority's tap limit (300 ms), past which it is a long press
TAP_MS = 60


@dataclass(frozen=True)
class MappedAction:
    tool: str
    arguments: dict[str, Any]
    geometry_ref: str
    preview: dict[str, Any] | None = None


def tap(box: list[int]) -> MappedAction:
    """A tap at the middle of `box` ([left, top, width, height] in screen pixels): the surface's bounded
    pointer input that neither moves nor holds, which an approval of taps alone accepts."""
    left, top, width, height = box
    x, y = left + width / 2, top + height / 2
    return MappedAction("browser_drag", {"start_x": x, "start_y": y, "end_x": x, "end_y": y,
                                         "duration_ms": TAP_MS}, "")


def map_action(plan: ActionPlan, state: StateEstimate, observation: Observation, png: bytes, adapter: Any, *,
               elements: dict[str, Any] | None = None) -> MappedAction:
    """The input for a one-step plan on the look its state was read from: bound to that state and look,
    legal by the rule model, and placed by the site's adapter (`adapters.SiteAdapter.target`), which reads
    the same look again first."""
    if (len(plan.steps) != 1 or plan.verify_every != 1 or plan.based_on_state != state.id
            or plan.rule_revision != state.rule_revision or observation.id not in state.observation_ids
            or state.unknown_fields):
        raise ValueError("unbound_plan")
    family = adapter.family()
    action, = plan.steps
    if action not in family.model.legal_actions(state.payload, family.rules):
        raise ValueError("illegal_action")
    return adapter.target(action, state, observation, png, elements)
