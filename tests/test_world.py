"""A game that goes on between requests (PLAN §30): what is kept of it, and an errand it does not offer yet."""
import json
import time

from gamelab import agent, direct, request, work, world
from gamelab.adaptation import Store
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult
from test_adaptation_mines import middle
from test_between import CLOSE, PLAY
from test_routine import Errands, Town
from test_work import ending

SITE = "https://town.example/"


def test_an_errand_done_is_out_of_reach_for_the_time_read_and_one_never_done_is_not():
    known = world.World()
    assert known.wait("harvest", 1000.0) is None
    known.done("harvest", 1000.0, 60, {"Wood": 22, "fur": "many", "": 3})
    assert known.wait("harvest", 1010.0) == 50.0 and known.wait("harvest", 1060.0) is None
    assert known.have == {"wood": 22} and known.wait("other", 1010.0) is None
    told = known.summary(1010.0)
    assert told["tasks"]["harvest"] == {"ready": False, "in_s": 50, "done": 1, "last_done_s_ago": 10}
    assert known.summary(1100.0)["tasks"]["harvest"]["ready"]
    # no time read: nothing says it cannot be done
    known.done("harvest", 1100.0, None)
    assert known.wait("harvest", 1101.0) is None and known.tasks["harvest"]["count"] == 2
    # a time that is none
    assert world.again(True) is None and world.again(-3) is None and world.again("60") is None
    assert world.again(10 ** 9) is None and world.again(1.5) == 1.5


def test_an_errand_found_out_of_reach_after_its_time_says_the_games_time_ran_slower():
    known = world.World()
    known.done("harvest", 1000.0, 60)
    # looked for before its time (the model's reading was short of the clock): nothing is said of the game's time
    known.not_yet("harvest", 1030.0, 40)
    assert known.wait("harvest", 1030.0) == 40.0 and "early" not in known.tasks["harvest"]
    known.not_yet("harvest", 1080.0, 35, {"wood": 5})
    assert known.tasks["harvest"]["early"] == 1 and known.wait("harvest", 1080.0) == 35.0 and known.have == {"wood": 5}
    # found out of reach with no time shown: nothing is known of when
    known.not_yet("harvest", 1090.0)
    assert known.wait("harvest", 1091.0) is None


def test_the_world_is_kept_in_one_file_of_the_games_folder(tmp_path):
    known = world.World()
    known.open(Store(tmp_path), SITE)
    known.keep(Store(tmp_path))
    assert not list(tmp_path.glob("*/world.json"))
    known.done("harvest", 1000.0, 60, {"wood": 4})
    known.keep(Store(tmp_path))
    assert next(tmp_path.glob("*/world.json")).is_file()
    again = world.World()
    again.open(Store(tmp_path), SITE)
    assert again.wait("harvest", 1010.0) == 50.0 and again.have == {"wood": 4}
    fresh = world.World()
    fresh.open(Store(tmp_path), SITE, fresh=True)
    assert fresh.wait("harvest", 1010.0) is None


def test_an_answer_that_ends_an_errand_carries_what_it_read_of_the_world():
    kinds = frozenset({"tap"})
    done = direct.check({"kind": "done", "end": "won", "again_in_s": 60, "have": {"wood": 22}}, (400, 400), kinds)
    assert done == {"kind": "done", "end": "won", "again_in_s": 60.0, "have": {"wood": 22}}
    assert direct.check({"kind": "stop", "because": "cooling", "again_in_s": 30}, (400, 400), kinds) == {"kind": "stop", "again_in_s": 30.0}
    assert direct.check({"kind": "stop", "again_in_s": "soon"}, (400, 400), kinds) == {"kind": "stop"}


class Harvest(Errands):
    """The errands' model, which says with the errand done when it can be done again and what the game holds."""

    again_in_s = 600

    async def complete(self, request):
        result = await super().complete(request)
        answer = json.loads(result.text)
        if answer.get("action", {}).get("kind") == "done":
            answer["action"].update(again_in_s=self.again_in_s, have={"coins": 30})
        return TurnResult(text=json.dumps(answer))


def play(fake, model, tmp_path, **options):
    return work.play(fake, model, None, goal=GoalSpec("play_until_end"), budget=Budget(20, 1, 60),
                     store=Store(tmp_path), task="daily_reward", **options)


async def test_an_errand_not_due_yet_is_not_gone_after_and_its_time_left_is_handed_back(tmp_path):
    fake = Town()
    played, made = await play(fake, Harvest(fake), tmp_path)
    assert ending(played) == ("goal_reached", "task_done"), (played.record, made)
    kept = Store(tmp_path).world(SITE)
    assert kept["have"] == {"coins": 30} and kept["tasks"]["daily_reward"]["count"] == 1
    assert made["work"]["world"]["tasks"]["daily_reward"]["ready"] is False
    # asked again before its time: nothing is pressed (the routine has both steps), no model is asked
    fake = Town()
    model = Harvest(fake)
    played, made = await play(fake, model, tmp_path)
    assert ending(played) == ("stopped", "not_yet"), (played.record, made)
    assert fake.taps == [] and model.asked == [] and played.moves == [] and made["questions"] == 0
    later = made["work"]["not_yet"]
    assert later["task"] == "daily_reward" and 590 <= later["seconds"] <= 600 and later["inputs_before"] == 0
    assert "again in about" in work.told(made["work"])
    # nothing of that changed what is kept
    assert Store(tmp_path).world(SITE) == kept
    # once its time has come it is done again, by the routine's steps
    path = next(tmp_path.glob("*/world.json"))
    kept["tasks"]["daily_reward"]["ready_at"] = time.time() - 1
    path.write_text(json.dumps(kept), encoding="utf-8")
    fake = Town()
    played, made = await play(fake, Harvest(fake), tmp_path)
    assert ending(played) == ("goal_reached", "task_done"), (played.record, made)
    assert [move["level"] for move in played.moves] == ["R", "R"]
    done = Store(tmp_path).world(SITE)["tasks"]["daily_reward"]
    assert done["count"] == 2 and len(done["done"]) == 2


class Cooling(Harvest):
    """A model that finds the errand out of reach the first time it looks, for a time the screen shows."""

    again_in_s = 1

    def __init__(self, fake, wait_s):
        super().__init__(fake)
        self.wait_s, self.stopped = wait_s, 0

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if context.get("play") and not self.stopped:
            self.stopped += 1
            self.asked.append(context)
            return TurnResult(text=json.dumps({"situation": "cooling", "scene": "map", "guided": False, "score": None,
                                               "action": {"kind": "stop", "because": "the button is cooling",
                                                          "again_in_s": self.wait_s}, "expect": "", "memory": {}}))
        return await super().complete(request)


async def test_a_model_that_finds_the_errand_out_of_reach_has_its_time_kept_and_handed_back(tmp_path):
    fake = Town()
    played, made = await play(fake, Cooling(fake, 400), tmp_path)
    assert ending(played) == ("stopped", "not_yet"), (played.record, made)
    assert fake.taps == [] and 390 <= made["work"]["not_yet"]["seconds"] <= 400
    assert made["work"]["not_yet"]["said"] == "the button is cooling"
    kept = Store(tmp_path).world(SITE)["tasks"]["daily_reward"]
    assert kept["ready_at"] > time.time() + 300 and not kept.get("count")
    # the next request is not gone after either
    fake = Town()
    model = Cooling(fake, 400)
    played, made = await play(fake, model, tmp_path)
    assert ending(played) == ("stopped", "not_yet") and model.asked == [] and fake.taps == []


async def test_the_time_is_waited_out_with_the_game_open_when_the_person_asked_for_that(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "WAIT_OVER_S", 0.1)
    fake = Town()
    model = Cooling(fake, 1.5)
    began = time.monotonic()
    played, made = await play(fake, model, tmp_path, wait=True)
    assert ending(played) == ("goal_reached", "task_done"), (played.record, made)
    assert time.monotonic() - began >= 1.5 and made["waited_s"] in ([1], [2])
    assert fake.taps == [tuple(middle(PLAY)), tuple(middle(CLOSE))]
    # the model was told the program waited
    assert model.asked[1]["history"][-1]["sent_by"] == "program"
    # a wait the budget's seconds do not hold is handed back
    fake = Town()
    played, made = await play(fake, Cooling(fake, 500), tmp_path / "other", wait=True)
    assert ending(played) == ("stopped", "not_yet") and fake.taps == []


async def test_a_request_waits_for_an_errand_only_when_it_says_so():
    class Says:
        def __init__(self, answer):
            self.answer = answer

        async def complete(self, asked):
            return TurnResult(text=json.dumps(self.answer))

    async def wait(said):
        answer = {"url": "https://town.example/", "objective": "play_until_end", "task": "harvest", "wait": said}
        return (await request.understand(Says(answer), "town.example 에서 수확해줘"))["wait"]
    assert await wait(True) is True and await wait(False) is False and await wait("yes") is False and await wait(None) is False


class Unmoved(Errands):
    """A model that tells the errand done on the home screen at once, reading the same counts each time, and when
    told its `done` was not taken either sees the button cooling (`insists` False) or says done again."""

    def __init__(self, fake, insists):
        super().__init__(fake)
        self.insists = insists

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if not context.get("play"):
            return await super().complete(request)
        self.asked.append(context)
        doubted = any(entry.get("not_taken") for entry in context["history"])
        action = {"kind": "done", "end": "won", "again_in_s": 60, "have": {"fur": 1, "Wood": 20}}
        if doubted and not self.insists:
            action = {"kind": "stop", "because": "the button is still cooling", "again_in_s": 400, "have": {"fur": 1}}
        return TurnResult(text=json.dumps({"situation": "home", "scene": "map", "guided": False, "score": None,
                                           "action": action, "expect": "", "memory": {}}))


def test_what_the_game_holds_is_unmoved_only_when_every_count_known_before_is_the_same():
    memory = world.World()
    assert memory.unmoved({"fur": 1}) == {}
    memory.done("check_traps", 1000.0, 60, {"fur": 1, "wood": 20})
    assert memory.unmoved({"Fur": 1, "wood": 20, "bait": 2}) == {"fur": 1, "wood": 20}
    assert memory.unmoved({"fur": 2, "wood": 20}) == {} and memory.unmoved({"bait": 2}) == {} and memory.unmoved(None) == {}


async def test_an_errand_told_done_with_nothing_the_game_holds_changed_is_asked_about_once_more(tmp_path):
    # the first time nothing is known of what the game held: the model's word is taken
    fake = Town()
    played, made = await play(fake, Unmoved(fake, False), tmp_path)
    assert ending(played) == ("goal_reached", "task_done") and "unmoved" not in made["work"]
    path = next(tmp_path.glob("*/world.json"))
    kept = json.loads(path.read_text(encoding="utf-8"))
    kept["tasks"]["daily_reward"]["ready_at"] = time.time() - 1
    path.write_text(json.dumps(kept), encoding="utf-8")
    # told done again with the same counts: not taken, the model is told why, and what it then sees is kept
    fake = Town()
    model = Unmoved(fake, False)
    played, made = await play(fake, model, tmp_path)
    assert ending(played) == ("stopped", "not_yet"), (played.record, made)
    assert len(model.asked) == 2 and "fur" in model.asked[1]["history"][-1]["not_taken"]
    entry = Store(tmp_path).world(SITE)["tasks"]["daily_reward"]
    assert entry["count"] == 1 and entry["ready_at"] > time.time() + 300
    # a model that says done again is believed, and the handback says nothing counted had changed
    kept["tasks"]["daily_reward"]["ready_at"] = time.time() - 1
    path.write_text(json.dumps(kept), encoding="utf-8")
    fake = Town()
    model = Unmoved(fake, True)
    played, made = await play(fake, model, tmp_path)
    assert ending(played) == ("goal_reached", "task_done") and len(model.asked) == 2
    assert made["work"]["unmoved"] == {"fur": 1, "wood": 20} and "nothing the game holds changed" in work.told(made["work"])


class StillCooling(Cooling):
    """A model that finds the errand out of reach twice: first for a time it reads, then, after that time was
    waited out, with no time it can read."""

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if context.get("play") and self.stopped == 1:
            self.stopped += 1
            self.asked.append(context)
            return TurnResult(text=json.dumps({"situation": "cooling", "scene": "map", "guided": False, "score": None,
                                               "action": {"kind": "stop", "because": "still cooling"}, "expect": "",
                                               "memory": {}}))
        return await super().complete(request)


async def test_a_wait_after_which_the_game_was_not_there_yet_is_followed_by_a_longer_one(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "WAIT_OVER_S", 0.1)
    fake = Town()
    model = StillCooling(fake, 1.2)
    began = time.monotonic()
    played, made = await play(fake, model, tmp_path, wait=True)
    assert ending(played) == ("goal_reached", "task_done"), (played.record, made)
    # the second wait is the code's own: twice the first, the model having read no time
    assert len(made["waited_s"]) == 2 and time.monotonic() - began >= 1.2 + 2 * 1.3
    assert Store(tmp_path).world(SITE)["tasks"]["daily_reward"]["early"] == 1
