"""A turn of a card fight read once by a model and searched by code (PLAN §27 M10, §36 E7). The game here is a toy
fight: cards dragged from a hand onto an enemy (or up, played on no one), the hand closing up as cards leave it, an
end-turn button after which the enemies hit for what they showed and a new hand is dealt."""
from io import BytesIO
import json

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
         "Cleave": {"damage": 4, "target": "all", "shade": 120}}
CARDS_RIGHT = {name: {"cost": 1, **{k: v for k, v in card.items() if k != "shade"}} for name, card in CARDS.items()}
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

    def __init__(self, enemies):
        self.enemies = [dict(enemy) for enemy in enemies]
        self.hp, self.block, self.energy, self.hand = 50, 0, 3, list(DEAL)
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
        self.played.append(self.hand.pop(index))
        for enemy in [target] if card["target"] == "enemy" else [e for e in self.enemies if e["hp"] > 0] \
                if card["target"] == "all" else []:
            enemy["hp"] = max(0, enemy["hp"] - card["damage"])
        self.block += card.get("block", 0)

    async def tap(self, x, y, duration_ms):
        if abs(x - END[0]) <= 40 and abs(y - END[1]) <= 20 and any(e["hp"] > 0 for e in self.enemies):
            incoming = sum(e["intent"] for e in self.enemies if e["hp"] > 0)
            self.hp -= max(0, incoming - self.block)
            self.block, self.energy, self.hand = 0, 3, list(DEAL)

    async def release(self):
        pass


class Model:
    """Reads the toy fight off the game itself; writes `rules` for the cards (the right ones unless told), and
    plays as `direct` says when it is asked to."""

    def __init__(self, game, rules=None, direct_answers=None):
        self.game, self.rules = game, rules or {name: {"cost": 1, **{k: v for k, v in card.items() if k != "shade"}}
                                                for name, card in CARDS.items()}
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
        return TurnResult(text=json.dumps({"situation": "a fight", "scene": "board", "fight_turn": True,
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
                "hand": [{"name": name, "at": [slot(i)[0] + 60, 640], "box": slot(i)} for i, name in enumerate(game.hand)],
                "end_turn": END, "play": "drag", "drop": [300, 420],
                "rules": {name: CARDS_RIGHT[name] if name in context.get("rules_off", {}) else self.rules[name]
                          for name in set(game.hand) if name not in context["cards_known"]}}


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


async def test_a_rule_the_screen_does_not_bear_out_is_dropped_and_the_turn_is_the_models():
    game = Duel([{"name": "ogre", "hp": 60, "intent": 2, "x": 200}])
    wrong = {name: {"cost": 1, **{k: v for k, v in card.items() if k != "shade"}} for name, card in CARDS.items()}
    wrong["Strike"] = {"cost": 1, "target": "enemy", "damage": 9}
    end = {"action": {"kind": "tap", "at": END, "then_wait_ms": 1000}}
    model = Model(game, rules=wrong, direct_answers=[{"action": {"kind": "wait", "ms": 100}}, end, end, end, end, end])
    played, made = await play(game, model, None, goal=GoalSpec("play_until_end"), budget=Budget(40, 1, 60))
    fought = made["direct"]["fight"]
    # the first turn foresaw the ogre at less health than it had: Strike lost its rule, and the turn was the model's
    assert fought["dropped"] and fought["dropped"][0]["cards"] == ["Strike"]
    assert fought["fell_back"].get("rule_off") == 1
    # the next reading was told what was off, and the rule written then is right: the fight goes on by code
    told = [read for read in model.reads if "rules_off" in read]
    assert told and "Strike" in told[0]["rules_off"] and fought["turns"] >= 2
    # one dropped as often as that is played by the search no more, and nor asked about
    again = fight.Fight({"Defend": CARDS_RIGHT["Defend"]})
    again.off["Strike"] = ["x", "y"]
    read = {"energy": 3, "hp": 9, "block": 0, "hand": [{"name": "Strike"}, {"name": "Defend"}],
            "enemies": [{"name": "o", "hp": 9, "block": 0, "damage": 3, "hits": 1, "at": [0, 0]}]}
    assert again.plan(read)["steps"] == [[1, None]]


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


def test_a_card_with_no_rule_is_left_out_and_the_rest_of_the_turn_is_the_models():
    rules = {"Strike": fight.rule({"cost": 1, "target": "enemy", "damage": 6})}
    read = {"energy": 3, "hp": 30, "block": 0, "hand": [{"name": "Strike"}, {"name": "Mystery"}, {"name": "Strike"}],
            "enemies": [{"name": "ogre", "hp": 40, "block": 0, "damage": 3, "hits": 1, "at": [0, 0]}]}
    fighting = fight.Fight({"Strike": {"cost": 1, "target": "enemy", "damage": 6}})
    found = fighting.plan(read)
    assert sorted(index for index, _ in found["steps"]) == [0, 2] and found["leave"] is True
    # when the known cards use up the energy, the turn is the program's to end
    found = fighting.plan({**read, "energy": 2})
    assert found["leave"] is False and len(found["steps"]) == 2
    # with no card it can play, the turn is the model's from the start
    assert fighting.plan({**read, "hand": [{"name": "Mystery"}]}) is None
    assert rules["Strike"]["damage"] == 6


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
