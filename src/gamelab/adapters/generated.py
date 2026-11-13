"""A site adapter whose reading is code a model wrote, run in the sandbox (adaptive perception plan §4.3, §7).

The code is one Python module with three functions, each given JSON and pictures (`sandbox.helpers.Picture`)
and returning JSON:

    measure(picture) -> layout | None
        Find the game on a whole screen. A layout is a JSON object with at least "board": [left, top,
        width, height] in the picture's pixels; whatever else the code wants to keep (cell boxes, a tray).
    read(picture, layout) -> state
        The rule model's state on this screen, laid out as `measure` found it; null for what it cannot read.
    target(action, state, layout) -> input
        How the rule model's `action` is played from `state`: {"kind": "key", "key": "ArrowLeft"},
        {"kind": "tap", "x", "y"} or {"kind": "drag", "x0", "y0", "x1", "y1", "ms"}.

Nothing it returns is believed: a layout must lie on the screen, a state is the rule model's to check, an
input must be a key the game may be given or lie inside the layout's regions. Code that fails (an error, a
hang, a wrong shape) reads nothing, which the session counts as a screen it could not read; it is never
allowed to play wrongly instead.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import logging
from typing import Any

from .. import catalog, pixels
from ..contracts import NeedObservation, Observation, StateEstimate
from ..execution import TAP_MS, MappedAction
from ..families import GridFamily
from ..sandbox import Sandbox, SandboxError

log = logging.getLogger("gamelab.generated")

# the keys a generated adapter may ask for: the ones a board game is played with, never a shortcut or text
KEYS = frozenset({"ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "w", "a", "s", "d", "h", "j", "k", "l", " ", "Enter"})
# the longest drag it may ask for
DRAG_MAX_MS = 3000
ADAPTER_FAILED = "adapter_failed"
# sandbox failures (a hang, a crash) after which the code is not started again in this game
MAX_FAILURES = 5
FUNCTIONS = ("measure", "read", "target")


class GeneratedAdapter:
    reads_dom = False
    registered = False
    awaits_look = False

    def __init__(self, family_id: str, source: str, *, rules: dict[str, Any] | None = None, site: str = "",
                 controls: dict[str, Any] | None = None, settling: dict[str, Any] | None = None,
                 call_timeout_s: float | None = None) -> None:
        self.family_id, self.source, self.site = family_id, source, site
        self.digest = hashlib.sha256(source.encode()).hexdigest()
        self._family = catalog.family(family_id).bind({"rules": rules or {}})
        # how the game is played and waited for: the family's starting values unless the site's are known
        start = catalog.descriptor(family_id).get("discovery", {})
        self._played = {"controls": controls or start.get("controls") or {"kind": catalog.CONTROLS[family_id].KIND},
                        "settling": settling if settling is not None else start.get("settling", {})}
        self.sandbox = Sandbox(source, **({"call_timeout_s": call_timeout_s} if call_timeout_s else {}))
        self.layout: dict[str, Any] | None = None
        self.size: list[int] | None = None
        self.failures = 0
        # every call's outcome, for whoever measures the adapter
        self.calls = {"ok": 0, "failed": 0}
        self.last_error: str | None = None

    # ── what the environment asks ─────────────────────────────────────

    @property
    def played(self) -> dict[str, Any]:
        return self._played

    @property
    def measured(self) -> bool:
        return self.layout is not None

    @property
    def viewport(self) -> list[int]:
        return self.size

    @property
    def geometry_ref(self) -> str | None:
        if self.layout is None:
            return None
        body = json.dumps([self.digest, self.size, self.layout], sort_keys=True)
        return f"generated:{hashlib.sha256(body.encode()).hexdigest()[:16]}"

    @property
    def address(self) -> str:
        return self.site

    def family(self) -> GridFamily:
        return self._family

    def scope(self, measuring: bool) -> dict[str, Any]:
        return {}

    def _call(self, name: str, png: bytes | None = None, **arguments: Any) -> Any:
        """One call of the code; None when it failed in any way (kept in `last_error`)."""
        if self.failures >= MAX_FAILURES:
            return None
        picture = self.sandbox.picture(png) if png is not None else None
        try:
            result = self.sandbox.call(name, **({"picture": picture} if picture else {}), **arguments)
            self.calls["ok"] += 1
            return result
        except SandboxError as error:
            self.calls["failed"] += 1
            self.last_error = f"{name}: {error}"
            if error.reason != "error":
                self.failures += 1       # the worker had to be ended: a hang, a crash, no answer
            log.info("generated adapter failed: %s", self.last_error)
            return None
        finally:
            if picture:
                self.sandbox.forget(picture)

    def measure(self, png: bytes) -> bool | NeedObservation:
        try:
            with pixels.decode(png, None) as image:
                size = list(image.size)
        except ValueError as error:
            return NeedObservation(str(error))
        layout = check_layout(self._call("measure", png), size)
        if layout is None:
            return NeedObservation("board_not_found")
        changed = (layout, size) != (self.layout, self.size)
        self.layout, self.size = layout, size
        return changed

    def locate(self, png: bytes, box: list[int]) -> bool | None:
        return None

    def board_picture(self, png: bytes) -> bytes:
        return png

    def read(self, observation: Observation, png: bytes, elements: Any = None) -> StateEstimate | NeedObservation:
        try:
            with pixels.decode(png, self.size):
                pass
        except ValueError as error:
            return NeedObservation(str(error))     # a screen of another size: the board is measured again
        payload = self._call("read", png, layout=self.layout)
        if not isinstance(payload, dict):
            return NeedObservation(ADAPTER_FAILED)
        return self._family.estimate(replace(observation, public_facts={"state": payload}, geometry_ref=self.geometry_ref))

    def target(self, action: dict[str, Any], state: StateEstimate, observation: Observation, png: bytes,
               elements: Any = None) -> MappedAction:
        if state.geometry_ref != self.geometry_ref or state.family_id != self.family_id:
            raise ValueError("geometry_changed")
        fresh = self.read(observation, png)
        if isinstance(fresh, NeedObservation) or fresh != state:
            raise ValueError("state_changed")
        return self.checked_input(self._call("target", action=action, state=state.payload, layout=self.layout))

    def checked_input(self, asked: Any) -> MappedAction:
        """The input the code asked for, as one the surface may be given: ValueError when it is none."""
        kind = asked.get("kind") if isinstance(asked, dict) else None
        allowed = self._played["controls"]["kind"]
        if kind == "key" and allowed == "keys" and asked.get("key") in KEYS and set(asked) == {"kind", "key"}:
            return MappedAction("browser_press", {"keys": [asked["key"]]}, self.geometry_ref)
        points = {"tap": ("x", "y"), "drag": ("x0", "y0", "x1", "y1")}.get(kind)
        if points is None or kind != allowed or not all(_number(asked.get(name)) for name in points):
            raise ValueError("input_refused")
        values = [float(asked[name]) for name in points]
        if not all(self._inside(x, y) for x, y in zip(values[::2], values[1::2])):
            raise ValueError("input_outside_region")
        if kind == "tap":
            x, y = values
            return MappedAction("browser_drag", {"start_x": x, "start_y": y, "end_x": x, "end_y": y,
                                                 "duration_ms": TAP_MS}, self.geometry_ref)
        duration = asked.get("ms")
        if type(duration) is not int or not 1 <= duration <= DRAG_MAX_MS:
            raise ValueError("input_refused")
        x0, y0, x1, y1 = values
        return MappedAction("browser_drag", {"start_x": x0, "start_y": y0, "end_x": x1, "end_y": y1,
                                             "duration_ms": duration}, self.geometry_ref)

    def _inside(self, x: float, y: float) -> bool:
        return any(left <= x < right and top <= y < bottom for left, top, right, bottom in self.regions())

    def regions(self) -> list[list[int]]:
        """The board, and any other boxes the layout names as places input lands (`regions`: a tray)."""
        boxes = [self.layout["board"], *self.layout.get("regions", [])]
        return [[left, top, left + width, top + height] for left, top, width, height in boxes]

    def guarded(self) -> list[list[int]]:
        return self.regions()

    def picture(self, png: bytes) -> str:
        digest = hashlib.sha256()
        with pixels.decode(png, None) as image:
            for region in self.regions():
                digest.update(image.crop(tuple(region)).convert("RGB").tobytes())
        return digest.hexdigest()

    def moved(self, before: tuple[dict[str, Any], dict[str, Any]] | None) -> None:
        pass

    def learn(self, observation: Observation, png: bytes, state: StateEstimate
              ) -> tuple[StateEstimate | NeedObservation, bool]:
        return state, False

    async def correct(self, observation: Observation, png: bytes, digest: str,
                      state: StateEstimate | NeedObservation, reader: Any, remaining: float | None
                      ) -> StateEstimate | NeedObservation | None:
        # what generated code cannot read is not patched cell by cell: the code is regenerated
        return state

    def propose(self, png: bytes, look: Any) -> Any:
        return NeedObservation("not_a_profile")

    def adopt_profile(self, measured: Any) -> Any:
        raise ValueError("not_a_profile")

    def record(self) -> dict[str, Any]:
        return {"geometry_ref": self.geometry_ref, "viewport": self.size, "board": self.layout["board"],
                "layout": self.layout, "adapter": self.digest}

    def restore(self, record: dict[str, Any]) -> None:
        if record["adapter"] != self.digest:
            raise ValueError("geometry_changed")
        self.layout, self.size = record["layout"], record["viewport"]
        if self.geometry_ref != record["geometry_ref"]:
            raise ValueError("geometry_changed")

    def close(self) -> None:
        self.sandbox.close()


def _number(value: Any) -> bool:
    return type(value) in (int, float) and value == value and abs(value) < 1e6


def _box(value: Any, size: list[int]) -> bool:
    return (isinstance(value, list) and len(value) == 4 and all(type(part) is int for part in value)
            and value[0] >= 0 and value[1] >= 0 and value[2] > 0 and value[3] > 0
            and value[0] + value[2] <= size[0] and value[1] + value[3] <= size[1])


def check_layout(layout: Any, size: list[int]) -> dict[str, Any] | None:
    """A layout as the code may give it: an object whose `board` (and each of `regions`, if any) is a box of
    whole pixels on the screen. None otherwise."""
    if not isinstance(layout, dict) or not _box(layout.get("board"), size):
        return None
    regions = layout.get("regions", [])
    if not isinstance(regions, list) or len(regions) > 8 or not all(_box(region, size) for region in regions):
        return None
    return layout
