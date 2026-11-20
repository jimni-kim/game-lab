"""A browser page as a surface, driven with Playwright directly.

Captures are in CSS pixels whatever the device scale, so a picture's coordinates are the mouse's.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import time
from typing import Any

from ..surface import DRAG, HOLD, KEYS, RELAUNCH, SCROLL, SECONDARY, TAP, TEXT, segments

# a held pointer moves this often along a drag
INTERPOLATION_INTERVAL_MS = 16
# one notch of the wheel, in CSS pixels
WHEEL_STEP_PX = 100
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

# What the page itself says of the screen (`outline`): every element in view that carries text of its own, can be
# pressed, is a picture, or is a coloured or pictured leaf (a tile, a cell), with its box clipped to the view.
# Nothing is read from a field. A thing pressed that has no text of its own speaks for what is inside it, which is
# then not listed again (a button's label, its icon). `drawn`: the share of the view under things the page cannot be read through (a canvas, an
# embedded page, a video, a large drawing).
OUTLINE_MOST, OUTLINE_TEXT, OUTLINE_NAME, OUTLINE_CLASSES = 400, 40, 28, 3
_OUTLINE = """([most, textMost, nameMost, classesMost]) => {
 const W = innerWidth, H = innerHeight, items = [], spoken = new WeakSet();
 let drawn = 0, cut = false;
 const OPAQUE = new Set(['canvas', 'iframe', 'video', 'object', 'embed', 'svg']);
 const SKIP = new Set(['script', 'style', 'noscript', 'head', 'meta', 'link', 'template', 'br', 'html', 'body']);
 const PRESSED = new Set(['button', 'a', 'input', 'select', 'textarea', 'summary']);
 const ROLES = new Set(['button', 'link', 'tab', 'checkbox', 'radio', 'menuitem', 'option', 'gridcell', 'switch']);
 const FIELD = 'input,textarea,select,[contenteditable]:not([contenteditable="false"])';
 const tidy = text => (text || '').replace(/\\s+/g, ' ').trim();
 const own = e => tidy(Array.from(e.childNodes, n => n.nodeType === 3 ? n.textContent : '').join(' '));
 const named = url => {
   if (!url || url.startsWith('data:') || url.startsWith('blob:')) return '';
   const last = url.split(/[?#]/)[0].split('/').pop() || '';
   return last.replace(/\\.[a-z0-9]+$/i, '');
 };
 const colour = value => {
   const m = /rgba?\\((\\d+), (\\d+), (\\d+)(?:, ([\\d.]+))?\\)/.exec(value || '');
   if (!m || (m[4] !== undefined && +m[4] < 0.5)) return '';
   return '#' + [1, 2, 3].map(i => (+m[i]).toString(16).padStart(2, '0')).join('');
 };
 const walk = root => {
   for (const e of root.querySelectorAll('*')) {
     if (e.shadowRoot) walk(e.shadowRoot);
     const tag = e.tagName.toLowerCase();
     if (SKIP.has(tag) || e.ownerSVGElement) continue;
     if (e.parentElement && spoken.has(e.parentElement)) { spoken.add(e); continue; }
     const r = e.getBoundingClientRect();
     const left = Math.max(r.left, 0), top = Math.max(r.top, 0);
     const w = Math.min(r.right, W) - left, h = Math.min(r.bottom, H) - top;
     if (w < 4 || h < 4) continue;
     if (e.checkVisibility && !e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true,
                                                  opacityProperty: true, visibilityProperty: true})) continue;
     const s = getComputedStyle(e);
     if (s.visibility !== 'visible' || +s.opacity < 0.1) continue;
     if (OPAQUE.has(tag) && w * h >= 0.1 * W * H) { drawn += w * h; continue; }
     const field = !!e.closest(FIELD);
     const role = e.getAttribute('role') || '';
     const parent = e.parentElement;
     const pressed = PRESSED.has(tag) || ROLES.has(role) || e.hasAttribute('onclick')
       || (s.cursor === 'pointer' && !(parent && getComputedStyle(parent).cursor === 'pointer'));
     let text = field ? '' : own(e);
     if (!text && pressed && !field && e.querySelectorAll('*').length <= 3) {
       const inner = tidy(e.innerText);
       if (inner.length <= textMost) { text = inner; spoken.add(e); }
     }
     const image = s.backgroundImage.startsWith('url(') ? named(s.backgroundImage.slice(4).replace(/["')]/g, '')) : '';
     const leaf = e.childElementCount === 0;
     const bg = leaf && !text ? colour(s.backgroundColor) : '';
     const picture = tag === 'img' || tag === 'svg' || OPAQUE.has(tag);
     if (!(text || pressed || picture || image || bg)) continue;
     let label = e.getAttribute('aria-label') || e.getAttribute('alt') || e.getAttribute('title')
       || (tag === 'img' ? named(e.getAttribute('src')) : '') || image;
     label = tidy(label);
     let holder = e;
     while (!holder.classList.length && holder.parentElement && holder.parentElement.childElementCount === 1)
       holder = holder.parentElement;
     const x = left + w / 2, y = top + h / 2;
     const hit = (root.elementFromPoint ? root : document).elementFromPoint(x, y);
     const flags = [];
     if (e.disabled || e.getAttribute('aria-disabled') === 'true') flags.push('disabled');
     if (['aria-selected', 'aria-pressed', 'aria-checked'].some(name => e.getAttribute(name) === 'true')) flags.push('selected');
     if (hit && hit !== e && !e.contains(hit) && !hit.contains(e)) flags.push('covered');
     if (items.length >= most) { cut = true; continue; }
     items.push({at: [Math.round(x), Math.round(y)], size: [Math.round(w), Math.round(h)],
                 tag: tag, text: text.slice(0, textMost), label: label === text ? '' : label.slice(0, nameMost),
                 classes: Array.from(holder.classList).slice(0, classesMost).map(name => name.slice(0, nameMost)),
                 bg: bg, flags: flags});
   }
 };
 walk(document);
 return {size: [W, H], drawn: drawn / (W * H), cut: cut, items: items};
}"""


class WebSurface:
    kinds = frozenset({KEYS, TAP, DRAG, HOLD, TEXT, SCROLL, SECONDARY, RELAUNCH})
    reads_dom = True

    def __init__(self, page: Any, name: str = "web") -> None:
        self.page, self.name = page, name
        self._held = (0.0, 0.0)

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

    async def outline(self) -> dict[str, Any] | None:
        """What the page says stands in view (`_OUTLINE`): `{"size", "drawn", "cut", "items": [{"at", "size", "tag",
        "text", "label", "classes", "bg", "flags"}]}` in the capture's pixels; None when the page cannot be read."""
        try:
            found = await self.page.evaluate(_OUTLINE, [OUTLINE_MOST, OUTLINE_TEXT, OUTLINE_NAME, OUTLINE_CLASSES])
        except Exception:
            return None
        return found if isinstance(found, dict) and isinstance(found.get("items"), list) else None

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

    async def press(self, x: float, y: float) -> None:
        await self.page.mouse.move(x, y)
        await self.page.mouse.down()
        self._held = (x, y)

    async def move(self, x: float, y: float, duration_ms: int) -> None:
        """The held pointer moved to a point over `duration_ms`, in steps a game follows as a hand's."""
        x0, y0 = self._held
        start = time.monotonic()
        deadline = start + duration_ms / 1000
        while (now := time.monotonic()) < deadline:
            await asyncio.sleep(min(INTERPOLATION_INTERVAL_MS / 1000, deadline - now))
            ratio = min(1, (time.monotonic() - start) / (duration_ms / 1000))
            await self.page.mouse.move(x0 + (x - x0) * ratio, y0 + (y - y0) * ratio)
        await self.page.mouse.move(x, y)
        self._held = (x, y)

    async def lift(self) -> None:
        await self.page.mouse.up()

    async def stroke(self, points: list[list[float]], duration_ms: int, hold_ms: int) -> None:
        await self.press(*points[0])
        try:
            await asyncio.sleep(hold_ms / 1000)
            for (x, y), share in segments(points):
                await self.move(x, y, round(duration_ms * share))
            await asyncio.sleep(hold_ms / 1000)
        finally:
            await self.lift()

    async def taps(self, x: float, y: float, count: int, gap_ms: int) -> None:
        if count == 2:
            # one double click, which a page also hears as `dblclick`
            await self.page.mouse.dblclick(x, y)
            return
        for index in range(count):
            await asyncio.sleep(gap_ms / 1000 if index else 0)
            await self.page.mouse.click(x, y)

    async def type(self, text: str) -> None:
        await self.page.keyboard.type(text)

    async def scroll(self, x: float, y: float, steps: int) -> None:
        await self.page.mouse.move(x, y)
        await self.page.mouse.wheel(0, steps * WHEEL_STEP_PX)

    async def right_tap(self, x: float, y: float) -> None:
        await self.page.mouse.click(x, y, button="right")

    async def relaunch(self) -> None:
        await self.page.reload()

    async def release(self) -> None:
        await self.page.mouse.up()


@asynccontextmanager
async def browser(*, viewport: list[int], headless: bool = True, scale: float | None = None,
                  proxy: dict[str, str] | None = None, args: tuple[str, ...] = (), profile: Path | None = None,
                  **options: Any):
    """A Chromium page of `viewport` CSS pixels, closed with its browser afterwards. `proxy` and `args` go to
    Playwright's launch as they are, and `options` to its new context. `profile`: a folder the browser keeps what
    sites save in (storage, cookies) from one run to the next, as an installed app keeps its data (PLAN §25, R4);
    without it every run starts with nothing saved."""
    from playwright.async_api import async_playwright

    if profile is not None:
        profile.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                str(profile), headless=headless, args=list(args), viewport={"width": viewport[0], "height": viewport[1]},
                **({"device_scale_factor": scale} if scale else {}), **({"proxy": proxy} if proxy else {}), **options)
            try:
                yield context.pages[0] if context.pages else await context.new_page()
            finally:
                await context.close()
        return
    async with async_playwright() as playwright:
        chromium = await playwright.chromium.launch(headless=headless, args=list(args), **({"proxy": proxy} if proxy else {}))
        try:
            context = await chromium.new_context(viewport={"width": viewport[0], "height": viewport[1]},
                                                 **({"device_scale_factor": scale} if scale else {}), **options)
            yield await context.new_page()
        finally:
            await chromium.close()
