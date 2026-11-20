"""A screen the harness may look at and touch: a browser page or a phone. The agent's loop sees it only
through this."""
from __future__ import annotations

from typing import Any, Protocol

# the inputs a surface may carry out (`Surface.kinds`); HOLD: a pointer pressed, moved and let go in separate
# calls with looks between them (`press`, `move`, `lift`), which a carried drag measures itself by (`carry`);
# TEXT: letters typed at once (`type`); SCROLL: a wheel turned (`scroll`); BACK: the device's own back button
# (`back`); SECONDARY: a press of the other mouse button (`right_tap`); RELAUNCH: the game started again from
# nothing, an app closed and opened or a page reloaded (`relaunch`)
KEYS, TAP, DRAG, HOLD = "keys", "tap", "drag", "hold"
TEXT, SCROLL, BACK, SECONDARY, RELAUNCH = "text", "scroll", "back", "secondary", "relaunch"


def segments(points: list[list[float]]) -> list[tuple[list[float], float]]:
    """Each point after the first of a stroke, with the share of the way's length that leads to it: how a
    stroke's time is spread over its legs."""
    legs = [((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in zip(points, points[1:])]
    total = sum(legs)
    return [(point, leg / total if total else 1 / len(legs)) for point, leg in zip(points[1:], legs)]


class Surface(Protocol):
    """A screen the harness may look at and touch. Coordinates are the pixels of its captures."""
    name: str
    # the inputs it carries out: KEYS, TAP, DRAG and those above
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

    async def key(self, key: str, hold_ms: int = 0) -> None: ...
    async def tap(self, x: float, y: float, duration_ms: int) -> None: ...
    async def drag(self, x0: float, y0: float, x1: float, y1: float, duration_ms: int) -> None: ...

    async def stroke(self, points: list[list[float]], duration_ms: int, hold_ms: int) -> None:
        """One pointer pressed at the first point and held `hold_ms`, moved through the others over
        `duration_ms`, held `hold_ms` again and let go (DRAG)."""

    async def taps(self, x: float, y: float, count: int, gap_ms: int) -> None:
        """`count` taps at one point, `gap_ms` apart: close enough together for a double tap (TAP)."""

    # each of these only where its kind is among `kinds`
    async def type(self, text: str) -> None: ...
    async def scroll(self, x: float, y: float, steps: int) -> None:
        """The wheel turned `steps` notches over a point; more than 0 scrolls down (the content moves up)."""
    async def back(self) -> None: ...
    async def right_tap(self, x: float, y: float) -> None: ...
    async def relaunch(self) -> None: ...

    async def release(self) -> None:
        """Let go of anything an interrupted input may still hold."""
