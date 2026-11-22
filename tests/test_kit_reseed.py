"""A board read wrongly that the rules could not catch (PLAN §32, session R1): the move played from it leaves a
screen that is no result of it, however rightly that screen is read. The game is not ended for it: when the
family's own reader and the models agree, cell by cell, on what the screen shows now, play starts over from that."""
from io import BytesIO

from PIL import Image, ImageDraw
import pytest

from gamelab import adaptation, agent, recovery
from gamelab.adapters.kit import KitAdapter
from gamelab.contracts import Budget, GoalSpec
from gamelab.rules import slide_merge
from gamelab.rules.minesweeper import COVERED, EXPLODED, is_open
from fake_screen import BOARD, PALETTE, SIZE, Screen
from test_adaptation import Author
import test_adaptation_mines as mines
from test_kit_values import MINE_NAMES, Light


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(agent, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(agent, "KIT_FIRST_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    settling = {"timeout_s": 0.4, "interval_s": 0.02, "stable_s": 0.05}
    made = KitAdapter.__init__
    monkeypatch.setattr(KitAdapter, "__init__", lambda self, *a, **k: made(self, *a, **{**k, "settling": settling}))


# how the tile a move brought in is drawn until the next key, by the number of the move: a look of its own
FRESH = {1: (150, 200, 250), 2: (150, 250, 200)}


class Appearing(Screen):
    """A 2048 whose first two moves each bring in a 2 drawn in a look of its own until the next key (a tile still
    fading in). The first is unlike every 2 seen while no 4 was seen yet, so the rules take it for the 4 a new
    tile may also be: the family's reader reads one new tile wrongly, once."""

    def __init__(self, looks=FRESH, **options):
        super().__init__(**options)
        self.looks, self.moved, self.fresh = looks, 0, None

    def _slide(self, direction):
        before = self.board
        super()._slide(direction)
        self.fresh = None
        if self.board is before:
            return
        self.moved += 1
        if self.moved in self.looks:
            slid = slide_merge.slide(before["grid"], direction)["grid"]
            cell, = [(r, c) for r in range(SIZE) for c in range(SIZE) if self.board["grid"][r][c] != slid[r][c]]
            self.board["grid"][cell[0]][cell[1]] = 2
            self.fresh = (cell, self.looks[self.moved])

    async def capture(self, region=None):
        png = await super().capture(region)
        if self.fresh is None:
            return png
        (row, col), colour = self.fresh
        image = Image.open(BytesIO(png)).convert("RGB")
        left, top, width, height = BOARD
        x, y = left + col * width / SIZE, top + row * height / SIZE
        ImageDraw.Draw(image).rectangle((int(x + 10), int(y + 10), int(x + width / SIZE - 10), int(y + height / SIZE - 10)),
                                        fill=colour)
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


def names(fresh=2):
    """What a light model calls each look: a tile by its colour, and a tile still coming in `fresh`."""
    return {**{colour: value for value, colour in PALETTE.items()}, **{colour: fresh for colour in FRESH.values()}}


async def test_a_new_tile_the_reader_took_for_another_does_not_end_the_game_two_readers_agree_and_play_starts_over():
    """The first move's new 2 is taken for a 4. The second move's result, read rightly by the model, is no result
    of that move from the board with the 4: before, the game was given up there (`inconsistent`). Now the reader's
    own reading of the screen and the light model's of the one cell it cannot read are held against what the model
    read; they agree, and the game goes on from that board to its goal."""
    fake = Appearing()
    author, light = Author(fake, []), Light(names())
    author.unblocked = True                 # asked what is in the way, it sees a board in play, as the game's model did
    played, made = await agent.play(fake, author, "slide_merge", goal=GoalSpec("actions", 30), budget=Budget(60, 20, 120),
                                    light=light)
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    # the board the second move was played from held a 4 the game never had
    first, second = played.moves[:2]
    assert (first["effect"], second["effect"]) == ("confirmed", "unknown"), played.moves[:3]
    assert (made["reseeds"]["agreed"], made["reseeds"]["alone"]) == (1, 0), made["reseeds"]
    started, = made["reseeds"]["each"]
    assert started == {"at_action": 2, "read_by": ["model", "kit", "light"], "doubted": "kit",
                       "cells": {"kit": 15, "light": 1}}, started
    # one question of the light model for it, none of the reading model beyond the reading the rules refused
    assert [kind for kind, _, _ in author.asked].count("label") == 1 + 1 + adaptation.LABEL_RETRIES, author.asked
    assert [kind for kind, _, _ in author.asked].count("recover") == 0
    # every move after it was read by the reader and borne out by the rules
    assert {move["effect"] for move in played.moves[2:]} == {"confirmed"}
    assert {move["level"] for move in played.moves[2:]} == {"L3"}, [move["level"] for move in played.moves]


async def test_a_second_reader_that_reads_the_screen_otherwise_leaves_the_game_held_to_the_last_move():
    """The same game with a light model that calls the cell the reader cannot read a 4. The model read a 2 there:
    no two readers agree on the screen, nothing vouches for the model's reading beyond itself, and the family
    plays no further move rather than go on from a reading taken on one reader's word (the model may not play the
    game itself here, so it ends)."""
    fake = Appearing()
    author, light = Author(fake, []), Light(names(fresh=4))
    author.unblocked = True
    played, made = await agent.play(fake, author, "slide_merge", goal=GoalSpec("actions", 30), budget=Budget(60, 20, 120),
                                    light=light, direct=False)
    assert (played.record["outcome"], played.record["reason"]) == ("gave_up", "inconsistent"), (played.record, made)
    assert "reseeds" not in made and len(fake.keys) == 2, made.get("reseeds")
    # nor with no light model at all: the cell the reader left unread has the model's word alone
    alone = Appearing()
    author = Author(alone, [])
    author.unblocked = True
    played, made = await agent.play(alone, author, "slide_merge", goal=GoalSpec("actions", 30), budget=Budget(60, 20, 120),
                                    direct=False)
    assert (played.record["outcome"], played.record["reason"]) == ("gave_up", "inconsistent"), (played.record, made)
    assert "reseeds" not in made and len(alone.keys) == 2


async def test_a_result_the_reader_itself_reads_as_none_the_rules_allow_is_gone_on_from_and_told_as_one_readers_word():
    """Only the first move's tile comes in looking otherwise. The reader reads the second move's result whole, and
    it is no result of that move from the board with the 4: the next board is read for itself, as it always was,
    and the game goes on. That is now told with the game, as a start on one reader's word."""
    fake = Appearing(looks={1: FRESH[1]})
    author = Author(fake, [])
    played, made = await agent.play(fake, author, "slide_merge", goal=GoalSpec("actions", 30), budget=Budget(60, 20, 120),
                                    light=Light(names()))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    assert played.moves[1]["effect"] == "inconsistent" and (made["reseeds"]["agreed"], made["reseeds"]["alone"]) == (0, 1)
    started, = made["reseeds"]["each"]
    assert started["at_action"] == 2 and len(started["read_by"]) == 1 and started["doubted"] == "kit", started
    assert {move["effect"] for move in played.moves[2:]} == {"confirmed"}


class Doubling(Screen):
    """A game that is not the one its rules say: every move brings in two tiles."""

    def _slide(self, direction):
        before = self.board
        super()._slide(direction)
        empty = [(r, c) for r in range(SIZE) for c in range(SIZE) if not self.board["grid"][r][c]]
        if self.board is not before and empty:
            row, col = empty[len(self.keys) % len(empty)]
            self.board["grid"][row][col] = 2


async def test_readings_that_keep_being_refused_are_not_one_board_misread_play_starts_over_once_and_no_more():
    """Every move of this game leaves a board the rules do not allow, and the reader and the model agree on each
    screen. Starting over at each would take any reading at all: it is done once, the next refusal comes before
    any move was borne out, and the game is given up as it was."""
    fake = Doubling()
    author = Author(fake, [])
    author.unblocked = True
    played, made = await agent.play(fake, author, "slide_merge", goal=GoalSpec("actions", 30), budget=Budget(60, 20, 120),
                                    light=Light(names()), direct=False)
    assert (played.record["outcome"], played.record["reason"]) == ("gave_up", "inconsistent"), (played.record, made)
    assert made["reseeds"]["agreed"] == 1 and played.record["confirmed_actions"] < agent.RESEED_EVERY, (played.record, made)
    agreed, = [entry["read_by"] for entry in made["reseeds"]["each"] if len(entry["read_by"]) > 1]
    assert agreed[:2] == ["model", "kit"], made["reseeds"]


class Playing(Author):
    """The model: it reads the board, says a board in play is in nobody's way, and when the game is its own to play
    presses a key `plays` times and then calls the game lost."""

    def __init__(self, fake, plays):
        super().__init__(fake, [])
        self.unblocked, self.plays = True, plays

    async def complete(self, request):
        import json

        from gamelab.model import TurnResult

        context = json.loads(request.messages[0].content[0]["text"])
        if not context.get("play"):
            return await super().complete(request)
        self.asked.append(("play", context, 1))
        self.plays -= 1
        action = {"kind": "key", "key": "ArrowDown" if self.plays % 2 else "ArrowLeft"} if self.plays >= 0 \
            else {"kind": "done", "end": "lost"}
        return TurnResult(text=json.dumps({"situation": "a board in play", "score": None, "action": action,
                                           "expect": "", "notes": ""}))


async def test_a_registered_family_that_cannot_follow_its_board_is_left_for_the_models_own_play_not_the_games_end():
    """The game whose every move breaks the rules, where the model may play: once play started over and the next
    reading was refused again, the game was given up with its board in plain sight. The family is let go
    instead, the model plays on with its own inputs, and the game ends where the model sees its end."""
    fake = Doubling()
    model = Playing(fake, plays=2)
    played, made = await agent.play(fake, model, "slide_merge", goal=GoalSpec("play_until_end"), budget=Budget(60, 20, 120),
                                    light=Light(names()))
    assert (played.record["outcome"], played.record["reason"]) == ("game_over", "end_screen"), (played.record, made)
    assert {"reason": "family_dropped", "detail": "inconsistent"} in made["generation_failures"], made["generation_failures"]
    assert made["let_go"][-1]["stage"] == "given_up" and made["let_go"][-1]["why"] == "inconsistent", made["let_go"]
    assert sum(kind == "play" for kind, _, _ in model.asked) == 3 and made["reseeds"]["agreed"] == 1


async def test_what_the_solver_kept_of_the_boards_before_is_not_carried_onto_the_board_play_starts_over_from(monkeypatch):
    """A solver that keeps something from screen to screen (`GridFamily.recall`) kept it of boards one of which
    was read wrongly: from the board play starts over on, it keeps anew."""
    from gamelab.solvers import slide_merge as solver

    choose, kept = solver.choose, []

    def recall(memory, state, rules):
        return {"boards": [*(memory or {"boards": []})["boards"], state["grid"]]}

    def chosen(state, rules, policy, compute, excluded, memory=None):
        kept.append(len(memory["boards"]))
        return choose(state, rules, policy, compute, excluded)
    monkeypatch.setattr(solver, "recall", recall, raising=False)
    monkeypatch.setattr(solver, "choose", chosen)
    fake = Appearing()
    author = Author(fake, [])
    played, made = await agent.play(fake, author, "slide_merge", goal=GoalSpec("actions", 6), budget=Budget(20, 20, 120),
                                    light=Light(names()))
    assert played.record["outcome"] == "goal_reached" and made["reseeds"]["agreed"] == 1, (played.record, made)
    # two boards seen, then the one started over from alone, and each after it added to that
    assert kept[:5] == [1, 2, 1, 2, 3], kept


class Smudged(mines.Mines):
    """A minesweeper that once draws the cells just opened to a count not shown before as another count (`drawn`),
    until the next tap (a digit still being drawn). What a cell opens to is the game's, so the rules take it."""

    def __init__(self, after=2, drawn=2, **options):
        super().__init__(**options)
        self.after, self.drawn, self.smudge, self.smudged, self.shown = after, drawn, [], False, set()

    async def tap(self, x, y, duration_ms):
        before = [list(row) for row in self.grid]
        await super().tap(x, y, duration_ms)
        self.smudge = []
        opened = [(r, c) for r in range(9) for c in range(9) if before[r][c] == COVERED and is_open(self.grid[r][c])]
        fresh = sorted({self.grid[r][c] for r, c in opened if self.grid[r][c] >= 1} - self.shown - {self.drawn})
        if fresh and not self.smudged and self.played >= self.after and not self.over():
            self.smudge, self.smudged = [(r, c) for r, c in opened if self.grid[r][c] == fresh[0]], True
        self.shown |= {self.grid[r][c] for r, c in opened if (r, c) not in self.smudge}

    async def capture(self, region=None):
        grid = [list(row) for row in self.grid]
        for row, col in self.smudge:
            grid[row][col] = self.drawn
        return mines.render(grid, self.covered, self.button)


async def test_the_same_way_starts_a_game_of_another_family_over_a_count_read_wrongly_is_not_held_against_the_next_screen():
    """Minesweeper by its family's reader: a count just opened is read as the 2 it was drawn as for a moment. The
    next move's result shows the count it is, a look the reader has not seen: the light model names it, the rules
    refuse it of the board with the 2, and so they do the model's reading. The reader and the light model then
    read the screen as the model did, and the game goes on from it to its end."""
    fake = Smudged()
    reader, light = mines.Reader(fake, recovering=[{"kind": "none"}] * 4), Light(MINE_NAMES)
    played, made = await agent.play(fake, reader, "minesweeper", goal=GoalSpec("play_until_end"),
                                    budget=Budget(60, mines.COMPUTE, 300), light=light)
    assert fake.smudged and made.get("reseeds"), (played.record, made)
    started = made["reseeds"]["each"][0]
    assert started["read_by"] == ["model", "kit", "light"] and started["doubted"] == "kit", made["reseeds"]
    assert started["cells"]["kit"] + started["cells"]["light"] == 81 and started["cells"]["light"] >= 1, started
    assert played.record["outcome"] == "goal_reached" and fake.over(), (played.record, made)
    assert not any(cell == EXPLODED for row in fake.grid for cell in row)
    # the move whose result was refused is the only one not borne out, and no screen stopped play
    at = started["at_action"]
    assert [move["effect"] for move in played.moves[at:]] == ["confirmed"] * (len(played.moves) - at), played.moves
    assert [kind for kind, _, _ in reader.asked].count("recover") == 0
