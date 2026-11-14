"""A screen the harness may look at and touch: a browser page or a phone. Whatever plays a game (the agent's
loop, the baseline's session) sees it only through this."""
from __future__ import annotations

from typing import Any, Protocol

# the inputs a surface may carry out (`Surface.kinds`)
KEYS, TAP, DRAG = "keys", "tap", "drag"


class Surface(Protocol):
    """A screen the harness may look at and touch. Coordinates are the pixels of its captures."""
    name: str
    # the inputs it carries out: KEYS, TAP, DRAG
    kinds: frozenset[str]
    # whether `elements` reads the page under the picture
    reads_dom: bool

    async def size(self) -> list[int]:
        """The whole screen's [width, height]."""

    async def capture(self, region: list[int] | None = None) -> bytes:
        """The screen as a PNG, or only `region` ([left, top, right, bottom]) of it."""

    async def elements(self, queries: list[dict[str, Any]]) -> dict[str, Any] | None:
        """For each query (`name`, CSS `selector`, `max`): `{"count", "truncated", "items": [{"classes", "text"}]}`,
        an item's text being its own text nodes; None when the page cannot be read."""

    async def address(self) -> str:
        """Where the game is: the page's URL, or the app (`android://<package>`)."""

    async def key(self, key: str) -> None: ...
    async def tap(self, x: float, y: float, duration_ms: int) -> None: ...
    async def drag(self, x0: float, y0: float, x1: float, y1: float, duration_ms: int) -> None: ...

    async def release(self) -> None:
        """Let go of anything an interrupted input may still hold."""
