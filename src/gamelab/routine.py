"""Play a model made by its own inputs, kept to be made again by code (PLAN §28).

Between two levels a game shows much the same screens every time (a result, the map, a start card), and a model
asked at each answers much the same input. Each input the model sent on a screen that is no board is kept as a
step: a small picture of the screen it was sent on, a closer one of the place it pressed, and the input. When a
screen like it stands there again, with the place looking as it did, the code sends the input and no model is
asked.

    the check    before a step is sent: the whole screen is like the one the model chose the input on, and the
                 place pressed looks as it did (most of it: a button whose number changed is the same button).
                 A map or home screen may look less like it than another screen must, since what is behind its
                 buttons moves. After the step: the screen changed
    where it     no step fits the screen (a notice this level put in the way): the model is asked from there on,
    breaks       and what it answers is kept as a step too. A step whose input changed nothing is left out for
                 the rest of the stretch and, when that happens again, kept no longer; the model's answer on that
                 screen takes its place
    waiting      a screen the model only waited on (loading) is kept as one to wait on: the code looks again
                 until another screen stands there
    the end      the step after which a level's board was read is marked (`opens`): after it the family is
                 tried on the screen without the model being asked what the screen is

What is never kept: an input on a level's board or in a puzzle beside the levels (it depends on the position), an
input that made a name or profile, typed text, and any input that uses up something of the player's (a booster,
coins, another try at a lost level): whether to spend is the model's to say each time, on the screen as it is.

A step is kept only when the stretch it was learned in got where it was going: a level's board was read, or a
move made on one. Until then it is on trial (it may be sent again within the stretch), and a stretch that ends
anywhere else (the model stopped, gave up, went round) leaves none of its new steps behind: what the model pressed
on the way to nowhere is not pressed again by code (2026-11-19: a stretch that ended at a stop had left two taps
that spent the game's energy).

A step is sent once in a stretch: play that comes back to a screen it left by that step is the model's to look at
(it went round). The exceptions are a step the model itself asked to be repeated (a story's lines) and one the
model sent more than once in a stretch on screens alike (a reward's pages), which may be sent that often.

A routine is the steps of one task (what a stretch of play is for: the next level, a new game); a game's routines
are a book, kept by the game (`adaptation.Store.routines`). The same steps serve wherever in the stretch play
stands, since a step is found by the screen and not by its place in an order.
"""
from __future__ import annotations

from io import BytesIO
from typing import Any

from . import direct, recovery, scenes

# the tasks a stretch of the model's own play may be for (`agent.Agent._task`)
NEXT_LEVEL, NEW_GAME = "next_level", "new_game"
# what a step may send: an input to a place, a key, the back button; and a wait, which is a screen to wait on
SENT = frozenset({direct.TAP, direct.DOUBLE_TAP, direct.LONG_PRESS, direct.KEY, direct.SWIPE, direct.BACK})
# the kinds of screen a step is kept for: not a level's board, not a puzzle beside the levels
SCENES = frozenset({scenes.RESULT, scenes.MAP, scenes.POPUP, scenes.TUTORIAL, scenes.STORY, scenes.LOADING})
# how far the screen's small picture (`recovery.signature`, of 255) may be from a step's for the step to fit, and
# how far the pressed place's. Set from one game's screens (PLAN §28, session D, 2026-11-19): two notices over a
# dimmed screen were 25 to 35 apart and one notice at two times (the picture in it another) 2.6; a map seen from
# two places was 21 apart, a map and anything else 36 or more. So a step of a map fits a screen within
# MAP_DISTANCE, any other within SCREEN_DISTANCE
SCREEN_DISTANCE, MAP_DISTANCE, SPOT_DISTANCE = 12.0, 30.0, 6.0
# the place pressed: a square this share of the screen's longer side around the point (a button, little of what is
# around it), as this many grey levels a side. Two places are as far apart as the nearest SPOT_ALIKE of their cells
# are on average: a button whose label changed (a level's number) is the same button, 0 to 2.3 in that game,
# where another button in the same place was 14 or more
SPOT_SHARE, SPOT_SIDE, SPOT_ALIKE = 0.06, 12, 0.7
# two inputs of one kind this near (the share of the screen's longer side) are the same input
SAME_PLACE = 0.03
# a step is sent this often in one stretch, unless the model sent it more often itself (`again`); one the model
# asked to be repeated (a story's lines), this often
STEP_MOST, REPEATED_MOST = 1, direct.REPEAT_MOST
# a screen to wait on is waited on for a few times as long as the model waited there, within these bounds (seconds):
# a board still drawing that a model once waited a moment on is no loading screen to sit out
PATIENCE_TIMES, PATIENCE_LEAST, PATIENCE_MOST = 3, 3.0, 30.0
# the steps one stretch may send in all before the model looks again, and the times a step may change nothing
# before it is kept no longer
STRETCH_MOST, FAILS_MOST = 24, 2
# the steps kept of one task; the least lately used go first
KEPT_MOST = 48
# what is told of the looks a stretch took (`Routine.looks`)
LOOKS_KEPT = 120


def _grey(png: bytes) -> Any:
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        return image.convert("L")


def _whole(grey: Any) -> list[int]:
    """The screen's small picture, as `recovery.signature` makes it."""
    return list(grey.resize((recovery.SIGNATURE_SIDE, recovery.SIGNATURE_SIDE)).tobytes())


def _spot(grey: Any, point: list[float]) -> list[int]:
    half = max(grey.size) * SPOT_SHARE / 2
    box = (round(point[0] - half), round(point[1] - half), round(point[0] + half), round(point[1] + half))
    return list(grey.crop(box).resize((SPOT_SIDE, SPOT_SIDE)).tobytes())


def spot(png: bytes, point: list[float]) -> list[int]:
    """The place around a point of the screen as a small grey picture."""
    return _spot(_grey(png), point)


def apart(one: list[int], other: list[int]) -> float:
    """How far two places are from looking the same: the average difference of the cells that differ least."""
    if len(one) != len(other):
        return 255.0
    nearest = sorted(abs(a - b) for a, b in zip(one, other))[:max(1, round(len(one) * SPOT_ALIKE))]
    return sum(nearest) / len(nearest)


def reach(scene: str | None) -> float:
    """How far a screen may be from a step's for the step to fit it, by the kind of screen the step is of."""
    return MAP_DISTANCE if scene == scenes.MAP else SCREEN_DISTANCE


def place(action: dict[str, Any]) -> list[float] | None:
    """Where an input presses the screen: None for one that presses nowhere (a key, the back button, a wait)."""
    return action.get("at") or action.get("from")


def kept(action: dict[str, Any], scene: str | None, answer: dict[str, Any]) -> bool:
    """Whether an input the model sent is one a step is made of."""
    if scene not in SCENES or answer.get("profile") or answer.get("uses"):
        return False
    return action["kind"] in SENT or action["kind"] == direct.WAIT


class Routine:
    """The steps kept of one task, and what was done with them in this game."""

    def __init__(self, task: str, steps: list[dict[str, Any]] | None = None) -> None:
        self.task = task
        self.steps = [step for step in steps or [] if _sound(step)]
        self.began = len(self.steps)
        # the screens the steps were learned on since they were last written down, by step id
        self.pictures: dict[str, bytes] = {}
        self.serial = max((step.get("used", 0) for step in self.steps), default=0)
        self.number = max((int(step["id"][1:]) for step in self.steps if step["id"][1:].isdigit()), default=0)
        # each time a screen was looked at for a step: which fitted, or how far the nearest was
        self.looks: list[dict[str, Any]] = []
        self.sent = self.failed = self.learned = self.waited = self.dropped = 0

    def find(self, png: bytes, *, avoid: Any = (), at: int = 0) -> dict[str, Any] | None:
        """The step that fits the screen: among those whose screen and place are near enough, the one whose
        place is nearest; a screen to wait on only when no input fits. `avoid`: ids of steps not to be sent now."""
        grey = _grey(png)
        seen = _whole(grey)
        best: tuple[bool, float, float, dict[str, Any]] | None = None
        nearest: tuple[float, float | None, str] | None = None
        for step in self.steps:
            far = recovery._distance(step["signature"], seen)
            point = place(step["action"])
            near = apart(step["spot"], _spot(grey, point)) if point is not None and step.get("spot") else None
            if nearest is None or far < nearest[0]:
                nearest = (far, near, step["id"])
            if far > reach(step.get("scene")) or (near or 0.0) > SPOT_DISTANCE or step["id"] in avoid:
                continue
            rank = (step["action"]["kind"] == direct.WAIT, near or 0.0, far)
            if best is None or rank < best[:3]:
                best = (*rank, step)
        look = {"at_action": at, "step": best[3]["id"] if best else None}
        if best is not None:
            look.update(screen=round(best[2], 1), spot=round(best[1], 1), kind=best[3]["action"]["kind"])
        elif nearest is not None:
            look["nearest"] = {"step": nearest[2], "screen": round(nearest[0], 1),
                               **({"spot": round(nearest[1], 1)} if nearest[1] is not None else {})}
        if self.looks and self.looks[-1]["at_action"] == at and self.looks[-1]["step"] == look["step"]:
            # the same screen looked at again (a wait, a scene on its way): the last look of it is the one told
            self.looks[-1] = look
        else:
            self.looks.append(look)
        return best[3] if best else None

    def used(self, step: dict[str, Any], changed: bool) -> None:
        """A step was sent: whether the screen changed after it. One that changed nothing FAILS_MOST times, with
        nothing it changed between, is kept no longer."""
        self.serial += 1
        step["used"] = self.serial
        if step["action"]["kind"] == direct.WAIT:
            self.waited += 1
            return
        self.sent += 1
        if changed:
            step["sent"], step["failed"] = step.get("sent", 0) + 1, 0
            return
        self.failed += 1
        step["failed"] = step.get("failed", 0) + 1
        if step["failed"] >= FAILS_MOST:
            self.steps = [other for other in self.steps if other is not step]
            self.pictures.pop(self._name(step), None)

    def learn(self, png: bytes, action: dict[str, Any], *, scene: str | None, sends: dict[str, int] | None = None
              ) -> dict[str, Any]:
        """The input a model sent on this screen kept as a step, and given back. A step already kept for a screen
        like it that sends the same input is that step (`sends`: how often each step was sent in this stretch
        before: one sent again by the model may be sent that often by code); a step of that screen whose input
        changed nothing gives way to this one."""
        seen, point = recovery.signature(png), place(action)
        wanted = {key: value for key, value in action.items()}
        self.serial += 1
        for step in self.steps:
            if recovery._distance(step["signature"], seen) > reach(step.get("scene")):
                continue
            if _same(step["action"], wanted, png):
                step.update(used=self.serial, failed=0)
                if "repeat" in wanted:
                    step["action"]["repeat"] = wanted["repeat"]
                if wanted["kind"] == direct.WAIT and (sends or {}).get(step["id"]):
                    # the code waited here as long as it may and the model waited on: twice as long from now on
                    step["patience"] = min(self.patience(step) * 2, PATIENCE_MOST)
                before = (sends or {}).get(step["id"], 0)
                if before:
                    step["again"] = max(step.get("again", 1), before + 1)
                return step
        # a step of the same screen whose input did nothing there gives way to what the model sends in its place
        self.steps = [step for step in self.steps if not (step.get("failed") and recovery._distance(step["signature"], seen)
                                                          <= reach(step.get("scene")))]
        self.number += 1
        step = {"id": f"{self.task[0]}{self.number}", "signature": seen, "action": wanted, "scene": scene,
                **({"spot": spot(png, point)} if point is not None else {}),
                "used": self.serial, "sent": 0, "failed": 0, "trial": True}
        self.steps.append(step)
        self.pictures[self._name(step)] = png
        self.learned += 1
        if len(self.steps) > KEPT_MOST:
            gone = min(self.steps, key=lambda old: old.get("used", 0))
            self.steps = [old for old in self.steps if old is not gone]
            self.pictures.pop(self._name(gone), None)
        return step

    def proved(self) -> None:
        """The stretch got to a level's board: the steps learned on the way are kept."""
        for step in self.steps:
            step.pop("trial", None)

    def settle(self) -> None:
        """The stretch is over: steps still on trial were learned on a way that reached no board, and are dropped."""
        for step in self.steps:
            if step.get("trial"):
                self.pictures.pop(self._name(step), None)
                self.dropped += 1
        self.steps = [step for step in self.steps if not step.get("trial")]

    def patience(self, step: dict[str, Any]) -> float:
        """How long a screen to wait on is waited on in one stretch, in seconds."""
        return step.get("patience") or min(max(PATIENCE_TIMES * step["action"].get("ms", 0) / 1000, PATIENCE_LEAST),
                                           PATIENCE_MOST)

    def most(self, step: dict[str, Any]) -> int:
        """How often a step may be sent in one stretch."""
        return REPEATED_MOST if step["action"].get("repeat") else max(STEP_MOST, step.get("again", 1))

    def opened(self, step: dict[str, Any] | None) -> None:
        """A level's board was read after this step: after it the family is tried without the model being asked."""
        if step is not None and step["action"]["kind"] != direct.WAIT:
            step["opens"] = True
            step.pop("unopened", None)

    def unopened(self, step: dict[str, Any]) -> None:
        """No board was read after a step marked as opening one (a notice this level put over it): twice in a
        row, and the step is no longer taken to open one."""
        step["unopened"] = step.get("unopened", 0) + 1
        if step["unopened"] >= FAILS_MOST:
            step.pop("opens", None)
            step.pop("unopened", None)

    def _name(self, step: dict[str, Any]) -> str:
        return f"{self.task}-{step['id']}"

    def summary(self) -> dict[str, Any]:
        return {"task": self.task, "kept_before": self.began, "kept": len(self.steps), "sent": self.sent,
                "failed": self.failed, "waited": self.waited, "learned": self.learned, "dropped": self.dropped,
                "looks": self.looks[-LOOKS_KEPT:]}


def _sound(step: Any) -> bool:
    return (isinstance(step, dict) and isinstance(step.get("id"), str) and isinstance(step.get("signature"), list)
            and isinstance(step.get("action"), dict) and step["action"].get("kind") in SENT | {direct.WAIT})


def _same(old: dict[str, Any], new: dict[str, Any], png: bytes) -> bool:
    """Whether two inputs are one: the same kind, and the same key or as good as the same place."""
    if old["kind"] != new["kind"]:
        return False
    if old["kind"] == direct.KEY:
        return old["key"] == new["key"]
    one, other = place(old), place(new)
    if one is None or other is None:
        return one is None and other is None
    longer = max(recovery._size(png))
    return max(abs(one[0] - other[0]), abs(one[1] - other[1])) <= longer * SAME_PLACE


class Book:
    """A game's routines, by task. Read from what is kept of the game once its address is known (`open`) and
    written back when a game of it ends (`keep`: steps still on trial are dropped first); with nothing to keep
    them in, they last as long as the work whose parts share the book."""

    def __init__(self) -> None:
        self.tasks: dict[str, Routine] = {}
        self.site: str | None = None

    def open(self, store: Any, site: str, fresh: bool = False) -> None:
        if self.site is not None:
            return
        self.site = site
        if store is not None and not fresh:
            self.tasks = {task: Routine(task, steps) for task, steps in store.routines(site).items()}

    def of(self, task: str) -> Routine:
        if task not in self.tasks:
            self.tasks[task] = Routine(task)
        return self.tasks[task]

    def keep(self, store: Any) -> None:
        for routine in self.tasks.values():
            routine.settle()
        if store is None or not self.site or not any(routine.steps or routine.began for routine in self.tasks.values()):
            return
        pictures = {name: png for routine in self.tasks.values() for name, png in routine.pictures.items()}
        store.save_routines(self.site, {task: routine.steps for task, routine in self.tasks.items()}, pictures)
        for routine in self.tasks.values():
            routine.pictures = {}

    def summary(self) -> list[dict[str, Any]]:
        return [routine.summary() for routine in self.tasks.values() if routine.looks or routine.learned]
