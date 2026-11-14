"""Klondike: the registered policies over the klondike rule model (PLAN §10, G9).

Pure like the rule model and ported with it. A policy sees one state and nothing of the moves before it, so
it never undoes what it did: it only plays a move that turns a card up, frees a pile for a king that waits,
builds a foundation, or brings the waste's card into play; else it turns the stock.
"""
from __future__ import annotations

from ..rules.klondike import EMPTY, RANKS, RED, _card, legal_actions

POLICIES = ("first", "greedy")
KING = len(RANKS) - 1


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


def choose(state: dict, rules: dict, policy: str, budget: int, excluded: list[dict] = ()) -> dict:
    """The move a policy picks: {"action": action | None, "nodes": moves weighed}. `excluded` moves are not
    picked (moves the screen showed had no effect from this state).

    first: the first move the rules list, the baseline. greedy: the move worth most (`_worth`): a card turned
    up or a safe card to its foundation first, then a pile freed for a waiting king, the waste's card into
    play, and the stock turned when nothing else gains."""
    if policy not in POLICIES:
        raise ValueError("unknown_policy")
    moves = [action for action in legal_actions(state, rules) if action not in excluded]
    if not moves:
        return {"action": None, "nodes": 0}
    if policy == "first":
        return {"action": moves[0], "nodes": 1}
    best, best_key = None, None
    for action in moves:
        key = _worth(state, action)
        if key is not None and (best_key is None or key > best_key):
            best, best_key = action, key
    return {"action": best, "nodes": len(moves)}
