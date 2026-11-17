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
from ..kits import grid
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
        # the unread cells of the screen last asked about (`steady`), and the looks a light model was asked of
        self._unread: list[Any] = []
        self._asked: set[bytes] = set()

    @property
    def played(self) -> dict[str, Any]:
        return self._played

    @property
    def layout(self) -> dict[str, Any] | None:
        if self.kit.board is None:
            return None
        return {"board": list(self.kit.board), "regions": [list(box) for box in self._tray().get("slots", [])]}

    def _tray(self) -> dict[str, Any]:
        """Where the family's reader knows dragged things to wait, when its games have such a place."""
        return getattr(self.kit, "tray", None) or {}

    @property
    def foresees(self) -> bool:
        """Whether the rules say enough of a move's result for a screen to be taken as soon as it shows one."""
        return self.kit.foresees

    def awaits(self, prior: dict[str, Any] | None) -> bool:
        """Whether the screen about to be read shows something the rules cannot say, to be taken only once two
        frames in a row read the same."""
        return self.kit.awaits(prior)

    def summary(self) -> dict[str, Any]:
        return {"relearned_alone": self.kit.relearned, **self.kit.summary()}

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
                if isinstance(look.panel, dict) and look.panel.get("moves"):
                    self.kit.place_counter(look.panel["moves"])
                return True
        return False

    def ticked(self, before: bytes, png: bytes) -> bool | None:
        """Whether the number of moves left the game shows changed from one screen to the other (`Grid.ticked`);
        None when nothing tells."""
        return self.kit.ticked(before, png)

    @property
    def counts(self) -> bool:
        """Whether the game's count of moves left is known to change with each move it takes."""
        return self.kit.counts

    def tally(self, ticked: bool | None) -> None:
        self.kit.tally(ticked)

    def agrees(self, look: Any) -> bool:
        """Whether a look a model read is read the same here."""
        state, _ = self.kit.read(look.png)
        return state is not None and self.kit.same(state, look.state)

    # ── what a screen leaves unread ───────────────────────────────────

    def teach(self, png: bytes, state: dict[str, Any], by: str = "rules") -> int:
        """The cells this screen leaves unread are what `state` has there (told by the rules, or by a light
        model): the reader keeps their looks and reads them from now on. How many it learned."""
        teach = getattr(self.kit, "teach", None)
        return teach(png, state, by) if teach is not None else 0

    def excludes(self, png: bytes, state: dict[str, Any]) -> bool:
        """Whether a state cannot be what this screen shows, by what its unread cells do not look like."""
        excludes = getattr(self.kit, "excludes", None)
        return excludes(png, state) if excludes is not None else False

    def steady(self, png: bytes) -> bool:
        """Whether what this screen leaves unread it left unread, looking the same, on the screen asked about
        just before: a thing caught moving (a tile sliding in, one growing as it appears) is not learned from."""
        unread = getattr(self.kit, "unread", None)
        seen = unread(png) if unread is not None else []
        was, self._unread = self._unread, seen
        return bool(seen) and len(seen) == len(was) and all(
            cell == other and grid.distance(look, before) <= grid.MERGE / 2
            for (cell, look), (other, before) in zip(seen, was))

    def settle(self, png: bytes, prior: dict[str, Any] | None) -> list[Any] | None:
        """For a family whose reader learns what a look is from a board at rest (`kits.match3`): what the rules
        say is learned, and the kinds of look still to be named are given back. None for any other family."""
        settle = getattr(self.kit, "settle", None)
        return settle(png, prior) if settle is not None else None

    def took(self, png: bytes, action: dict[str, Any]) -> None:
        """The game took a move: what the reader learns from that of the cells the move swapped."""
        took = getattr(self.kit, "took", None)
        if took is not None and isinstance(action.get("cell"), list) and isinstance(action.get("to"), list):
            took(png, [action["cell"], action["to"]])

    def refused(self, png: bytes, action: dict[str, Any]) -> bool:
        """The game did not take a move: whether the reader learned from that what a cell the move used is."""
        refused = getattr(self.kit, "refused", None)
        cells = [action[name] for name in ("cell", "to") if isinstance(action.get(name), list)]
        return bool(refused(png, cells)) if refused is not None and cells else False

    def questions(self, png: bytes) -> list[tuple[list[tuple[int, int]], list[bytes]]] | None:
        """What a light model is to be asked of a screen's unread cells: each kind of them (cells that look
        alike) with pictures of a few. None when the family's cells are not a light model's to name, the kinds
        are too many to be new values (something lies over the board), or these were asked about before."""
        if getattr(self.kit, "options", lambda: None)() is None:
            return None
        kinds = self.kit.kinds(png)
        if not kinds or len(kinds) > self.kit.KINDS_MOST:
            return None
        looks = dict(self.kit.unread(png))
        fresh = [kind for kind in kinds if looks[kind[0]] not in self._asked]
        if len(fresh) != len(kinds):
            return None
        self._asked.update(looks[kind[0]] for kind in kinds)
        return [(kind, [picture for cell in kind[:self.kit.SHOWN]
                        if (picture := self.kit.cell_picture(png, cell, self.kit.AROUND)) is not None]) for kind in kinds]

    def arrange(self, things: list[tuple[Any, Any]]) -> tuple[list[Any], list[Any]] | None:
        """Things a reading lists, each with its box, put into the slots they wait in: each in the slot whose
        middle its own is nearest. None when the slots are not known or two fall into one."""
        slots = self._tray().get("slots")
        if not slots:
            return None
        listed: list[Any] = [None] * len(slots)
        boxes: list[Any] = [None] * len(slots)
        for thing, box in things:
            if not (isinstance(box, list) and len(box) == 4 and all(type(part) in (int, float) for part in box)):
                return None
            middle = (box[0] + box[2] / 2, box[1] + box[3] / 2)
            slot = min(range(len(slots)), key=lambda index: abs(slots[index][0] + slots[index][2] / 2 - middle[0])
                       + abs(slots[index][1] + slots[index][3] / 2 - middle[1]))
            if listed[slot] is not None:
                return None
            listed[slot], boxes[slot] = thing, box
        return listed, boxes

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
        return list(getattr(self.kit, "sources", []))

    def source_on(self, png: bytes, index: int) -> list[int] | None:
        """Where the piece waiting in one slot is on a screen, as the reader sees it."""
        kit = self.kit
        if not self._tray() or not 0 <= index < len(kit.tray["slots"]):
            return None
        image = grid.open_picture(png)
        if list(image.size) != kit.size:
            return None
        piece, source = kit._slot(image, kit.tray["slots"][index])
        return source if piece else None

    def _call(self, name: str, **arguments: Any) -> Any:
        """The one thing asked of an adapter by name: the input a move is played with (`target`)."""
        if name != "target":
            return None
        from ..adaptation import input_for

        action, state, controls = arguments["action"], arguments["state"], self._played["controls"]
        try:
            return input_for(controls, action, self.kit.board, (self.kit.rows, self.kit.cols), state, self.sources())
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def checked_input(self, asked: Any) -> Any:
        kind = asked.get("kind") if isinstance(asked, dict) else None
        names = {"key": (), "tap": ("x", "y"), "drag": ("x0", "y0", "x1", "y1")}.get(kind)
        controls = self._played["controls"]
        allowed = {{"keys": "key"}.get(controls["kind"], controls["kind"]), *(("tap",) if controls.get("taps") else ())}
        if names is None or kind not in allowed \
                or not all(type(asked.get(name)) in (int, float) for name in names):
            raise ValueError("input_refused")
        return asked

    def hover(self, state: dict[str, Any], action: dict[str, Any]) -> Any:
        """What tells, from a frame taken while a move's piece is held over the board, whether it lies where the
        move puts it (`carry`): None when the move names no cells."""
        controls = self._played["controls"]
        if not hasattr(self.kit, "hover"):
            return None
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
