"""A game whose board is no grid, read and played with no profile (PLAN §10, G9): the model reads the piles
and says where each place is, the solver picks the moves, the stock is tapped and cards are dragged between
places, and the code written is adopted only when it reads every recorded screen and plays each recorded move
between the same places. The screen is a fake drawn from the family's own deal; a card shows its rank and
suit as two patches of colour."""
from functools import partial
from io import BytesIO
import json

from PIL import Image, ImageDraw
import pytest

from gamelab import adaptation, agent, catalog, recovery
from gamelab.adaptation import Store, place
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult
from gamelab.rules.klondike import RANKS, SUITS
from gamelab.surface import DRAG, KEYS, TAP

SIZE = (640, 620)
CARD, STEP_UP, STEP_DOWN = (60, 80), 24, 12
STOCK, WASTE, TOP, LEFT, ACROSS = (40, 40), (120, 40), 160, 40, 80
PAGE, FACE, BACK, OUTLINE = (20, 90, 50), (245, 245, 240), (40, 60, 160), (10, 60, 30)
RANK_INKS = {rank: (20 + 18 * index, 200 - 12 * index, 90) for index, rank in enumerate(RANKS)}
SUIT_INKS = {"S": (0, 0, 0), "H": (220, 0, 0), "D": (230, 120, 0), "C": (0, 110, 0)}
# a deal the greedy policy wins, and one it does not
WON_DEAL, LOST_DEAL = 0, 34


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(agent, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    monkeypatch.setattr(adaptation, "GeneratedAdapter", partial(
        adaptation.GeneratedAdapter, settling={"timeout_s": 0.4, "interval_s": 0.05, "stable_s": 0.1}))


def foundation_at(index):
    return 280 + ACROSS * index, 40


class Patience:
    name = "fake"
    reads_dom = False
    kinds = frozenset({KEYS, TAP, DRAG})

    def __init__(self, draw=WON_DEAL):
        self.family = catalog.family("klondike")
        self.state = self.family.initial_state(draw)
        self.moves, self.refused = [], 0

    @property
    def seen(self):
        return self.family.shown(self.state)

    def places(self):
        """The box of every place, as the screen draws it."""
        seen = self.seen
        tableau = []
        for index, pile in enumerate(seen["tableau"]):
            x, y = LEFT + ACROSS * index, TOP + pile["down"] * STEP_DOWN
            boxes = [[x, y + place * STEP_UP, CARD[0], STEP_UP if place < len(pile["up"]) - 1 else CARD[1]]
                     for place in range(len(pile["up"]))]
            tableau.append(boxes or [[x, TOP, *CARD]])
        return {"stock": [*STOCK, *CARD], "waste": [*WASTE, *CARD],
                "foundation": [[*foundation_at(index), *CARD] for index in range(4)], "tableau": tableau}

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "https://patience.example/"

    async def elements(self, queries):
        return None

    def _draw_card(self, draw, x, y, card):
        draw.rectangle((x, y, x + CARD[0] - 1, y + CARD[1] - 1), fill=FACE, outline=OUTLINE)
        draw.rectangle((x + 6, y + 4, x + 26, y + 18), fill=RANK_INKS[card[:-1]])
        draw.rectangle((x + 34, y + 4, x + 54, y + 18), fill=SUIT_INKS[card[-1]])

    async def capture(self, region=None):
        image = Image.new("RGB", SIZE, PAGE)
        draw = ImageDraw.Draw(image)
        seen = self.seen
        for (x, y), card in ((STOCK, None), (WASTE, seen["waste"]), *((foundation_at(i), top) for i, top in enumerate(seen["foundations"]))):
            draw.rectangle((x, y, x + CARD[0] - 1, y + CARD[1] - 1), outline=OUTLINE)
            if card not in (None, "0"):
                self._draw_card(draw, x, y, card)
        if seen["stock"]:
            draw.rectangle((STOCK[0], STOCK[1], STOCK[0] + CARD[0] - 1, STOCK[1] + CARD[1] - 1), fill=BACK, outline=OUTLINE)
        for index, pile in enumerate(seen["tableau"]):
            x, y = LEFT + ACROSS * index, TOP
            draw.rectangle((x, y, x + CARD[0] - 1, y + CARD[1] - 1), outline=OUTLINE)
            for _ in range(pile["down"]):
                draw.rectangle((x, y, x + CARD[0] - 1, y + CARD[1] - 1), fill=BACK, outline=OUTLINE)
                y += STEP_DOWN
            for card in pile["up"]:
                self._draw_card(draw, x, y, card)
                y += STEP_UP
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    def _at(self, x, y, taking):
        """The place a point is on, as a move names it."""
        places = self.places()
        inside = lambda box: box[0] <= x < box[0] + box[2] and box[1] <= y < box[1] + box[3]  # noqa: E731
        if inside(places["waste"]):
            return ["waste"]
        for index, box in enumerate(places["foundation"]):
            if inside(box):
                return ["foundation", index]
        for index, boxes in enumerate(places["tableau"]):
            for position, box in enumerate(boxes):
                if inside(box):
                    return ["tableau", index, len(boxes) - position] if taking else ["tableau", index]
        return None

    def _play(self, action):
        if action not in self.family.model.legal_actions(self.seen, self.family.rules):
            self.refused += 1
            return
        self.moves.append(action)
        self.state = self.family.next_state(self.state, action, 0)

    async def tap(self, x, y, duration_ms):
        box = self.places()["stock"]
        if box[0] <= x < box[0] + box[2] and box[1] <= y < box[1] + box[3]:
            self._play({"kind": "draw"} if self.seen["stock"] else {"kind": "recycle"})
        else:
            self.refused += 1

    async def drag(self, x0, y0, x1, y1, duration_ms):
        origin, to = self._at(x0, y0, True), self._at(x1, y1, False)
        if origin is None or to is None or origin[0] == "foundation":
            self.refused += 1
            return
        self._play({"kind": "move", "from": origin, "to": to})

    async def key(self, key):
        raise AssertionError("a card game is given no key")

    async def release(self):
        pass


class Croupier:
    """The model: it reads the fake's piles and says where each place is, a few pixels off."""

    def __init__(self, fake, modules=()):
        self.fake, self.modules, self.asked = fake, list(modules), []
        self.shy = False

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "examples" in context:
            self.asked.append(("write", context, len(request.messages[0].content) - 1))
            return TurnResult(text=self.modules.pop(0))
        if "tried" in context:
            self.asked.append(("recover", context, 1))
            return TurnResult(text=json.dumps({"situation": "the piles at rest", "action": {"kind": "none"}}))
        self.asked.append(("label", context, len(request.messages[0].content) - 1))

        def off(value):
            return [off(part) for part in value] if isinstance(value[0], list) else [value[0] + 2, value[1] - 1, value[2] - 3, value[3]]
        seen = self.fake.seen
        if self.shy:
            # it cannot read a card another lies on: only the top card of each pile
            seen = {**seen, "tableau": [{"down": pile["down"], "up": [None] * (len(pile["up"]) - 1) + pile["up"][-1:]}
                                        for pile in seen["tableau"]]}
        return TurnResult(text=json.dumps({
            "screen": "board", "board": [20, 20, 600, 580], "rules": {}, "state": {**seen, "scene": "won" if seen["scene"] == "won" else "playing"},
            "places": {name: off(value) for name, value in self.fake.places().items()}}))


ADAPTER = """
from gamelab.sandbox.helpers import distance, nearest

CARD, STEP_UP, STEP_DOWN = {card}, {step_up}, {step_down}
STOCK, WASTE, TOP, LEFT, ACROSS = {stock}, {waste}, {top}, {left}, {across}
FACE, BACK = {face}, {back}
RANKS, SUITS = {ranks}, {suits}

def measure(picture):
    if picture.width < 640 or picture.height < 620:
        return None
    return {{"board": [20, 20, 600, 580]}}

def foundation_at(index):
    return 280 + ACROSS * index, 40

def card_at(picture, x, y):
    rank, far = nearest(picture.pixel(x + 16, y + 11), RANKS)
    suit, other = nearest(picture.pixel(x + 44, y + 11), SUITS)
    return rank + suit if far <= 6 and other <= 6 else None

def piles(picture):
    found = []
    for index in range(7):
        x, y, down, up = LEFT + ACROSS * index, TOP, 0, []
        while distance(picture.pixel(x + 30, y + 6), BACK) <= 6:
            down, y = down + 1, y + STEP_DOWN
        while (card := card_at(picture, x, y)) is not None:
            up.append(card)
            y += STEP_UP
        found.append({{"down": down, "up": up}})
    return found

def read(picture, layout):
    top = lambda at: card_at(picture, *at) or "0"
    return {{"stock": 1 if distance(picture.pixel(STOCK[0] + 30, STOCK[1] + 40), BACK) <= 6 else 0, "waste": top(WASTE),
            "foundations": [top(foundation_at(index)) for index in range(4)], "tableau": piles(picture), "scene": "playing"}}

def spot(state, named, taking):
    if named[0] == "waste":
        return WASTE[0] + 30, WASTE[1] + 12
    if named[0] == "foundation":
        x, y = foundation_at(named[1])
        return x + 30, y + 40
    pile = state["tableau"][named[1]]
    x, y = LEFT + ACROSS * named[1], TOP + pile["down"] * STEP_DOWN
    if not pile["up"]:
        return x + 30, TOP + 40
    position = len(pile["up"]) - named[2] if taking else len(pile["up"]) - 1
    return x + 30, y + position * STEP_UP + TAKEN

def target(action, state, layout):
    if action["kind"] in ("draw", "recycle"):
        return {{"kind": "tap", "x": STOCK[0] + 30, "y": STOCK[1] + 40}}
    (x0, y0), (x1, y1) = spot(state, action["from"], True), spot(state, action["to"], False)
    return {{"kind": "drag", "x0": x0, "y0": y0, "x1": x1, "y1": y1, "ms": 400}}
"""


# the same adapter with nothing that tells one card from another: it says where each card's corner is and
# leaves what it shows to the host
POINTING = """
def card_at(picture, x, y):
    if distance(picture.pixel(x + 30, y + 2), FACE) > 6 or distance(picture.pixel(x + 30, y + 20), FACE) > 6:
        return None
    if distance(picture.pixel(x + 16, y + 11), FACE) <= 6:
        return None
    return {"look": [x + 2, y + 2, 56, 18]}
"""


def pointing():
    text = source()
    start, end = text.index("def card_at"), text.index("def piles")
    return text[:start] + POINTING.strip() + "\n\n" + text[end:]


def source(taken=12):
    return ADAPTER.format(card=CARD, step_up=STEP_UP, step_down=STEP_DOWN, stock=STOCK, waste=WASTE, top=TOP, left=LEFT,
                          across=ACROSS, face=FACE, back=BACK, ranks=RANK_INKS, suits=SUIT_INKS).replace("TAKEN", str(taken))


def test_a_place_among_piles_is_the_deepest_card_taken_or_the_top_card_to_put_on():
    places = {"stock": [1, 1, 5, 5], "foundation": [[10, 1, 5, 5], [20, 1, 5, 5]], "tableau": [[[1, 10, 5, 2], [1, 12, 5, 2], [1, 14, 5, 8]], [[9, 10, 5, 8]]]}
    assert place(places, ["stock"], taking=False) == [1, 1, 5, 5] and place(places, ["foundation", 1], taking=False) == [20, 1, 5, 5]
    assert place(places, ["tableau", 0, 2], taking=True) == [1, 12, 5, 2] and place(places, ["tableau", 0], taking=False) == [1, 14, 5, 8]
    assert place(places, ["tableau", 1, 1], taking=True) == [9, 10, 5, 8]


async def test_a_card_game_is_explored_adapted_and_played_among_its_places_to_the_win(tmp_path):
    fake = Patience()
    croupier = Croupier(fake, [source()])
    played, made = await agent.play(fake, croupier, "klondike", goal=GoalSpec("play_until_end"), budget=Budget(400, 200, 300),
                                    store=Store(tmp_path))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "play_until_end"), (played.record, made)
    assert fake.seen["foundations"] == ["KS", "KH", "KD", "KC"] or sorted(fake.seen["foundations"]) == ["KC", "KD", "KH", "KS"]
    assert not fake.refused and [move["action"] for move in played.moves] == fake.moves
    assert played.record["levels"]["L2"] == adaptation.EXPLORE_MOVES and played.record["levels"]["L3"] > 50
    assert {move["effect"] for move in played.moves} == {"confirmed"}
    # the model was told the places to give boxes for, saw no grid drawn over a board, and the writer got no cells
    assert set(croupier.asked[0][1]["places"]) == {"stock", "waste", "foundation", "tableau"}
    assert all(images == 1 for kind, _, images in croupier.asked if kind == "label")
    write = next(context for kind, context, _ in croupier.asked if kind == "write")
    assert "probes" not in write and all("cells" not in example and "places" in example for example in write["examples"])


async def test_an_adapter_that_takes_the_wrong_card_of_a_pile_is_not_adopted():
    fake = Patience()
    played, made = await agent.play(fake, Croupier(fake, [source(taken=-30)] * adaptation.MAX_GENERATIONS), "klondike",
                                    goal=GoalSpec("actions", 9), budget=Budget(60, 200, 120))
    assert played.record["outcome"] == "goal_reached" and played.record["levels"]["L3"] == 0, (played.record, made)
    assert "target" in made["generation_failures"][0]["detail"]


async def test_a_deal_going_round_its_stock_with_nothing_to_play_ends_as_the_game_over():
    """The solver has no move that gains and turns the stock; the game offers that move for ever. A state come
    back to for the third time by the game's own moves ends it."""
    fake = Patience(draw=LOST_DEAL)
    played, made = await agent.play(fake, Croupier(fake, [source()]), "klondike", goal=GoalSpec("play_until_end"),
                                    budget=Budget(400, 200, 300))
    assert (played.record["outcome"], played.record["reason"]) == ("game_over", "no_progress"), (played.record, made)
    assert not fake.refused and fake.seen["scene"] == "playing" and len(fake.moves) < 200


async def test_code_that_only_says_where_the_cards_are_plays_by_samples_and_learns_new_cards_without_being_rewritten(tmp_path):
    """The adapter tells no card from another: it gives the box of each card's corner, and the host names it
    from the samples of the screens the model read. A card not seen before is a value nobody read: the model
    reads that screen, its samples are kept, and the same code plays on. The whole deck is learned so, with
    the code written once."""
    fake = Patience()
    croupier = Croupier(fake, [pointing()])
    played, made = await agent.play(fake, croupier, "klondike", goal=GoalSpec("play_until_end"), budget=Budget(400, 200, 300),
                                    store=Store(tmp_path))
    assert (played.record["outcome"], played.record["reason"]) in (("goal_reached", "play_until_end"), ("goal_reached", "won")), (played.record, made)
    assert not fake.refused and [move["action"] for move in played.moves] == fake.moves
    writes = [context for kind, context, _ in croupier.asked if kind == "write"]
    assert len(writes) == 1 and made["generations"] == 1 and 10 <= made["relearned"] <= 52
    # more moves are read by the code than by the model, which reads only the screens that show a card for the first time
    assert played.record["levels"]["L3"] > played.record["levels"]["L2"]
    assert {move["effect"] for move in played.moves} <= {"confirmed", "unknown"}
    # the next deal on the site starts from what was learned: far fewer screens go to the model
    again = Patience(draw=1)
    second = Croupier(again, [])
    played, made = await agent.play(again, second, "klondike", goal=GoalSpec("play_until_end"), budget=Budget(400, 200, 300),
                                    store=Store(tmp_path))
    assert played.record["outcome"] == "goal_reached" and made["reused"] and made["generations"] == 1, (played.record, made)
    assert sum(kind == "label" for kind, _, _ in second.asked) <= 6


def test_a_cut_is_named_by_its_nearest_sample_and_one_like_none_is_not_named():
    from gamelab import samples
    from PIL import Image

    def flat(colour, spot=None, size=(40, 12)):
        image = Image.new("RGB", (60, 30), colour)
        if spot is not None:             # a small mark in one corner: what tells two things apart
            for y in range(3, 7):
                for x in range(3, 7):
                    image.putpixel((x, y), spot)
        return samples.cut(image, [2, 2, *size])
    bank = samples.Bank()
    red, blue = flat((200, 10, 10)), flat((10, 10, 200))
    bank.add("R", red)
    bank.add("B", blue)
    assert bank.name(red) == (True, "R") and bank.name(flat((198, 12, 10))) == (True, "R")
    assert bank.name(flat((120, 120, 120))) == (False, None)
    # a small difference in one place is a difference: the same red with a mark in its corner is not the red kept
    assert bank.name(flat((200, 10, 10), spot=(0, 0, 0))) == (False, None)
    # a thing seen to vary is given the room it was seen to need
    assert bank.name(flat((10, 10, 180))) == (False, None)
    bank.add("B", flat((10, 10, 190)))
    assert bank.name(flat((10, 10, 180))) == (True, "B")
    # the same pixels read as two values tell neither
    bank.add("X", red)
    assert bank.name(red) == (False, None)
    # a box of another size is another thing, and one off the picture gives no sample
    assert samples.distance(red, flat((200, 10, 10), size=(41, 12))) == 255.0
    assert samples.cut(Image.new("RGB", (60, 30)), [50, 2, 40, 12]) is None


async def test_a_card_the_model_can_no_longer_read_under_another_is_the_one_the_rules_say_lies_there():
    """A card another was laid on shows only its edge, and the model leaves it unread. The rules know which card
    it is: the one that lay there before the move. Regression: the reading was refused for ever and the game
    given up at its second move."""
    fake = Patience()
    croupier = Croupier(fake, ["def measure(picture):\n    return None\n"] * adaptation.MAX_GENERATIONS)
    croupier.shy = True
    played, made = await agent.play(fake, croupier, "klondike", goal=GoalSpec("actions", 25), budget=Budget(60, 200, 120))
    assert (played.record["outcome"], played.record["reason"]) == ("goal_reached", "actions"), (played.record, made)
    assert not fake.refused and {move["effect"] for move in played.moves} == {"confirmed"}
