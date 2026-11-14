"""A browser page as a surface, driven with Playwright directly.

Captures are in CSS pixels whatever the device scale, so a picture's coordinates are the mouse's.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import time
from typing import Any

from ..surface import DRAG, KEYS, TAP

# a held pointer moves this often along a drag
INTERPOLATION_INTERVAL_MS = 16
# what an element read gives back of one element at most (the rest is `cut`): a board's tiles, a score, a
# banner, never a scrape
MAX_CLASSES, MAX_CLASS, MAX_TEXT = 16, 64, 64
# A match's own text nodes only, and nothing at all from a field: a typed value is never read.
_ELEMENTS = """(queries) => {
 const own = e => Array.from(e.childNodes, n => n.nodeType === 3 ? n.textContent : '').join('').trim();
 const out = {};
 for (const q of queries) {
   let found;
   try { found = document.querySelectorAll(q.selector); } catch (error) { return null; }
   out[q.name] = {count: found.length, items: Array.from(found).slice(0, q.max).map(e =>
     e.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"])')
       ? {protected: true} : {classes: Array.from(e.classList), text: own(e)})};
 }
 return out;
}"""


class WebSurface:
    kinds = frozenset({KEYS, TAP, DRAG})
    reads_dom = True

    def __init__(self, page: Any, name: str = "web") -> None:
        self.page, self.name = page, name

    async def size(self) -> list[int]:
        viewport = self.page.viewport_size
        return [viewport["width"], viewport["height"]]

    async def capture(self, region: list[int] | None = None) -> bytes:
        clip = None
        if region is not None:
            left, top, right, bottom = region
            clip = {"x": left, "y": top, "width": right - left, "height": bottom - top}
        try:
            return await self.page.screenshot(type="png", scale="css", clip=clip)
        except Exception as error:
            # a page navigating or closing under the capture: the look shows nothing
            raise ValueError("capture_failed") from error

    async def elements(self, queries: list[dict[str, Any]]) -> dict[str, Any] | None:
        try:
            found = await self.page.evaluate(_ELEMENTS, queries)
        except Exception:
            return None
        if not isinstance(found, dict):
            return None
        out = {}
        for query in queries:
            read = found.get(query["name"]) or {"count": 0, "items": []}
            items = []
            for item in read["items"]:
                if item.get("protected"):
                    items.append({"protected": True})
                    continue
                classes, text = item["classes"], item["text"]
                cut = len(classes) > MAX_CLASSES or len(text) > MAX_TEXT or any(len(name) > MAX_CLASS for name in classes)
                items.append({"classes": [name[:MAX_CLASS] for name in classes[:MAX_CLASSES]], "text": text[:MAX_TEXT],
                              **({"cut": True} if cut else {})})
            out[query["name"]] = {"count": read["count"], "truncated": read["count"] > query["max"], "items": items}
        return out

    async def address(self) -> str:
        return self.page.url

    async def key(self, key: str) -> None:
        await self.page.keyboard.press(key)

    async def tap(self, x: float, y: float, duration_ms: int) -> None:
        await self.drag(x, y, x, y, duration_ms)

    async def drag(self, x0: float, y0: float, x1: float, y1: float, duration_ms: int) -> None:
        mouse = self.page.mouse
        await mouse.move(x0, y0)
        await mouse.down()
        try:
            start = time.monotonic()
            deadline = start + duration_ms / 1000
            while (now := time.monotonic()) < deadline:
                await asyncio.sleep(min(INTERPOLATION_INTERVAL_MS / 1000, deadline - now))
                ratio = min(1, (time.monotonic() - start) / (duration_ms / 1000))
                await mouse.move(x0 + (x1 - x0) * ratio, y0 + (y1 - y0) * ratio)
            await mouse.move(x1, y1)
        finally:
            await mouse.up()

    async def release(self) -> None:
        await self.page.mouse.up()


@asynccontextmanager
async def browser(*, viewport: list[int], headless: bool = True, scale: float | None = None):
    """A Chromium page of `viewport` CSS pixels, closed with its browser afterwards."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        chromium = await playwright.chromium.launch(headless=headless)
        try:
            context = await chromium.new_context(viewport={"width": viewport[0], "height": viewport[1]},
                                                 **({"device_scale_factor": scale} if scale else {}))
            yield await context.new_page()
        finally:
            await chromium.close()
