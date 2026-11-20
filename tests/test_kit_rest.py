"""A board read once it has come to rest, and a reader that comes back (PLAN §26, session C3).

- a move's result is taken when what the move set off is over, however long that lasts and however many moves
  the reader read before: no move is sent while pieces still fall;
- what keeps changing in the same places while a board stands (a glint on a piece, a hint's wiggle) is not
  waited on, where it was seen to do so on the board the move was played from; a thing that changes other places
  from frame to frame is waited on until it ends; and nothing is waited on past the limit;
- a reader of the family's own that is doubted is held against every screen the model reads whole, not only
  against one a confirmed move vouches for;
- a level whose first board the reader kept of the site reads is still looked at once by a model, for its goals;
- what a reader learned of things that are no piece of a colour is kept when it learns the same board again;
- a move refused that tried a way of moving the game was not yet seen to take says the game does not take it;
- the screens a reader gave up on are kept with the game's result.
"""
from io import BytesIO
import json
import time

from PIL import Image, ImageDraw
import pytest

from gamelab import adaptation, agent, motion, recovery
from gamelab.adaptation import Store, Trace
from gamelab.adapters.kit import KitAdapter
from gamelab.contracts import Budget, GoalSpec
from gamelab.kits import match3 as kit
from gamelab.rules import match3 as rules
import test_adaptation_match3 as gems
import test_kit_values as values

KIT_INIT = KitAdapter.__init__
# the frames a move's effect lasts on the fakes here
FALL = 6


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(agent, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(agent, "KIT_FIRST_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    # a settling whose time is long over before a board that never rests is given up on
    settling = {"timeout_s": 3.0, "interval_s": 0.0, "stable_s": 0.05, "animation_s": 0.0, "still": True}
    monkeypatch.setattr(KitAdapter, "__init__", lambda self, *a, **k: KIT_INIT(self, *a, **{**k, "settling": settling}))


def redrawn(png, draw_on):
    image = Image.open(BytesIO(png)).convert("RGB")
    draw_on(ImageDraw.Draw(image))
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class Falling(gems.Gems):
    """A game whose every move sets off FALL frames in which pieces are still on their way: each such frame shows
    the board the move leaves with some of its cells drawn as other pieces, different ones on each frame. A move
    sent while they fall is counted (`early`). `glints`: cells in whose corner a mark flashes on every other frame
    for as long as the game stands, beside the piece's middle (the cell reads as it is)."""

    def __init__(self, glints=(), fall=FALL, **options):
        super().__init__(**options)
        self.glints, self.fall, self.falling, self.shots, self.early = list(glints), fall, 0, 0, 0

    def _play(self, action):
        taken = len(self.swaps) + len(self.pops)
        self.early += self.falling > 0
        super()._play(action)
        if len(self.swaps) + len(self.pops) > taken:
            self.falling = self.fall

    def _box(self, cell):
        return gems.BOARD[0] + cell[1] * gems.CELL, gems.BOARD[1] + cell[0] * gems.CELL

    async def capture(self, region=None):
        self.shots += 1
        png = await super().capture()
        falling, left = self.falling > 0, self.falling

        def over(draw):
            if falling:
                # three cells of the upper rows, others on each frame of the fall (told by how much of it is
                # left, so that it shows the same however many frames were taken before), each as a piece of
                # another colour, one far lighter or darker than its own
                for index in range(3):
                    row, col = (left + index) % 3, (left * 3 + index * 2) % 8
                    x, y = self._box((row, col))
                    ink = gems.INKS[1 if rules.colour_of(self.state["grid"][row][col]) in (2, 3) else 3]
                    draw.ellipse((x + 5, y + 5, x + gems.CELL - 6, y + gems.CELL - 6), fill=ink)
            for cell in self.glints:
                x, y = self._box(cell)
                shade = 255 if self.shots % 2 else 0
                draw.rectangle((x, y, x + 11, y + 11), fill=(shade, shade, shade))
        self.falling = max(0, self.falling - 1)
        return redrawn(png, over)


GLINTS = [(1, 1), (3, 5), (5, 2), (6, 6), (7, 0)]


async def test_a_moves_result_is_taken_once_what_it_set_off_is_over_however_many_moves_were_read_before():
    fake = Falling()
    played, made = await agent.play(fake, gems.Jeweller(fake), "match3", goal=GoalSpec("actions", 12),
                                    budget=Budget(30, 200, 120))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    # no move was sent while pieces still fell, after the reader's first moves as during them: every move was one
    # the board at rest allowed, and the model read the first screen only
    assert not fake.early and not fake.refused, (fake.early, fake.refused, played.moves)
    assert [move["action"] for move in played.moves] == fake.swaps and played.record["levels"]["L3"] == 12
    # each result was read on frames taken as they were, the board read the same on the last of them
    reads = [move["timing"]["read"] for move in played.moves]
    assert all(read["frames"] >= FALL + agent.SAME_READS - 1 and not read["drawn_through"] for read in reads), reads


async def test_what_keeps_flashing_in_the_same_places_on_a_board_at_rest_is_not_waited_on():
    fake = Falling(glints=GLINTS)
    played, made = await agent.play(fake, gems.Jeweller(fake), "match3", goal=GoalSpec("actions", 10),
                                    budget=Budget(30, 200, 120))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    assert not fake.early and not fake.refused, (fake.early, fake.refused)
    # the flashing was seen on the board each move was played from and left out: no result was waited for until
    # the longer time a board that keeps changing in places not known is given
    reads = [move["timing"]["read"] for move in played.moves]
    assert all(read["s"] < agent.UNQUIET_S / 2 and read["frames"] <= FALL + agent.SAME_READS + 1 for read in reads), reads


def watched(recurring=None):
    left, top, width, height = gems.BOARD
    return motion.Resting([left, top, left + width, top + height], recurring)


async def test_a_part_of_the_screen_is_at_rest_when_only_the_places_known_to_keep_changing_change():
    fake = Falling(glints=GLINTS)
    boxes = [[*fake._box(cell), 12, 12] for cell in GLINTS]
    # with nothing known of it, flashing in five places is a board that has not come to rest
    blind = watched()
    assert [blind.seen(await fake.capture()) for _ in range(4)] == [False] * 4
    # those places left out, it is at rest from the second frame on; and where the two last frames differed is
    # told as places on the screen, each inside one of the flashing cells
    knowing = watched(boxes)
    assert [knowing.seen(await fake.capture()) for _ in range(4)] == [False, True, True, True]
    changing = knowing.changing()
    assert changing and all(any(motion.inside(box, [[*fake._box(cell), gems.CELL, gems.CELL]]) for cell in GLINTS)
                            for box in changing), changing
    # pieces on their way change other places from frame to frame: no rest until they are there
    fake.falling = 3
    assert [knowing.seen(await fake.capture()) for _ in range(5)] == [False, False, False, False, True]
    # places that cover most of the part say nothing of where it keeps changing: none is left out
    assert not watched([[0, 0, *gems.SIZE]]).masked
    # and only what is small is a place something keeps moving at
    assert motion.small([[0, 0, 12, 12], [0, 0, 300, 300]], gems.SIZE[0] * gems.SIZE[1]) == [[0, 0, 12, 12]]


def waiting(fake, monkeypatch, limit):
    """An agent on the fake's board, as it stands when a model is about to read a move's result."""
    monkeypatch.setattr(agent, "STILL_TIMEOUT_S", limit)
    one = agent.Agent(fake, gems.Jeweller(fake), "match3", goal=GoalSpec("actions", 1), budget=Budget(5, 10, 60))
    one.board, one.trace = list(gems.BOARD), Trace("match3", {}, "")
    return one


async def test_a_screen_the_model_is_to_read_is_waited_for_until_what_the_move_set_off_ends_and_no_longer(monkeypatch):
    fake = Falling(glints=GLINTS)
    boxes = [[*fake._box(cell), 12, 12] for cell in GLINTS]
    before = agent.Reading(await fake.capture(), fake.state, 0.0, agent.L2, moving=boxes)
    # the flashing places are known of the board the move was played from: the wait ends two frames after the
    # pieces came to rest, long before the limit
    one = waiting(fake, monkeypatch, 30.0)
    fake.falling, taken = FALL, fake.shots
    await one._rest(before)
    assert one.rest_told["rested"] and fake.shots - taken == FALL + 2 and one.rest_told["s"] < 5.0, one.rest_told
    assert one.rest_told["left_out"] >= len(GLINTS)
    # nothing known of them: they never rest, and the wait ends at its limit, not after it
    other = waiting(fake, monkeypatch, 0.3)
    began = time.monotonic()
    await other._rest(agent.Reading(before.png, fake.state, 0.0, agent.L2, moving=[]))
    assert not other.rest_told["rested"] and 0.3 <= time.monotonic() - began < 1.5, other.rest_told
    # (a screen something kept moving on is no longer read at once: 2026-11-18, sixteen of eighteen screens of a
    # level went to the model with pieces still falling)
    again = waiting(fake, monkeypatch, 30.0)
    again.seen.moving = [list(box) for box in boxes]
    fake.falling, taken = FALL, fake.shots
    await again._rest(agent.Reading(before.png, fake.state, 0.0, agent.L3))
    assert again.rest_told["rested"] and fake.shots - taken == FALL + 2, again.rest_told


class Gapped(gems.Jeweller):
    """A model that leaves cells unread on every screen it is shown right after a move, and reads the screen whole
    when it is asked again a turn later."""

    def __init__(self, fake):
        super().__init__(fake)
        self.read_at, self.gaps = 0, 0

    async def complete(self, request):
        result = await super().complete(request)
        answer = json.loads(result.text)
        if "state" not in answer:
            return result
        moved = len(self.fake.swaps)
        if moved > self.read_at and self.gaps < 1 + adaptation.LABEL_RETRIES:
            self.gaps += 1
            answer["state"]["grid"][0][0] = None
            return type(result)(text=json.dumps(answer))
        self.read_at, self.gaps = moved, 0
        return result


async def test_a_doubted_reader_is_held_against_each_screen_the_model_reads_whole_and_comes_back(monkeypatch):
    fake = gems.Gems()
    model = Gapped(fake)
    confirmed, doubted = agent.Agent._confirmed, []

    async def doubting(self, before, action, sent, after, sent_at):
        await confirmed(self, before, action, sent, after, sent_at)
        if self.confirmed == 2 and not doubted:
            doubted.append(self.used)
            self._doubt()

    monkeypatch.setattr(agent.Agent, "_confirmed", doubting)
    played, made = await agent.play(fake, model, "match3", goal=GoalSpec("actions", 12), budget=Budget(40, 200, 120))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    assert doubted and made["rereads"]["by_why"].get("distrusted", 0) >= 1, made["rereads"]
    # the model read the result of the move after the doubt with cells left out, so no move of its reading was
    # confirmed; the screen it then read whole taught the reader again, and the reader read on from there
    after = [move for move in played.moves if int(move["action_id"][1:]) > doubted[0] + 1]
    assert after and all(move["level"] == "L3" for move in after), [(m["action_id"], m["level"]) for m in played.moves]
    assert not made["rereads"]["by_why"].get("distrusted", 0) > 2, made["rereads"]


async def test_a_level_the_kept_reader_reads_first_is_looked_at_once_by_a_model_for_its_goals(tmp_path):
    first = values.Goaled()
    played, made = await agent.play(first, values.Aiming(first), "match3", goal=GoalSpec("actions", 6),
                                    budget=Budget(20, 200, 120), store=Store(tmp_path))
    assert played.record["outcome"] == "goal_reached" and made["kit"]["goals"], (played.record, made)
    # the same game again: the reader kept of it reads the first board, and the model is still asked once, so the
    # level is played by its goals from the first move
    fake = values.Goaled()
    model = values.Aiming(fake)
    played, made = await agent.play(fake, model, "match3", goal=GoalSpec("actions", 6), budget=Budget(20, 200, 120),
                                    store=Store(tmp_path))
    assert played.record["outcome"] == "goal_reached" and made["kit"]["restored"], (played.record, made)
    assert [kind for kind, _ in model.asked] == ["label"] and made["rereads"]["by_why"] == {"goals": 1}, made["rereads"]
    assert made["kit"]["goals"] and made["kit"]["goals_seen"][0]["at_action"] == 0, made["kit"]
    assert played.record["levels"] == {"L2": 0, "L3": 6}


def board_png(grid, marks=()):
    """A board drawn as the fakes draw theirs, with something that is no piece (a white box) at `marks`."""
    image = Image.new("RGB", gems.SIZE, gems.PAGE)
    draw = ImageDraw.Draw(image)
    for row, cells in enumerate(grid):
        for col, value in enumerate(cells):
            x, y = gems.BOARD[0] + col * gems.CELL, gems.BOARD[1] + row * gems.CELL
            draw.rectangle((x, y, x + gems.CELL - 1, y + gems.CELL - 1), fill=gems.WELL)
            if (row, col) in marks:
                draw.rectangle((x + 4, y + 4, x + gems.CELL - 5, y + gems.CELL - 5), fill=(245, 240, 235))
            else:
                draw.ellipse((x + 5, y + 5, x + gems.CELL - 6, y + gems.CELL - 6), fill=gems.INKS[value])
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def test_what_a_reader_learned_of_things_that_are_no_piece_is_kept_when_it_learns_the_same_board_again():
    grid = [[(row * 2 + col) % 6 + 1 for col in range(8)] for row in range(8)]
    boxed = [list(row) for row in grid]
    boxed[0][0] = boxed[0][7] = rules.BLOCK
    reader = kit.Match3Kit({"rows": 8, "cols": 8})
    assert reader.calibrate(board_png(grid, [(0, 0), (0, 7)]), list(gems.BOARD), {"grid": boxed})
    assert reader.read(board_png(grid, [(0, 0), (0, 7)]))[0]["grid"] == boxed
    # the boxes are gone from the screen, and the reader learns it again from a reading that shows none
    assert reader.calibrate(board_png(grid), list(gems.BOARD), {"grid": grid})
    # they come back (the level played again): they are still what they were, with nobody asked
    assert reader.read(board_png(grid, [(0, 0), (0, 7)]))[0]["grid"] == boxed
    # a reading that calls such a look a piece now has its way, as any reading has over what was said before
    recoloured = [list(row) for row in grid]
    recoloured[0][0] = recoloured[0][7] = 7
    assert reader.calibrate(board_png(grid, [(0, 0), (0, 7)]), list(gems.BOARD), {"grid": recoloured})
    assert reader.read(board_png(grid, [(0, 0), (0, 7)]))[0]["grid"] == recoloured
    # and on a board elsewhere on the screen nothing of the one before is kept
    moved = [gems.BOARD[0] + 20, gems.BOARD[1], gems.BOARD[2] - 48, gems.BOARD[3]]
    other = kit.Match3Kit({"rows": 8, "cols": 8})
    other.calibrate(board_png(grid, [(0, 0)]), list(gems.BOARD), {"grid": boxed})
    narrow = kit.Match3Kit({"rows": 8, "cols": 7})
    narrow.looks, narrow.board, narrow.size = other.looks, other.board, other.size
    narrow.calibrate(board_png(grid), moved, {"grid": [row[:7] for row in grid]})
    assert rules.BLOCK not in narrow.looks.kept


async def test_a_refused_move_that_tried_a_way_not_yet_seen_taken_says_the_game_does_not_take_it():
    """Four in a square, tried on a game that takes none: the refusal settles that, and no colour of piece is taken
    for something fixed because a move that swapped it was refused (2026-11-20)."""
    fake = values.Wide()
    played, made = await agent.play(fake, values.Narrow(fake), "match3", goal=GoalSpec("actions", 20),
                                    budget=Budget(40, 200, 120))
    assert played.record["outcome"] == "goal_reached" and fake.refused == 1, (played.record, fake.refused)
    assert made["kit"]["ways"].get("square") is False and made["kit"]["values"] == len(gems.INKS), made["kit"]


class Veiled(Falling):
    """A game that, after its third move, shows something over its whole board for as long as it stands."""

    async def capture(self, region=None):
        png = await super().capture()
        if len(self.swaps) < 3:
            return png
        left, top, width, height = gems.BOARD
        return redrawn(png, lambda draw: draw.rectangle((left, top, left + width, top + height), fill=(250, 250, 250)))


async def test_a_screen_the_reader_gave_up_on_is_kept_with_why(tmp_path):
    from gamelab.eval import measure

    fake = Veiled(fall=1)
    played, made = await agent.play(fake, gems.Jeweller(fake), "match3", goal=GoalSpec("actions", 6),
                                    budget=Budget(8, 200, 30))
    kept = played.screens
    assert kept and kept[0]["why"] == "scene_changed" and kept[0]["at_action"] == 3, [
        {key: value for key, value in one.items() if key != "png"} for one in kept]
    assert Image.open(BytesIO(kept[0]["png"])).size == gems.SIZE
    # written beside the game's result, and listed in it
    measure.write_game(tmp_path, "game", {}, {}, played, measure.Recorder("game"), [], None)
    listed = json.loads((tmp_path / "game.json").read_text())["unread"]
    assert listed[0]["file"] == "game-unread-p1-a3-scene_changed.png" and (tmp_path / listed[0]["file"]).is_file()
    assert "png" not in listed[0]
