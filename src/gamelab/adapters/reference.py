"""The reference site adapter: a registered or session profile read by the hand-written readers
(adaptive perception plan §3, §4.3). Generated adapters are measured against it.

The board is measured on a whole screen (`geometry.measure`) and the measured profile kept as the
session's geometry; a profile reads a screen only on that geometry (`perception.estimate`, or the page's
elements, `estimate_dom`) and plays a move with its family's controls. A game no profile registers gets
one from a model's look the code confirms (`discovery.propose`).

A tile in a colour the profile does not list can still be learned in play: when a move's result reads
everywhere as the rules predict except cells the move filled with a value no colour stands for yet, and
two looks in a row show those cells in the same colours, the colours join the session's palette as that
value (`perception.colours`). The rules give the value; the colour is only what the screen shows for it.
"""
from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
from typing import Any

from .. import catalog, discovery, geometry, perception, pixels, profiles
from .. import registered
from ..contracts import NeedObservation, Observation, StateEstimate
from ..execution import MappedAction
from ..families import GridFamily


class ReferenceAdapter:
    def __init__(self, profile: profiles.Profile | None, *, family_id: str | None = None,
                 site: str | None = None) -> None:
        # a game no profile registers (plan §4.5): its family and its page's address, the profile made for it
        # on the first look the code confirms
        self.profile = profile
        self.family_id = profile.family_id if profile is not None else family_id
        self.site = site
        # the profile as measured on this screen, the session's geometry; None until the board is found
        self.geometry: profiles.Profile | None = None
        # colours learned for values the profile lists no colour for, the last move (the state it was
        # played on and its action) whose result may teach one, and colours one look offered to learn
        self.learned: list[dict[str, Any]] = []
        self.expected: tuple[dict[str, Any], dict[str, Any]] | None = None
        self.teaching: list[dict[str, Any]] | None = None
        # an accepted correction, by the pixels it was read from: an identical picture is not asked again
        self.corrected: tuple[str, dict[str, Any]] | None = None
        # the last look's unread state: a model is asked only when the next look shows it again,
        # so a frame caught mid-animation never spends the vision budget
        self.unread: str | None = None
        self.reads_dom = profile is not None and perception.reads_dom(profile)

    @classmethod
    def measured_as(cls, measured: profiles.Profile) -> ReferenceAdapter:
        """An adapter whose board is already measured, as `measured` lays it out."""
        adapter = cls(measured)
        adapter.geometry = measured
        return adapter

    @property
    def played(self) -> dict[str, Any]:
        """Where the controls and settling come from: the profile, or until one is made for a game no profile
        registers, its family's starting values (`discovery`)."""
        return self.profile.data if self.profile is not None else catalog.descriptor(self.family_id)["discovery"]

    @property
    def measured(self) -> bool:
        return self.geometry is not None

    @property
    def viewport(self) -> list[int]:
        return self.geometry.layout["viewport"]

    @property
    def geometry_ref(self) -> str | None:
        return self.geometry.geometry_ref if self.geometry is not None else None

    @property
    def awaits_look(self) -> bool:
        return self.profile is None

    @property
    def registered(self) -> bool:
        return self.profile is not None and not self.profile.id.startswith(profiles.SESSION)

    @property
    def address(self) -> str:
        return self.site if self.profile is None else self.profile.data.get("source", {}).get("url", "")

    def family(self) -> GridFamily:
        return catalog.family(self.family_id).bind(self.profile.data)

    def scope(self, measuring: bool) -> dict[str, Any]:
        # a page read is an input observation's own: its elements, and the board alone as evidence once
        # measured (the whole screen while measuring)
        if not self.reads_dom:
            return {}
        return perception.dom_scope(self.profile if measuring else self.geometry)

    def measure(self, png: bytes) -> bool | NeedObservation:
        measured = geometry.measure(png, self.profile)
        if isinstance(measured, NeedObservation):
            return measured
        return self._take(measured)

    def locate(self, png: bytes, box: list[int]) -> bool | None:
        with pixels.decode(png, None) as image:
            measured = geometry.locate(image, self.profile, box)
        return self._take(measured) if isinstance(measured, profiles.Profile) else None

    def _take(self, measured: profiles.Profile) -> bool:
        """A layout every check confirmed: the session's geometry when it differs from the one in use."""
        if self.geometry is not None and measured.geometry_ref == self.geometry.geometry_ref:
            return False
        self.geometry, self.corrected = measured, None
        return True

    def board_picture(self, png: bytes) -> bytes:
        left, top, width, height = self.geometry.layout["board"]["rect"]
        with pixels.decode(png, self.geometry.layout["viewport"]) as image:
            output = BytesIO()
            image.crop((left, top, left + width, top + height)).save(output, format="PNG")
        return output.getvalue()

    def read(self, observation: Observation, png: bytes, elements: Any = None) -> StateEstimate | NeedObservation:
        if self.reads_dom:
            return perception.estimate_dom(observation, elements, png, self.geometry)
        return perception.estimate(observation, png, self.geometry)

    def target(self, action: dict[str, Any], state: StateEstimate, observation: Observation, png: bytes,
               elements: Any = None) -> MappedAction:
        checked = profiles.check(self.geometry)
        if (checked != self.geometry or checked.layout is None or state.geometry_ref != checked.geometry_ref
                or observation.geometry_ref not in (None, checked.geometry_ref)):
            raise ValueError("geometry_changed")
        if state.family_id != checked.family_id:
            raise ValueError("unbound_plan")
        if self.reads_dom:
            fresh = perception.estimate_dom(observation, elements, png, checked)
        else:
            # corrected cells are validated again against this exact capture: known pixels cannot be overwritten
            fresh = perception.estimate(observation, png, checked, correction={
                "observation_id": observation.id, "image_sha256": hashlib.sha256(png).hexdigest(),
                "geometry_ref": checked.geometry_ref, "state": state.payload})
        if isinstance(fresh, NeedObservation) or fresh != state:
            raise ValueError("state_changed")
        controls = checked.data.get("controls")
        if controls is None:
            raise ValueError("controls_unregistered")
        tool, arguments, preview = registered.CONTROLS[checked.family_id].map_action(
            action, state.payload, checked.layout, controls, png)
        return MappedAction(tool, arguments, checked.geometry_ref, preview)

    def regions(self) -> list[list[int]]:
        """The measured board and tray."""
        layout = self.geometry.layout
        tray = [layout["tray_band"]] if "tray_band" in layout else layout["tray"]
        return [[x, y, x + width, y + height] for x, y, width, height in (r["rect"] for r in [layout["board"], *tray])]

    def guarded(self) -> list[list[int]]:
        return self.regions()[:1] if self.reads_dom else self.regions()

    def picture(self, png: bytes) -> str:
        """A page read pictures the board alone."""
        digest = hashlib.sha256()
        with pixels.decode(png, None) as image:
            crops = [image] if self.reads_dom else [image.crop(tuple(region)) for region in self.guarded()]
            for crop in crops:
                digest.update(crop.convert("RGB").tobytes())
        return digest.hexdigest()

    def moved(self, before: tuple[dict[str, Any], dict[str, Any]] | None) -> None:
        self.expected, self.teaching = before, None

    def learn(self, observation: Observation, png: bytes, state: StateEstimate
              ) -> tuple[StateEstimate | NeedObservation, bool]:
        """The move's result read with the colours its unread cells teach, once two looks agree on them."""
        if self.expected is None:
            return state, False
        before, action = self.expected
        cells = self.family().explain(before, action, state.payload)
        if not cells:
            return state, False
        with pixels.decode(png, self.geometry.layout["viewport"]) as image:
            entries = perception.colours(image, self.geometry, cells)
        offered, self.teaching = self.teaching, entries
        if entries is None or entries != offered:
            return state, False              # a tile still popping into place is not learned from one look
        try:
            taught = profiles.learn(self.geometry, entries)
        except ValueError:
            return state, False
        self.profile = profiles.learn(self.profile, entries)
        self.learned = [*self.learned, *entries]
        self.geometry, self.teaching = taught, None
        return self.read(observation, png), True

    async def correct(self, observation: Observation, png: bytes, digest: str,
                      state: StateEstimate | NeedObservation, reader: Any, remaining: float | None
                      ) -> StateEstimate | NeedObservation | None:
        """Cells the code left unknown, from an earlier accepted correction of these very pixels or, when
        the same unread state shows twice, from the bounded model reader; None when out of time."""
        unread = state.id if isinstance(state, StateEstimate) and state.unknown_fields else None
        if unread is not None and self.corrected is not None and self.corrected[0] == digest:
            # validated against this capture like the model's own answer: it cannot override known cells
            state = perception.estimate(observation, png, self.geometry, correction={
                "observation_id": observation.id, "image_sha256": hashlib.sha256(png).hexdigest(),
                "geometry_ref": self.geometry.geometry_ref, "state": self.corrected[1]})
        elif reader and unread is not None and unread == self.unread:
            if remaining is not None and remaining <= 0:
                return None
            try:
                async with asyncio.timeout(remaining):
                    state = await reader.read(observation, png, self.geometry)
            except TimeoutError:
                return None
            if isinstance(state, StateEstimate) and not state.unknown_fields:
                self.corrected = digest, state.payload
        self.unread = unread
        return state

    def propose(self, png: bytes, look: Any) -> profiles.Profile | NeedObservation:
        return discovery.propose(png, look, self.site)

    def adopt_profile(self, measured: profiles.Profile) -> profiles.Profile:
        self.profile = profiles.validate(measured.data)
        self._take(measured)
        return self.profile

    def record(self) -> dict[str, Any]:
        return {"geometry_ref": self.geometry.geometry_ref, "viewport": self.geometry.layout["viewport"],
                "board": self.geometry.layout["board"]["rect"], "learned": self.learned}

    def restore(self, record: dict[str, Any]) -> None:
        taught = profiles.learn(self.profile, record["learned"]) if record["learned"] else self.profile
        measured = profiles.measure(taught, record["viewport"], record["board"])
        if measured.geometry_ref != record["geometry_ref"]:
            raise ValueError("geometry_changed")
        self.profile, self.geometry, self.learned = taught, measured, record["learned"]
