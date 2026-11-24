"""Play the model made between two levels is made again by code (PLAN §28), and what goes with it: a sequence of
inputs checked between them (§27, M1), a memory in parts that outlasts the work (M4), and things kept by the one
game of several an app holds (§12, A1).

The screens are the garden of `test_between.py`: a level's end, a story of three lines, a map with the button
that deals the next level and a shop beside it. A stretch the model played once is played by the steps kept of
it the next time, in the same work and in the next one; where a screen no step fits stands in the way the model
is asked there and nowhere else, and its answer is kept too; a step that no longer does anything gives way to
what the model then answers."""
import json
import time

from gamelab import agent, direct, request, routine, work
from gamelab.adaptation import Store, keyed
from gamelab.contracts import Budget, GoalSpec, Scope
from gamelab.model import TurnResult
from test_adaptation_mines import COMPUTE, SIZE, WINNING, middle, quick as mines_quick  # noqa: F401
from test_between import CLOSE, LINES, PLAY, SHOP, Garden, Guide, inside, page, plays, quick as direct_quick  # noqa: F401
from test_work import ending

LEVELS = GoalSpec("levels", 3)


def budget():
    return Budget(300, COMPUTE, 600)


def between(played, part):
    """The inputs sent outside the level in one part of the work: who sent each, on what kind of screen."""
    return [(move["level"], move.get("scene")) for move in played.moves
            if move["part"] == part and (move["level"] == "R" or move.get("scene") not in (None, "board"))]


async def test_a_stretch_the_model_played_is_played_by_code_the_next_time_and_in_the_next_work(tmp_path):
    fake = Garden([WINNING] * 3)
    model = Guide(fake, repeat=5)
    played, made = await work.play(fake, model, "minesweeper", goal=LEVELS, budget=budget(), store=Store(tmp_path))
    assert ending(played) == ("goal_reached", "levels"), (played.record, made["work"])
    # the first time the model was asked at every screen; the second, at none, not even at the board
    assert between(played, 2) == [("L0", "result"), ("L0", "story")] + [("L0", "map")] and len(plays(model)) == 4
    assert between(played, 3) == [("R", "result")] + [("R", "story")] * LINES + [("R", "map")]
    parts = made["work"]["parts"]
    assert [part["between"]["by_model"] for part in parts] == [0, 3, 0]
    assert [part["between"]["by_routine"] for part in parts] == [0, 0, LINES + 2]
    told = made["work"]["routines"][0]
    assert (told["task"], told["kept_before"], told["kept"], told["learned"], told["sent"], told["failed"]) == \
        ("next_level", 0, 3, 3, LINES + 2, 0)
    # what was learned is kept by the game: its steps, and the screen each was learned on
    kept = Store(tmp_path).routines("https://mines.example/")["next_level"]
    assert [step["scene"] for step in kept] == ["result", "story", "map"] and kept[-1]["opens"] and "opens" not in kept[0]
    assert kept[1]["action"]["repeat"] == 5
    folder = next(tmp_path.glob("*/routines"))
    assert sorted(path.name for path in folder.glob("*.png")) == [f"next_level-n{number}.png" for number in (1, 2, 3)]
    # another work on the same game: every stretch is the code's, and the model is asked nothing of the way
    fake = Garden([WINNING] * 3)
    model = Guide(fake, repeat=5)
    played, made = await work.play(fake, model, "minesweeper", goal=LEVELS, budget=budget(), store=Store(tmp_path))
    assert ending(played) == ("goal_reached", "levels") and fake.ends == ["won"] * 3
    assert plays(model) == [] and between(played, 2) == between(played, 3) == [("R", "result")] + [("R", "story")] * LINES + [("R", "map")]
    assert made["work"]["routines"][0]["kept_before"] == 3 and made["work"]["routines"][0]["learned"] == 0


class Sale(Garden):
    """The garden, whose map is covered by the shop of its own accord before some levels (`sales`: the levels
    dealt before which it is)."""

    def __init__(self, draws, sales):
        super().__init__(draws)
        self.sales, self.offered = set(sales), set()

    async def tap(self, x, y, duration_ms):
        stage = self.stage
        await super().tap(x, y, duration_ms)
        if stage == "story" and self.stage == "map" and self.dealt in self.sales and self.dealt not in self.offered:
            self.offered.add(self.dealt)
            self.stage = "shop"


async def test_a_screen_no_step_fits_is_the_models_from_there_on_and_what_it_answers_is_kept_too(tmp_path):
    # the shop comes up by itself before the third level and the fourth: the first time it is a screen no step
    # is kept for
    fake = Sale([WINNING] * 4, sales={2, 3})
    model = Guide(fake, repeat=5)
    played, made = await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 4), budget=budget(),
                                   store=Store(tmp_path))
    assert ending(played) == ("goal_reached", "levels"), (played.record, made["work"])
    # the stretch to the third level: the steps as far as they go, the model at the shop and nowhere else, the
    # steps again from the map
    assert between(played, 3) == [("R", "result")] + [("R", "story")] * LINES + [("L0", "popup"), ("R", "map")]
    asked = plays(model)
    assert len(asked) == 5 and asked[4]["history"][-1] == {
        "action": {"kind": "tap", "at": [300.0, 300.0]}, "sent_by": "program", "scene": "story", "screen_changed": True}
    # the stretch to the fourth: the shop is passed by the step kept of it
    assert between(played, 4) == [("R", "result")] + [("R", "story")] * LINES + [("R", "popup"), ("R", "map")]
    assert [step["scene"] for step in Store(tmp_path).routines("https://mines.example/")["next_level"]] == \
        ["result", "story", "map", "popup"]


class Moved(Garden):
    """The garden, whose map takes the next level from its other button once `moved` (the screen looks the same)."""
    moved = False

    async def tap(self, x, y, duration_ms):
        if self.stage == "map" and self.moved:
            self.taps.append((x, y))
            if inside(x, y, SHOP):
                self.covered, self.stage = True, "board"
                await super(Garden, self).tap(*middle(self.button), duration_ms)
                self.taps.pop()
            return
        await super().tap(x, y, duration_ms)


async def test_a_step_that_changes_nothing_gives_way_to_what_the_model_answers_in_its_place(tmp_path):
    fake = Moved([WINNING] * 2)
    played, _ = await work.play(fake, Guide(fake, repeat=5), "minesweeper", goal=GoalSpec("levels", 2), budget=budget(),
                                store=Store(tmp_path))
    assert ending(played) == ("goal_reached", "levels")
    old = Store(tmp_path).routines("https://mines.example/")["next_level"][-1]
    assert old["scene"] == "map" and inside(*old["action"]["at"], PLAY)
    # the game changed: the map's old button does nothing now
    fake = Moved([WINNING] * 3)
    fake.moved = True
    model = Guide(fake, repeat=5, shops=True)
    played, made = await work.play(fake, model, "minesweeper", goal=LEVELS, budget=budget(), store=Store(tmp_path))
    assert ending(played) == ("goal_reached", "levels"), (played.record, made["work"])
    # the step was sent, changed nothing, and the model was asked on that screen; its answer is the step from then on
    assert between(played, 2) == [("R", "result")] + [("R", "story")] * LINES + [("R", "map"), ("L0", "map")]
    assert [move["effect"] for move in played.moves if move["part"] == 2 and move.get("scene") == "map"] == ["absent", "confirmed"]
    assert between(played, 3) == [("R", "result")] + [("R", "story")] * LINES + [("R", "map")]
    # (asked twice in all: at the map, and at the board that the new step opened for the first time)
    assert len(plays(model)) == 2
    kept = Store(tmp_path).routines("https://mines.example/")["next_level"]
    assert [step["scene"] for step in kept] == ["result", "story", "map"] and inside(*kept[-1]["action"]["at"], SHOP)
    assert made["work"]["routines"][0]["failed"] == 1


def test_a_step_fits_a_screen_only_when_the_screen_and_the_place_pressed_look_as_they_did():
    town = page((120, 190, 140), [(PLAY, (40, 120, 220)), (SHOP, (220, 160, 40))])
    kept = routine.Routine("next_level")
    step = kept.learn(town, {"kind": "tap", "at": middle(PLAY)}, scene="map")
    assert kept.find(town) is step and kept.looks[-1]["step"] == step["id"]
    # the same screen with the button gone from its place: the whole screen is still near, the place is not
    bare = page((120, 190, 140), [(SHOP, (220, 160, 40))])
    assert kept.find(bare, at=1) is None and kept.looks[-1]["nearest"]["spot"] > routine.SPOT_DISTANCE
    assert kept.looks[-1]["nearest"]["screen"] <= routine.SCREEN_DISTANCE
    # another screen altogether
    assert kept.find(page((60, 60, 70)), at=2) is None and kept.looks[-1]["nearest"]["screen"] > routine.SCREEN_DISTANCE
    # a step left out for now
    assert kept.find(town, avoid={step["id"]}, at=3) is None
    # the same input learned again on a screen like it is the same step; a screen to wait on is taken only where
    # no input fits
    assert kept.learn(town, {"kind": "tap", "at": [middle(PLAY)[0] + 4, middle(PLAY)[1]]}, scene="map") is step
    waiting = kept.learn(town, {"kind": "wait", "ms": 1500}, scene="loading")
    assert len(kept.steps) == 2 and kept.find(town, at=4) is step and kept.find(town, avoid={step["id"]}, at=5) is waiting
    # one that changed nothing twice running is kept no longer
    kept.used(step, False)
    kept.used(step, True)
    kept.used(step, False)
    assert step in kept.steps
    kept.used(step, False)
    assert step not in kept.steps and kept.summary()["failed"] == 3


def test_what_is_kept_as_a_step_and_what_never_is():
    tap = {"kind": "tap", "at": [10.0, 10.0]}
    assert routine.kept(tap, "map", {"uses": []}) and routine.kept({"kind": "back"}, "popup", {})
    assert routine.kept({"kind": "wait", "ms": 500}, "loading", {})
    # a level's move, a side puzzle's, a name made, anything that uses up what is the player's, typed text
    assert not routine.kept(tap, "board", {}) and not routine.kept(tap, "other_puzzle", {}) and not routine.kept(tap, None, {})
    assert not routine.kept(tap, "popup", {"profile": "Player"}) and not routine.kept(tap, "result", {"uses": ["retry"]})
    assert not routine.kept({"kind": "text", "text": "Player"}, "popup", {})
    # a step the model sent twice in one stretch on screens alike may be sent twice by code; any other, once
    kept = routine.Routine("next_level")
    step = kept.learn(page((200, 180, 120), bar=0), tap, scene="result", sends={})
    assert kept.most(step) == 1
    assert kept.learn(page((200, 180, 120), bar=1), tap, scene="result", sends={step["id"]: 1}) is step and kept.most(step) == 2
    assert kept.most(kept.learn(page((60, 60, 70)), {**tap, "repeat": 4}, scene="story")) == routine.REPEATED_MOST
    # steps read back from what was kept: one that is no step is left out
    again = routine.Routine("next_level", json.loads(json.dumps(kept.steps)) + [{"id": "n9"}, "noise"])
    assert [step["id"] for step in again.steps] == ["n1", "n2"] and again.learn(page((9, 9, 9)), tap, scene="map")["id"] == "n3"


class Rewards(Guide):
    """The model, which answers a story's screen with every tap it will take at once (`taps` of them) and the
    map's button after them, a screen it does not see yet."""

    def __init__(self, fake, taps):
        super().__init__(fake)
        self.taps = taps

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if context.get("play") and self.fake.stage == "story":
            self.asked.append(("play", context, 1))
            inputs = [{"kind": "tap", "at": [300, 300]}] * self.taps + [{"kind": "tap", "at": middle(SHOP)}]
            return TurnResult(text=json.dumps({"situation": "story", "scene": "story", "guided": False, "score": None,
                                               "action": {"kind": "sequence", "inputs": inputs}, "expect": "the map",
                                               "memory": {"ways": "three lines, then the map"}}))
        return await super().complete(request)


async def test_the_inputs_of_a_sequence_off_the_board_are_sent_only_while_each_changes_a_little_of_the_screen():
    fake = Garden([WINNING] * 2)
    model = Rewards(fake, taps=LINES)
    played, made = await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 2), budget=budget())
    assert ending(played) == ("goal_reached", "levels"), (played.record, made["work"])
    # the third tap brought the map: the tap meant for a screen the model had not seen was not sent
    story = next(move for move in played.moves if move.get("scene") == "story")
    assert story["repeated"] == {"sent": LINES, "of": LINES + 1, "stopped": "scene_changed"} and fake.shopped == 0
    told = next(context for context in plays(model) if context["history"] and context["history"][-1].get("repeated"))
    assert told["history"][-1]["repeated"]["stopped"] == "scene_changed"
    # six inputs at most, none of them a wait or typed text
    tap = {"kind": "tap", "at": [5, 5]}
    assert direct.check({"kind": "sequence", "inputs": [tap] * 6}, SIZE, fake.kinds) is not None
    assert direct.check({"kind": "sequence", "inputs": [tap] * 7}, SIZE, fake.kinds) is None
    assert direct.check({"kind": "sequence", "inputs": [tap, {"kind": "wait", "ms": 100}]}, SIZE, fake.kinds) is None


async def test_what_the_model_noted_of_a_game_is_kept_by_its_parts_and_given_to_the_next_work(tmp_path):
    fake = Garden([WINNING] * 2)
    model = Rewards(fake, taps=1)
    await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 2), budget=budget(), store=Store(tmp_path))
    # a part given replaces what it held, one left out stays; a plain text is the plan. All but the plan are kept
    asked = plays(model)
    assert asked[0]["memory"] == {} and set(asked[0]["memory_parts"]) == {"rules", "ways", "failed", "run", "plan"}
    assert asked[-1]["memory"]["ways"] == "three lines, then the map" and "plan" in asked[-1]["memory"]
    assert Store(tmp_path).memory("https://mines.example/") == {"ways": "three lines, then the map"}
    fake = Sale([WINNING] * 2, sales={1})
    model = Guide(fake)
    await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 2), budget=budget(), store=Store(tmp_path))
    assert plays(model)[0]["memory"] == {"ways": "three lines, then the map"}
    # a memory as the model keeps it
    player = direct.Direct(model, notes={"rules": "  match three  ", "plan": "x" * 900, "other": "no part", "failed": 3})
    assert player.memory == {"rules": "match three", "plan": "x" * direct.MEMORY_MOST} and player.lasting() == {}
    player.questions = 1
    player._remember({"plan": "", "ways": "the right button"})
    assert player.lasting() == {"rules": "match three", "ways": "the right button"} and "plan" not in player.memory
    # a part longer than it may be is cut, counted, and said at the next question; the rules may be longer than the rest
    assert player.cut == {} and direct.memory_most("rules") > direct.memory_most("ways") == direct.MEMORY_MOST
    player._remember({"rules": "r" * 1000, "ways": "w" * 450})
    assert len(player.memory["rules"]) == 1000 and len(player.memory["ways"]) == direct.MEMORY_MOST
    assert player.cut == {"ways": 1} and "50 were cut" in player.cut_last["ways"] and player.summary()["memory_cut"] == {"ways": 1}
    player._remember({"ways": "short"})
    assert player.cut_last == {} and player.cut == {"ways": 1}


async def test_things_are_kept_by_the_one_game_of_several_an_app_holds(tmp_path):
    app = "android://name.boyle.chris.sgtpuzzles"
    assert keyed(app, None) == keyed(app, " ") == app
    assert keyed(app, "Mines") == keyed(app, "mines!") == f"{app}#game=mines" != keyed(app, "Fifteen")
    store = Store(tmp_path)
    store.save_recoveries(keyed(app, "mines"), "minesweeper", [{"action": {"kind": "tap", "at": [1, 1]}, "signature": [0]}])
    store.save_memory(keyed(app, "fifteen"), {"rules": "slide the tiles in order"})
    store.save_memory(app, {"rules": "a list of puzzles"})
    assert store.recoveries(keyed(app, "fifteen"), "minesweeper") == [] and store.recoveries(app, "minesweeper") == []
    assert len(store.recoveries(keyed(app, "mines"), "minesweeper")) == 1
    assert store.memory(keyed(app, "fifteen")) == {"rules": "slide the tiles in order"} and store.memory(keyed(app, "mines")) is None
    assert store.memory(app) == {"rules": "a list of puzzles"}
    assert sorted(path.name.split("+")[-1] for path in tmp_path.iterdir() if "+" in path.name) == ["fifteen", "mines"]
    # a game played as one of several keeps its routines apart from the app's other games
    fake = Garden([WINNING] * 2)
    await work.play(fake, Guide(fake), "minesweeper", goal=GoalSpec("levels", 2), budget=budget(), store=store, game="Mines")
    assert store.routines("https://mines.example/#game=mines")["next_level"] and store.routines("https://mines.example/") == {}

    class Says:
        async def complete(self, turn):
            return TurnResult(text=json.dumps({"url": None, "app": "name.boyle.chris.sgtpuzzles", "objective": "play_until_end",
                                               "game": "Mines 9x9", "within": " mines "}))
    apps = [{"package": "name.boyle.chris.sgtpuzzles", "label": "Puzzles"}]
    asked = await request.understand(Says(), "퍼즐 앱에서 Mines 끝까지", apps)
    assert (asked["within"], asked["hint"]) == ("mines", "Mines 9x9")
    # a name that only says the app's own again picks no game inside it
    apps = [{"package": "com.example.mines", "label": "Mines Classic"}]

    class Named:
        async def complete(self, turn):
            return TurnResult(text=json.dumps({"url": None, "app": "com.example.mines", "objective": "play_until_end",
                                               "game": "Mines", "within": "Mines"}))
    assert (await request.understand(Named(), "Mines 끝까지", apps))["within"] is None


async def test_a_screen_on_its_way_is_looked_at_again_before_the_model_is_asked(monkeypatch):
    """After a step sent by code the next screen may not stand yet: it is looked at again until it does or has
    stood still a while, and only then is anyone asked."""

    class Slow(Garden):
        """The garden, whose map shows a blank page for a moment after the story's last line."""
        until = 0.0

        async def capture(self, region=None):
            if self.stage == "map" and time.monotonic() < self.until:
                return page((10, 10, 10))
            return await super().capture(region)

        async def tap(self, x, y, duration_ms):
            if self.stage == "map" and time.monotonic() < self.until:
                return
            stage = self.stage
            await super().tap(x, y, duration_ms)
            if stage == "story" and self.stage == "map" and self.dealt >= 2:
                self.until = time.monotonic() + 0.3

    monkeypatch.setattr(agent, "STILL_S", 30.0)
    monkeypatch.setattr(agent, "TRANSIT_S", 3.0)
    fake = Slow([WINNING] * 3)
    model = Guide(fake, repeat=5)
    played, made = await work.play(fake, model, "minesweeper", goal=LEVELS, budget=budget())
    assert ending(played) == ("goal_reached", "levels"), (played.record, made["work"])
    # the blank page was waited out: the map's step was sent once the map stood there, and no model saw the page
    assert between(played, 3) == [("R", "result")] + [("R", "story")] * LINES + [("R", "map")] and len(plays(model)) == 4


def test_a_maps_step_fits_the_map_seen_from_elsewhere_with_its_button_relabelled_and_another_screens_does_not():
    def town(shift, label):
        # a map: something large that stands elsewhere when the map is seen from elsewhere, and a button with a label
        return page((120, 190, 140), [((400 + shift, 100, 300, 200), (30, 60, 30)), (PLAY, (40, 120, 220)),
                                      ((PLAY[0] + 50, PLAY[1] + 10, 20, 20), label)])
    tap = {"kind": "tap", "at": middle(PLAY)}
    first, later = town(0, (250, 250, 250)), town(-300, (250, 200, 40))
    kept = routine.Routine("next_level")
    step = kept.learn(first, tap, scene="map")
    assert kept.find(later) is step
    look = kept.looks[-1]
    assert routine.SCREEN_DISTANCE < look["screen"] <= routine.MAP_DISTANCE and look["spot"] <= routine.SPOT_DISTANCE
    # the same input learned from there is the same step
    assert kept.learn(later, tap, scene="map") is step and len(kept.steps) == 1
    # a step of any other kind of screen must have the whole screen like it
    other = routine.Routine("next_level")
    other.learn(first, tap, scene="popup")
    assert other.find(later) is None and other.find(town(0, (250, 200, 40))) is not None
    # the button gone from the map: no fit, however like the rest is
    assert kept.find(page((120, 190, 140), [((400, 100, 300, 200), (30, 60, 30))]), at=1) is None
    # two places are apart by the cells that differ least: a label's worth of them changes nothing
    assert routine.apart([0] * 100, [0] * 75 + [200] * 25) == 0 and routine.apart([0] * 100, [200] * 100) == 200
    assert routine.apart([0] * 4, [0] * 5) == 255.0


async def test_a_stretch_that_reaches_no_board_leaves_no_step_behind(tmp_path):
    """What the model pressed on a way that ended at a stop is not pressed again by code: the steps of a stretch
    are kept once it got to a level's board, and not before."""

    class Stops(Guide):
        async def complete(self, request):
            context = json.loads(request.messages[0].content[0]["text"])
            if context.get("play") and self.fake.stage == "map":
                self.asked.append(("play", context, 1))
                return TurnResult(text=json.dumps({"situation": "the map", "scene": "map", "guided": False, "score": None,
                                                   "action": {"kind": "stop", "because": "the way on costs coins"},
                                                   "expect": "", "memory": {}}))
            return await super().complete(request)

    fake = Garden([WINNING] * 2)
    played, made = await work.play(fake, Stops(fake, repeat=5), "minesweeper", goal=GoalSpec("levels", 2), budget=budget(),
                                   store=Store(tmp_path))
    assert ending(played) == ("stopped", "stop_condition") and fake.stage == "map"
    told = made["work"]["routines"][0]
    assert (told["learned"], told["dropped"], told["kept"]) == (2, 2, 0)
    assert Store(tmp_path).routines("https://mines.example/") == {} and not list(tmp_path.glob("*/routines/*.png"))
    # the same game played on by a model that goes on: the stretch is the model's again, and kept this time
    model = Guide(fake, repeat=5)
    played, made = await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 1, scope=Scope.CURRENT_GAME),
                                   budget=budget(), store=Store(tmp_path))
    assert ending(played) == ("goal_reached", "levels"), (played.record, made["work"])
    assert [step["scene"] for step in Store(tmp_path).routines("https://mines.example/")["next_level"]] == ["map"]
    assert all("trial" not in step for step in Store(tmp_path).routines("https://mines.example/")["next_level"])


class Reward(Garden):
    """The garden, whose map's button puts a reward window (the shop's screen) over the map before some levels in
    place of dealing the next (`rewards`: the levels dealt before which it does, once each; `always`: every time);
    the window's close button brings the map back."""

    def __init__(self, draws, rewards=(), always=False):
        super().__init__(draws)
        self.rewards, self.always, self.given = set(rewards), always, set()

    async def tap(self, x, y, duration_ms):
        if self.stage == "map" and inside(x, y, PLAY) and (self.always or self.dealt in self.rewards - self.given):
            self.taps.append((x, y))
            self.given.add(self.dealt)
            self.stage = "shop"
            return
        await super().tap(x, y, duration_ms)


def steps(played, part):
    """Who sent each input on the map and the window over it in one part of the work, and by which step."""
    return [(move["level"], move.get("step")) for move in played.moves
            if move["part"] == part and move.get("scene") in ("map", "popup")]


async def test_a_step_is_sent_again_when_play_came_back_to_its_screen_by_way_of_another_and_not_round_and_round(tmp_path):
    # the map's button puts a reward window over the map before the third level and the fourth; closed, the map
    # stands there again and its button deals the level
    fake = Reward([WINNING] * 4, rewards={2, 3})
    model = Guide(fake, repeat=5)
    played, made = await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 4), budget=budget(),
                                   store=Store(tmp_path))
    assert ending(played) == ("goal_reached", "levels"), (played.record, made["work"])
    # the stretch to the third level: the model at the window, which no step was kept for (the family tried on it
    # first, the map's step having opened a level before, and let go: a stretch of its own from there)
    assert between(played, 3) == [("R", "result")] + [("R", "story")] * LINES + [("R", "map"), ("L0", "popup"), ("R", "map")]
    assert steps(played, 3) == [("R", "n3"), ("L0", None), ("R", "n3")]
    # the stretch to the fourth, one stretch all of it the code's: the map's button pressed again once play came
    # back to the map by way of the window's step, and the model asked nothing
    assert steps(played, 4) == [("R", "n3"), ("R", "n4"), ("R", "n3")] and len(plays(model)) == 5
    kept = Store(tmp_path).routines("https://mines.example/")["next_level"]
    assert [step["scene"] for step in kept] == ["result", "story", "map", "popup"] and "again" not in kept[2]
    # a game whose window comes back every time the map's button is pressed: the button is pressed by code again
    # once by way of the window, and the second time play comes back that way the model is asked
    fake = Reward([WINNING] * 2, always=True)
    model = Guide(fake, repeat=5)
    played, made = await work.play(fake, model, "minesweeper", goal=GoalSpec("levels", 2), budget=budget(),
                                   store=Store(tmp_path))
    assert ending(played) == ("gave_up", "going_round"), (played.record, made["work"])
    assert steps(played, 2)[:5] == [("R", "n3"), ("R", "n4"), ("R", "n3"), ("R", "n4"), ("L0", None)]
    assert plays(model)[0]["been_here"]["times"] == 2


def test_a_step_is_spent_when_play_comes_straight_back_or_back_again_by_the_same_way():
    town = page((120, 190, 140), [(PLAY, (40, 120, 220)), (SHOP, (220, 160, 40))])
    shop = page((60, 60, 70), [((60, 60, 400, 380), (250, 250, 250)), (CLOSE, (200, 40, 40))])
    kept = routine.Routine("next_level")
    button = kept.learn(town, {"kind": "tap", "at": middle(PLAY)}, scene="map")
    close = kept.learn(shop, {"kind": "tap", "at": middle(CLOSE)}, scene="popup")
    other = kept.learn(page((60, 60, 70)), {"kind": "back"}, scene="popup")
    assert not kept.spent(button, 0, None, {}) and not kept.spent(button, 0, button, {})
    # straight back to its screen: by the step itself, or with no step between that changed the screen
    assert kept.spent(button, 1, button, {}) and kept.spent(button, 1, None, {})
    # back by way of the window's step: once more, and not again by the same way
    assert not kept.spent(button, 1, close, {})
    assert kept.spent(button, 2, close, {button["id"]: {close["id"]}})
    # by way of another step it may once more, up to RETURNS_MOST more sends in all
    assert not kept.spent(button, 2, other, {button["id"]: {close["id"]}})
    assert kept.spent(button, 1 + routine.RETURNS_MOST, other, {button["id"]: {close["id"]}})


class Town:
    """A game's home screen with nothing to play on it: a reward's button, which opens a window with a claim
    button; claimed, the home screen stands there again without the reward's button."""
    name = "fake"
    reads_dom = False
    kinds = frozenset({"keys", "tap", "drag"})

    def __init__(self):
        self.stage, self.taps = "home", []

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "https://town.example/"

    async def capture(self, region=None):
        if self.stage == "home":
            return page((90, 140, 200), [(PLAY, (240, 200, 40))])
        if self.stage == "window":
            return page((60, 60, 70), [((60, 60, 400, 380), (250, 250, 250)), (CLOSE, (40, 200, 40))])
        return page((90, 140, 200))

    async def tap(self, x, y, duration_ms):
        self.taps.append((x, y))
        if self.stage == "home" and inside(x, y, PLAY):
            self.stage = "window"
        elif self.stage == "window" and inside(x, y, CLOSE):
            self.stage = "claimed"

    async def key(self, key):
        pass

    async def drag(self, *args):
        raise AssertionError("nothing here is dragged")

    async def release(self):
        pass


class Errands:
    """The model, which names no family for the town and passes its screens: the reward's button, the claim, and
    the errand done on the home screen once the reward is claimed."""

    def __init__(self, fake):
        self.fake, self.asked, self.pictures = fake, [], []

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "families" in context:
            raise AssertionError("an errand is no game to find a family for")
        if not context.get("play"):
            return TurnResult(text=json.dumps({"screen": "other", "board": None, "rules": None, "state": None}))
        self.asked.append(context)
        self.pictures.append(sum(part.get("type") == "image" for part in request.messages[0].content))
        if self.fake.stage == "home":
            scene, action = "map", {"kind": "tap", "at": middle(PLAY)}
        elif self.fake.stage == "window":
            scene, action = "popup", {"kind": "tap", "at": middle(CLOSE)}
        else:
            scene, action = "map", {"kind": "done", "end": "won"}
        return TurnResult(text=json.dumps({"situation": self.fake.stage, "scene": scene, "guided": False, "score": None,
                                           "action": action, "expect": "the reward", "memory": {}}))


async def test_an_errand_the_request_names_is_a_routine_of_its_own_played_by_code_the_next_time(tmp_path):
    fake = Town()
    model = Errands(fake)
    played, made = await work.play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60),
                                   store=Store(tmp_path), task="daily_reward")
    assert ending(played) == ("goal_reached", "task_done"), (played.record, made)
    assert [move["level"] for move in played.moves] == ["L0", "L0"] and len(model.asked) == 3
    # the model is told what the errand is, and not that a game from its start is wanted
    assert all(asked["errand"] == "daily_reward" and "anew" not in asked for asked in model.asked)
    # the steps are kept under the errand's name, apart from the steps between levels
    kept = Store(tmp_path).routines("https://town.example/")
    assert set(kept) == {"daily_reward"} and [step["scene"] for step in kept["daily_reward"]] == ["map", "popup"]
    assert next(tmp_path.glob("*/routines/daily_reward.json")).is_file()
    # the same errand asked again: the code sends both steps, and the model is asked only where it is done
    fake = Town()
    model = Errands(fake)
    played, made = await work.play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60),
                                   store=Store(tmp_path), task="daily_reward")
    assert ending(played) == ("goal_reached", "task_done"), (played.record, made)
    assert [(move["level"], move.get("step")) for move in played.moves] == [("R", "d1"), ("R", "d2")]
    assert len(model.asked) == 1 and model.asked[0]["history"][-1]["sent_by"] == "program"
    # the end is checked against the screen the work found: the model is shown both
    assert model.asked[0]["began"] and model.pictures[-1] == 2
    assert "next_level" not in Store(tmp_path).routines("https://town.example/")


def written(*labels):
    """A bare page: black writing on white and nothing else, a few labels each at its place."""
    from io import BytesIO
    from PIL import Image, ImageDraw

    image = Image.new("RGB", SIZE, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for text, (x, y) in labels:
        draw.rectangle((x - 40, y - 12, x + 40, y + 12), outline=(0, 0, 0))
        draw.text((x - 30, y - 6), text, fill=(0, 0, 0))
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def test_on_a_bare_page_of_writing_a_step_fits_only_where_its_place_reads_as_it_did():
    # two screens of one page, alike all over: one button in the same place under another label (PLAN §28, S1)
    spot, tab = (300.0, 80.0), (500.0, 30.0)
    room = written(("stoke fire", spot), ("a silent forest", tab), ("the fire is roaring.", (100, 30)))
    forest = written(("gather wood", spot), ("a silent forest", tab), ("the room is hot.", (100, 30)))
    kept = routine.Routine("gather_wood")
    stoke = kept.learn(room, {"kind": "tap", "at": list(spot)}, scene="map")
    assert routine.strict(stoke)
    # by the screen and by the cells that differ least the two are one, by what the place reads they are not
    assert recovering_distance(room, forest) <= routine.SCREEN_DISTANCE
    assert routine.apart(stoke["spot"], routine.spot(forest, list(spot))) <= routine.SPOT_DISTANCE
    # nor does it matter what the button under it shows
    assert kept.find(written(("stoke fire", spot), ("check traps", (spot[0], spot[1] + 32)), ("a silent forest", tab)), at=9) is stoke
    assert kept.find(room) is stoke and kept.looks[-1]["read"] and kept.looks[-1]["spot"] == 0
    assert kept.find(forest, at=1) is None and kept.looks[-1]["nearest"]["spot"] > routine.SPOT_DISTANCE
    # the other button pressed in the same place is a step of its own, and each is found on its own screen
    gather = kept.learn(forest, {"kind": "tap", "at": list(spot)}, scene="map")
    assert gather is not stoke and len(kept.steps) == 2
    assert kept.find(forest, at=2) is gather and kept.find(room, at=3) is stoke
    assert kept.learn(forest, {"kind": "tap", "at": [spot[0] + 3, spot[1]]}, scene="map") is gather
    # the button gone, or its place taken by a filled bar: no fit
    assert kept.find(written(("a silent forest", tab)), avoid={stoke["id"]}, at=4) is None
    # writing elsewhere on the page changed (the log, a count): the same step
    assert kept.find(written(("gather wood", spot), ("a silent forest", tab), ("wood 54", (700, 80))), at=5) is gather
    # a pictured screen's step is not read: a button relabelled is the same button there (the test above)
    town = page((120, 190, 140), [((400, 100, 300, 200), (30, 60, 30)), (PLAY, (40, 120, 220))])
    assert not routine.strict(routine.Routine("next_level").learn(town, {"kind": "tap", "at": middle(PLAY)}, scene="map"))
    # places are read by their inner half, against the screen's ground
    assert routine.apart([255] * 144, [0] * 36 + [255] * 108, 255) == 0 and routine.apart([255] * 144, [0] * 144, 255) == 255


def recovering_distance(one, other):
    from gamelab import recovery

    return recovery._distance(recovery.signature(one), recovery.signature(other))
