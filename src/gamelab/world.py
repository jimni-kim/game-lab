"""What a game that goes on between requests is known to hold, and when each errand done in it can be done again
(PLAN §30).

A game whose state outlasts a request (a farm, a camp) offers its errands only now and then: what was harvested
grows back, a button cools down. One file of the game's store (`world.json`, `adaptation.Store`) keeps

    have     what the game counted of the things it holds, as a model last read them off the screen
    tasks    by errand (`agent.Agent.errand`): when it was done, and when it can be done again

and one question is answered from it before anything is sent (`World.wait`): whether an errand is known to be
out of reach now, and for how long. An errand not yet due is not gone after: no screen is searched and no step
of its routine is sent (`agent.Agent._due_yet`), the time left is handed back, or waited out with the game open
when the person asked for that.

Nothing here knows a game. The times are a model's reading of the screen where the errand was done or found
out of reach (`again_in_s`: a timer shown, a bar's length), kept against the clock of the machine; the code adds
what it saw itself: an errand found out of reach after the time kept for it had passed (`early`) says the
game's time did not run as the clock did (a game whose timers stand still while it is closed), and the wait is
then taken from the newer reading.
"""
from __future__ import annotations

from typing import Any

# the longest time to the next doing a reading is taken for (a week), and the things held kept of one reading
AGAIN_MOST_S = 7 * 24 * 3600.0
HAVE_MOST, NAME_MOST = 24, 40
# the times an errand was done kept, newest last
DONE_KEPT = 8
# a wait shorter than this is none: the screens before the errand take longer
NEAR_S = 1.0


def again(value: Any) -> float | None:
    """A model's reading of the seconds until an errand can be done again; None when it gave none to go by."""
    return float(value) if type(value) in (int, float) and 0 < value <= AGAIN_MOST_S else None


def held(value: Any) -> dict[str, float]:
    """A model's reading of what the game counts of the things it holds: each by a plain name with its number."""
    if not isinstance(value, dict):
        return {}
    return {name.strip().lower()[:NAME_MOST]: count for name, count in list(value.items())[:HAVE_MOST]
            if isinstance(name, str) and name.strip() and type(count) in (int, float)}


class World:
    def __init__(self) -> None:
        self.have: dict[str, float] = {}
        self.read_at: float | None = None
        self.tasks: dict[str, dict[str, Any]] = {}
        self.site: str | None = None
        self.changed = False

    def open(self, store: Any, site: str, fresh: bool = False) -> None:
        self.site = site
        kept = store.world(site) if store is not None and not fresh else None
        if kept:
            self.have = held(kept.get("have"))
            self.read_at = kept.get("read_at") if type(kept.get("read_at")) in (int, float) else None
            self.tasks = {name: dict(task) for name, task in (kept.get("tasks") or {}).items() if isinstance(task, dict)}

    def keep(self, store: Any) -> None:
        if store is not None and self.site and self.changed:
            store.save_world(self.site, {"have": self.have, "read_at": self.read_at, "tasks": self.tasks})
            self.changed = False

    def wait(self, task: str, now: float) -> float | None:
        """The seconds until `task` can be done again as far as is known; None when nothing says it cannot be
        done now (never done, no time read for it, or the time has passed)."""
        ready = (self.tasks.get(task) or {}).get("ready_at")
        return ready - now if type(ready) in (int, float) and ready - now >= NEAR_S else None

    def done(self, task: str, now: float, again_in_s: Any = None, have: Any = None) -> None:
        """The errand was done at `now`, and can be again `again_in_s` later as the screen showed it."""
        entry = self.tasks.setdefault(task, {})
        entry["done"] = [*(entry.get("done") or []), round(now, 1)][-DONE_KEPT:]
        entry["count"] = (entry.get("count") or 0) + 1
        self._ready(entry, now, again_in_s)
        self._hold(have, now)
        self.changed = True

    def unmoved(self, have: Any) -> dict[str, float]:
        """What a model reads the game to hold now, when every thing of it known from before is held in the same
        number, and at least one is: nothing the game counts has changed since it was last read. Empty when
        something changed or nothing can be compared."""
        read = held(have)
        shared = {name: count for name, count in read.items() if name in self.have}
        return shared if shared and all(self.have[name] == count for name, count in shared.items()) else {}

    def not_yet(self, task: str, now: float, again_in_s: Any = None, have: Any = None) -> None:
        """The errand was looked for at `now` and the game did not offer it: for `again_in_s` more, when the
        screen showed how long."""
        entry = self.tasks.setdefault(task, {})
        if entry.get("ready_at") is not None and now >= entry["ready_at"]:
            # the time kept for it had passed and the game was not there yet: its time ran slower than the clock
            entry["early"] = (entry.get("early") or 0) + 1
        self._ready(entry, now, again_in_s)
        self._hold(have, now)
        self.changed = True

    def _ready(self, entry: dict[str, Any], now: float, again_in_s: Any) -> None:
        seconds = again(again_in_s)
        entry["ready_at"] = None if seconds is None else round(now + seconds, 1)
        entry["told_at"] = round(now, 1)

    def _hold(self, have: Any, now: float) -> None:
        read = held(have)
        if read:
            self.have.update(read)
            self.read_at = round(now, 1)

    def summary(self, now: float) -> dict[str, Any]:
        """What is known now, for the person: the errands that can be done as far as is known (`ready`) and those
        that cannot yet, each with the seconds left."""
        tasks = {}
        for name, entry in self.tasks.items():
            left = self.wait(name, now)
            tasks[name] = {"ready": left is None, **({"in_s": round(left)} if left is not None else {}),
                           "done": entry.get("count") or 0,
                           **({"last_done_s_ago": round(now - entry["done"][-1])} if entry.get("done") else {}),
                           **({"early": entry["early"]} if entry.get("early") else {})}
        return {"have": dict(self.have), "tasks": tasks}
