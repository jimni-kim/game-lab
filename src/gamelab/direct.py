"""A game no family is registered for, played by a model (PLAN §11, H2; the base of the levels, L0).

The harness has rules and a solver for a handful of families. Any other game is not left unplayed: a model is
shown the screen and answers with one input of its own, what it expects that input to change, and a few notes
it wants to keep (the rules as far as it has made them out, what it is trying). The input is sent, the screen
looked at again, and the model is told what the code saw of it: whether anything changed and where. Nothing
here knows the game, so nothing checks a move by its rules; the one check is the code's own, that the screen
changed.

What keeps moving on the screen whatever is done (a tutorial's hand, a pulsing button) is left out of the
pictures the model is shown and of what is said to have changed (`motion`); the model is told where it was. A
drag the model marks `carry` is aimed by where the thing it picked up is drawn while held, which the code
measures on the way (`carry.Carrier`), not by where the finger is: a game draws a held piece above the finger,
larger. Such a drag also tells whether the thing was picked up at all: one that was not at one press and was at
a longer one sets the shortest press for the rest of the game. A drag that missed with the thing picked up
missed its place, not its press, and the model is told so.

The game ends when the model says its own end screen stands there, when it sees nothing more to do, when its
inputs keep changing nothing, when it says a condition the person set for stopping is met (`stop`), or at a
budget.

An input that uses up something of the player's beyond the board (a booster, coins, extra moves, another try at
a lost level) is one the model must say so of (`uses`, by the names of `contracts.META_ACTIONS`), and it is sent
only when the person allowed each of them (PLAN §25, R2): one that is not allowed is not sent, and the model is
told it was refused. What was used is counted for the work's account.

The model names each screen by its kind (`scenes`: a level's board, its result, the map, a notice, a story, a
puzzle of another kind) and says when the game forces one input (`guided`). The loop goes by the name (PLAN §25,
R5 and R6): which inputs are moves of a level, when a family's own reader is tried again, and, on a screen that
is no board, a tap sent again and again without asking for as long as each changes a little of the screen
(`repeat`: a story's lines; PLAN §27, M1). A sequence of inputs on such a screen is sent the same way, each only
once the one before changed a little of the screen.

A story is played the same way with one more mark (PLAN §27, session T). An input the model says only moves the
story on a line (`advance`) is sent again and again by the loop without asking, for as long as the screen stays a
line: on a page that can be read, while the things to press stay the same (`pressable`); on a picture alone, while
each input changes only where the model said the lines are shown or where lines changed before (`within`). Where the story asks the player to choose, the model
says what is asked, the options, the one it picks and whether the choice lasts (`choice`); one that lasts is sent
only when the person said how such choices are to be made (`goal.choices`), else the work is handed back there
(`agent._play_direct`).

What the model keeps for itself is in parts (`MEMORY`; PLAN §27, M4): the game's rules as far as it made them out,
how the game gets from screen to screen outside a level, what it tried in vain, and its plan. All but the plan
outlast the work: they are kept by the game (`adaptation.Store.memory`) and given to the model that plays it next.

What a question costs is kept down three ways (PLAN §27, session S2). The prompt is put together for the
question (`prompt`): a paragraph of `play_direct.md` tagged `[when ...]` is given only when what it speaks of is
there (an input the screen takes, an errand, levels to get to). Where the page under the picture can be read
(`Surface.outline`), what it says stands in view is given as lines of text (`lines`; M2) and the picture is shown
smaller; the model may ask for it at full size (`LOOK`). And on a level's board a sequence is several moves, sent
one at a time while each changes something (`agent._play_direct`; M1).

An input the code sent itself between two questions, as a step of the game's routine (`routine`), is put in the
history the model is shown, marked as the program's.

A game that goes on for hours over fights and stages (a deck builder, a role-playing game) is played the same way
with three things more (PLAN §36, session E3). An input that hands the turn to the game or another player carries
how long the screen may take to come to rest (`then_wait_ms`): the program waits that out (`rested`) and the model
is asked at its next turn, not in the middle of the other side's. What a named thing of the game does (a card, an
item, an enemy), once read, is kept by its name (`known`) and given with every question, in this work and in later
ones, so its description is not opened again. And one more part of the memory, `run`, says where such a game
stands; it outlasts the work as the rules do, and the model holds it against the screen when it is given it again.

A board of square cells on a screen that is a picture alone is read by code though no family knows it (PLAN §36,
session E2; `lattice`). The model says once where the grid is and how many rows and columns it has (`grid`); from
the next question on it is given the board's rows as letters, one for each kind of cell the code tells apart
(`cells`), names each letter once (`names`), and may aim a press at a cell by its row and column. The picture is
then shown smaller, as beside a page's text, and while the board stands exactly as it was told (every cell a kind
named before) the instructions leave out what only other screens need (`board_read`).

A game looked up before it was played (PLAN §27 M8, §36 E6; `research`) has what was found given with every
question (`research`): from outside the game as not confirmed, from its own help screens as read there. While the
game was not yet looked at from inside (`looking`), the model is asked to open and read what the game itself tells
of how it is played before the first move of a level, and to say so (`research_done`) when it did or there is
none; what it reads there it gives as `found`. The questions asked so and their tokens are counted apart
(`looked`), for the board's budget not to bear them.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from typing import Any

from . import carry, fight, frames, motion, recovery, research, resources, scenes, world
from .lattice import Lattice
from .contracts import FORCED_FREE, META_ACTIONS
from .execution import TAP_MS
from .model import Model, Msg, TurnRequest

TAP, KEY, SWIPE, TEXT, WAIT, DONE, GIVE_UP, STOP = "tap", "key", "swipe", "text", "wait", "done", "give_up", "stop"
OUT_OF_LIVES = recovery.OUT_OF_LIVES
DOUBLE_TAP, LONG_PRESS, DRAG, SCROLL, BACK = "double_tap", "long_press", "drag", "scroll", "back"
RIGHT_TAP, RELAUNCH, SEQUENCE, LOOK = "right_tap", "relaunch", "sequence", "look"
# how a game the model says is over ended
ENDS = ("won", "lost")
# what an input is carried out with (`Surface.kinds`): any one of these
NEEDS = {TAP: ("tap",), DOUBLE_TAP: ("tap",), LONG_PRESS: ("tap",), KEY: ("keys",), SWIPE: ("drag",), DRAG: ("drag",),
         TEXT: ("text", "keys"), SCROLL: ("scroll",), BACK: ("back",), RIGHT_TAP: ("secondary",),
         RELAUNCH: ("relaunch",), SEQUENCE: ("tap",)}
# what a sequence may be made of: inputs to one place, never a wait, a reload or an end
SEQUENCED = frozenset({TAP, DOUBLE_TAP, LONG_PRESS, KEY, SWIPE, DRAG})
# the keys a game may be played with beyond those a notice is answered with, and the letters and digits typed
KEYS = recovery.KEYS | {"Backspace", "Tab"}
TYPED = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ")
TEXT_MOST = 32
# moves told to the model of what it did before, the notes it may keep, and the changed places it is told of
HISTORY_KEPT, NOTES_MOST, PLACES_TOLD = 8, 2200, 6
PLAY_TOKENS, PLAY_TIMEOUT_S = 3000, 90.0
# how long an input is left to take effect, then until the screen shows the same twice this far apart, at most
SETTLE_S, STILL_GAP_S, STILL_TIMEOUT_S = 0.6, 0.3, 3.0
SWIPE_MS = 150
# a drag: its points, its time and how long it rests before moving and before letting go, as given or by default.
# A quick drag is taken for a tap by some games (a merge game's item was only selected at 150 and 300 ms and
# moved at 500, 2026-11-15), so the default is slower than a swipe
PATH_MOST, DRAG_MS, DRAG_MS_RANGE, HOLD_MS, HOLD_MS_MOST = 16, 600, (100, 5000), 150, 1500
# a double tap's two taps this far apart: more than a game takes for two taps at once, less than its limit
DOUBLE_TAP_GAP_MS = 120
LONG_PRESS_MS, LONG_PRESS_RANGE = 800, (400, 3000)
SCROLL_MOST = 10
# a sequence: at most this many inputs, this far apart (a selection drawn before the next tap lands)
SEQUENCE_MOST, SEQUENCE_GAP_S = 6, 0.3
# on a level's board a sequence is moves one after another (PLAN §27, M1): at most this many inputs, and the share
# of the screen one of them may change and still be followed by the next (a move changes more of a board than a
# tap changes of a notice; more than this is a result or another screen)
MOVES_MOST, MOVE_SHARE = 12, 0.5
# on a board the model is given as text (`lattice`) it sees every cell exactly and may give this many
CELL_MOVES_MOST = 24
# the page's own account of the screen given as text (PLAN §27, M2): only when little of the view is under what
# the page cannot be read through, it names at least this many things and its lines are no longer than this in
# all (about twice what the whole picture costs: what is told is exact, which a picture's reading is not). The
# picture is then shown with its longer side this long
DRAWN_MOST, ELEMENTS_LEAST, ELEMENTS_CHARS, SMALL_SIDE = 0.1, 3, 8000, 768
# a grid the model describes again within this many pixels of the board read is that board (`Direct._grid`)
SAME_GRID = 6
# a text of the page counts as words when it has this many letters; such a text coming up ends a sequence of moves
WORD_LETTERS = 4
_TAGGED = re.compile(r"^(\s*(?:- )?)((?:\[(?:when|unless) [a-z_ ]+\] )+)")
_TAG = re.compile(r"\[(when|unless) ([a-z_ ]+)\] ")
# what the model keeps for itself, each part with what it is told to put there, and how long a part may be. PLAN is
# of the work in hand only; the others are kept by the game for the next work on it
PLAN = "plan"
MEMORY = {
    "rules": "the game's rules as far as you have made them out: what a move is, how it is made here, what wins",
    "ways": "how this game gets from screen to screen outside a level: what opens the next level, what it puts in "
            "the way and how each is passed",
    "failed": "what you tried that did not work, so as not to try it again",
    "run": "only for a game that goes on over many fights or stages: where it stands (the character and how it is "
           "built, the stage reached, what is held) and what you are building toward",
    PLAN: "what you are doing now and mean to do next",
}
MEMORY_MOST = 400
# what a named thing of the game does, kept once read (PLAN §36, E3): this many things, each name and text this long
KNOWN_MOST, KNOWN_NAME, KNOWN_TEXT = 40, 40, 120
# an input that hands the turn over (`then_wait_ms`): how long the screen may be waited for, and how long it must
# show the same to be at rest (longer than the pause between two things the other side does: at 2 s two of four
# waits ended with two enemies still to act, 2026-11-23)
REST_MS_RANGE, REST_S = (500, 20000), 3.0
# a wait for the screen to change: at most this long, looked at this often
CHANGE_WAIT_MS, CHANGE_LOOK_S = 15000, 0.5
# the game started again from nothing at most this many times a game
RELAUNCHES_MOST = 2
# a tap sent at most this many times at one answer (`repeat`), and the share of the screen a tap of them may
# change and still be followed by the next: more is another scene, which the model looks at first
REPEAT_MOST, REPEAT_SHARE = 8, 0.3
# a story's line moved on by the same input again and again without asking (`advance`; PLAN §27, session T): at most
# this many at one answer, and how many of a choice's options and how much of each text is kept
LINES_MOST, OPTIONS_MOST, OPTION_MOST = 60, 8, 120
# the tags of a page a thing to press has (`pressable`)
PRESSED_TAGS = frozenset({"button", "a", "input", "select", "textarea", "summary"})
# how near a change must be to where a story's lines changed before to be one more line (`within`), in pixels
LINES_NEAR = 12


def _point(value: Any, size: tuple[int, int]) -> list[float] | None:
    if (isinstance(value, list) and len(value) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
            and 0 <= value[0] < size[0] and 0 <= value[1] < size[1]):
        return [float(value[0]), float(value[1])]
    return None


def _ms(value: Any, default: int, least: int, most: int) -> int | None:
    if value is None:
        return default
    return value if type(value) is int and least <= value <= most else None


def prompt(flags: set[str]) -> str:
    """The instructions for a question: `play_direct.md` with each line tagged `[when a b]` kept only when one of
    the names is among `flags` (the inputs the screen takes, `onward`, `levels`, `anew`, `errand`, `elements`,
    `look`, `grid`, `cells`, `board_read`), and each tagged `[unless a b]` only when none is; a line with several
    tags is kept when every one of them keeps it. The tags are taken off. A line with no tag is always given
    (PLAN §27, M7: the part by situation)."""
    kept = []
    for line in (resources.FRAGMENT / "prompts" / "play_direct.md").read_text(encoding="utf-8").split("\n"):
        tagged = _TAGGED.match(line)
        if tagged is None:
            kept.append(line)
        elif all((word == "when") == bool(set(names.split()) & flags) for word, names in _TAG.findall(tagged[2])):
            kept.append(tagged[1] + line[tagged.end():])
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept))


def lines(outline: Any, scale: float = 1.0) -> list[str] | None:
    """What the page says stands in view (`Surface.outline`) as lines for a model, places in the pixels of the
    picture it is shown (`scale`): `x,y WxH tag "text" [label] .classes #colour flags`. None when the page does
    not hold the screen: much of the view is a canvas or the like, it names next to nothing, or more than is
    worth telling."""
    if not isinstance(outline, dict) or outline.get("cut") or (outline.get("drawn") or 0) >= DRAWN_MOST:
        return None
    out = []
    for item in outline.get("items") or []:
        if "covered" in (item.get("flags") or []) and not (item.get("text") or item.get("label")):
            # a box that says nothing and lies under something else (a tile's back, a cell under its piece)
            continue
        (x, y), (width, height) = item["at"], item["size"]
        parts = [f"{round(x * scale)},{round(y * scale)}", f"{round(width * scale)}x{round(height * scale)}"]
        if item.get("tag") not in ("div", "span"):
            parts.append(str(item.get("tag")))
        if item.get("text"):
            parts.append(json.dumps(item["text"], ensure_ascii=False))
        if item.get("label"):
            parts.append(f"[{item['label']}]")
        if item.get("classes"):
            parts.append("." + ".".join(item["classes"]))
        if item.get("bg"):
            parts.append(item["bg"])
        out.append(" ".join(parts + list(item.get("flags") or [])))
    return out if len(out) >= ELEMENTS_LEAST and sum(map(len, out)) <= ELEMENTS_CHARS else None


def words(outline: Any) -> frozenset[str]:
    """The texts the page shows uncovered that are words, numbers left out: a score that counts up is the same
    text, a notice or a result that came up is a new one."""
    found = set()
    for item in (outline.get("items") or []) if isinstance(outline, dict) else []:
        text = re.sub(r"\d+", "", item.get("text") or "").strip()
        if sum(letter.isalpha() for letter in text) >= WORD_LETTERS and "covered" not in (item.get("flags") or []):
            found.add(text)
    return frozenset(found)


def pressable(outline: Any) -> frozenset[tuple[str, int, int]] | None:
    """The things the page shows that can be pressed and that nothing lies over, each by its tag and place: what a
    story's line leaves as it is and a choice, a prompt or another screen does not. None when the page gives no
    account of the screen."""
    if not isinstance(outline, dict) or not isinstance(outline.get("items"), list):
        return None
    return frozenset((str(item.get("tag")), round(item["at"][0] / 8), round(item["at"][1] / 8))
                     for item in outline["items"]
                     if (item.get("tag") in PRESSED_TAGS or item.get("pressed")) and "covered" not in (item.get("flags") or []))


def within(boxes: list[list[int]], ground: list[list[int]]) -> bool:
    """Whether every changed box has its middle inside one of `ground` (where a story's lines changed before)."""
    def inside(box: list[int]) -> bool:
        x, y = box[0] + box[2] / 2, box[1] + box[3] / 2
        return any(left - LINES_NEAR <= x <= left + width + LINES_NEAR and top - LINES_NEAR <= y <= top + height + LINES_NEAR
                   for left, top, width, height in ground)
    return all(inside(box) for box in boxes)


def _choice(value: Any) -> dict[str, Any] | None:
    """A model's account of the choice a story puts on the screen: what is asked, the options, the one it picks,
    and whether the choice lasts (it cannot be taken back). None when it gave none with at least two options."""
    if not isinstance(value, dict) or not isinstance(value.get("options"), list):
        return None
    options = [option.strip()[:OPTION_MOST] for option in value["options"] if isinstance(option, str) and option.strip()]
    if len(options) < 2:
        return None
    asks, picked = value.get("asks"), value.get("picked")
    return {"asks": asks.strip()[:200] if isinstance(asks, str) else "", "options": options[:OPTIONS_MOST],
            "picked": picked.strip()[:OPTION_MOST] if isinstance(picked, str) and picked.strip() else None,
            # anything but a plain "it can be taken back" is a choice that lasts
            "lasting": value.get("lasting") is not False}


def can(kind: Any, kinds: frozenset[str]) -> bool:
    return kind in NEEDS and any(needs in kinds for needs in NEEDS[kind])


def check(action: Any, size: tuple[int, int], kinds: frozenset[str], most: int = SEQUENCE_MOST
          ) -> dict[str, Any] | None:
    """The model's action as one the surface can be given, or None. `most`: how many inputs a sequence may have.
    An input to a place, or a sequence of them, may say how long the screen is to be waited for afterwards
    (`then_wait_ms`, taken into its range)."""
    checked = _check(action, size, kinds, most)
    if checked is not None and (checked["kind"] in SEQUENCED or checked["kind"] == SEQUENCE) \
            and "repeat" not in checked and "advance" not in checked and type(action.get("then_wait_ms")) is int:
        checked["then_wait_ms"] = min(max(action["then_wait_ms"], REST_MS_RANGE[0]), REST_MS_RANGE[1])
    return checked


def _check(action: Any, size: tuple[int, int], kinds: frozenset[str], most: int) -> dict[str, Any] | None:
    if not isinstance(action, dict):
        return None
    kind = action.get("kind")
    if kind in NEEDS and not can(kind, kinds):
        return None
    if kind == TAP and action.get("repeat") is not None:
        tap = recovery.check(action, size, kinds)
        return {**tap, "repeat": action["repeat"]} if tap is not None and type(action["repeat"]) is int \
            and 2 <= action["repeat"] <= REPEAT_MOST else None
    if kind in (TAP, KEY) and action.get("advance") is True:
        plain = _check({name: value for name, value in action.items() if name not in ("advance", "repeat")}, size, kinds, most)
        if plain is None:
            return None
        # where the story's lines are shown, when the model says: a change there is one more line
        box = action.get("lines")
        shown = isinstance(box, list) and len(box) == 4 and all(type(part) in (int, float) for part in box) \
            and box[2] > 0 and box[3] > 0 and _point(box[:2], size) is not None
        return {**plain, "advance": True, **({"lines": [float(part) for part in box]} if shown else {})}
    if kind in (TAP, SWIPE):
        return recovery.check(action, size, kinds)
    if kind == WAIT and action.get("until_change") is True:
        return {"kind": WAIT, "ms": action["ms"], "until_change": True} \
            if type(action.get("ms")) is int and 0 < action["ms"] <= CHANGE_WAIT_MS else None
    if kind == WAIT:
        return recovery.check(action, size, kinds)
    if kind in (DOUBLE_TAP, RIGHT_TAP) and (at := _point(action.get("at"), size)) is not None:
        return {"kind": kind, "at": at}
    if kind == LONG_PRESS and (at := _point(action.get("at"), size)) is not None \
            and (ms := _ms(action.get("ms"), LONG_PRESS_MS, *LONG_PRESS_RANGE)) is not None:
        return {"kind": kind, "at": at, "ms": ms}
    if kind == DRAG and isinstance(action.get("path"), list) and 2 <= len(action["path"]) <= PATH_MOST:
        path = [_point(point, size) for point in action["path"]]
        ms, hold = _ms(action.get("ms"), DRAG_MS, *DRAG_MS_RANGE), _ms(action.get("hold_ms"), HOLD_MS, 0, HOLD_MS_MOST)
        if None not in path and ms is not None and hold is not None:
            # carried: the path's end is where the middle of what is picked up goes, not the finger
            carried = {"carry": True} if action.get("carry") is True and len(path) == 2 else {}
            return {"kind": kind, "path": path, "ms": ms, "hold_ms": hold, **carried}
        return None
    if kind == SCROLL and (at := _point(action.get("at"), size)) is not None and type(action.get("steps")) is int \
            and 0 < abs(action["steps"]) <= SCROLL_MOST:
        return {"kind": kind, "at": at, "steps": action["steps"]}
    if kind in (BACK, RELAUNCH):
        return {"kind": kind}
    if kind == SEQUENCE and isinstance(action.get("inputs"), list) and 2 <= len(action["inputs"]) <= most:
        inputs = [_check(part, size, kinds, most) if isinstance(part, dict) and part.get("kind") in SEQUENCED else None
                  for part in action["inputs"]]
        return {"kind": kind, "inputs": inputs} if None not in inputs else None
    if kind == KEY and (action.get("key") in KEYS or (isinstance(action.get("key"), str) and len(action["key"]) == 1
                                                      and action["key"] in TYPED)):
        return {"kind": KEY, "key": action["key"]}
    if kind == TEXT and isinstance(action.get("text"), str) and 0 < len(action["text"]) <= TEXT_MOST \
            and set(action["text"]) <= TYPED:
        return {"kind": TEXT, "text": action["text"]}
    if kind == DONE and action.get("end") in ENDS:
        return {"kind": DONE, "end": action["end"], **recovery.left_of(action), **_world(action)}
    if kind == OUT_OF_LIVES:
        return {"kind": OUT_OF_LIVES, "minutes": recovery.minutes(action.get("minutes"))}
    if kind in (GIVE_UP, LOOK):
        return {"kind": kind}
    if kind == STOP:
        return {"kind": STOP, **_world(action)}
    return None


def _world(action: dict[str, Any]) -> dict[str, Any]:
    """What an answer that ends an errand read of the game's lasting state (`world`): the seconds until the errand
    can be done again, and what the game counts of the things it holds."""
    seconds, have = world.again(action.get("again_in_s")), world.held(action.get("have"))
    return {**({"again_in_s": seconds} if seconds is not None else {}), **({"have": have} if have else {})}


uses = recovery.uses


def _brief(told: dict[str, Any]) -> dict[str, Any]:
    """An action as the history tells it: a press aimed at a cell by the cell alone."""
    if "inputs" in told:
        return {**told, "inputs": [_brief(part) for part in told["inputs"]]}
    return {name: value for name, value in told.items() if name != "at"} if "cell" in told else told


def _celled(action: dict[str, Any], said: Any) -> dict[str, Any]:
    """A checked action as the model is told it afterwards: a press it aimed at a cell keeps the cell."""
    told = dict(action)
    if not isinstance(said, dict):
        return told
    if action["kind"] == SEQUENCE and isinstance(said.get("inputs"), list) and len(said["inputs"]) == len(action["inputs"]):
        told["inputs"] = [_celled(part, other) for part, other in zip(action["inputs"], said["inputs"])]
    elif "at" in action and isinstance(said.get("cell"), list):
        told["cell"] = said["cell"]
    return told


def scaled(action: dict[str, Any], scale: float) -> dict[str, Any]:
    """The action with every point in it divided by `scale`: from the picture the model saw to the screen."""
    def point(value: list[float]) -> list[float]:
        return [part / scale for part in value]
    out = dict(action)
    for key in ("at", "from", "to"):
        if key in out:
            out[key] = point(out[key])
    if "path" in out:
        out["path"] = [point(value) for value in out["path"]]
    if "lines" in out:
        out["lines"] = point(out["lines"])
    if "inputs" in out:
        out["inputs"] = [scaled(part, scale) for part in out["inputs"]]
    return out


async def send(surface: Any, action: dict[str, Any], carrier: Any = None, before: bytes | None = None
               ) -> dict[str, Any] | None:
    """Send an action; a carried drag gives what was measured of it. `before`: the screen the action was chosen
    on, when nothing was done since (a carried drag finds what it picked up against it)."""
    kind = action["kind"]
    if kind == TAP:
        await surface.tap(*action["at"], TAP_MS)
    elif kind == DOUBLE_TAP:
        await surface.taps(*action["at"], 2, DOUBLE_TAP_GAP_MS)
    elif kind == LONG_PRESS:
        await surface.tap(*action["at"], action["ms"])
    elif kind == RIGHT_TAP:
        await surface.right_tap(*action["at"])
    elif kind == KEY:
        await surface.key(action["key"])
    elif kind == TEXT and "text" in surface.kinds:
        await surface.type(action["text"])
    elif kind == TEXT:
        for letter in action["text"]:
            await surface.key(letter)
    elif kind == SWIPE:
        await surface.drag(*action["from"], *action["to"], SWIPE_MS)
    elif kind == DRAG and action.get("carry") and carrier is not None and "hold" in surface.kinds:
        (start, goal) = action["path"]
        return await carrier.carry(tuple(start), tuple(goal), action["ms"], action["hold_ms"] / 1000, before=before)
    elif kind == DRAG:
        await surface.stroke(action["path"], action["ms"], action["hold_ms"])
    elif kind == SCROLL:
        await surface.scroll(*action["at"], action["steps"])
    elif kind == BACK:
        await surface.back()
    elif kind == RELAUNCH:
        await surface.relaunch()
    elif kind == SEQUENCE:
        for index, part in enumerate(action["inputs"]):
            if index:
                await asyncio.sleep(SEQUENCE_GAP_S)
            await send(surface, part, carrier, before if index == 0 else None)
    elif kind == WAIT and action.get("until_change"):
        look = _looker(surface)
        before = previous = await look(None)
        moving = list(getattr(surface, "moving", []))
        deadline = time.monotonic() + action["ms"] / 1000
        while time.monotonic() < deadline:
            previous = await look(previous.at + CHANGE_LOOK_S)
            moving = motion.joined(moving + list(getattr(surface, "moving", [])))
            if motion.outside(carry._boxes(before.png, previous.png), moving):
                return None
    elif kind == WAIT:
        await asyncio.sleep(action["ms"] / 1000)
    return None


def _looker(surface: Any) -> Any:
    """How the screen is looked at no earlier than a time: drawn through what keeps moving on it when the surface
    can (`motion.Steady`), else a frame as it is."""
    steady = getattr(surface, "steady", None)
    return steady if callable(steady) else frames.source_of(surface).frame


async def settled(surface: Any) -> bytes:
    """The screen once an input's effect is over: the same twice in a row, or as it is at the time limit.
    What keeps moving all the while (`motion.Steady`) is no effect still going on."""
    look = _looker(surface)
    previous = await look(time.monotonic() + SETTLE_S)
    moving = list(getattr(surface, "moving", []))
    deadline = time.monotonic() + STILL_TIMEOUT_S
    while time.monotonic() < deadline:
        seen = await look(previous.at + STILL_GAP_S)
        moving = motion.joined(moving + list(getattr(surface, "moving", [])))
        if not motion.outside(carry._boxes(previous.png, seen.png), moving):
            return seen.png
        previous = seen
    return previous.png


async def rested(surface: Any, ms: int) -> bytes:
    """The screen once it came to rest after an input that handed the turn over: the same for `REST_S` on end
    from now (what keeps moving all the while left out), or as it is `ms` after this was asked."""
    look = _looker(surface)
    deadline = time.monotonic() + ms / 1000
    previous = await look(None)
    still_since = time.monotonic()
    moving = list(getattr(surface, "moving", []))
    while time.monotonic() < deadline and time.monotonic() - still_since < REST_S:
        seen = await look(previous.at + CHANGE_LOOK_S)
        moving = motion.joined(moving + list(getattr(surface, "moving", [])))
        if motion.outside(carry._boxes(previous.png, seen.png), moving):
            still_since = time.monotonic()
        previous = seen
    return previous.png


def _known(said: Any) -> dict[str, str]:
    """What a model said named things of the game do, as it is kept: short names with short texts."""
    if not isinstance(said, dict):
        return {}
    return {name.strip()[:KNOWN_NAME]: text.strip()[:KNOWN_TEXT] for name, text in said.items()
            if isinstance(name, str) and isinstance(text, str) and name.strip() and text.strip()}


class Direct:
    """One game played by a model's own inputs."""

    def __init__(self, model: Model, *, hint: str | None = None, accept_consent: bool = False,
                 refused: dict[str, int] | None = None, notes: Any = "", found: dict[str, Any] | None = None,
                 looking: bool = False, looked: dict[str, Any] | None = None) -> None:
        self.model, self.hint, self.accept_consent = model, hint, accept_consent
        # what was found out of the game before it was played (`research`), whether the model is still to look at
        # the game's own help before the first move, and the questions and tokens spent so (`research.QUESTIONS_MOST`;
        # shared with the questions that got to the game, which may have looked first: `recovery.Recovery`)
        self.found: dict[str, Any] = found if found is not None else {}
        self.looked = looked if looked is not None else research.fresh_looked()
        self.looking = looking and self.looked["done"] is None
        # what the model wants kept from one question to the next (`MEMORY`; a plain text is its plan), and what
        # it last said the screen shows
        self.memory: dict[str, str] = {}
        # what named things of the game do, as the model read it of them (`KNOWN_MOST`; the last read kept longest),
        # and how many of them were read in this work
        self.known: dict[str, str] = {}
        self.learned = 0
        self._remember(notes if isinstance(notes, dict) else {PLAN: notes} if isinstance(notes, str) else {})
        self._learn(notes.get("known") if isinstance(notes, dict) else None)
        # whether the memory given came from an earlier work (its `run` is then to be held against the screen)
        self.earlier = bool(self.memory.get("run"))
        self.situation = ""
        # the kind of screen it last named (`scenes`), and whether it said the game forces one input there
        self.scene: str | None = None
        self.guided = False
        # the score it read on the first screen that showed one, and on the last
        self.first_score: float | None = None
        self.score: float | None = None
        self.history: list[dict[str, Any]] = []
        self.questions = 0
        # times the game was started again from nothing at the model's word
        self.relaunches = 0
        # the shortest press before a drag moves that this game takes, once a drag showed it (ms)
        self.hold_ms = 0
        # the model asked for the screen at full size where the page's text came with a small picture (`LOOK`)
        self.sharp = False
        # the board of cells the model described, read by code from then on (`lattice`; one kept by an earlier work
        # is read again when it still stands); whether the grid it last described read as no board; and the
        # questions given the board as text, and of those the ones given the shorter instructions
        self.lattice: Lattice | None = Lattice.restore(notes.get("grid")) if isinstance(notes, dict) else None
        self.grid_refused = False
        self.texted = self.plain = 0
        # inputs not sent because they used what the person did not allow, by what they would have used (the
        # game's own account when it gives one)
        self.refused: dict[str, int] = refused if refused is not None else {}
        # the rules of the game's cards and the turn of a fight read and searched by code (PLAN §27 M10, §36 E7)
        self.fight = fight.Fight(notes.get("card_rules") if isinstance(notes, dict) else None)

    async def ask(self, png: bytes, kinds: frozenset[str], goal: dict[str, Any], left: dict[str, Any],
                  moving: list[list[int]] | None = None, onward: bool = False,
                  been_here: dict[str, Any] | None = None, anew: bool = False,
                  errand: str | None = None, began: bytes | None = None, outline: Any = None,
                  counting: int | None = None) -> dict[str, Any] | None:
        """The model's answer for the screen as it is: `{"action", "expect", "uses", "scene", "guided"}` with the
        action in the screen's pixels, or None when it gave none the surface can be given or one that uses what
        `goal` does not allow. `moving`: where something kept moving over the screen, left out of the picture.
        `onward`: more levels are wanted and none was played in this part of the work yet: whatever the screen
        shows, the next level is to be reached and played (`agent.Agent.onward`). `been_here`: play left a
        screen like this one before (`scenes.Trail`). `anew`: a game from its start was asked for and nothing was
        played here yet: a game the screen shows ended was played before (`agent.Agent.anew`). `errand`: the one
        thing beside the levels the person asked for, by its plain name (`agent.Agent.errand`): that is the work,
        and it ends when it is done. `began`: the screen as the work found it, shown beside this one for the
        model to tell what the work itself did from what stood there before (an errand's end has no other check).
        `outline`: what the page under the picture says stands in view (`Surface.outline`), when it can be read:
        given as text (`lines`) with the picture shown smaller, until the model asks for it whole (`LOOK`).
        `counting`: the work counts levels up to one by the number the game shows, and this part began where it
        showed this one (`work`, `until_level`): the model is asked for the number the screen shows (`progress`),
        given back with the answer (PLAN §36, E5)."""
        read = lines(outline) is not None
        # the board's cells as the code reads them, where the model described a grid and it stands on this screen
        known = len(self.lattice.kinds) if self.lattice is not None else 0
        rows = self.lattice.read(png) if self.lattice is not None and not read else None
        # every kind it shows has a name: the letters say all the picture would, which is then shown smaller
        named = rows is not None and not self.lattice.unnamed(rows)
        # and the board stands as it was told before, on a screen the model called a board: nothing else is here
        plain = named and self.scene == scenes.BOARD and len(self.lattice.kinds) == known
        small = (read or named) and not self.sharp
        shown, scale = recovery.view(png, SMALL_SIDE if small else recovery.VIEW_SIDE)
        size = recovery._size(shown)
        offered = sorted(kind for kind in NEEDS if can(kind, kinds)
                         and (kind != RELAUNCH or self.relaunches < RELAUNCHES_MOST)) + [WAIT] \
            + ([LOOK] if small and scale != 1.0 else [])
        # a grid is asked for where the screen is a picture alone that can be pressed and no board is read on it
        gridded = rows is None and not read and can(TAP, kinds)
        refused, self.grid_refused = self.grid_refused, False
        self.texted, self.plain = self.texted + (rows is not None), self.plain + plain
        looking = self.looking and self.looked["questions"] < research.QUESTIONS_MOST
        given = research.told(self.found)
        told_moving = [[round(part * scale) for part in box] for box in moving or []]
        asked = {"play": True, "goal": goal, "left": left, "screen": list(size),
                 **({"moving": told_moving} if told_moving else {}),
                 **({"drag_hold_ms_at_least": self.hold_ms} if self.hold_ms else {}),
                 "inputs": offered, **({"elements": lines(outline, scale)} if read else {}),
                 **({"cells": self.lattice.told(rows, scale)} if rows is not None else {}),
                 **({"grid_refused": "the grid you described did not read as a board of few kinds of cell (its box "
                                     "or its counts were off, or it is no such board)"} if refused and gridded else {}),
                 "keys": sorted(KEYS), "accept_consent": self.accept_consent, "meta_actions": META_ACTIONS,
                 "scenes": scenes.KINDS,
                 **({"onward": True} if onward else {}),
                 **({"counting": {"at": counting, "what": "the number the game counts its progress by: a level, "
                                  "stage, floor or day"}} if counting is not None else {}),
                 **({"anew": True} if anew else {}),
                 **({"errand": errand} if errand else {}),
                 **({"began": "second image"} if began else {}),
                 **({"been_here": been_here} if been_here else {}), "memory": self.memory, "memory_parts": MEMORY,
                 **({"known": self.known} if self.known else {}),
                 **({"research": given} if given else {}),
                 **({"look_first": research.look_first(self.looked)} if looking else {}),
                 **({"run_from_earlier_work": True} if self.earlier and not self.history else {}),
                 "history": self.history[-HISTORY_KEPT:], **({"hint": self.hint} if self.hint else {})}
        image = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                              "data": base64.b64encode(shown).decode("ascii")}}
        first = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                              "data": base64.b64encode(recovery.view(began)[0]).decode("ascii")}}] if began else []
        flags = set(offered) | {name for name, on in (
            ("onward", onward), ("levels", goal.get("objective") == "levels"), ("anew", anew), ("errand", errand),
            ("elements", read), ("cells", rows is not None), ("grid", gridded), ("board_read", plain),
            ("counting", counting is not None), ("research", given is not None), ("looking", looking)) if on}
        self.questions += 1
        result = await self.model.complete(TurnRequest(
            system_stable=prompt(flags), name="play_direct", max_tokens=PLAY_TOKENS, timeout_s=PLAY_TIMEOUT_S,
            messages=[Msg("user", [{"type": "text", "text": json.dumps(asked)}, image, *first])]))
        if looking:
            self.looked["questions"] += 1
            self.looked["tokens"] += result.usage.input_tokens
        try:
            answer = json.loads(result.text or "")
        except ValueError:
            return None
        if not isinstance(answer, dict):
            return None
        researched = looking and self._looked(answer)
        if isinstance(answer.get("situation"), str):
            self.situation = answer["situation"][:300]
        if isinstance(answer.get("memory"), dict):
            self._remember(answer["memory"])
        elif isinstance(answer.get("notes"), str):
            self._remember({PLAN: answer["notes"]})
        self.learned += self._learn(answer.get("learned"))
        self.scene, self.guided = scenes.kind(answer.get("scene")), answer.get("guided") is True
        if type(answer.get("score")) in (int, float):
            self.score = answer["score"]
            self.first_score = self.score if self.first_score is None else self.first_score
        if isinstance(answer.get("action"), dict) and answer["action"].get("advance") is True and "lines" in answer:
            # where the lines are shown, given beside the action (as a model did, 2026-11-21)
            answer["action"].setdefault("lines", answer["lines"])
        if rows is not None:
            self.lattice.name(answer.get("names"))
            # a press aimed at a cell is one at its middle
            answer["action"] = self._aimed(answer.get("action"), scale)
        if "grid" in answer and (gridded or rows is not None):
            self._grid(png, answer["grid"], scale, rows is not None)
        action = check(answer.get("action"), size, kinds, SEQUENCE_MOST if self.scene != scenes.BOARD else
                       CELL_MOVES_MOST if rows is not None else MOVES_MOST)
        used = uses(answer.get("action"))
        if action is None or used is None or action["kind"] not in offered + [DONE, OUT_OF_LIVES, GIVE_UP, STOP]:
            return None
        if action["kind"] == LOOK:
            self.sharp = True
        # a booster the game hands over for nothing and will not go on without is used unasked (`FORCED_FREE`)
        forced = answer["action"].get("forced_free") is True and bool(used) and set(used) <= FORCED_FREE
        refused = [name for name in used if name not in set(goal.get("allowed_meta_actions") or ()) and not forced]
        if refused:
            # not sent: the model is told so with its next question, as it is told what any input did
            self.history.append({"action": action, "expected": answer.get("expect"), "screen_changed": False,
                                 "refused": f"not sent: the person did not allow {', '.join(refused)}"})
            for name in refused:
                self.refused[name] = self.refused.get(name, 0) + 1
            return None
        self.relaunches += action["kind"] == RELAUNCH
        if action["kind"] == DRAG and action["hold_ms"] < self.hold_ms:
            action["hold_ms"] = self.hold_ms
        told = _celled(action, answer["action"]) if rows is not None else dict(action)
        if scale != 1.0:
            action = scaled(action, scale)
        expect = answer["expect"][:200] if isinstance(answer.get("expect"), str) else ""
        because = answer["action"].get("because") if action["kind"] == STOP else None
        made = recovery.profile(answer["action"]) if action["kind"] in (TAP, TEXT, KEY, SEQUENCE) else None
        chosen = _choice(answer.get("choice")) if action["kind"] in NEEDS or action["kind"] == STOP else None
        progress = answer.get("progress")
        self.looked["inputs"] += researched
        return {**({"fight": True} if answer.get("fight_turn") is True and self.scene == scenes.BOARD else {}),
                **({"research": True} if researched else {}), **({"progress": progress} if type(progress) is int and progress >= 1 else {}),
                **({"choice": chosen} if chosen is not None else {}), "action": action, "told": told, "expect": expect, "scale": scale, "uses": used + (["forced_free"] if forced else []),
                "scene": self.scene, "guided": self.guided,
                **({"because": because[:200]} if isinstance(because, str) else {}),
                **({"profile": made} if made is not None else {})}

    def saw(self, answer: dict[str, Any], before: bytes, after: bytes, moving: list[list[int]] | None = None,
            carried: dict[str, Any] | None = None, repeated: dict[str, Any] | None = None) -> bool:
        """What the code saw of an input, kept for the next question: whether the screen changed and where
        (not counting where something kept moving all along), and for a carried drag where the thing held
        was drawn from the finger. `repeated`: a tap sent again unasked, how many times it was sent in all and
        why the last was the last; `before` is then the screen its last sending was made on."""
        scale = answer["scale"]
        places = sorted(motion.outside(carry._boxes(before, after), moving or []),
                        key=lambda box: -box[2] * box[3])[:PLACES_TOLD]
        entry = {"action": _brief(answer["told"]), "expected": answer["expect"], **({"uses": answer["uses"]} if answer.get("uses") else {}),
                 **({"scene": answer["scene"]} if answer.get("scene") else {}),
                 "screen_changed": bool(places),
                 "changed_at": [[round(part * scale) for part in box] for box in places],
                 **({"repeated": repeated} if repeated else {})}
        if self.lattice is not None and (cells := self.lattice.changed(before, after)) is not None:
            # the board stood either side of the input: what it changed is told by cells
            entry["cells_changed"] = cells
        if carried:
            # whether the thing was seen held on the way, the press it was given, and how far its drawing's middle
            # was from where it was to land when the finger let go (when it was seen then)
            entry["picked_up"], entry["held_ms"] = bool(carried.get("picked")), round(carried.get("hold_s", 0) * 1000)
            if carried.get("off") is not None:
                entry["landed_off_by"] = [round(part * scale) for part in carried["off"]]
        self.history.append(entry)
        if entry.get("picked_up") and any(old.get("picked_up") is False and old["held_ms"] < entry["held_ms"]
                                          for old in self.history[:-1]):
            # nothing was picked up at a shorter press and something was at this one: this game wants the press
            self.hold_ms = max(self.hold_ms, entry["held_ms"])
        return bool(places)

    def _grid(self, png: bytes, said: Any, scale: float, told: bool) -> None:
        """What the model said of the board's grid: a box with its rows and columns is read as a board from now on
        when it reads as one of few kinds of cell (else the model is told it did not, and a board read before is
        kept); null in answer to a board given as text lets that board go."""
        if said is None:
            if told:
                self.lattice = None
            return
        found = Lattice.said(png, said, scale)
        if found is None:
            self.grid_refused = True
            return
        old = self.lattice
        if old is not None and (old.rows, old.cols) == (found.rows, found.cols) \
                and all(abs(one - other) <= SAME_GRID for one, other in zip(old.box, found.box)):
            return                      # the same board said again: its kinds and their names stay
        self.lattice = found

    def _aimed(self, action: Any, scale: float) -> Any:
        """The action with each press aimed at a cell of the board (`cell`) aimed at that cell's middle in the
        picture the model was shown; the cell is kept beside it."""
        if not isinstance(action, dict):
            return action
        if action.get("kind") == SEQUENCE and isinstance(action.get("inputs"), list):
            return {**action, "inputs": [self._aimed(part, scale) for part in action["inputs"]]}
        if action.get("kind") in (TAP, DOUBLE_TAP, LONG_PRESS) and "at" not in action \
                and (at := self.lattice.at(action.get("cell"), scale)) is not None:
            return {**action, "at": at}
        return action

    def _looked(self, answer: dict[str, Any]) -> bool:
        """Whether the model's input is one of looking at the game's own help rather than of play
        (`research.looked_at`)."""
        marked = research.looked_at(answer, self.found, self.looked, scenes.kind(answer.get("scene")) == scenes.BOARD)
        self.looking = self.looked["done"] is None
        return marked

    def _remember(self, said: dict[str, Any]) -> None:
        """The parts of the memory the model gave take the place of what they held; a part it left out stays."""
        for part in MEMORY:
            if isinstance(said.get(part), str):
                self.memory[part] = said[part].strip()[:MEMORY_MOST]
        self.memory = {part: text for part, text in self.memory.items() if text}

    def _learn(self, said: Any) -> int:
        """What the model read of named things is kept by their names, the oldest dropped beyond `KNOWN_MOST`;
        how many were new or read otherwise than before."""
        new = {name: text for name, text in _known(said).items() if self.known.get(name) != text}
        for name, text in new.items():
            self.known.pop(name, None)
            self.known[name] = text
        for name in list(self.known)[:max(0, len(self.known) - KNOWN_MOST)]:
            del self.known[name]
        return len(new)

    @property
    def notes(self) -> str:
        """The memory as one text, for the record."""
        return "\n".join(f"{part}: {text}" for part, text in self.memory.items())[:NOTES_MOST]

    def lasting(self) -> dict[str, str]:
        """What of the memory outlasts the work: all but the plan, what named things do, and the board of cells
        with the names of its kinds, when the model was asked at all."""
        if not self.questions:
            return {}
        return {**{part: text for part, text in self.memory.items() if part != PLAN},
                **({"known": dict(self.known)} if self.known else {}),
                **({"card_rules": self.fight.kept()} if self.fight.rules else {}),
                **({"grid": self.lattice.kept()} if self.lattice is not None and self.lattice.names else {})}

    def replayed(self, told: dict[str, Any], scene: str | None, changed: bool) -> None:
        """An input the code sent itself, as a step of the game's routine (`routine`), kept for the next question
        as the model's own are: `told` is the input in the pixels of the picture the model is shown."""
        self.history.append({"action": told, "sent_by": "program", **({"scene": scene} if scene else {}),
                             "screen_changed": changed})

    def summary(self) -> dict[str, Any]:
        return {"questions": self.questions, "score_first": self.first_score, "score_last": self.score,
                "notes": self.notes, "memory": {**self.memory, **({"known": dict(self.known)} if self.known else {})},
                **({"learned": self.learned} if self.learned else {}),
                **({"lattice": {**self.lattice.summary(), "told": self.texted, "plain": self.plain}}
                   if self.lattice is not None else {}),
                "situation": self.situation, "scene": self.scene,
                **({"refused": dict(self.refused)} if self.refused else {}),
                **({"looked": dict(self.looked)} if self.looked["questions"] else {}),
                **({"fight": self.fight.summary()} if self.fight.account["reads"] else {})}
