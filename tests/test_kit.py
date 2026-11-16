"""A block game read by the family's own reader (PLAN §19): no code is written for the game, a change of colours
is learned from the screen, a held piece is let go only where the board shows it lies right, and once the game's
way of hanging held pieces is known a drag goes straight there."""
from io import BytesIO
import json

from PIL import Image, ImageDraw
import pytest

from gamelab import adaptation, agent, carry, kits, recovery
from gamelab.adaptation import Store
from gamelab.adapters.kit import KitAdapter
from gamelab.carry import Hang
from gamelab.contracts import Budget, GoalSpec
import test_adaptation_blocks as blocks
from test_adaptation_blocks import BOARD, CELL, Blocks, Dealer


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(agent, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    monkeypatch.setattr(carry, "LIFT_S", 0.0)
    monkeypatch.setattr(carry, "REST_S", 0.0)
    monkeypatch.setattr(carry, "STILL_GAP_S", 0.0)
    settling = {"timeout_s": 0.4, "interval_s": 0.05, "stable_s": 0.1}
    made = KitAdapter.__init__
    monkeypatch.setattr(KitAdapter, "__init__", lambda self, *a, **k: made(self, *a, **{**k, "settling": settling}))


class Centred(Blocks):
    """A block game whose pieces wait in the middle of their slots, as such games lay them."""

    def slot_origin(self, slot):
        piece = self.state["tray"][slot]
        rows, cols = blocks.extent(piece) if piece else (1, 1)
        return (blocks.TRAY[0] + slot * blocks.SLOT + (blocks.SLOT - cols * blocks.SMALL) // 2,
                blocks.TRAY[1] + (blocks.TRAY[3] - rows * blocks.SMALL) // 2)

    async def press(self, x, y):
        for slot, piece in enumerate(self.state["tray"]):
            sx, sy, w, h = self.source(slot) if piece else (0, 0, 0, 0)
            if piece and sx <= x < sx + w and sy <= y < sy + h:
                self.held = (slot, (x, y))


async def test_a_block_game_is_read_by_the_familys_reader_from_its_first_look_and_no_code_is_written(tmp_path):
    fake = Centred()
    dealer = Dealer(fake, [])
    played, made = await agent.play(fake, dealer, "block_placement", goal=GoalSpec("actions", 20), budget=Budget(40, 200, 120),
                                    store=Store(tmp_path))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    # the model read one screen; the reader every one after, and nobody was asked to write anything
    assert played.record["levels"] == {"L2": 1, "L3": 19} and [kind for kind, _ in dealer.asked] == ["label"]
    assert made["generations"] == 0 and made["kit"]["adopted"] == 1
    assert {move["effect"] for move in played.moves} == {"confirmed"} and not fake.missed
    assert [(move["action"]["slot"], move["action"]["anchor"]) for move in played.moves] == fake.placed
    carried = [move["carried"] for move in played.moves]
    # the first carries looked on the way; once they agreed on how the game hangs a held piece, the rest went
    # straight there, and every one was let go where the board showed the piece lying right
    assert carried[0]["measured"] and all(entry["checked"] for entry in carried), carried
    straight = [entry for entry in carried if entry.get("known")]
    assert len(straight) >= 12 and all(not entry["looks"] for entry in straight), carried
    kept = Store(tmp_path).profile("https://blocks.example/", "block_placement")
    assert kept["kit"]["board"] and len(kept["hang"]) >= 2


async def test_the_next_game_on_a_site_begins_from_what_was_kept_and_asks_no_model(tmp_path):
    first = Centred()
    await agent.play(first, Dealer(first, []), "block_placement", goal=GoalSpec("actions", 8), budget=Budget(40, 200, 120),
                     store=Store(tmp_path))
    fake = Centred(draw=7)
    dealer = Dealer(fake, [])
    played, made = await agent.play(fake, dealer, None, goal=GoalSpec("actions", 8), budget=Budget(40, 200, 120),
                                    store=Store(tmp_path))
    assert played.record["outcome"] == "goal_reached" and played.record["levels"] == {"L2": 0, "L3": 8}, (played.record, made)
    assert dealer.asked == [] and made["kit"]["restored"]
    assert all(move["carried"].get("known") for move in played.moves), played.moves


class Themed(Centred):
    """A block game that changes every colour after some placements, as a game does from one level to the next."""

    def __init__(self, after=6, **options):
        super().__init__(**options)
        self.after = after

    async def capture(self, region=None):
        old = (blocks.EMPTY, blocks.FILLED, blocks.PIECE, blocks.PAGE)
        if len(self.placed) >= self.after:
            blocks.EMPTY, blocks.FILLED, blocks.PIECE, blocks.PAGE = (96, 70, 44), (60, 120, 230), (230, 90, 160), (60, 40, 24)
        try:
            return await super().capture(region)
        finally:
            blocks.EMPTY, blocks.FILLED, blocks.PIECE, blocks.PAGE = old


async def test_a_board_that_changes_its_colours_is_learned_again_from_the_screen_with_no_model():
    fake = Themed()
    dealer = Dealer(fake, [])
    played, made = await agent.play(fake, dealer, "block_placement", goal=GoalSpec("actions", 16), budget=Budget(40, 200, 120))
    assert played.record["outcome"] == "goal_reached", (played.record, made)
    assert [kind for kind, _ in dealer.asked] == ["label"] and played.record["levels"] == {"L2": 1, "L3": 15}
    assert made["kit"]["relearned_alone"] >= 1 and made["generations"] == 0
    assert {move["effect"] for move in played.moves} == {"confirmed"} and not fake.missed


class Sparkling(Centred):
    """A block game that flashes something over two empty cells for the first captures after each placement."""

    def __init__(self, **options):
        super().__init__(**options)
        self.flashes = 0

    async def lift(self):
        placed = len(self.placed)
        await super().lift()
        if len(self.placed) > placed:
            self.flashes = 2

    async def capture(self, region=None):
        png = await super().capture(region)
        if self.flashes <= 0 or self.held:
            return png
        self.flashes -= 1
        empty = [(r, c) for r, row in enumerate(self.state["board"]) for c, value in enumerate(row) if not value][:2]
        image = Image.open(BytesIO(png)).convert("RGB")
        draw = ImageDraw.Draw(image)
        for r, c in empty:
            x, y = BOARD[0] + c * CELL, BOARD[1] + r * CELL
            draw.ellipse((x + 6, y + 6, x + CELL - 7, y + CELL - 7), fill=(255, 250, 200))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


async def test_an_effect_over_empty_cells_is_waited_out_and_no_model_is_asked():
    fake = Sparkling()
    dealer = Dealer(fake, [])
    played, made = await agent.play(fake, dealer, "block_placement", goal=GoalSpec("actions", 10), budget=Budget(40, 200, 120))
    assert played.record["outcome"] == "goal_reached", (played.record, made)
    assert [kind for kind, _ in dealer.asked] == ["label"] and {move["effect"] for move in played.moves} == {"confirmed"}


def test_a_games_way_of_hanging_held_pieces_holds_for_a_piece_of_a_height_not_carried_yet():
    hang = Hang()
    # held by its foot: the middle of a piece 2.5 times as large lies 126 above where it waited and half its
    # drawn height more; the drawing moves 1.44 times the pointer's way
    def drawn(source, start, pointer):
        middle = (source[0] + source[2] / 2, source[1] + source[3] / 2)
        return (middle[0] + 1.44 * (pointer[0] - start[0]), middle[1] - 126 - 1.25 * source[3] + 1.44 * (pointer[1] - start[1]))
    for source in ([100, 1800, 102, 52], [400, 1800, 153, 152]):
        start = (source[0] + 20, source[1] + 20)
        pointer = (start[0] + 60, start[1] - 150)
        assert hang.pointer(source, start, (500, 900)) is None
        hang.add(source, start, pointer, drawn(source, start, pointer), 1.44)
    tall = [700, 1790, 52, 206]
    start = (tall[0] + 26, tall[1] + 26)
    pointer = hang.pointer(tall, start, (300.0, 700.0))
    assert all(abs(a - b) <= 2 for a, b in zip(drawn(tall, start, pointer), (300.0, 700.0)))
    # a carry by it that did not do what it should: not used again until a carry bears it out
    hang.shaken = True
    assert hang.pointer(tall, start, (300.0, 700.0)) is None
    assert Hang.from_json(hang.to_json()).pointer(tall, start, (300.0, 700.0)) is not None


async def test_a_held_piece_lying_a_cell_off_is_told_by_the_boards_cells_and_how_far():
    fake = Centred()
    dealer = Dealer(fake, [])
    look = json.loads((await dealer.complete(type("R", (), {"messages": [type("M", (), {"content": [{"text": "{}"}]})()]})())).text)
    kit = kits.make("block_placement", {"rows": 8, "cols": 8, "tray_size": 3})
    png = await fake.capture()
    assert kit.calibrate(png, look["board"], look["state"], look["sources"])
    state, why = kit.read(png)
    assert why is None and state["board"] == fake.state["board"] and state["tray"] == fake.state["tray"]
    piece = fake.state["tray"][0]
    cells = [[2 + r, 3 + c] for r, c in piece]
    source = fake.source(0)
    await fake.press(source[0] + 12, source[1] + 12)
    # the piece's own drawing a cell to the right of where it is to go
    rows, cols = blocks.extent(piece)
    await fake.move(BOARD[0] + (4 + cols / 2) * CELL, BOARD[1] + (2 + 0.85 * rows + 1.7) * CELL, 0)
    off = kit.hover(await fake.capture(), fake.state["board"], cells)
    assert not off["ok"] and off["shift"] == [CELL, 0], off
    await fake.move(BOARD[0] + (3 + cols / 2) * CELL, BOARD[1] + (2 + 0.85 * rows + 1.7) * CELL, 0)
    assert kit.hover(await fake.capture(), fake.state["board"], cells)["ok"]


async def test_a_glow_beside_where_the_piece_is_to_go_does_not_move_it_and_an_untaken_move_is_no_new_scene():
    fake = Centred()
    board = [[1] * 8 for _ in range(8)]
    for r, c in [(3, 3), (3, 4), (4, 3), (4, 4), (5, 2), (5, 5), (0, 0), (0, 1), (7, 6)]:
        board[r][c] = 0
    fake.state = {**fake.state, "board": board, "tray": [[[0, 0], [0, 1], [1, 0], [1, 1]], None, None]}
    kit = kits.make("block_placement", {"rows": 8, "cols": 8, "tray_size": 3})
    png = await fake.capture()
    assert kit.calibrate(png, BOARD, fake.state, [fake.source(0), None, None])
    cells = [[3, 3], [3, 4], [4, 3], [4, 4]]
    source = fake.source(0)
    await fake.press(source[0] + 12, source[1] + 12)
    await fake.move(BOARD[0] + 4 * CELL, BOARD[1] + (3 + 0.85 * 2 + 1.7) * CELL, 0)
    held = Image.open(BytesIO(await fake.capture())).convert("RGB")
    draw = ImageDraw.Draw(held)
    for r, c in [(5, 2), (5, 5)]:            # the lines the piece would clear glow out over two empty cells
        x, y = BOARD[0] + c * CELL, BOARD[1] + r * CELL
        draw.rectangle((x + 2, y + 2, x + CELL - 3, y + CELL - 3), fill=(250, 240, 180))
    out = BytesIO()
    held.save(out, format="PNG")
    seen = kit.hover(out.getvalue(), board, cells)
    assert seen["ok"] and seen["seen"] == 6, seen
    await fake.release()
    # the move was not taken: the board is as it was, far from what the move would have left, and is read so
    after = [[0] * 8 for _ in range(8)]
    state, why = kit.read(png, {"board": after, "tray": [None, None, None]}, fake.state)
    assert why is None and state["board"] == board and state["tray"] == fake.state["tray"], (why, state)
    assert kit.read(png, {"board": after, "tray": [None, None, None]})[1] == "scene_changed"
