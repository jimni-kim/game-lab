"""Klondike patience: seven piles, a stock, a waste and four foundations (PLAN §10, G9). The first rule model
whose state is not a grid.

A card is its rank and suit as a string ("AS", "10H", "KD"; suits S H D C). The state is what a screen shows:

    stock        1 while cards wait face down in it, 0 when it is empty
    waste        the card on top of the waste, "0" when it is empty
    foundations  the top card of each of the four, "0" for an empty one
    tableau      seven piles, each {"down": how many cards lie face down, "up": the face-up cards from the
                 deepest to the top one}
    scene

Null is a value nobody read. What a move turns up (the card under a pile's last face-up one, the next card of
the stock, what is left of the waste) is not known before it shows: `apply` leaves it unread, and `assess`
holds the screen to everything else.

The replay game keeps the whole deal (`initial_state`, `next_state`) and `shown` gives the state above.
Pure like every rule model: JSON in and out, no IO, no clock, randomness only from the caller's `draw`.
"""
from __future__ import annotations

RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUITS = ("S", "H", "D", "C")
RED = ("H", "D")
EMPTY = "0"
PILES, FOUNDATIONS = 7, 4
PLAYING, OVER, WON = "playing", "over", "won"
# draw: how many cards one turn of the stock moves to the waste (the replay's own; a screen shows what it shows)
DEFAULT_RULES = {"draw": 1}
CAPABILITIES = ("chance_sampled", "hidden")


def _card(value) -> tuple[int, str] | None:
    """A card's rank (0 for an ace) and suit, or None when the value is no card."""
    if not isinstance(value, str) or len(value) < 2 or value[-1] not in SUITS or value[:-1] not in RANKS:
        return None
    return RANKS.index(value[:-1]), value[-1]


def _stacks(lower, upper) -> bool:
    """Whether `upper` may lie on `lower` in the tableau: one rank less, the other colour."""
    (rank, suit), (above, other) = _card(lower), _card(upper)
    return above == rank - 1 and (suit in RED) != (other in RED)


def _visible(state: dict) -> list:
    return [state.get("waste"), *state.get("foundations", []), *(card for pile in state.get("tableau", []) for card in pile["up"])]


def _unread(state: dict) -> list[str]:
    found = [name for name in ("stock", "waste") if state.get(name) is None]
    found += [f"foundations[{index}]" for index, card in enumerate(state["foundations"]) if card is None]
    for index, pile in enumerate(state["tableau"]):
        found += [f"tableau[{index}].down"] if pile.get("down") is None else []
        found += [f"tableau[{index}].up[{place}]" for place, card in enumerate(pile["up"]) if card is None]
    return found


def unknown_fields(state: dict) -> list[str]:
    return _unread(state) if state.get("scene", PLAYING) == PLAYING else []


def validate(state: dict, rules: dict) -> list[str]:
    tableau, foundations = state.get("tableau"), state.get("foundations")
    if (not isinstance(tableau, list) or len(tableau) != PILES or not isinstance(foundations, list)
            or len(foundations) != FOUNDATIONS or any(
                not isinstance(pile, dict) or not isinstance(pile.get("up"), list) for pile in tableau)):
        return ["shape"]
    problems = []
    if state.get("stock") not in (0, 1, None) or any(
            pile.get("down") is not None and (type(pile["down"]) is not int or not 0 <= pile["down"] <= 6) for pile in tableau):
        problems.append("count")
    cards = [card for card in _visible(state) if card not in (None, EMPTY)]
    if any(_card(card) is None for card in cards) or any(card == EMPTY for pile in tableau for card in pile["up"]):
        problems.append("card")
    elif len(set(cards)) != len(cards):
        problems.append("card_twice")
    elif any(_card(suit_top)[1] in [_card(other)[1] for other in foundations[:index] if other not in (None, EMPTY)]
             for index, suit_top in enumerate(foundations) if suit_top not in (None, EMPTY)):
        problems.append("foundation_suit_twice")
    elif any(not _stacks(lower, upper) for pile in tableau for lower, upper in zip(pile["up"], pile["up"][1:])
             if lower is not None and upper is not None):
        problems.append("run")       # the face-up cards of a pile always run down in turning colours
    elif any(pile.get("down") and not pile["up"] for pile in tableau) and state.get("scene", PLAYING) == PLAYING:
        problems.append("unturned")  # a pile at rest shows a card whenever it has one
    if state.get("scene", PLAYING) not in (PLAYING, OVER, WON):
        problems.append("scene")
    return problems


def _require_known(state: dict) -> None:
    if _unread(state):
        raise ValueError("unknown_cells")


def _foundation_for(state: dict, card: str) -> int | None:
    """The foundation `card` may go to now: its suit's when it is the next rank, the first empty one for an ace."""
    rank, suit = _card(card)
    for index, top in enumerate(state["foundations"]):
        if top != EMPTY and _card(top)[1] == suit:
            return index if _card(top)[0] == rank - 1 else None
    if rank == 0:
        return next((index for index, top in enumerate(state["foundations"]) if top == EMPTY), None)
    return None


def _onto(pile: dict, card: str) -> bool:
    """Whether `card` (the first of the cards moved) may be put on a tableau pile."""
    if not pile["up"]:
        return pile["down"] == 0 and _card(card)[0] == len(RANKS) - 1       # only a king on an empty pile
    return _stacks(pile["up"][-1], card)


def legal_actions(state: dict, rules: dict) -> list[dict]:
    """Every move the rules allow: a turn of the stock (or the waste put back as the stock), the waste's card
    or a pile's top card to a foundation, the waste's card or a pile's face-up cards from any of them on to
    another pile."""
    if state.get("scene", PLAYING) != PLAYING:
        return []
    _require_known(state)
    found = []
    if state["stock"]:
        found.append({"kind": "draw"})
    elif state["waste"] != EMPTY:
        found.append({"kind": "recycle"})
    sources = [(["waste"], state["waste"])] if state["waste"] != EMPTY else []
    for index, pile in enumerate(state["tableau"]):
        sources += [(["tableau", index, len(pile["up"]) - place], card) for place, card in enumerate(pile["up"])]
    for origin, card in sources:
        single = origin[0] == "waste" or origin[2] == 1
        if single and (to := _foundation_for(state, card)) is not None:
            found.append({"kind": "move", "from": origin, "to": ["foundation", to]})
        for index, pile in enumerate(state["tableau"]):
            if (origin[0] == "waste" or origin[1] != index) and _onto(pile, card):
                found.append({"kind": "move", "from": origin, "to": ["tableau", index]})
    return found


def apply(state: dict, rules: dict, action: dict) -> dict:
    """What a move leaves as far as the cards in sight decide it. What it turns up is left unread: the card
    under a pile's last face-up one, the waste after a turn of the stock or after its card was taken, and
    whether the stock still holds cards."""
    if action not in legal_actions(state, rules):
        raise ValueError("illegal_action")
    after = {**state, "foundations": list(state["foundations"]),
             "tableau": [{"down": pile["down"], "up": list(pile["up"])} for pile in state["tableau"]]}
    if action["kind"] == "draw":
        return {"state": {**after, "stock": None, "waste": None}}
    if action["kind"] == "recycle":
        return {"state": {**after, "stock": 1, "waste": EMPTY}}
    origin, to = action["from"], action["to"]
    if origin[0] == "waste":
        moved, after["waste"] = [state["waste"]], None
    else:
        pile = after["tableau"][origin[1]]
        moved, pile["up"] = pile["up"][-origin[2]:], pile["up"][:-origin[2]]
        if not pile["up"] and pile["down"]:
            pile["down"], pile["up"] = pile["down"] - 1, [None]
    if to[0] == "foundation":
        after["foundations"][to[1]] = moved[0]
    else:
        after["tableau"][to[1]]["up"] += moved
    return {"state": after}


def explain(before: dict, action: dict, after: dict, rules: dict) -> list[dict]:
    return []


def ended(before: dict, action: dict, after: dict, rules: dict) -> bool:
    return False


def _won(state: dict) -> bool:
    return all(top not in (None, EMPTY) and _card(top)[0] == len(RANKS) - 1 for top in state["foundations"])


def status(state: dict, rules: dict, goal: dict) -> dict:
    """Won when every foundation shows its king (or the game's own screen says so); over when its own screen
    says so, or nothing at all can be moved. A game going round its stock with nothing to play is not ended
    here: whoever plays it sees that."""
    scene = state.get("scene", PLAYING)
    won = scene == WON or (scene == PLAYING and not _unread(state) and _won(state))
    terminal = won or scene != PLAYING or (not _unread(state) and not legal_actions(state, rules))
    return {"terminal": terminal, "goal": "reached" if won else "not_reached"}


def _same(told, seen) -> bool:
    """Whether what was read agrees with what the rules predict, unread parts of the prediction left open."""
    if told is None:
        return True
    if isinstance(told, dict):
        return isinstance(seen, dict) and all(_same(value, seen.get(key)) for key, value in told.items())
    if isinstance(told, list):
        return isinstance(seen, list) and len(told) == len(seen) and all(_same(a, b) for a, b in zip(told, seen))
    return told == seen


def assess(before: dict, action: dict, after: dict, rules: dict) -> dict:
    """Whether the screen after a move shows that move: every card where the rules put it, and a card of the
    game's choosing wherever the move turned one up."""
    if _unread(after):
        return {"effect": "unknown"}
    board = ("stock", "waste", "foundations", "tableau")
    if all(after[name] == before[name] for name in board):
        return {"effect": "absent"}
    try:
        predicted = apply(before, rules, action)["state"]
    except ValueError:
        return {"effect": "inconsistent"}
    return {"effect": "confirmed" if all(_same(predicted[name], after[name]) for name in board) else "inconsistent"}


def unread_state(rules: dict, scene: str) -> dict:
    return {"stock": None, "waste": None, "foundations": [None] * FOUNDATIONS,
            "tableau": [{"down": None, "up": []} for _ in range(PILES)], "scene": scene}


# ── the replay game (seeded by the caller): the whole deal, of which `shown` is what a screen shows ────

def _shuffled(draw: int) -> list[str]:
    deck = [rank + suit for suit in SUITS for rank in RANKS]
    value = draw & ((1 << 64) - 1)
    for last in range(len(deck) - 1, 0, -1):
        value = (value * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        pick = (value >> 33) % (last + 1)
        deck[last], deck[pick] = deck[pick], deck[last]
    return deck


def initial_state(rules: dict, draw: int) -> dict:
    """The deal: pile i holds i cards face down under one face up; the rest is the stock (its top card last)."""
    deck = _shuffled(draw)
    tableau = []
    for index in range(PILES):
        cards, deck = deck[:index + 1], deck[index + 1:]
        tableau.append({"down": cards[:-1], "up": cards[-1:]})
    return {"stock": deck, "waste": [], "foundations": [EMPTY] * FOUNDATIONS, "tableau": tableau, "scene": PLAYING}


def shown(state: dict) -> dict:
    """The deal as a screen shows it: counts for what lies face down, the waste's top card."""
    if not isinstance(state["stock"], list):
        return state
    return {"stock": 1 if state["stock"] else 0, "waste": state["waste"][-1] if state["waste"] else EMPTY,
            "foundations": list(state["foundations"]),
            "tableau": [{"down": len(pile["down"]), "up": list(pile["up"])} for pile in state["tableau"]],
            "scene": state["scene"]}


def next_state(state: dict, rules: dict, action: dict, draw: int) -> dict:
    if action not in legal_actions(shown(state), rules):
        raise ValueError("illegal_action")
    after = {**state, "stock": list(state["stock"]), "waste": list(state["waste"]), "foundations": list(state["foundations"]),
             "tableau": [{"down": list(pile["down"]), "up": list(pile["up"])} for pile in state["tableau"]]}
    if action["kind"] == "draw":
        for _ in range(min(rules["draw"], len(after["stock"]))):
            after["waste"].append(after["stock"].pop())
        return after
    if action["kind"] == "recycle":
        after["stock"], after["waste"] = after["waste"][::-1], []
        return after
    origin, to = action["from"], action["to"]
    if origin[0] == "waste":
        moved = [after["waste"].pop()]
    else:
        pile = after["tableau"][origin[1]]
        moved, pile["up"] = pile["up"][-origin[2]:], pile["up"][:-origin[2]]
        if not pile["up"] and pile["down"]:
            pile["up"] = [pile["down"].pop()]
    if to[0] == "foundation":
        after["foundations"][to[1]] = moved[0]
    else:
        after["tableau"][to[1]]["up"] += moved
    if _won(shown(after)):
        after["scene"] = WON
    return after


def hidden_state(state: dict) -> dict:
    return {**state, "waste": None}


def stray_state(state: dict) -> dict:
    """The waste's card another than any move leaves it: a fault the replay injects."""
    seen = set(_visible(state))
    other = next(rank + suit for suit in SUITS for rank in RANKS if rank + suit not in seen)
    return {**state, "waste": other}
