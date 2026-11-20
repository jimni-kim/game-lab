"""Klondike: the registered policies over the klondike rule model (PLAN §10, G9; PLAN §33 P3).

Pure like the rule model and ported with it. A policy sees one state, so it never undoes what it did: it only
plays a move that turns a card up, frees a pile for a king that waits, builds a foundation, or brings the
waste's card into play; else it turns the stock.

What a screen does not show but a player remembers is kept by `recall`, as JSON the caller carries from one
screen of a game to the next: the cards the stock has shown, in their order, and how long the stock has been
turned with nothing else played. With it a policy stops once it has gone round the whole stock for nothing
(without it, it turns the stock for ever), and `hindsight` knows which card each turn brings.

hindsight (hindsight optimisation, Bjarnason, Fern and Tadepalli 2009): the cards nobody has seen are dealt
at random several times, each of the moves worth weighing is followed by the greedy policy to the end of each
such deal with every card in sight, and the move that does best on average is played. The deals come from a
fixed generator seeded by the state, so the same screen and memory always give the same move.
"""
from __future__ import annotations

from ..rules.klondike import EMPTY, PILES, RANKS, RED, SUITS, _card, legal_actions

POLICIES = ("first", "greedy", "hindsight")
KING = len(RANKS) - 1
DECK = len(RANKS) * len(SUITS)
# hindsight: the deals a move is tried on at most, and the moves one line of play may take
MAX_DEALS = 64
ROLLOUT_MOVES = 400
# a line of play's worth: a game won, over every card brought to a foundation or turned up
WIN_WORTH = 1000
# the deals' stream of draws: a fixed linear congruential generator (Knuth's MMIX constants), as the rule
# model's own shuffle
LCG_MULTIPLIER, LCG_INCREMENT, LCG_MASK = 6364136223846793005, 1442695040888963407, (1 << 64) - 1


def _safe(state: dict, card: str) -> bool:
    """Whether a card may go to its foundation with nothing lost: no card of the other colour still needs it
    to lie on (both of them, two ranks lower at most, are already up)."""
    rank, suit = _card(card)
    if rank <= 1:
        return True
    others = [(_card(top)[0] if top != EMPTY else -1) for top in state["foundations"]
              if top == EMPTY or (_card(top)[1] in RED) != (suit in RED)]
    tops = sorted(others)[:2] if len(others) >= 2 else [-1, -1]
    # an empty foundation may be either colour's: counted as not yet begun
    return min(tops) >= rank - 2


def _worth(state: dict, action: dict) -> tuple | None:
    """How good a move is, larger first; None for a move that gains nothing (a run shifted from one pile to
    another with no card turned up and no pile freed for a king, a part of a run moved, a king moved from one
    empty pile to another)."""
    if action["kind"] != "move":
        return (0, 0)
    origin, to = action["from"], action["to"]
    tableau = state["tableau"]
    kings_wait = state["waste"] != EMPTY and _card(state["waste"])[0] == KING or any(
        pile["down"] and _card(pile["up"][0])[0] == KING for pile in tableau if pile["up"])
    if origin[0] == "waste":
        card = state["waste"]
        if to[0] == "foundation":
            return (5 if _safe(state, card) else 2, 0)
        return (3, _card(card)[0])
    pile = tableau[origin[1]]
    whole = origin[2] == len(pile["up"])
    if to[0] == "foundation":
        turns = pile["down"] > 0 and len(pile["up"]) == 1
        return (6 if turns else (5 if _safe(state, pile["up"][-1]) else 1), pile["down"])
    if not whole:
        return None
    if pile["down"] == 0:
        # nothing is turned up: worth it only to free the pile for a king that waits (and not for itself)
        return (4, 0) if kings_wait and _card(pile["up"][0])[0] != KING else None
    return (6, pile["down"])


# ── what a player remembers of the stock ──────────────────────────────

def _talon(state: dict) -> int:
    """How many cards the stock and the waste hold together: the deck less every card in sight or face down
    in a pile."""
    built = sum(_card(top)[0] + 1 for top in state["foundations"] if top != EMPTY)
    return DECK - built - sum(pile["down"] + len(pile["up"]) for pile in state["tableau"])


def _board(state: dict) -> dict:
    return {name: state[name] for name in ("stock", "waste", "foundations", "tableau")}


def recall(memory: dict | None, state: dict, rules: dict) -> dict | None:
    """What is known of the stock once `state` is seen too: {"stock": the cards face down, the next one to
    turn last; "waste": the waste from its first card to the one on top (null for a card never seen);
    "rounds": times the waste was put back as the stock since anything else was played; "seen": the screen this was read from}.
    A game starts with None. A screen that does not follow from the one before (another game, a move nobody
    saw) starts it again from what that screen alone shows; a screen with unread parts keeps it as it was."""
    if state.get("scene", "playing") != "playing" or state.get("stock") is None or state.get("waste") is None \
            or any(top is None for top in state["foundations"]) \
            or any(pile.get("down") is None or None in pile["up"] for pile in state["tableau"]):
        return memory
    held, top = _talon(state), state["waste"]
    if memory is not None:
        before = memory["seen"]
        stock, waste, rounds = list(memory["stock"]), list(memory["waste"]), memory["rounds"]
        if _board(state) == before:
            return memory
        if state["tableau"] == before["tableau"] and state["foundations"] == before["foundations"]:
            if before["stock"] == 0 and state["stock"] == 1 and top == EMPTY:
                stock, waste = waste[::-1], []
                rounds += 1
            else:
                for _ in range(min(rules["draw"], len(stock))):
                    waste.append(stock.pop())
        else:
            rounds = 0
            if top != before["waste"] and waste:
                waste.pop()                      # the waste's card was played
        if waste and top != EMPTY and waste[-1] in (None, top):
            waste[-1] = top
        # what is remembered holds only while it fits the screen: as many cards as the stock and waste hold,
        # the waste's card on top, and none of them among the cards the piles and foundations show
        shown = {card for pile in state["tableau"] for card in pile["up"]} | {
            rank + _card(built)[1] for built in state["foundations"] if built != EMPTY for rank in RANKS[:_card(built)[0] + 1]}
        if len(stock) + len(waste) == held and bool(stock) == bool(state["stock"]) and bool(waste) == (top != EMPTY) \
                and (not waste or waste[-1] == top) and not shown & set(stock + waste):
            return {"stock": stock, "waste": waste, "rounds": rounds, "seen": _board(state)}
    # nothing to go on but this screen: the waste's top card, and the rest unseen under it or in the stock
    under = held - (top != EMPTY)
    waste = [] if top == EMPTY else [top] if state["stock"] else [None] * under + [top]
    return {"stock": [None] * (held - len(waste)), "waste": waste, "rounds": 0, "seen": _board(state)}


def spent(memory: dict | None, rules: dict) -> bool:
    """Whether the stock has been turned all the way round since anything else was played: it was put back
    and turned to its end again, so every card it can show has been on top of the waste with the piles as
    they are."""
    return memory is not None and memory["rounds"] >= 1 and not memory["stock"]


# ── hindsight: the unseen cards dealt, and a line of play to its end ───────

def _number(card: str) -> int:
    rank, suit = _card(card)
    return SUITS.index(suit) * len(RANKS) + rank


_RED_SUITS = frozenset(SUITS.index(suit) for suit in RED)


def _lies_on(lower: int, upper: int) -> bool:
    return lower % 13 == upper % 13 + 1 and (lower // 13 in _RED_SUITS) != (upper // 13 in _RED_SUITS)


def _free(found: list[int], card: int) -> bool:
    """`_safe` on a deal: the foundations (the next rank each suit wants) lose nothing by taking the card."""
    rank, suit = card % 13, card // 13
    if rank <= 1:
        return True
    red = suit in _RED_SUITS
    return all(found[other] >= rank - 1 for other in range(4) if (other in _RED_SUITS) != red)


def _deals(state: dict, memory: dict, count: int, seed: int):
    """`count` whole deals that agree with the screen and with what is remembered: (foundations, face-down
    cards of each pile, face-up cards of each pile, stock, waste), every card a number."""
    known = [_number(card) for pile in state["tableau"] for card in pile["up"]]
    found = [0] * len(SUITS)
    for top in state["foundations"]:
        if top != EMPTY:
            rank, suit = _card(top)
            found[SUITS.index(suit)] = rank + 1
    stock = [None if card is None else _number(card) for card in memory["stock"]]
    waste = [None if card is None else _number(card) for card in memory["waste"]]
    taken = set(known) | {card for card in stock + waste if card is not None} \
        | {suit * 13 + rank for suit in range(4) for rank in range(found[suit])}
    unseen = [card for card in range(DECK) if card not in taken]
    ups = [[_number(card) for card in pile["up"]] for pile in state["tableau"]]
    value = seed & LCG_MASK
    for _ in range(count):
        cards = list(unseen)
        for last in range(len(cards) - 1, 0, -1):
            value = (value * LCG_MULTIPLIER + LCG_INCREMENT) & LCG_MASK
            pick = (value >> 33) % (last + 1)
            cards[last], cards[pick] = cards[pick], cards[last]
        downs = [[cards.pop() for _ in range(pile["down"])] for pile in state["tableau"]]
        yield (list(found), downs, [list(up) for up in ups],
               [cards.pop() if card is None else card for card in stock],
               [cards.pop() if card is None else card for card in waste])


def _act(deal, action: dict, draw: int) -> None:
    """A move the rules allow, played on a deal."""
    found, downs, ups, stock, waste = deal
    if action["kind"] == "draw":
        for _ in range(min(draw, len(stock))):
            waste.append(stock.pop())
        return
    if action["kind"] == "recycle":
        stock[:], waste[:] = waste[::-1], []
        return
    origin, to = action["from"], action["to"]
    if origin[0] == "waste":
        moved = [waste.pop()]
    else:
        pile = ups[origin[1]]
        moved = pile[-origin[2]:]
        del pile[-origin[2]:]
        if not pile and downs[origin[1]]:
            pile.append(downs[origin[1]].pop())
    if to[0] == "foundation":
        found[moved[0] // 13] += 1
    else:
        ups[to[1]] += moved


def _place(found, downs, ups, card: int) -> int | None:
    """Where a single card goes on a deal: -1 for its foundation, a pile's index, None for nowhere."""
    if found[card // 13] == card % 13:
        return -1
    for index, pile in enumerate(ups):
        if pile:
            if _lies_on(pile[-1], card):
                return index
        elif card % 13 == KING and not downs[index]:
            return index
    return None


def _rollout(deal, draw: int) -> int:
    """The greedy policy played on a deal to its end, every card known: its worth (WIN_WORTH for a game won,
    over a point for each card on a foundation and each card turned up)."""
    found, downs, ups, stock, waste = deal
    piles = range(PILES)
    for _ in range(ROLLOUT_MOVES):
        if found[0] + found[1] + found[2] + found[3] == DECK:
            break
        # a pile's top card to its foundation, when that turns a card up or loses nothing
        moved = False
        for i in piles:
            pile = ups[i]
            if pile:
                card = pile[-1]
                if found[card // 13] == card % 13 and ((len(pile) == 1 and downs[i]) or _free(found, card)):
                    pile.pop()
                    found[card // 13] += 1
                    if not pile and downs[i]:
                        pile.append(downs[i].pop())
                    moved = True
        if moved:
            continue
        # a whole run moved off face-down cards, the deepest pile first
        best = None
        for i in piles:
            if downs[i] and ups[i] and (best is None or len(downs[i]) > len(downs[best[0]])):
                first = ups[i][0]
                for j in piles:
                    if j != i and (_lies_on(ups[j][-1], first) if ups[j] else first % 13 == KING and not downs[j]):
                        best = (i, j)
                        break
        if best is not None:
            i, j = best
            ups[j] += ups[i]
            ups[i][:] = [downs[i].pop()]
            continue
        # a pile freed for a king that waits
        if (waste and waste[-1] % 13 == KING) or any(downs[i] and ups[i] and ups[i][0] % 13 == KING for i in piles):
            for i in piles:
                if ups[i] and not downs[i] and ups[i][0] % 13 != KING:
                    first = ups[i][0]
                    j = next((j for j in piles if j != i and ups[j] and _lies_on(ups[j][-1], first)), None)
                    if j is not None:
                        ups[j] += ups[i]
                        ups[i][:] = []
                        moved = True
                        break
            if moved:
                continue
        # the waste's card into play
        if waste and (to := _place(found, downs, ups, waste[-1])) is not None:
            card = waste.pop()
            if to < 0:
                found[card // 13] += 1
            else:
                ups[to].append(card)
            continue
        # a pile's top card to its foundation though a card may yet want to lie on it
        for i in piles:
            if ups[i] and found[ups[i][-1] // 13] == ups[i][-1] % 13:
                card = ups[i].pop()
                found[card // 13] += 1
                moved = True
                break
        if moved:
            continue
        # the stock turned until its card can be played: twice put back with nothing played is the end
        rounds = 0
        while rounds < 2:
            if stock:
                for _ in range(min(draw, len(stock))):
                    waste.append(stock.pop())
                if _place(found, downs, ups, waste[-1]) is not None:
                    break
            elif waste:
                stock[:], waste[:] = waste[::-1], []
                rounds += 1
            else:
                rounds = 2
        if rounds == 2:
            break
    built = found[0] + found[1] + found[2] + found[3]
    return (WIN_WORTH if built == DECK else 0) + built + sum(len(pile) for pile in ups)


def _hindsight(state: dict, rules: dict, memory: dict, weighed: list[tuple[tuple, dict]], budget: int) -> dict:
    """The move among `weighed` (each with its greedy worth) whose lines of play do best over the deals."""
    deals = max(1, min(MAX_DEALS, budget // len(weighed)))
    # the seed: every card in sight and remembered, in its place, folded into one number
    seed = 0
    places = [state["waste"], *state["foundations"], *memory["stock"], "|", *memory["waste"]]
    for pile in state["tableau"]:
        places += ["|", pile["down"], *pile["up"]]
    for place in places:
        number = _number(place) + 2 if _card(place) is not None else place if isinstance(place, int) else 1 if place else 0
        seed = (seed * LCG_MULTIPLIER + number + 1) & LCG_MASK
    totals = [0] * len(weighed)
    for deal in _deals(state, memory, deals, seed):
        for index, (_, action) in enumerate(weighed):
            found, downs, ups, stock, waste = deal
            copy = (list(found), [list(pile) for pile in downs], [list(pile) for pile in ups], list(stock), list(waste))
            _act(copy, action, rules["draw"])
            totals[index] += _rollout(copy, rules["draw"])
    # a tie keeps the move the greedy policy would play
    return max(zip(totals, weighed), key=lambda item: (item[0], item[1][0]))[1][1]


def choose(state: dict, rules: dict, policy: str, budget: int, excluded: list[dict] = (), memory: dict | None = None) -> dict:
    """The move a policy picks: {"action": action | None, "nodes": moves weighed}. `excluded` moves are not
    picked (moves the screen showed had no effect from this state). `memory` is what `recall` kept of the game.

    first: the first move the rules list, the baseline. greedy: the move worth most (`_worth`): a card turned
    up or a safe card to its foundation first, then a pile freed for a waiting king, the waste's card into
    play, and the stock turned when nothing else gains. hindsight: a move that loses nothing at once, else the
    one of greedy's moves that does best over `budget` lines of play on deals of the unseen cards. Neither
    turns the stock once `memory` shows it went all the way round for nothing: no move is left (None)."""
    if policy not in POLICIES:
        raise ValueError("unknown_policy")
    moves = [action for action in legal_actions(state, rules) if action not in excluded]
    if not moves:
        return {"action": None, "nodes": 0}
    if policy == "first":
        return {"action": moves[0], "nodes": 1}
    weighed = [(key, action) for action in moves if (key := _worth(state, action)) is not None]
    if spent(memory, rules):
        weighed = [(key, action) for key, action in weighed if action["kind"] == "move"]
    if not weighed:
        return {"action": None, "nodes": len(moves)}
    key, best = max(weighed, key=lambda item: item[0])
    # (the first of the moves worth most, as the rules list them)
    best = next(action for other, action in weighed if other == key)
    sure = key[0] == 5 or (key[0] == 6 and best["to"][0] == "foundation")
    if policy == "greedy" or len(weighed) == 1 or sure:
        return {"action": best, "nodes": len(moves)}
    known = memory if memory is not None else recall(None, state, rules)
    return {"action": _hindsight(state, rules, known, weighed, budget), "nodes": len(moves)}
