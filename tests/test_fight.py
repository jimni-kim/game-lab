"""A turn of a card fight read once by a model and searched by code (PLAN §27 M10, §36 E7). The game here is a toy
fight: cards dragged from a hand onto an enemy (or up, played on no one), the hand closing up as cards leave it, an
end-turn button after which the enemies hit for what they showed and a new hand is dealt. The cards are pictures
with no names (§36 E7b): a model may call one otherwise at every reading, and may know no rule for any."""
from io import BytesIO
import json
import time

import pytest
from PIL import Image, ImageDraw

from gamelab import direct, fight, recovery
from gamelab.adaptation import Store
from gamelab.agent import play
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult, Usage

SIZE = (600, 800)
END = [520, 760]
CARDS = {"Strike": {"damage": 6, "target": "enemy", "shade": 40},
         "Defend": {"block": 5, "target": "none", "shade": 200},
         "Cleave": {"damage": 4, "target": "all", "shade": 120},
         # 4 for each card played this turn, itself too
         "Knight": {"damage_each": 4, "target": "enemy", "shade": 80}}
CARDS_RIGHT = {name: {"cost": 1, **{k: v for k, v in card.items() if k not in ("shade", "damage_each")}}
               for name, card in CARDS.items() if name != "Knight"}
DEAL = ["Defend", "Strike", "Cleave", "Strike"]


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(direct, "SETTLE_S", 0.0)
    monkeypatch.setattr(direct, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(direct, "CHANGE_LOOK_S", 0.02)
    monkeypatch.setattr(direct, "REST_S", 0.1)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)


def slot(index):
    return [20 + index * 140, 560, 120, 160]


class Duel:
    name = "fake"
    reads_dom = False
    kinds = frozenset({"tap", "drag"})

    def __init__(self, enemies, deal=DEAL):
        self.enemies = [dict(enemy) for enemy in enemies]
        self.deal, self.turn_played = list(deal), 0
        self.hp, self.block, self.energy, self.hand = 50, 0, 3, list(deal)
        self.played: list[str] = []

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "https://cards.example/"

    def enemy_at(self, x, y):
        for enemy in self.enemies:
            if enemy["hp"] > 0 and enemy["x"] <= x <= enemy["x"] + 150 and 100 <= y <= 300:
                return enemy
        return None

    async def capture(self, region=None):
        image = Image.new("RGB", SIZE, (90, 160, 90))
        draw = ImageDraw.Draw(image)
        for enemy in self.enemies:
            if enemy["hp"] > 0:
                draw.rectangle((enemy["x"], 100, enemy["x"] + 150, 300), fill=(150, 60, 60))
                draw.rectangle((enemy["x"], 310, enemy["x"] + enemy["hp"] * 6, 320), fill=(250, 0, 0))
        draw.rectangle((20, 400, 20 + self.hp * 4, 410), fill=(0, 0, 250))
        draw.rectangle((20, 420, 20 + self.block * 10, 430), fill=(200, 200, 200))
        draw.rectangle((20, 440, 20 + self.energy * 30, 450), fill=(250, 250, 0))
        for index, name in enumerate(self.hand):
            left, top, width, height = slot(index)
            shade = CARDS[name]["shade"]
            draw.rectangle((left, top, left + width, top + height), fill=(shade, shade, shade))
            draw.rectangle((left + 30, top + 40, left + 90, top + 60 + index % 1), fill=(255 - shade, 30, 200))
        draw.rectangle((END[0] - 40, END[1] - 20, END[0] + 40, END[1] + 20), fill=(30, 30, 30))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    async def stroke(self, path, ms, hold_ms):
        (x, y), (to_x, to_y) = path[0], path[-1]
        index = next((i for i in range(len(self.hand)) if slot(i)[0] <= x <= slot(i)[0] + 120 and 560 <= y <= 720), None)
        if index is None or self.energy < 1:
            return
        card = CARDS[self.hand[index]]
        target = self.enemy_at(to_x, to_y)
        if card["target"] == "enemy" and target is None or card["target"] != "enemy" and to_y > 500:
            return
        self.energy -= 1
        self.turn_played += 1
        self.played.append(self.hand.pop(index))
        for enemy in [target] if card["target"] == "enemy" else [e for e in self.enemies if e["hp"] > 0] \
                if card["target"] == "all" else []:
            enemy["hp"] = max(0, enemy["hp"] - card.get("damage", 0) - card.get("damage_each", 0) * self.turn_played)
        self.block += card.get("block", 0)

    async def tap(self, x, y, duration_ms):
        if abs(x - END[0]) <= 40 and abs(y - END[1]) <= 20 and any(e["hp"] > 0 for e in self.enemies):
            incoming = sum(e["intent"] for e in self.enemies if e["hp"] > 0)
            self.hp -= max(0, incoming - self.block)
            self.block, self.energy, self.hand, self.turn_played = 0, 3, list(self.deal), 0

    async def release(self):
        pass


class Model:
    """Reads the toy fight off the game itself; writes `rules` for the cards (the right ones unless told), and
    plays as `direct` says when it is asked to."""

    def __init__(self, game, rules=None, direct_answers=None, names=None, says_fight=True):
        self.game, self.rules, self.says_fight = game, CARDS_RIGHT if rules is None else rules, says_fight
        # what a card is called at a reading (the number of the reading given): its own name unless told
        self.names = names or (lambda name, reading: name)
        self.direct = list(direct_answers or [])
        self.asked: list[str] = []
        self.reads: list[dict] = []

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        self.asked.append(request.name)
        if "families" in context:
            return TurnResult(text=json.dumps({"screen": "game", "families": [], "situation": "a card fight"}))
        if request.name == "read_turn":
            self.reads.append(context)
            return TurnResult(text=json.dumps(self.read(context)), usage=Usage(input_tokens=1000))
        if not context.get("play"):
            return TurnResult(text=json.dumps({"screen": "other", "fits": False}))
        game = self.game
        if not any(e["hp"] > 0 for e in game.enemies):
            return TurnResult(text=json.dumps({"situation": "won", "scene": "result", "action": {"kind": "done", "end": "won"}}))
        answer = self.direct.pop(0) if self.direct else {"action": {"kind": "wait", "ms": 100}}
        return TurnResult(text=json.dumps({"situation": "a fight", "scene": "board", "fight_turn": self.says_fight,
                                           "expect": "a card is played", **answer}), usage=Usage(input_tokens=5000))

    def read(self, context):
        game = self.game
        alive = [e for e in game.enemies if e["hp"] > 0]
        if not alive:
            return {"fight_turn": False}
        return {"fight_turn": True, "situation": "my turn", "energy": game.energy,
                "me": {"hp": game.hp, "hp_most": 50, "block": game.block},
                "enemies": [{"name": e["name"], "hp": e["hp"], "block": 0, "at": [e["x"] + 75, 200],
                             "intent": {"damage": e["intent"], "hits": 1}} for e in alive],
                "hand": [{"name": self.names(name, len(self.reads)), "at": [slot(i)[0] + 60, 640], "box": slot(i)}
                         for i, name in enumerate(game.hand)],
                "end_turn": END, "play": "drag", "drop": [300, 420],
                "rules": {self.names(name, len(self.reads)): self.rules.get(name) for name in set(game.hand)
                          if self.names(name, len(self.reads)) not in context["cards_known"]}}


def test_the_search_kills_what_it_can_and_blocks_what_it_cannot():
    read = {"energy": 2, "hp": 30, "block": 0, "hand": [{"name": "Strike"}, {"name": "Defend"}, {"name": "Strike"}],
            "enemies": [{"name": "rat", "hp": 6, "block": 0, "damage": 8, "hits": 1, "at": [0, 0]},
                        {"name": "rat", "hp": 20, "block": 0, "damage": 4, "hits": 1, "at": [100, 0]}]}
    rules = {"Strike": fight.rule({"cost": 1, "target": "enemy", "damage": 6}),
             "Defend": fight.rule({"cost": 1, "target": "none", "block": 5})}
    found = fight.search(read, rules)
    # the weak rat that hits hardest dies first, the other's blow is blocked
    assert sorted(found["steps"], key=str) == sorted([[0, 0], [1, None]], key=str) or \
        sorted(found["steps"], key=str) == sorted([[2, 0], [1, None]], key=str)
    assert found["predicted"]["lost"] == 0 and found["predicted"]["hp"] == 30
    # what grows with the cards played before it is played last
    rules["Knight"] = fight.rule({"cost": 1, "target": "enemy", "damage_per_card": 5})
    read = {**read, "energy": 3, "hand": [{"name": "Knight"}, {"name": "Strike"}, {"name": "Strike"}],
            "enemies": [{"name": "ogre", "hp": 40, "block": 0, "damage": 0, "hits": 1, "at": [0, 0]}]}
    assert fight.search(read, rules)["steps"][-1][0] == 0
    # a rule needs a cost; unknown keys are dropped
    assert fight.rule({"damage": 3}) is None and "foo" not in fight.rule({"cost": 0, "foo": 1})


async def test_a_turn_is_read_once_and_its_cards_are_found_where_they_moved(tmp_path):
    game = Duel([{"name": "rat", "hp": 6, "intent": 8, "x": 40}, {"name": "ogre", "hp": 30, "intent": 3, "x": 380}])
    model = Model(game)
    played, made = await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(60, 1, 60),
                              store=Store(tmp_path))
    assert played.record["outcome"] == "goal_reached"
    # one question of the model's own to see the fight, then one reading a turn
    fought = made["direct"]["fight"]
    assert fought["turns"] >= 2 and fought["ended"] >= 2 and not fought["dropped"]
    reads = model.asked.count("read_turn")
    assert model.asked.count("play_direct") <= 2 and reads <= fought["turns"] + 1
    # the rat that hit for 8 died in the first turn: the player only lost what the ogre hit for
    assert game.hp >= 50 - 3 * fought["turns"]
    # the rules were written once and kept for the game
    assert fought["written"] == 3 and sum(bool(read.get("cards_known")) for read in model.reads) == len(model.reads) - 1
    kept = Store(tmp_path).memory("https://cards.example/")
    assert set(kept["card_rules"]) == {"Strike", "Defend", "Cleave"}


async def test_rules_the_screen_does_not_bear_out_are_written_again_from_what_is_seen():
    game = Duel([{"name": "ogre", "hp": 60, "intent": 2, "x": 200}])
    wrong = {**CARDS_RIGHT, "Strike": {"cost": 1, "target": "enemy", "damage": 9}}
    model = Model(game, rules=wrong)
    played, made = await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(80, 1, 60))
    fought = made["direct"]["fight"]
    # the first turn foresaw the ogre at less health than it had, and one card alone could account for it: its
    # rule was written from what the screen showed. No rule was dropped and no turn was the model's
    assert not fought["dropped"]
    assert not fought["fell_back"].get("rule_off") and not fought["fell_back"].get("no_rule")
    assert [one["is"] for one in fought["learned"] if one["card"] == "Strike"] == [{"damage": 6, "damage_per_card": 0}]
    assert played.record["outcome"] == "goal_reached" and model.asked.count("play_direct") <= 2
    # one dropped twice gets no rule again: it is played with what energy is left, after the cards that have rules
    again = fight.Fight({"Defend": CARDS_RIGHT["Defend"]})
    again.off["Strike"] = ["x", "y"]
    read = {"energy": 3, "hp": 9, "block": 0, "hand": [{"name": "Strike"}, {"name": "Defend"}],
            "enemies": [{"name": "o", "hp": 9, "block": 0, "damage": 3, "hits": 1, "at": [0, 0]}]}
    assert again.plan(read)["steps"] == [[1, None], [0, 0]]


async def test_cards_are_told_by_their_pictures_whatever_they_are_called(tmp_path):
    game = Duel([{"name": "rat", "hp": 30, "intent": 4, "x": 40}, {"name": "ogre", "hp": 40, "intent": 3, "x": 380}])
    # every reading calls every card something else, and the model knows no rule for any
    model = Model(game, rules={}, names=lambda name, reading: f"{name[0]} picture {reading}")
    played, made = await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(120, 1, 60),
                              store=Store(tmp_path))
    fought = made["direct"]["fight"]
    assert played.record["outcome"] == "goal_reached"
    # three pictures, three cards, under the names they were first given; later names were only kept beside them
    assert fought["told_apart"] == 3 and fought["looks"] == 3 and fought["renamed"] > 3
    kept = Store(tmp_path).memory("https://cards.example/")
    assert set(kept["card_looks"]) == set(kept["card_rules"]) == {"S picture 1", "D picture 1", "C picture 1"}
    assert len(kept["card_looks"]["S picture 1"]["said"]) > 1
    # each was played once with no rule and got its rule from what the next reading showed
    rules = kept["card_rules"]
    assert rules["S picture 1"]["damage"] == 6 and rules["C picture 1"]["damage"] == 4 and rules["D picture 1"]["block"] == 5
    assert not fought["fell_back"].get("no_rule") and model.asked.count("play_direct") <= 2
    # the next work on the game knows the cards from its first reading
    again = Duel([{"name": "rat", "hp": 12, "intent": 4, "x": 40}])
    model = Model(again, rules={}, names=lambda name, reading: f"card {reading}")
    played, made = await play(again, model, None, goal=GoalSpec("play_until_end"), budget=Budget(40, 1, 60),
                              store=Store(tmp_path))
    assert made["direct"]["fight"]["told_apart"] == 0 and made["direct"]["fight"]["reads"] <= 2
    assert played.record["outcome"] == "goal_reached"


async def test_what_grows_with_each_card_is_counted_as_the_screen_shows():
    # the model's rule counts the cards before the Knight; the game counts the Knight too
    game = Duel([{"name": "ogre", "hp": 90, "intent": 0, "x": 200}], deal=["Strike", "Knight", "Strike", "Defend"])
    said = {**CARDS_RIGHT, "Knight": {"cost": 1, "target": "enemy", "damage_per_card": 4}}
    model = Model(game, rules=said)
    played, made = await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(80, 1, 60))
    fought = made["direct"]["fight"]
    # the Knight and the Strikes were in doubt together when the ogre lost more than foreseen: played apart in
    # the next turn (read again in its middle), the Knight alone was off
    assert played.record["outcome"] == "goal_reached" and not fought["fell_back"]
    assert fought["apart"][0]["cards"] == ["Knight", "Strike"] and fought["mid_reads"] >= 1
    learned = [one for one in fought["learned"] if one["card"] == "Knight"]
    assert learned and learned[-1]["is"] == {"damage": 4, "damage_per_card": 4}
    # seen after one number of cards, the share for each card stays as said; after two, the line through them
    assert fight.fit([[2, 12]], 4) == (4, 4) and fight.fit([[2, 12]]) == (12, 0)
    assert fight.fit([[2, 12], [0, 4]]) == (4, 4) and fight.fit([[1, 6], [2, 6]]) == (6, 0)


def test_a_card_is_found_again_where_it_is_drawn():
    game = Duel([{"name": "ogre", "hp": 30, "intent": 0, "x": 200}])
    import asyncio
    first = asyncio.run(game.capture())
    game.hand.pop(0)
    now = asyncio.run(game.capture())
    found = fight.locate(first, slot(2), now)          # Cleave moved one slot left
    assert found is not None and abs(found[0] - (slot(1)[0] + 60)) <= 8
    game.hand = []
    assert fight.locate(first, slot(2), asyncio.run(game.capture())) is None


def test_a_card_with_no_rule_is_played_where_what_it_does_will_show():
    read = {"energy": 3, "hp": 30, "block": 0, "hand": [{"name": "Strike"}, {"name": "Mystery"}, {"name": "Strike"}],
            "enemies": [{"name": "ogre", "hp": 40, "block": 0, "damage": 3, "hits": 1, "at": [0, 0]}]}
    after = {**read, "energy": 0, "hand": [], "enemies": [{**read["enemies"][0], "hp": 23}]}
    fighting = fight.Fight({"Strike": {"cost": 1, "target": "enemy", "damage": 6}})
    # the Strikes are in doubt themselves: the card with no rule waits, and the turn is not ended but read again
    found = fighting.plan(read)
    assert sorted(index for index, _ in found["steps"]) == [0, 2] and found["more"] is True and found["filled"] == 0
    # once a turn bore the Strikes out, it goes on the ogre after them, and the turn is ended
    fighting.borne["Strike"] = 1
    found = fighting.plan(read)
    assert found["steps"][-1] == [1, 0] and found["more"] is True and found["filled"] == 1
    # the ogre lost 5 more than the Strikes take: that is the card's rule from now on
    fighting.predicted = found["predicted"]
    found = fighting.plan(after)
    assert fighting.rules["Mystery"] == {"cost": 1, "target": "enemy", "damage": 5, "hits": 1}
    assert found["steps"] == [] and found["more"] is False
    # with no enemy that tells them apart, two cards with no rule are played one reading apart
    fighting = fight.Fight()
    found = fighting.plan({**read, "hand": [{"name": "Mystery"}, {"name": "Riddle"}]})
    assert found["steps"] == [[0, 0]] and found["more"] is True
    # in the middle of a turn the block read is what the card gave
    fighting.predicted = found["predicted"]
    fighting.plan({**read, "block": 7, "energy": 2, "hand": [{"name": "Riddle"}]})
    assert fighting.rules["Mystery"] == {"cost": 1, "target": "enemy", "block": 7, "hits": 1}


async def test_a_reading_that_finds_no_turn_once_does_not_end_the_fight():
    class Says:
        def __init__(self):
            self.times = 0

        async def complete(self, request):
            self.times += 1
            return TurnResult(text=json.dumps({"fight_turn": False}), usage=Usage(input_tokens=10))

    game = Duel([{"name": "ogre", "hp": 30, "intent": 0, "x": 200}])
    fighting, model = fight.Fight(), Says()
    fighting.on = True
    png = await game.capture()
    assert await fighting.read(model, png) is None and fighting.on is True
    assert await fighting.read(model, png) is None and fighting.on is False and fighting.misses == 0


async def test_a_fight_is_seen_by_the_cards_known_in_the_hands_places(tmp_path):
    game = Duel([{"name": "rat", "hp": 12, "intent": 4, "x": 40}])
    played, made = await play(game, Model(game), None, goal=GoalSpec("play_until_end"), budget=Budget(60, 1, 60),
                              store=Store(tmp_path))
    kept = Store(tmp_path).memory("https://cards.example/")
    assert played.record["outcome"] == "goal_reached" and len(kept["card_places"]["boxes"]) >= 3
    assert made["direct"]["fight"]["sighted"] == 0
    # the next work's model never says a fight stands there: the code sees the known cards and reads the turn
    again = Duel([{"name": "rat", "hp": 12, "intent": 4, "x": 40}, {"name": "ogre", "hp": 20, "intent": 3, "x": 380}])
    model = Model(again, says_fight=False)
    played, made = await play(again, model, None, goal=GoalSpec("play_until_end"), budget=Budget(60, 1, 60),
                              store=Store(tmp_path))
    fought = made["direct"]["fight"]
    assert played.record["outcome"] == "goal_reached" and fought["sighted"] == 1 and fought["sighted_no_turn"] == 0
    # the model's own questions: only the one that saw the fight won
    assert model.asked.count("play_direct") == 1 and fought["turns"] >= 2


async def test_cards_that_show_where_no_turn_stands_cost_one_reading():
    class Says:
        def __init__(self):
            self.times = 0

        async def complete(self, request):
            self.times += 1
            return TurnResult(text=json.dumps({"fight_turn": False}), usage=Usage(input_tokens=10))

    game = Duel([{"name": "ogre", "hp": 30, "intent": 0, "x": 200}])
    png = await game.capture()
    known = fight.Fight()
    known._tell(png, {"hand": [{"name": name, "box": slot(index)} for index, name in enumerate(game.hand)]}, {})
    fighting, model = fight.Fight(None, known.kept_looks(), known.kept_places()), Says()
    assert fighting.sighted(png) and fighting.sight(png) and fighting.on
    # one reading that finds no turn ends it, and the same cards are not tried again while they stay
    assert await fighting.read(model, png) is None and fighting.on is False
    assert fighting.sight(png) is False and fighting.account["sighted_no_turn"] == 1
    # once they are gone and come up again, they are tried again
    game.hand = []
    assert fighting.sight(await game.capture()) is False
    game.hand = list(DEAL)
    assert fighting.sight(await game.capture()) is True
    # a screen of another size, or a hand of cards never seen, is no sighting
    game.hand = ["Knight", "Knight"]
    fighting.on = False
    assert fighting.sighted(await game.capture()) is False


async def test_the_next_hand_is_waited_for_by_looking_not_by_asking(tmp_path, monkeypatch):
    class Slow(Duel):
        """The enemies take a while over their turn: the hand is not drawn again until some time after it ended,
        longer than the screen is waited for to rest."""
        until = 0.0

        async def tap(self, x, y, duration_ms):
            before = self.hp, len(self.hand), self.energy
            await super().tap(x, y, duration_ms)
            if (self.hp, len(self.hand), self.energy) != before:
                self.until = time.monotonic() + 0.5

        async def capture(self, region=None):
            hand = self.hand
            self.hand = [] if time.monotonic() < self.until else hand
            try:
                return await super().capture(region)
            finally:
                self.hand = hand

    monkeypatch.setattr(fight, "HAND_LOOK_S", 0.01)
    monkeypatch.setattr(fight, "MISS_WAIT_MS", 500)
    game = Slow([{"name": "ogre", "hp": 40, "intent": 2, "x": 200}])
    model = Model(game)
    played, made = await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(80, 1, 60))
    fought = made["direct"]["fight"]
    assert played.record["outcome"] == "goal_reached" and fought["turns"] >= 3
    # no reading was spent on a screen without the hand while the fight went on
    assert fought["hand_waits"] >= 2 and fought["fell_back"].get("no_turn", 0) <= 2
