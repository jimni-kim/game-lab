"""A sokoban level played by the agent on a fake screen (PLAN §36, E1): a model reads the first board, the family's
reader reads every later one and the solver taps the cell the player steps into. The figure is drawn the same on
a goal as on floor (it hides the mark), so the reader tells the cell under it by what is known of the level
(`rules.sokoban.beneath`); a level left where no pushes win is started again by the game's restart button, only
when another try was allowed (`agent.Agent._start_over`)."""
from io import BytesIO
import json
import random

from PIL import Image, ImageDraw
import pytest

from gamelab import adaptation, agent, kits, recovery
from gamelab.adapters.kit import KitAdapter
from gamelab.contracts import Budget, GoalSpec
from gamelab.kits.sokoban import SokobanKit
from gamelab.model import TurnResult
from gamelab.recovery import START_OVER
from gamelab.rules import sokoban as rules
from test_sokoban import level

SIZE = (600, 640)
CELL = 40
BOARD = [200, 80, 4 * CELL, 12 * CELL]
# the game's restart button, beside the board: the level from its start
RESTART = [440, 80, 120, 48]
PAGE, LINE = (245, 245, 240), (90, 90, 90)
WALL_FACE, FLOOR_FACE = (70, 60, 80), (225, 215, 190)
GOAL_INK, BOX_INK, HOME_INK, FIGURE_INK = (230, 170, 30), (150, 90, 40), (60, 170, 80), (40, 90, 220)
# the level of test_sokoban: two rooms joined by a passage one cell wide whose two cells are the goals. Winning
# it walks the player up the passage, over both goals
START, _ = level("####",
                 "#  #",
                 "#  #",
                 "#  #",
                 "#$ #",
                 "#  #",
                 "#.##",
                 "#.##",
                 "#  #",
                 "#$@#",
                 "#  #",
                 "####")
# the upper box pushed into the mouth of the passage, the player above it: no pushes win from here
DEAD, _ = level("####",
                "#  #",
                "#  #",
                "#  #",
                "#@ #",
                "#$ #",
                "#.##",
                "#.##",
                "#  #",
                "#$ #",
                "#  #",
                "####")
RULES = {"rows": 12, "cols": 4}
RESTART_TAP = {"kind": "tap", "uses": ["retry"]}


@pytest.fixture(autouse=True)
def quick(monkeypatch, _readers_under_test):
    # the family's own reader, which the other tests outside test_kit*.py are kept from
    monkeypatch.setattr(kits, "KITS", {**kits.KITS, "sokoban": SokobanKit})
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(agent, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(agent, "KIT_FIRST_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    settling = {"timeout_s": 0.4, "interval_s": 0.02, "stable_s": 0.05}
    made = KitAdapter.__init__
    monkeypatch.setattr(KitAdapter, "__init__", lambda self, *a, **k: made(self, *a, **{**k, "settling": settling}))


def render(grid):
    """The board as flat cells with lines between them, and the restart button beside it. The figure is drawn
    the same wherever it stands: on a goal its mark is hidden."""
    image = Image.new("RGB", SIZE, PAGE)
    draw = ImageDraw.Draw(image)
    left, top = BOARD[:2]
    for r, row in enumerate(grid):
        for c, value in enumerate(row):
            x, y = left + c * CELL, top + r * CELL
            draw.rectangle((x, y, x + CELL - 1, y + CELL - 1), fill=WALL_FACE if value == rules.WALL else FLOOR_FACE,
                           outline=LINE)
            if value == rules.GOAL:
                draw.rectangle((x + CELL * 0.35, y + CELL * 0.35, x + CELL * 0.65, y + CELL * 0.65), fill=GOAL_INK)
            elif value in rules.BOXES:
                draw.rectangle((x + CELL * 0.15, y + CELL * 0.15, x + CELL * 0.85, y + CELL * 0.85),
                               fill=HOME_INK if value == rules.BOX_ON_GOAL else BOX_INK)
            elif value in rules.PLAYERS:
                draw.ellipse((x + CELL * 0.15, y + CELL * 0.15, x + CELL * 0.85, y + CELL * 0.85), fill=FIGURE_INK)
    x, y, w, h = RESTART
    draw.rectangle((x, y, x + w - 1, y + h - 1), fill=(200, 40, 40))
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def inside(x, y, box):
    left, top, width, height = box
    return left <= x < left + width and top <= y < top + height


def middle(box):
    left, top, width, height = box
    return [left + width / 2, top + height / 2]


class Warehouse:
    """A surface showing a sokoban level: a tap on the cell beside the player steps or pushes there by the rules,
    the restart button lays the level out from its start, any other tap does nothing. A level won shows every box
    on its goal."""
    name = "fake"
    reads_dom = False
    kinds = frozenset({"tap", "drag"})

    def __init__(self, grid=None, notice=False):
        # whether a level won is covered at once by the game's own message, the board under it not to be read
        self.notice = notice
        self.grid = [list(row) for row in (grid or START["grid"])]
        self.taps = []
        # taps that changed the board, and presses of the restart button
        self.moved = self.restarts = self.shown = 0

    @property
    def won(self):
        return rules.solved(self.grid)

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "https://boxes.example/"

    async def elements(self, queries):
        return None

    async def capture(self, region=None):
        if self.notice and self.won:
            # the message lies over the middle of the board, a picture that moves (another on every capture: no
            # cell under it looks twice the same); the rows above and below it still show
            image = Image.open(BytesIO(render(self.grid)))
            left, top, width, height = BOARD
            self.shown += 1
            draws = random.Random(self.shown)
            band = (left, round(top + 0.3 * height), left + width, round(top + 0.75 * height))
            size = (band[2] - band[0], band[3] - band[1])
            noise = Image.frombytes("RGB", (size[0] // 8 + 1, size[1] // 8 + 1), bytes(
                draws.randrange(256) for _ in range(3 * (size[0] // 8 + 1) * (size[1] // 8 + 1))))
            image.paste(noise.resize(size, Image.NEAREST), band[:2])
            out = BytesIO()
            image.save(out, format="PNG")
            return out.getvalue()
        return render(self.grid)

    async def key(self, key):
        pass

    async def tap(self, x, y, duration_ms):
        self.taps.append((x, y))
        if inside(x, y, RESTART):
            self.grid = [list(row) for row in START["grid"]]
            self.restarts += 1
            return
        if self.won or not inside(x, y, BOARD):
            return
        cell = (int((y - BOARD[1]) / CELL), int((x - BOARD[0]) / CELL))
        pr, pc = rules.player(self.grid)
        direction = next((d for d, (dr, dc) in rules.STEPS.items() if (pr + dr, pc + dc) == cell), None)
        after = rules.step(self.grid, direction) if direction is not None else None
        if after is not None:
            self.grid = after
            self.moved += 1

    async def drag(self, x0, y0, x1, y1, duration_ms):
        raise AssertionError("a sokoban level is played by taps")

    async def release(self):
        pass


class Keeper:
    """The model: it reads the screen as the picture shows it (the player on a goal is the player), and answers a
    question about a level to start again with `restart` (the context in, an action out)."""

    def __init__(self, fake, restart=None):
        self.fake, self.restart = fake, restart
        self.asked = []

    def kinds(self, kind):
        return [context for asked, context in self.asked if asked == kind]

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if request.name == "play_direct" or context.get("play"):
            self.asked.append(("play", context))
            return TurnResult(text=json.dumps({"situation": "a level", "scene": "board", "guided": False, "score": None,
                                               "action": {"kind": "stop", "because": "not to be asked"},
                                               "expect": "", "notes": ""}))
        if context.get("name_screen"):
            self.asked.append(("name", context))
            return TurnResult(text=json.dumps({"scene": "board", "situation": "a level"}))
        if "examples" in context:
            raise AssertionError("no code is written for a family with its own reader")
        if "tried" in context:
            self.asked.append(("recover", context))
            action = {"kind": "give_up"}
            if context["reason"] == START_OVER and self.restart is not None:
                action = self.restart(context)
            if action.get("kind") == "tap" and "at" not in action:
                # the button's middle, in the pixels of the picture the model was shown
                scale = context["screen"][0] / SIZE[0]
                action = {**action, "at": [part * scale for part in middle(RESTART)]}
            return TurnResult(text=json.dumps({"situation": "a level", "action": action}))
        self.asked.append(("label", context))
        shown = [[rules.PLAYER if cell == rules.PLAYER_ON_GOAL else cell for cell in row] for row in self.fake.grid]
        return TurnResult(text=json.dumps({
            "screen": "board", "board": list(BOARD), "rules": dict(RULES),
            "state": {"grid": shown, "score": None, "scene": "won" if self.fake.won else "playing"}}))


async def play(fake, model, allowed=()):
    return await agent.play(fake, model, "sokoban", goal=GoalSpec("play_until_end", allowed_meta_actions=frozenset(allowed)),
                            budget=Budget(150, 20000, 300))


def ending(played):
    return played.record["outcome"], played.record["reason"]


async def test_the_player_walking_over_goals_is_read_by_the_familys_reader_and_the_level_won():
    """Before `beneath` the reader read the figure on a goal as the player on floor: the board then had a goal
    fewer than boxes, no move gave it, and the family let the level go."""
    fake = Warehouse()
    model = Keeper(fake)
    played, made = await play(fake, model)
    assert ending(played) == ("goal_reached", "play_until_end"), (played.record, made)
    assert fake.won and fake.moved == len(fake.taps) == played.record["confirmed_actions"]
    assert not made.get("let_go") and all(move["effect"] == "confirmed" for move in played.moves), made.get("let_go")
    # the player stood on a goal on the way, and those boards were read by the reader, not the model
    assert len(model.kinds("label")) <= 2 and not model.kinds("recover") and not model.kinds("play"), model.asked
    assert played.moves[-1]["level"] == "L3" and played.record["levels"]["L3"] >= len(played.moves) - 2, played.record
    # nor was the reader doubted and taught the screen again on the way
    assert made["generations"] == 0 and made["kit"]["adopted"] == 1 and not made["rereads"]["by_why"], made["rereads"]


async def test_a_level_whose_last_push_brings_the_games_message_over_the_board_is_won_not_over():
    """The last box pushed onto its goal and the game's message lies over the board at once: the cells under it
    are not read after the move. The rules leave one board after it, every box on a goal, so the level is won; it was told as
    over (`game_over`, `terminal`), counted as lost and played again as a lost one (2026-11-23, five levels)."""
    fake = Warehouse(notice=True)
    model = Keeper(fake)
    played, made = await play(fake, model)
    assert ending(played) == ("goal_reached", "play_until_end"), (played.record, made)
    # told by the rules alone: no model is shown the message
    assert fake.won and len(model.kinds("label")) == 1 and not model.kinds("recover") and not model.kinds("play"), model.asked


async def test_a_level_no_pushes_win_is_started_again_by_the_restart_button_when_another_try_is_allowed():
    fake = Warehouse(DEAD["grid"])
    model = Keeper(fake, restart=lambda context: dict(RESTART_TAP))
    played, made = await play(fake, model, allowed=("retry",))
    assert ending(played) == ("goal_reached", "play_until_end"), (played.record, made)
    asked = model.kinds("recover")
    assert [context["reason"] for context in asked] == [START_OVER] and "retry" in asked[0]["allowed_meta_actions"]
    # the level was laid out from its start once and won from there by the family
    assert fake.restarts == 1 and fake.won and made["started_over"]["times"] == 1, made.get("started_over")
    assert made["account"]["used"] == {"retry": 1}, made["account"]
    assert not model.kinds("play") and not made.get("let_go") and len(model.kinds("label")) <= 2, model.asked
    after = [move for move in played.moves if move["effect"] == "confirmed"]
    assert len(after) == fake.moved and after[-1]["level"] == "L3"


async def test_a_level_no_pushes_win_ends_the_game_with_no_input_when_no_other_try_is_allowed():
    fake = Warehouse(DEAD["grid"])
    model = Keeper(fake, restart=lambda context: dict(RESTART_TAP))
    played, made = await play(fake, model)
    assert ending(played) == ("game_over", "no_progress"), (played.record, made)
    assert not [context for context in model.kinds("recover") if context["reason"] == START_OVER]
    assert not model.kinds("play") and "started_over" not in made
    assert fake.moved == fake.restarts == 0 and fake.grid == DEAD["grid"] and not fake.taps


@pytest.mark.parametrize("answer", [{"kind": "none"}, {"kind": "give_up"}])
async def test_a_level_no_pushes_win_ends_the_game_when_the_model_finds_nothing_that_starts_it_again(answer):
    fake = Warehouse(DEAD["grid"])
    model = Keeper(fake, restart=lambda context: dict(answer))
    played, made = await play(fake, model, allowed=("retry",))
    assert ending(played) == ("game_over", "no_progress"), (played.record, made)
    asked = model.kinds("recover")
    assert 1 <= len(asked) <= agent.START_OVER_MOST and {context["reason"] for context in asked} == {START_OVER}
    assert not model.kinds("play") and "started_over" not in made
    assert fake.moved == fake.restarts == 0 and fake.grid == DEAD["grid"] and not fake.taps


async def test_inputs_that_do_not_start_the_level_again_are_spent_only_so_many_times_and_the_game_ends():
    fake = Warehouse(DEAD["grid"])
    # a tap on the page beside the board, which does nothing
    model = Keeper(fake, restart=lambda context: {"kind": "tap", "at": [20, 20], "uses": ["retry"]})
    played, made = await play(fake, model, allowed=("retry",))
    assert ending(played) == ("game_over", "no_progress"), (played.record, made)
    asked = model.kinds("recover")
    assert len(asked) == agent.START_OVER_MOST == len(fake.taps) and {context["reason"] for context in asked} == {START_OVER}
    # each question after the first is told the input before changed nothing
    assert all(context["tried"] and context["tried"][-1]["screen_changed"] is False for context in asked[1:]), asked
    assert made["started_over"]["times"] == agent.START_OVER_MOST and not model.kinds("play")
    assert fake.moved == fake.restarts == 0 and fake.grid == DEAD["grid"]
