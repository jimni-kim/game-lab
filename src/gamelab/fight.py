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
"""
from __future__ import annotations

import base64
import json
from io import BytesIO
from typing import Any

from . import recovery, resources
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
# a card is dragged this long and held this long before it moves
DRAG_MS, HOLD_MS = 700, 200


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


def search(read: dict[str, Any], rules: dict[str, dict[str, Any]], nodes: int = NODES_MOST) -> dict[str, Any]:
    """The best turn the rules foresee for the reading: `steps` (each `[card, enemy]`, indexes into the hand and
    the enemies; enemy None for a card on no one), its `value` and what it leaves (`predicted`). A card with no
    rule, or one that says nothing countable, is not played."""
    hand, enemies = read["hand"], read["enemies"]
    playable = [index for index, card in enumerate(hand) if card["name"] in rules and counted(rules[card["name"]])]
    start = {"energy": read["energy"], "hp": [enemy["hp"] for enemy in enemies], "eb": [enemy["block"] for enemy in enemies],
             "block": read["block"], "played": 0, "drawn": 0, "steps": [], "by": {}, "blockers": []}
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
        for one in aimed if damage else []:
            for _ in range(card["hits"]):
                if hp[one] > 0:
                    hp[one], eb[one] = _hit(hp[one], eb[one], damage)
            by.setdefault(one, []).append(hand[index]["name"])
        block = card.get("block", 0) + card.get("block_per_card", 0) * state["played"]
        return {"energy": state["energy"] - cost + card.get("energy", 0), "hp": hp, "eb": eb,
                "block": state["block"] + block, "played": state["played"] + 1,
                "drawn": state["drawn"] + card.get("draw", 0), "steps": state["steps"] + [[index, target]], "by": by,
                "blockers": state["blockers"] + ([hand[index]["name"]] if block else [])}

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
                                       "by": state["by"].get(i, [])} for i, enemy in enumerate(enemies)],
                          "hp": read["hp"] - lost, "lost": lost, "incoming": incoming,
                          "blockers": state["blockers"], "energy": state["energy"]}}


def _value(state: dict[str, Any], read: dict[str, Any]) -> float:
    enemies = read["enemies"]
    kills = sum(hp <= 0 for hp in state["hp"])
    incoming = sum(enemy["damage"] * enemy["hits"] for enemy, hp in zip(enemies, state["hp"]) if hp > 0)
    lost = max(0, incoming - state["block"])
    dealt = sum(enemy["hp"] - max(0, hp) for enemy, hp in zip(enemies, state["hp"]))
    share = sum((enemy["hp"] - max(0, hp)) / enemy["hp"] for enemy, hp in zip(enemies, state["hp"]))
    return KILL * kills - LOST * lost + DEALT * dealt + SHARE * share + DRAWN * state["drawn"] - INPUT * len(state["steps"])


def held(predicted: dict[str, Any] | None, now: dict[str, Any]) -> tuple[set[str], list[str]]:
    """The cards whose rules the reading `now` does not bear out, against what the turn before foresaw, and what
    was off: an enemy's health not what the cards played on it left (one foreseen dead and still there too), the
    player's health not what the block kept, when cards gave block."""
    if predicted is None:
        return set(), []
    wrong: set[str] = set()
    off: list[str] = []
    left = list(now["enemies"])
    for enemy in predicted["enemies"]:
        if not enemy["by"]:
            continue
        same = [one for one in left if one["name"] == enemy["name"]]
        match = min(same, key=lambda one: abs(one["at"][0] - enemy["x"])) if same else None
        if match is not None:
            left.remove(match)
        if enemy["hp"] <= 0 and match is None:
            continue
        if match is None or abs(match["hp"] - enemy["hp"]) > TOLERANCE:
            wrong.update(enemy["by"])
            off.append(f"{enemy['name']}: foreseen {max(0, enemy['hp'])}, read {match['hp'] if match else 'gone'}")
    if predicted["blockers"] and predicted["incoming"] and abs(now["hp"] - predicted["hp"]) > TOLERANCE:
        wrong.update(predicted["blockers"])
        off.append(f"own health: foreseen {predicted['hp']}, read {now['hp']}")
    return wrong, off


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
    """The rules of a game's cards as the model wrote them, and the turn of a fight read and searched."""

    def __init__(self, kept: Any = None) -> None:
        self.rules: dict[str, dict[str, Any]] = {}
        for name, said in (kept.items() if isinstance(kept, dict) else []):
            if (checked := rule(said)) is not None and _name(name):
                self.rules[_name(name)] = checked
        # whether the model said the screen is a turn of a card fight, and the turns read since
        self.on = False
        # what was off for each card whose rule was dropped, told to the next reading; a card dropped this many times
        # is played by the search no more (it is left in hand) and not asked about again
        self.off: dict[str, list[str]] = {}
        # a turn left to the model's own inputs: not read again until the model hands the turn over
        self.paused = False
        self.misses = 0
        self.predicted: dict[str, Any] | None = None
        self.account: dict[str, Any] = {"reads": 0, "tokens": 0, "turns": 0, "searched": 0, "cards": 0,
                                        "ended": 0, "fell_back": {}, "dropped": [], "hp": [], "written": 0}

    def _fell(self, why: str) -> None:
        self.account["fell_back"][why] = self.account["fell_back"].get(why, 0) + 1
        self.paused = self.on and why != "no_turn"

    async def read(self, model: Model, png: bytes, *, known: dict[str, str] | None = None, memory: dict[str, str] | None = None,
                   research: dict[str, Any] | None = None, history: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        """The model's reading of the screen as a turn (`turn`), the rules it wrote for cards that had none taken
        in; None, and the fight is off until the model says again that one stands, when it is no such turn."""
        shown, scale = recovery.view(png)
        size = recovery._size(shown)
        given_up = sorted(name for name, offs in self.off.items() if len(offs) >= DROPS_MOST)
        asked = {"screen": list(size), "cards_known": sorted(self.rules) + given_up,
                 **({"rules_off": {name: offs[-1] for name, offs in self.off.items() if name not in self.rules
                                   and len(offs) < DROPS_MOST}} if self.off else {}),
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
        if read is None:
            self.misses += 1
            if self.misses >= MISSES_MOST:
                self.on, self.predicted, self.misses = False, None, 0
            self._fell("no_turn")
            return None
        self.misses = 0
        for name, written in (said.get("rules") or {}).items() if isinstance(said.get("rules"), dict) else []:
            if _name(name) and _name(name) not in self.rules and (checked := rule(written)) is not None:
                self.rules[_name(name)] = checked
                self.account["written"] += 1
        while len(self.rules) > RULES_MOST:
            del self.rules[next(iter(self.rules))]
        self.account["hp"].append({"turn": self.account["reads"], "hp": read["hp"], "block": read["block"],
                                   "enemies": [enemy["hp"] for enemy in read["enemies"]]})
        return read

    def plan(self, read: dict[str, Any]) -> dict[str, Any] | None:
        """The turn to play, or None when the model is to play it: the reading did not bear out the rules of the
        turn before (those cards' rules are dropped), or a card in hand has no rule the search can go by."""
        wrong, off = held(self.predicted, read)
        self.predicted = None
        if wrong:
            for name in wrong:
                self.rules.pop(name, None)
                self.off.setdefault(name, []).append("; ".join(off)[:200])
            self.account["dropped"].append({"cards": sorted(wrong), "off": off})
            self._fell("rule_off")
            return None
        # a card with no rule is left out of the search; when the energy the searched cards leave could still
        # play one, the rest of the turn is the model's (2026-11-24: one starting card the model never read made
        # ten of twelve turns the model's)
        unruled = [card["name"] for card in read["hand"] if card["name"] not in self.rules
                   and len(self.off.get(card["name"], ())) < DROPS_MOST]
        found = search(read, self.rules)
        self.account["searched"] += found["searched"]
        left = found["predicted"]["energy"]
        found["leave"] = bool(unruled) and left > 0 and any(card.get("cost", 1) <= left for card in read["hand"]
                                                             if card["name"] in unruled)
        if not found["steps"] and (found["leave"] or read["energy"] and any(
                counted(self.rules[c["name"]]) for c in read["hand"] if c["name"] in self.rules)):
            # nothing the search can play, or cards it could play and none was worth it: the model looks
            self._fell("no_rule" if found["leave"] else "nothing_found")
            return None
        if found["leave"]:
            self.account["left_to_model"] = self.account.get("left_to_model", 0) + 1
        return found

    def drop(self, name: str, why: str) -> None:
        if self.rules.pop(name, None) is not None:
            self.off.setdefault(name, []).append(why)
            self.account["dropped"].append({"cards": [name], "off": [why]})

    def kept(self) -> dict[str, dict[str, Any]]:
        return {name: dict(card) for name, card in self.rules.items()}

    def summary(self) -> dict[str, Any]:
        lost = sum(max(0, one["hp"] - other["hp"]) for one, other in zip(self.account["hp"], self.account["hp"][1:]))
        return {**self.account, "hp_lost_between_reads": lost, "rules": len(self.rules)}

