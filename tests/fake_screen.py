"""A 2048 game on a fake screen: the ovolve site's capture with the game's state painted over it, playing the
keys and swipes it is given by the family's own rules. No browser, device or paid model is used.

The board's box and its colours are the ones the site's capture shows (measured once on it); nothing here
reads the screen, so a test sees only what the harness reads of it.
"""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path

from PIL import Image, ImageDraw

from gamelab import catalog
from gamelab.model import TurnResult
from gamelab.surface import DRAG, KEYS, TAP

CAPTURE = Path(__file__).parent / "fixtures/game/ovolve_initial.png"
VIEWPORT = [1280, 1000]
# the board's box on the capture ([left, top, width, height]) and each tile value's colour
BOARD = [398, 346, 485, 485]
PALETTE = {0: (205, 193, 180), 2: (238, 228, 218), 4: (237, 224, 200), 8: (242, 177, 121), 16: (245, 149, 99),
           32: (246, 124, 95), 64: (246, 94, 59), 128: (237, 207, 114), 256: (237, 204, 97), 512: (237, 200, 80),
           1024: (237, 197, 63), 2048: (237, 194, 46)}
SIZE = 4
DIRECTIONS = {"ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right"}
# the board the game starts from
START = {"grid": [[0, 0, 0, 0], [2, 0, 0, 0], [0, 2, 0, 0], [0, 0, 0, 0]], "score": None, "scene": "playing"}


def render(state):
    """The site's capture with `state` painted over it: each tile's inside, its border and the page kept."""
    image = Image.open(CAPTURE).convert("RGB")
    draw = ImageDraw.Draw(image)
    left, top, width, height = BOARD
    for row, cells in enumerate(state["grid"]):
        for col, value in enumerate(cells):
            x, y = left + col * width / SIZE, top + row * height / SIZE
            draw.rectangle((int(x + 10), int(y + 10), int(x + width / SIZE - 10), int(y + height / SIZE - 10)),
                           fill=PALETTE.get(value, (1, 2, 3)))
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class Screen:
    """A surface, and the model asked how to get past what covers it."""
    name = "fake"
    reads_dom = False

    def __init__(self, kinds=(KEYS, TAP, DRAG)):
        self.kinds = frozenset(kinds)
        self.rect = list(BOARD)
        self.family = catalog.family("slide_merge").bind({"rules": {"size": SIZE}})
        self.board = deepcopy(START)
        self.keys, self.taps, self.drags = [], [], []
        self.no_effect = False
        # a dialog over the board until its button is tapped
        self.covered = False
        # what the model does about a screen play stopped on, each in turn, then gives up
        self.recovering, self.recover_calls = [], []

    # ── the surface ───────────────────────────────────────────────────

    async def size(self):
        return list(VIEWPORT)

    async def capture(self, region=None):
        png = render(self.board)
        return self._cover(png) if self.covered else png

    async def elements(self, queries):
        return None

    async def address(self):
        return "https://unregistered.example/"

    async def key(self, key):
        self.keys.append(key)
        self._slide(DIRECTIONS[key])

    def _slide(self, direction):
        if not self.no_effect and not self.covered:
            self.board = self.family.next_state(self.board, {"kind": "slide", "direction": direction}, 0)

    async def tap(self, x, y, duration_ms):
        self.taps.append((x, y))
        left, top, width, height = self.button
        if left <= x < left + width and top <= y < top + height:
            self.covered = False

    async def drag(self, x0, y0, x1, y1, duration_ms):
        self.drags.append((x0, y0, x1, y1))
        left, top, width, height = self.rect
        assert all(left <= x < left + width and top <= y < top + height for x, y in ((x0, y0), (x1, y1)))
        across, down = x1 - x0, y1 - y0
        self._slide(("right" if across > 0 else "left") if abs(across) > abs(down) else ("down" if down > 0 else "up"))

    async def release(self):
        pass

    @property
    def button(self):
        left, top, width, _ = self.rect
        return [left + width // 2 - 60, top + 40, 120, 40]

    def _cover(self, png):
        """A dialog over the board's upper part, its frame included, with one button."""
        image = Image.open(BytesIO(png)).convert("RGB")
        draw = ImageDraw.Draw(image)
        left, top, width, height = self.rect
        draw.rectangle((left - 30, top - 30, left + width + 30, top + height // 2), fill=(250, 250, 250))
        x, y, w, h = self.button
        draw.rectangle((x, y, x + w - 1, y + h - 1), fill=(40, 120, 220))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    # ── the model ─────────────────────────────────────────────────────

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        assert "tried" in context, context
        self.recover_calls.append(context)
        action = self.recovering.pop(0) if self.recovering else {"kind": "give_up"}
        return TurnResult(text=json.dumps({"situation": "as drawn", "action": action}))
