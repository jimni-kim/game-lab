"""A turn of a fight played with cards from a hand: read by a model, searched by code (PLAN §27 M10, §36 E7).

A model playing such a fight on its own asks a question or several for each turn and picks the cards as it sees
them at a glance (2026-11-23: 1.9 to 2.4 questions and 16 to 19 thousand tokens a turn). Here the work is split
three ways. The model reads the turn once, as text (`read`): the hand, the energy to spend, each enemy's health,
block and what it means to do, the player's health and block, and where the cards, the enemies and the end of
the turn are. What a card does it writes once as a rule (`rule`: cost, target, damage and hits, block, draw,
energy, and what grows with each card played before it), kept by the card's name for the game (`Fight.rules`,
kept with the model's memory). The code then searches the turn (`search`): which cards, in what order, on which
enemy, for the most the turn is worth (enemies killed, damage done, health not lost to what the enemies mean to
do), and sends them (`agent.Agent._fight_turn`).

The rules make the turn's outcome foreseeable (`search`'s `predicted`), and the next turn's reading is held
against it (`Fight.held`): an enemy's health that is not what the cards played on it should have left, or the
player's that is not what the block should have kept, throws out the rules of the cards that made that number.
Those cards are written again at the next reading, and the turn is played by the model's own inputs
(`direct.Direct`), as is any turn with a card the model cannot say a rule for. Nothing here knows a game: a fight
of cards is the model's word for the screen (`fight_turn` in `play_direct.md`), and every number is read.

A card is told by how it looks, not by what the model calls it (PLAN §36 E7b): a hand of pictures with no names was
named anew at every reading, and a rule kept by a name that changes is kept for nothing. The middle of each card's
box is made a small picture (`look`), and a card that looks like one seen before is that card, whatever it is
called this time (`Fight._tell`); the model's names are kept beside it. And a rule is held against what the
screen then shows card by card (`Fight._bear`): when one card alone can account for what was off, its rule is
written again from what was seen (`fit`: so much at the first card, so much more for each card before it), and a
card with no rule at all is played with the energy the search leaves, on an enemy of its own, and gets its rule
from what that did. No turn goes to the model for a rule.

A fight is also seen by the code itself (PLAN §36 M11b): the boxes a hand's cards were read in are kept
(`Fight.places`), and a screen that shows cards known by their look in two of them is read as a turn without the
model first saying one stands there (`Fight.sighted`). That is tried once for each time such cards come up
(`Fight.blind`), so a screen that shows the hand but is no turn costs one reading and no more.
"""
from __future__ import annotations

import base64
import json
import os
from io import BytesIO
from typing import Any

from . import recovery, resources
from .kits import grid
from .model import Model, Msg, TurnRequest

# what a rule may say of a card, as whole numbers from 0 to this; who a card is played on
NUMBERS = ("cost", "damage", "hits", "block", "draw", "energy", "damage_per_card", "block_per_card")
NUMBER_MOST, TARGETS = 999, ("enemy", "all", "none")
# how many cards a hand, enemies a fight and rules a game may have; rules kept longest for the last read
HAND_MOST, ENEMIES_MOST, RULES_MOST, NAME_MOST, OTHER_MOST = 12, 8, 60, 40, 120
READ_TOKENS, READ_TIMEOUT_S = 2500, 90.0
# what a turn's outcome is worth: an enemy killed, a point of the player's health lost, a point of damage done, a
# card drawn, an input sent (the less, the better at the same worth), and the share of an enemy's health taken
KILL, LOST, DEALT, DRAWN, INPUT, SHARE = 60.0, 4.0, 1.0, 2.0, 0.01, 10.0
# how many turns of cards the search looks at, at most
NODES_MOST = 40000
# a card whose rule the screen did not bear out this many times is no longer written or played by the search
DROPS_MOST = 2
# readings in a row that found no turn before the fight is taken for over (the other side may still be acting when
# the screen first rests: 2026-11-24, four of nine turns cost a reading, a wait and a question more so), and how
# long the screen is waited for between them
MISSES_MOST, MISS_WAIT_MS = 2, 10000
# how far a number read may be from the one foreseen and still be the same
TOLERANCE = 1
# how a card is found again on the screen after others were played: the middle of its picture as it was, made
# this wide, looked for in a band this many card heights about where it was, found when the mean difference of its
# pixels' colours is no more than this (of 255)
FINGERPRINT_W, BAND, FOUND_MOST = 24, 0.75, 16.0
# how a card is told from another: the middle of its box (this share in from each side) made this many pixels a
# side; two looks are one card when their colours differ by no more than this on average (of 255; 2026-11-24, a
# game's pixel-art cards: the same card 0 to 7 with the box read a few pixels off, another card 66 or more); a
# card keeps this many looks, a game this many cards, a card this many of the names it was called
LOOK_INSET, LOOK_SIDE, LOOK_NEAR, LOOKS_MOST, CARDS_MOST, SAID_MOST = 0.2, 12, 18.0, 3, 80, 6
# what was seen of a card's damage or block, kept at most; a card with no rule that showed neither this many times
# is taken to do something the program cannot count
SEEN_MOST, TRIES_MOST = 6, 2
# a card is dragged this long and held this long before it moves
DRAG_MS, HOLD_MS = 700, 200
# where a hand's cards were read (PLAN §36 M11b): this many boxes kept, two within this many pixels being one; a
# screen that shows a known card in this many of them is taken for a fight's turn without the model saying so
PLACES_MOST, PLACE_SAME, SIGHTED_LEAST = 24, 8, 2
# after a turn was ended, how long the hand is waited for to show known cards again before the screen is read, and
# how often it is looked at
HAND_WAIT_S, HAND_LOOK_S = 8.0, 0.5


def _int(value: Any, least: int = 0, most: int = NUMBER_MOST) -> int | None:
    return value if type(value) is int and least <= value <= most else None


def _name(value: Any) -> str | None:
    return value.strip()[:NAME_MOST] if isinstance(value, str) and value.strip() else None


def rule(said: Any) -> dict[str, Any] | None:
    """A model's rule for a card as the search goes by it, or None: a cost is needed, numbers are whole and not
    below 0, anything else it does is a few words (`other`), which the search does not count."""
    if not isinstance(said, dict) or _int(said.get("cost"), 0, 20) is None:
        return None
    out: dict[str, Any] = {key: said[key] for key in NUMBERS if _int(said.get(key)) is not None}
    out["hits"] = max(1, out.get("hits", 1))
    out["target"] = said.get("target") if said.get("target") in TARGETS else "none"
    if isinstance(said.get("other"), str) and said["other"].strip():
        out["other"] = said["other"].strip()[:OTHER_MOST]
    if said.get("unplayable") is True:
        out["unplayable"] = True
    return out


def counted(card: dict[str, Any]) -> bool:
    """Whether a rule says anything the search can count."""
    return not card.get("unplayable") and any(card.get(key) for key in
                                              ("damage", "block", "draw", "energy", "damage_per_card", "block_per_card"))


def look(image: Any, box: list[float]) -> bytes:
    """How the card drawn in `box` looks: the middle of its picture, small."""
    return grid.look(image, box, LOOK_INSET, LOOK_SIDE)


def fit(seen: list[list[int]], per_card: int = 0) -> tuple[int, int]:
    """What a card does at the first card of a turn and what it adds for each card played before it, from what
    was seen of it (`[cards played before it, what it did]`). Seen after different numbers of cards, the line
    through them; seen after one number only, what it adds for each card is as it was said (`per_card`)."""
    counts = sorted({n for n, _ in seen})
    if len(counts) > 1:
        mean_n, mean_v = sum(n for n, _ in seen) / len(seen), sum(v for _, v in seen) / len(seen)
        slope = sum((n - mean_n) * (v - mean_v) for n, v in seen) / sum((n - mean_n) ** 2 for n, _ in seen)
        each = max(0, round(slope))
        return max(0, round(mean_v - each * mean_n)), each
    n, value = seen[-1]
    return (value - per_card * n, per_card) if value - per_card * n >= 0 else (value, 0)


def _point(value: Any, size: tuple[int, int], scale: float) -> list[float] | None:
    if isinstance(value, list) and len(value) == 2 and all(type(v) in (int, float) for v in value) \
            and 0 <= value[0] < size[0] and 0 <= value[1] < size[1]:
        return [value[0] / scale, value[1] / scale]
    return None


def _box(value: Any, size: tuple[int, int], scale: float) -> list[float] | None:
    if isinstance(value, list) and len(value) == 4 and all(type(v) in (int, float) for v in value) \
            and value[2] > 4 and value[3] > 4 and _point(value[:2], size, 1.0) is not None:
        return [part / scale for part in value]
    return None


def turn(said: Any, size: tuple[int, int], scale: float = 1.0) -> dict[str, Any] | None:
    """A model's reading of a turn, its points in the screen's pixels, or None when it is no turn of the player's
    in a card fight or not enough was read to play it."""
    if not isinstance(said, dict) or said.get("fight_turn") is not True:
        return None
    energy, me = _int(said.get("energy"), 0, 50), said.get("me")
    end = _point(said.get("end_turn"), size, scale)
    if energy is None or end is None or not isinstance(me, dict) or _int(me.get("hp"), 1) is None:
        return None
    enemies = []
    for one in said.get("enemies") or []:
        if not isinstance(one, dict) or _int(one.get("hp"), 1) is None or (at := _point(one.get("at"), size, scale)) is None:
            continue
        intent = one.get("intent") if isinstance(one.get("intent"), dict) else {}
        enemies.append({"name": _name(one.get("name")) or "enemy", "hp": one["hp"], "block": _int(one.get("block")) or 0,
                        "at": at, "damage": _int(intent.get("damage")) or 0, "hits": max(1, _int(intent.get("hits")) or 1)})
    hand = []
    for card in said.get("hand") or []:
        if not isinstance(card, dict) or (name := _name(card.get("name"))) is None \
                or (at := _point(card.get("at"), size, scale)) is None:
            continue
        hand.append({"name": name, "at": at, **({"box": box} if (box := _box(card.get("box"), size, scale)) else {}),
                     **({"cost": card["cost"]} if _int(card.get("cost"), 0, 20) is not None else {})})
    if not enemies or len(enemies) > ENEMIES_MOST or len(hand) > HAND_MOST:
        return None
    drop = _point(said.get("drop"), size, scale) or [size[0] / 2 / scale, size[1] * 0.45 / scale]
    return {"energy": energy, "hp": me["hp"], "block": _int(me.get("block")) or 0, "enemies": enemies, "hand": hand,
            "end_turn": end, "play": "tap" if said.get("play") == "tap" else "drag", "drop": drop,
            "situation": str(said.get("situation") or "")[:300]}


def _hit(hp: int, block: int, damage: int) -> tuple[int, int]:
    taken = min(block, damage)
    return hp - (damage - taken), block - taken


def search(read: dict[str, Any], rules: dict[str, dict[str, Any]], nodes: int = NODES_MOST,
           doubt: dict[str, int] | None = None) -> dict[str, Any]:
    """The best turn the rules foresee for the reading: `steps` (each `[card, enemy]`, indexes into the hand and
    the enemies; enemy None for a card on no one), its `value` and what it leaves (`predicted`). A card with no
    rule, or one that says nothing countable, is not played. `doubt` names the cards whose rules no turn bore out
    yet (1) and of those the ones to be told apart (2): such a one goes on no enemy with another card in doubt, and
    gives block in no turn with another's block in doubt, so that what is off afterwards is its own."""
    doubt = doubt or {}

    def apart(name: str, others: list[list[Any]]) -> bool:
        return bool(doubt.get(name)) and any(other != name and doubt.get(other) and 2 in (doubt[name], doubt[other])
                                             for other, _, _ in others)

    hand, enemies = read["hand"], read["enemies"]
    playable = [index for index, card in enumerate(hand) if card["name"] in rules and counted(rules[card["name"]])]
    start = {"energy": read["energy"], "hp": [enemy["hp"] for enemy in enemies], "eb": [enemy["block"] for enemy in enemies],
             "block": read["block"], "played": read.get("played", 0), "drawn": 0, "steps": [], "by": {}, "blockers": []}
    best = {"value": _value(start, read), "state": start}
    seen = 0

    def play(state: dict[str, Any], index: int, target: int | None) -> dict[str, Any] | None:
        card = rules[hand[index]["name"]]
        cost = hand[index].get("cost", card["cost"])
        if cost > state["energy"]:
            return None
        hp, eb, by = list(state["hp"]), list(state["eb"]), {key: list(value) for key, value in state["by"].items()}
        damage = card.get("damage", 0) + card.get("damage_per_card", 0) * state["played"]
        aimed = [target] if card["target"] == "enemy" else [i for i in range(len(hp)) if hp[i] > 0] \
            if card["target"] == "all" else []
        name = hand[index]["name"]
        if damage and any(apart(name, by.get(one, [])) for one in aimed) \
                or (card.get("block") or card.get("block_per_card")) and apart(name, state["blockers"]):
            return None
        for one in aimed if damage else []:
            for _ in range(card["hits"]):
                if hp[one] > 0:
                    hp[one], eb[one] = _hit(hp[one], eb[one], damage)
            by.setdefault(one, []).append([hand[index]["name"], state["played"], damage * card["hits"]])
        block = card.get("block", 0) + card.get("block_per_card", 0) * state["played"]
        return {"energy": state["energy"] - cost + card.get("energy", 0), "hp": hp, "eb": eb,
                "block": state["block"] + block, "played": state["played"] + 1,
                "drawn": state["drawn"] + card.get("draw", 0), "steps": state["steps"] + [[index, target]], "by": by,
                "blockers": state["blockers"] + ([[hand[index]["name"], state["played"], block]] if block else [])}

    def walk(state: dict[str, Any]) -> None:
        nonlocal seen
        used = {step[0] for step in state["steps"]}
        for index in playable:
            if index in used or seen >= nodes or all(hp <= 0 for hp in state["hp"]):
                continue
            card = rules[hand[index]["name"]]
            targets = [i for i, hp in enumerate(state["hp"]) if hp > 0] if card["target"] == "enemy" else [None]
            for target in targets:
                after = play(state, index, target)
                seen += 1
                if after is None:
                    continue
                value = _value(after, read)
                if value > best["value"]:
                    best.update(value=value, state=after)
                walk(after)

    walk(start)
    state = best["state"]
    alive = [i for i, hp in enumerate(state["hp"]) if hp > 0]
    incoming = sum(enemies[i]["damage"] * enemies[i]["hits"] for i in alive)
    lost = max(0, incoming - state["block"])
    return {"steps": state["steps"], "value": round(best["value"], 2), "searched": seen,
            "predicted": {"enemies": [{"name": enemy["name"], "hp": state["hp"][i], "x": enemy["at"][0],
                                       "by": state["by"].get(i, []), "fills": [], "hp0": enemy["hp"],
                                       "eb0": enemy["block"], "hit": enemy["damage"] * enemy["hits"]}
                                      for i, enemy in enumerate(enemies)],
                          "hp": read["hp"] - lost, "lost": lost, "incoming": incoming, "hp0": read["hp"],
                          "block": state["block"], "played": state["played"],
                          "blockers": state["blockers"], "energy": state["energy"]}}


def _value(state: dict[str, Any], read: dict[str, Any]) -> float:
    enemies = read["enemies"]
    kills = sum(hp <= 0 for hp in state["hp"])
    incoming = sum(enemy["damage"] * enemy["hits"] for enemy, hp in zip(enemies, state["hp"]) if hp > 0)
    lost = max(0, incoming - state["block"])
    dealt = sum(enemy["hp"] - max(0, hp) for enemy, hp in zip(enemies, state["hp"]))
    share = sum((enemy["hp"] - max(0, hp)) / enemy["hp"] for enemy, hp in zip(enemies, state["hp"]))
    return KILL * kills - LOST * lost + DEALT * dealt + SHARE * share + DRAWN * state["drawn"] - INPUT * len(state["steps"])


def _matched(predicted: dict[str, Any], now: dict[str, Any]) -> list[dict[str, Any] | None]:
    """The enemy of the reading `now` that each enemy foreseen is: of the same name, the nearest."""
    left, out = list(now["enemies"]), []
    for enemy in predicted["enemies"]:
        same = [one for one in left if one["name"] == enemy["name"]]
        match = min(same, key=lambda one: abs(one["at"][0] - enemy["x"])) if same else None
        if match is not None:
            left.remove(match)
        out.append(match)
    return out


def _gray(png: bytes, factor: float) -> Any:
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        return image.convert("RGB").resize((max(1, round(image.width * factor)), max(1, round(image.height * factor))))


def locate(first: bytes, box: list[float], now: bytes) -> list[float] | None:
    """Where the card drawn in `box` of the screen `first` is drawn on the screen `now`, by the middle of its
    picture, looked for in a band about where it was; None when it is drawn nowhere there."""
    from PIL import ImageChops, ImageStat

    left, top, width, height = box
    factor = FINGERPRINT_W / (width * 0.6)
    was, is_ = _gray(first, factor), _gray(now, factor)
    x0, y0 = round((left + width * 0.2) * factor), round((top + height * 0.2) * factor)
    w, h = max(4, round(width * 0.6 * factor)), max(4, round(height * 0.6 * factor))
    if x0 + w > was.width or y0 + h > was.height:
        return None
    print_ = was.crop((x0, y0, x0 + w, y0 + h))
    y_from = max(0, round((top - height * BAND) * factor))
    y_to = min(is_.height, round((top + height * (1 + BAND)) * factor))
    if y_to - y_from < h or is_.width < w:
        return None

    def off(x: int, y: int) -> float:
        return sum(ImageStat.Stat(ImageChops.difference(is_.crop((x, y, x + w, y + h)), print_)).mean) / 3

    # every second place first, then the places about the best of those
    tried = {(x, y): off(x, y) for x in range(0, is_.width - w + 1, 2) for y in range(y_from, y_to - h + 1, 2)}
    bx, by = min(tried, key=tried.get)
    for x in range(max(0, bx - 1), min(is_.width - w, bx + 1) + 1):
        for y in range(max(y_from, by - 1), min(y_to - h, by + 1) + 1):
            if (x, y) not in tried:
                tried[(x, y)] = off(x, y)
    (x, y) = min(tried, key=tried.get)
    if tried[(x, y)] > FOUND_MOST:
        return None
    return [(x + w / 2) / factor, (y + h / 2) / factor]


def action(read: dict[str, Any], at: list[float], target: int | None) -> dict[str, Any]:
    """The input that plays a card from `at` on enemy `target` (or on no one), as this game takes cards."""
    goal = read["enemies"][target]["at"] if target is not None else read["drop"]
    if read["play"] == "tap":
        return {"kind": "sequence", "inputs": [{"kind": "tap", "at": at}, {"kind": "tap", "at": goal}]}
    return {"kind": "drag", "path": [at, goal], "ms": DRAG_MS, "hold_ms": HOLD_MS}


class Fight:
    """The cards of a game as they look and the rules of each, and the turn of a fight read and searched."""

    def __init__(self, kept: Any = None, looks: Any = None, places: Any = None) -> None:
        self.rules: dict[str, dict[str, Any]] = {}
        # where the cards of a hand were read, on a screen of which size (`sighted`); and whether the cards now
        # showing there were already tried as a turn
        self.places: list[list[int]] = []
        self.screen: list[int] | None = None
        self.blind = False
        # whether the hand last read showed cards known by their looks, and whether a turn was just ended there
        # (`agent.Agent._fight_turn` then waits for such a hand before it reads)
        self.hand_known = self.awaited = False
        try:
            if isinstance(places, dict) and len(places["screen"]) == 2:
                self.screen = [int(part) for part in places["screen"]]
                self.places = [[int(part) for part in box] for box in places["boxes"] if len(box) == 4][-PLACES_MOST:]
        except (KeyError, TypeError, ValueError):
            self.places, self.screen = [], None
        for name, said in (kept.items() if isinstance(kept, dict) else []):
            if (checked := rule(said)) is not None and _name(name):
                self.rules[_name(name)] = checked
        # each card by the name it was first called: how it looks, what it was called and how often, how many
        # turns bore its rule out, what was seen of its damage and block (`[cards before it, what it did]`), and
        # how often it was played with no rule and showed nothing
        self.looks: dict[str, list[bytes]] = {}
        self.said: dict[str, dict[str, int]] = {}
        self.borne: dict[str, int] = {}
        self.seen: dict[str, dict[str, list[list[int]]]] = {}
        self.tries: dict[str, int] = {}
        for name, card in (looks.items() if isinstance(looks, dict) else []):
            try:
                found = [bytes.fromhex(one) for one in card["looks"]][:LOOKS_MOST]
                if not _name(name) or not found or any(len(one) != 3 * LOOK_SIDE * LOOK_SIDE for one in found):
                    continue
                self.looks[_name(name)] = found
                self.said[_name(name)] = {str(k)[:NAME_MOST]: int(v) for k, v in (card.get("said") or {}).items()}
                self.borne[_name(name)] = int(card.get("borne") or 0)
                self.tries[_name(name)] = int(card.get("tries") or 0)
                self.seen[_name(name)] = {part: [[int(n), int(v)] for n, v in (card.get("seen") or {}).get(part, [])]
                                          for part in ("damage", "block")}
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
        # whether the model said the screen is a turn of a card fight, and the turns read since
        self.on = False
        # what was off for each card whose rule was dropped; a card dropped this many times gets no rule again
        # (it is played with what energy is left, and nothing is learned of it)
        self.off: dict[str, list[str]] = {}
        # a turn left to the model's own inputs: not read again until the model hands the turn over
        self.paused = False
        # cards in doubt to be told apart (`_apart`)
        self.apart: set[str] = set()
        self.so_far = 0
        self.misses = 0
        # the reading under way was begun by the code's own sighting of the hand
        self.sighting = False
        self.predicted: dict[str, Any] | None = None
        self.account: dict[str, Any] = {"reads": 0, "tokens": 0, "turns": 0, "searched": 0, "cards": 0,
                                        "ended": 0, "fell_back": {}, "dropped": [], "hp": [], "written": 0,
                                        "learned": [], "apart": [], "tried": 0, "mid_reads": 0, "told_apart": 0, "renamed": 0,
                                        "sighted": 0, "sighted_no_turn": 0,
                                        "hand_waits": 0, "hand_gone": 0}

    def _fell(self, why: str) -> None:
        self.account["fell_back"][why] = self.account["fell_back"].get(why, 0) + 1
        self.paused = self.on and why != "no_turn"

    def _tell(self, png: bytes, read: dict[str, Any], written: dict[str, Any]) -> None:
        """Each card of the hand read is named by how it looks: the card seen before that it looks like, or a
        new one under the name the model gave (`said` keeps what it was called this time). A card with no rule
        takes the rule of the name it is called now, when that name has one or the model wrote one."""
        image = grid.open_picture(png)
        if self.screen != list(image.size):
            self.places, self.screen = [], list(image.size)
        for card in read["hand"]:
            said = card["said"] = card["name"]
            if "box" in card:
                self._place(card["box"])
                seen = look(image, card["box"])
                near = min(self.looks, key=lambda key: min(grid.distance(seen, one) for one in self.looks[key]), default=None)
                far = min(grid.distance(seen, one) for one in self.looks[near]) if near is not None else None
                if far is not None and far <= LOOK_NEAR:
                    if far > LOOK_NEAR / 3 and len(self.looks[near]) < LOOKS_MOST:
                        self.looks[near].append(seen)
                    self.account["renamed"] += said != near
                    card["name"] = near
                else:
                    key, number = said, 1
                    while key in self.looks:
                        number += 1
                        key = f"{said[:NAME_MOST - 4]} #{number}"
                    self.looks[key] = [seen]
                    self.account["told_apart"] += 1
                    card["name"] = key
                    while len(self.looks) > CARDS_MOST:
                        self._forget(next(key for key in self.looks if key != card["name"]))
            key = card["name"]
            names = self.said.setdefault(key, {})
            if said in names or len(names) < SAID_MOST:
                names[said] = names.get(said, 0) + 1
            if key in self.rules or self.off.get(key):
                continue
            # the model's word for what a card is counts until the screen says otherwise: its rule for the name
            # it gave, or the rule that name already has
            checked = rule(written.get(said)) if said in written else None
            if checked is None and said != key and said in self.rules:
                checked = dict(self.rules[said])
            if checked is not None:
                self.rules[key] = checked
                self.borne[key] = 0
                self.account["written"] += 1

    def _place(self, box: list[float]) -> None:
        """A box a card of the hand was read in is kept, the newest last."""
        place = [round(part) for part in box]
        self.places = [old for old in self.places if any(abs(a - b) > PLACE_SAME for a, b in zip(old, place))]
        self.places.append(place)
        del self.places[:-PLACES_MOST]

    def sighted(self, png: bytes) -> bool:
        """Whether the screen shows cards known by their look where a hand's cards were read before: a turn of a
        fight may stand there though no one said so."""
        if not self.places or not self.looks:
            return False
        image = grid.open_picture(png)
        if list(image.size) != self.screen:
            return False
        known = [one for looks in self.looks.values() for one in looks]
        found: list[list[int]] = []
        for place in self.places:
            # two boxes over one card are one card seen
            if any(abs(place[0] - other[0]) < other[2] / 2 and abs(place[1] - other[1]) < other[3] / 2 for other in found):
                continue
            seen = look(image, place)
            if any(grid.distance(seen, one) <= LOOK_NEAR for one in known):
                found.append(place)
        return len(found) >= SIGHTED_LEAST

    def sight(self, png: bytes) -> bool:
        """Whether the code is to try this screen as a turn by itself: known cards show in the hand's places and
        were not tried since they came up. The try is then spent until they are gone again, and one reading that
        finds no turn ends it."""
        if self.on:
            return False
        if not self.sighted(png):
            self.blind = False
            return False
        if self.blind:
            return False
        self.blind, self.on, self.misses = True, True, MISSES_MOST - 1
        self.sighting = True
        self.account["sighted"] += 1
        return True

    def _forget(self, key: str) -> None:
        for kept in (self.looks, self.said, self.borne, self.seen, self.tries, self.rules, self.off):
            kept.pop(key, None)

    async def read(self, model: Model, png: bytes, *, known: dict[str, str] | None = None, memory: dict[str, str] | None = None,
                   research: dict[str, Any] | None = None, history: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        """The model's reading of the screen as a turn (`turn`), its cards named by how they look and the rules
        it wrote for cards that had none taken in; None, and the fight is off until the model says again that one
        stands, when it is no such turn."""
        shown, scale = recovery.view(png)
        size = recovery._size(shown)
        asked = {"screen": list(size), "cards_known": sorted(self.rules),
                 **({"known": known} if known else {}), **({"memory": memory} if memory else {}),
                 **({"research": research} if research else {}), **({"history": history} if history else {})}
        image = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                              "data": base64.b64encode(shown).decode("ascii")}}
        result = await model.complete(TurnRequest(
            system_stable=(resources.FRAGMENT / "prompts" / "read_turn.md").read_text(encoding="utf-8"),
            name="read_turn", max_tokens=READ_TOKENS, timeout_s=READ_TIMEOUT_S,
            messages=[Msg("user", [{"type": "text", "text": json.dumps(asked, ensure_ascii=False)}, image])]))
        self.account["reads"] += 1
        self.account["tokens"] += result.usage.input_tokens
        try:
            said = json.loads(result.text or "")
        except ValueError:
            said = None
        read = turn(said, size, scale)
        if os.environ.get("GAMELAB_FIGHT_DUMP"):
            # for looking into a measured run: every screen read and what was read of it
            folder = os.environ["GAMELAB_FIGHT_DUMP"]
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, f"read-{os.getpid()}-{self.account['reads']:03d}.png"), "wb") as out:
                out.write(png)
            with open(os.path.join(folder, f"read-{os.getpid()}-{self.account['reads']:03d}.json"), "w", encoding="utf-8") as out:
                json.dump({"said": said, "scale": scale}, out, ensure_ascii=False)
        if read is None:
            self.account["sighted_no_turn"] += self.sighting
            self.sighting = False
            self.misses += 1
            if self.misses >= MISSES_MOST:
                self.on, self.predicted, self.misses, self.hand_known = False, None, 0, False
            self._fell("no_turn")
            return None
        self.misses, self.sighting = 0, False
        self._tell(png, read, said["rules"] if isinstance(said.get("rules"), dict) else {})
        while len(self.rules) > RULES_MOST:
            del self.rules[next(iter(self.rules))]
        self.hand_known = self.sighted(png)
        self.account["mid_reads"] += bool(self.predicted and self.predicted.get("mid"))
        self.account["hp"].append({"turn": self.account["reads"], "hp": read["hp"], "block": read["block"],
                                   "enemies": [enemy["hp"] for enemy in read["enemies"]]})
        return read

    def _saw(self, key: str, part: str, n: int, value: int, why: str) -> None:
        """What a card was seen to do (`part`: damage of one hit, or block) after `n` cards, and its rule written
        again from all that was seen of it."""
        seen = self.seen.setdefault(key, {"damage": [], "block": []})[part]
        seen.append([n, value])
        del seen[:-SEEN_MOST]
        card = self.rules.get(key)
        if card is None:
            return
        was = {name: card.get(name, 0) for name in (part, f"{part}_per_card")}
        first, each = fit(seen, card.get(f"{part}_per_card", 0))
        for name, number in ((part, first), (f"{part}_per_card", each)):
            card.pop(name, None)
            if number:
                card[name] = min(NUMBER_MOST, number)
        if part == "damage" and (first or each) and card["target"] == "none":
            card["target"] = "enemy"
        now = {name: card.get(name, 0) for name in was}
        if now != was:
            self.borne[key] = 0
            self.account["learned"].append({"card": key, "was": was, "is": now, "seen": why})

    def _bear(self, now: dict[str, Any]) -> None:
        """The reading `now` held against what the turn before foresaw (`predicted`), enemy by enemy and then the
        player's own health. What was foreseen and came bears out the cards behind it. What did not is laid on a
        card when one alone can account for it (the one whose rule no turn bore out yet, or the one played with no
        rule): what it was seen to do is kept and its rule written from that. When two or more could, they are
        played apart from then on. An enemy that had block, or was given a card of a rule the
        program cannot count, says nothing."""
        predicted, self.predicted = self.predicted, None
        # the cards played so far this turn, when it is read again in its middle
        self.so_far = predicted["played_all"] if predicted is not None and predicted.get("mid") else 0
        if predicted is None:
            return
        struck: set[str] = set()
        matches = _matched(predicted, now)
        tried = {entry[0] for enemy in predicted["enemies"] for entry in enemy["fills"] if entry[2] == "new"}
        for enemy, match in zip(predicted["enemies"], matches):
            if not enemy["by"] and not enemy["fills"]:
                continue
            fog = enemy["eb0"] or any(kind != "new" for _, _, kind in enemy["fills"])
            new = [entry for entry in enemy["fills"] if entry[2] == "new"]
            if match is None:
                # gone: what killed one foreseen alive did at least what was left of it
                if enemy["hp"] > 0 and len(new) == 1 and not fog:
                    self._saw(new[0][0], "damage", new[0][1], enemy["hp"], f"{enemy['name']} foreseen {enemy['hp']}, gone")
                continue
            more = enemy["hp"] - match["hp"]
            if abs(more) <= TOLERANCE:
                for name, _, _ in enemy["by"]:
                    self.borne[name] = self.borne.get(name, 0) + 1
                for name, n, _ in new if not fog else []:
                    self._saw(name, "damage", n, 0, f"{enemy['name']} as foreseen")
                continue
            if fog:
                continue
            off = f"{enemy['name']}: foreseen {max(0, enemy['hp'])}, read {match['hp']}"
            doubted = [entry for entry in enemy["by"] if not self.borne.get(entry[0])]
            could = doubted + [[name, n, 0] for name, n, _ in new]
            if could and len({name for name, _, _ in could}) == 1:
                # one card, or copies of one: each did its share of what was off
                hits = self.rules[could[0][0]]["hits"] if could[0][0] in self.rules else 1
                did = [round((foreseen + more / len(could)) / hits) for _, _, foreseen in could]
                if min(did) >= 0:
                    for (name, n, _), value in zip(could, did):
                        self._saw(name, "damage", n, value, off)
                    struck |= {name for name, _, _ in new}
                    continue
            if not doubted:
                # cards borne out before, and off now: once more and they are doubted again
                for name, _, _ in enemy["by"]:
                    self.borne[name] = max(0, self.borne.get(name, 0) - 1)
                continue
            self._apart({name for name, _, _ in doubted}, off)
        # the player's own health: what the enemies still standing meant to do, less the block foreseen
        mid = predicted.get("mid")
        coming = sum(enemy["hit"] for enemy, match in zip(predicted["enemies"], matches) if match is not None)
        fills = [entry for enemy in predicted["enemies"] for entry in enemy["fills"]]
        if (mid or coming) and not any(kind != "new" for _, _, kind in fills):
            # in the middle of a turn the block stands as it was given; after the enemies' turn it shows in the
            # health they did not take
            foreseen_own = predicted["block"] if mid else predicted["hp0"] - max(0, coming - predicted["block"])
            more = (now["block"] if mid else now["hp"]) - foreseen_own
            new = [entry for entry in fills if entry[2] == "new"]
            doubted = [entry for entry in predicted["blockers"] if not self.borne.get(entry[0])]
            off = f"own {'block' if mid else 'health'}: foreseen {foreseen_own}, read {now['block'] if mid else now['hp']}"
            # of the cards with no rule, one that took an enemy's health is not looked to for block first
            quiet = [entry for entry in new if entry[0] not in struck] or new
            could = doubted + [[name, n, 0] for name, n, _ in (quiet if more > 0 else [])]
            if abs(more) <= TOLERANCE:
                for name, _, _ in predicted["blockers"]:
                    self.borne[name] = self.borne.get(name, 0) + 1
                # a card with no rule gave no block when more block would have shown
                for name, n, _ in new if mid or coming > predicted["block"] else []:
                    self._saw(name, "block", n, 0, "own block as foreseen")
            elif could and len({name for name, _, _ in could}) == 1:
                for name, n, foreseen in could:
                    self._saw(name, "block", n, max(0, round(foreseen + more / len(could))), off)
            elif len(doubted) > 1:
                self._apart({name for name, _, _ in doubted}, off)
        # a card played with no rule gets one from what it was seen to do, or is tried again
        for name in tried:
            seen = self.seen.get(name, {})
            if name in self.rules or not (seen.get("damage") or seen.get("block")):
                continue
            damage, block = (fit(seen[part]) if seen.get(part) else (0, 0) for part in ("damage", "block"))
            made = rule({"cost": predicted["costs"].get(name, 1), "target": "enemy", "damage": damage[0],
                         "damage_per_card": damage[1], "block": block[0], "block_per_card": block[1]})
            made = {key: value for key, value in made.items() if value or key == "cost"}
            if not counted(made):
                self.tries[name] = self.tries.get(name, 0) + 1
                if self.tries[name] < TRIES_MOST:
                    continue
                made["other"] = "played by the program, it showed no damage and no block"
            self.rules[name] = {**made, "hits": 1}
            self.borne[name] = 1
            self.account["learned"].append({"card": name, "is": made, "seen": "played with no rule"})

    def _apart(self, names: set[str], why: str) -> None:
        """Cards in doubt that were played together when something was off: from now on each is played where
        what is off is its own (`search`'s `doubt`), until a turn bears it out."""
        self.apart |= names
        self.account["apart"].append({"cards": sorted(names), "off": why})

    def _drop(self, names: set[str], why: str) -> None:
        for name in names:
            if self.rules.pop(name, None) is not None:
                self.off.setdefault(name, []).append(why[:200])
        self.account["dropped"].append({"cards": sorted(names), "off": [why]})

    def plan(self, read: dict[str, Any]) -> dict[str, Any] | None:
        """The turn to play: the cards the search chose by their rules, then, with the energy that leaves, the
        cards it has no rule to count, each on an enemy still standing. One with no rule at all goes on an enemy
        that no other card in doubt goes on, so that what it does shows at the next reading; when there is no such
        enemy left for one, the turn is not ended (`more`): the screen is read again after these cards and the
        turn planned on from there. None when there is nothing to play at all: the model looks."""
        self._bear(read)
        hand = read["hand"]
        self.apart = {name for name in self.apart if name in self.rules and not self.borne.get(name)}
        doubt = {name: 2 if name in self.apart else 1 for name in self.rules if not self.borne.get(name)}
        costs = [rules["cost"] for rules in self.rules.values()]
        usual = max(set(costs), key=costs.count) if costs else 1
        found = search({**read, "played": self.so_far}, self.rules, doubt=doubt)
        self.account["searched"] += found["searched"]
        unknown = [card.get("cost", usual) for card in hand if card["name"] not in self.rules
                   and len(self.off.get(card["name"], ())) < DROPS_MOST]
        spare = 0
        if unknown and min(unknown) <= read["energy"] and any(enemy["hp"] > 0 for enemy in found["predicted"]["enemies"]):
            # a card with no rule gets its rule by being played: unless the turn wins the fight without it, the
            # energy for one such card is kept from the search
            spare = min(unknown)
            found = search({**read, "played": self.so_far, "energy": read["energy"] - spare}, self.rules, doubt=doubt)
            self.account["searched"] += found["searched"]
        predicted = found["predicted"]
        used = {index for index, _ in found["steps"]}
        rest = [index for index, card in enumerate(hand) if index not in used
                and not self.rules.get(card["name"], {}).get("unplayable")
                and not (card["name"] in self.rules and counted(self.rules[card["name"]]))]
        new = [index for index in rest if hand[index]["name"] not in self.rules
               and len(self.off.get(hand[index]["name"], ())) < DROPS_MOST]
        rest = sorted(new, key=lambda index: self.tries.get(hand[index]["name"], 0)) + [i for i in rest if i not in new]
        standing = [i for i, enemy in enumerate(predicted["enemies"]) if enemy["hp"] > 0]
        left, played, predicted["costs"] = predicted["energy"] + spare, predicted["played"], {}
        found["more"] = False
        for index in rest if standing else []:
            card = hand[index]
            cost = card.get("cost", self.rules.get(card["name"], {}).get("cost", usual))
            if cost > left:
                continue
            kind = "new" if index in new else "other"
            # an enemy that no card in doubt went on (but copies of this one), the one the fewest cards went on
            free = [i for i in standing if all(name == card["name"] for name, _, k in predicted["enemies"][i]["fills"] if k == "new")
                    and all(name == card["name"] or self.borne.get(name) for name, _, _ in predicted["enemies"][i]["by"])
                    and not predicted["enemies"][i]["eb0"]]
            # one that showed nothing when it was tried is tried alone
            alone = kind == "new" and any(k == "new" and (self.tries.get(name) or self.tries.get(card["name"])) and name != card["name"]
                                          for enemy in predicted["enemies"] for name, _, k in enemy["fills"])
            # and one whose rule the program cannot count waits for the reading after a card with no rule, so that
            # what that one did is not mixed with it
            if kind == "new" and (not free or alone) or kind == "other" and predicted["costs"] and any(
                    k == "new" for enemy in predicted["enemies"] for _, _, k in enemy["fills"]):
                found["more"] = True
                continue
            target = min(free if kind == "new" else standing,
                         key=lambda i: (len(predicted["enemies"][i]["by"]) + len(predicted["enemies"][i]["fills"]),
                                        predicted["enemies"][i]["hp"]))
            if kind == "other" and self.rules.get(card["name"], {}).get("target") in ("none", "all"):
                found["steps"].append([index, None])
                # what it does is not counted: the player's health says nothing of the others this turn
                predicted["enemies"][standing[0]]["fills"].append([card["name"], played, kind])
            else:
                found["steps"].append([index, target])
                predicted["enemies"][target]["fills"].append([card["name"], played, kind])
            predicted["costs"][card["name"]] = cost
            left, played = left - cost, played + 1
            self.account["tried"] += 1
        found["filled"] = len(found["steps"]) - len(used)
        # the turn is not ended yet when a card with no rule was played (what it did, its block too, is read off
        # the screen before the enemies act), or one was held back, or cards in doubt wait to be played apart
        waits = any(index not in {i for i, _ in found["steps"]} and card["name"] in self.apart and counted(self.rules[card["name"]])
                    and card.get("cost", self.rules[card["name"]]["cost"]) <= left for index, card in enumerate(hand))
        tried = any(kind == "new" for enemy in predicted["enemies"] for _, _, kind in enemy["fills"])
        found["more"] = (found["more"] or waits or tried) and bool(found["steps"])
        predicted["mid"], predicted["played_all"] = found["more"], played
        if not found["steps"] and read["energy"] and hand and not self.so_far:
            # cards in hand, energy to play them, and nothing the program would play all turn: the model looks
            self._fell("nothing_found")
            return None
        return found

    def failed(self, name: str, predicted: dict[str, Any]) -> None:
        """A card played with what energy was left changed nothing: it is not counted among what was played, and
        one with no rule that does so again is taken for a card that cannot be played."""
        for enemy in predicted["enemies"]:
            for entry in enemy["fills"]:
                if entry[0] == name:
                    enemy["fills"].remove(entry)
                    break
        if name not in self.rules:
            self.tries[name] = self.tries.get(name, 0) + 1
            if self.tries[name] >= TRIES_MOST:
                self.rules[name] = {"cost": predicted["costs"].get(name, 1), "hits": 1, "target": "none", "unplayable": True}

    def drop(self, name: str, why: str) -> None:
        if name in self.rules:
            self._drop({name}, why)

    def kept(self) -> dict[str, dict[str, Any]]:
        return {name: dict(card) for name, card in self.rules.items()}

    def kept_looks(self) -> dict[str, dict[str, Any]]:
        """The cards as they look, with what was learned of each, for the next work on the game."""
        return {name: {"looks": [one.hex() for one in looks], "said": dict(self.said.get(name, {})),
                       "borne": self.borne.get(name, 0), "tries": self.tries.get(name, 0),
                       "seen": self.seen.get(name, {})} for name, looks in self.looks.items()}

    def kept_places(self) -> dict[str, Any]:
        return {"screen": list(self.screen or []), "boxes": [list(box) for box in self.places]}

    def summary(self) -> dict[str, Any]:
        lost = sum(max(0, one["hp"] - other["hp"]) for one, other in zip(self.account["hp"], self.account["hp"][1:]))
        return {**self.account, "hp_lost_between_reads": lost, "rules": len(self.rules), "looks": len(self.looks)}
