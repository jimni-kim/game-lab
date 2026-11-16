"""A site adapter whose reading is a family's own reader (`kits`, PLAN §19): no code is written for the game.

It answers what the agent's loop asks of an adapter (`adapters.generated.GeneratedAdapter`): `measure`, `read`,
`picture`, the input a move is played with, `keep` (learn from the looks a model read). What it learned of a
screen is numbers (`record`), kept for the site's next game and learned again when the screen stops fitting them.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from typing import Any

from .. import catalog, kits, pixels
from ..contracts import CELLS_UNREAD, NeedObservation, Observation, StateEstimate
from ..families import GridFamily


class KitAdapter:
    reads_dom = False
    registered = False
    awaits_look = False
    written = False

    def __init__(self, family_id: str, *, rules: dict[str, Any] | None = None, site: str = "",
                 settling: dict[str, Any] | None = None) -> None:
        self.family_id, self.site = family_id, site
        self._family = catalog.family(family_id).bind({"rules": rules or {}})
        self.kit = kits.make(family_id, self._family.rules)
        start = catalog.descriptor(family_id)["discovery"]
        self._played = {"controls": start["controls"],
                        "settling": settling if settling is not None else start.get("settling", {})}
        self.digest = f"kit:{family_id}"
        self.calls = {"ok": 0, "failed": 0}
        self.partial: dict[str, Any] | None = None
        self.last_error: str | None = None
        # what the rules say the screen about to be read shows (`expecting`)
        self.prior: dict[str, Any] | None = None
        self.unmoved: dict[str, Any] | None = None

    @property
    def played(self) -> dict[str, Any]:
        return self._played

    @property
    def layout(self) -> dict[str, Any] | None:
        if self.kit.board is None:
            return None
        return {"board": list(self.kit.board), "regions": [list(box) for box in (self.kit.tray or {}).get("slots", [])]}

    @property
    def size(self) -> list[int] | None:
        return self.kit.size

    @property
    def geometry_ref(self) -> str | None:
        if self.layout is None:
            return None
        return f"kit:{hashlib.sha256(json.dumps([self.size, self.layout], sort_keys=True).encode()).hexdigest()[:16]}"

    def family(self) -> GridFamily:
        return self._family

    def expecting(self, prior: dict[str, Any] | None, unmoved: dict[str, Any] | None = None) -> None:
        """What the rules say the next screen read shows, when a move was just played, and the state it was played
        from: either fills what the screen leaves unclear, and a screen far from both is another scene."""
        self.prior, self.unmoved = prior, unmoved

    def keep(self, looks: list[Any]) -> bool:
        """Learn the screen from the last look a model read that it can be learned from. False when none."""
        for look in reversed(looks[-3:]):
            if self.kit.calibrate(look.png, look.board, look.state, look.sources):
                return True
        return False

    def agrees(self, look: Any) -> bool:
        """Whether a look a model read is read the same here."""
        state, _ = self.kit.read(look.png)
        if state is None:
            return False
        tray = [seen for seen, said in zip(state["tray"], look.state.get("tray") or []) if seen != []]
        said = [said for seen, said in zip(state["tray"], look.state.get("tray") or []) if seen != []]
        return state["board"] == look.state.get("board") and tray == said

    def measure(self, png: bytes) -> bool | NeedObservation:
        try:
            with pixels.decode(png, None) as image:
                size = list(image.size)
        except ValueError as error:
            return NeedObservation(str(error))
        if self.kit.board is None or size != self.kit.size:
            return NeedObservation("board_not_found")
        return False

    def raw(self, png: bytes) -> Any:
        return None

    def read(self, observation: Observation, png: bytes, elements: Any = None) -> StateEstimate | NeedObservation:
        payload, why = self.kit.read(png, self.prior, self.unmoved)
        if payload is None:
            self.calls["failed"] += 1
            return NeedObservation(why or "board_not_found")
        self.calls["ok"] += 1
        state = self._family.estimate(replace(observation, public_facts={"state": payload}, geometry_ref=self.geometry_ref))
        if isinstance(state, StateEstimate) and state.unknown_fields:
            self.partial = payload
            return NeedObservation(CELLS_UNREAD)
        return state

    def sources(self) -> list[list[int] | None]:
        """Where each waiting piece was last read to be."""
        return list(self.kit.sources)

    def source_on(self, png: bytes, index: int) -> list[int] | None:
        """Where the piece waiting in one slot is on a screen, as the reader sees it."""
        kit = self.kit
        if kit.tray is None or not 0 <= index < len(kit.tray["slots"]):
            return None
        from ..kits.block_placement import _open

        image = _open(png)
        if list(image.size) != kit.size:
            return None
        piece, source = kit._slot(image, kit.tray["slots"][index])
        return source if piece else None

    def _call(self, name: str, **arguments: Any) -> Any:
        """The one thing asked of an adapter by name: the input a move is played with (`target`)."""
        if name != "target":
            return None
        from ..adaptation import drag_for

        action, state, controls = arguments["action"], arguments["state"], self._played["controls"]
        try:
            index = action[controls["index"]]
            source, piece = self.kit.sources[index], state[controls["source"]][index]
            if not source or not piece:
                return None
            return drag_for(source, self.kit.board, (self.kit.rows, self.kit.cols), piece, action[controls["onto"]],
                            controls["duration_ms"])
        except (KeyError, IndexError, TypeError):
            return None

    def checked_input(self, asked: Any) -> Any:
        if not isinstance(asked, dict) or asked.get("kind") != "drag" \
                or not all(type(asked.get(name)) in (int, float) for name in ("x0", "y0", "x1", "y1")):
            raise ValueError("input_refused")
        return asked

    def hover(self, state: dict[str, Any], action: dict[str, Any]) -> Any:
        """What tells, from a frame taken while a move's piece is held over the board, whether it lies where the
        move puts it (`carry`): None when the move names no cells."""
        controls = self._played["controls"]
        try:
            piece, anchor = state[controls["source"]][action[controls["index"]]], action[controls["onto"]]
            cells = [[anchor[0] + r, anchor[1] + c] for r, c in piece]
        except (KeyError, IndexError, TypeError):
            return None
        board = [list(row) for row in state["board"]]
        return lambda png: self.kit.hover(png, board, cells)

    def regions(self) -> list[list[int]]:
        layout = self.layout or {"board": [0, 0, 1, 1], "regions": []}
        return [[left, top, left + width, top + height] for left, top, width, height in [layout["board"], *layout["regions"]]]

    def picture(self, png: bytes) -> str:
        digest = hashlib.sha256()
        with pixels.decode(png, None) as image:
            for region in self.regions():
                digest.update(image.crop(tuple(region)).convert("RGB").tobytes())
        return digest.hexdigest()

    def record(self) -> dict[str, Any]:
        return self.kit.to_json()

    def restore(self, record: dict[str, Any]) -> bool:
        return self.kit.restore(record)

    def close(self) -> None:
        pass
