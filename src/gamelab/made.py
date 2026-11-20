"""A family made while a game is played (PLAN §11, H4): the rules and the solver of a game nobody registered,
written by a model as code and held to what the screen then shows.

The harness plays a registered family by its rule model and solver. A game of none is described by a model
once (`describe`: is it a grid played by one kind of move, how is a cell written down, what are its rules in
words); from that a descriptor is built, as a registered family has one, and the model writes the game's
rules as a module (`legal_actions`, `assess`, `status`, `choose`, and `result`: the state a move leaves, where
the rules decide all of it, from which the loop takes what a reader could not make out of the next screen:
`Code.outcomes`, PLAN §27, S3). The module runs in the sandbox, behind the
same interface a hand-written rule model and solver have, so the one loop plays it: the model reads each
screen by the descriptor, the code picks each move, and each move's result must be one the written rules
allow. A result they refuse is shown to the model, and the rules are written again from it; rules that keep
failing are dropped, and the game goes on played by the model's own inputs (`direct`).

Only games the description says fit are made: one grid and one kind of move, made by a tap on a cell, an
arrow key, a drag from cell to cell, or a tap that picks a cell followed by what completes the move (the same
tap again, a key, a tap on a second cell). Anything else stays with `direct`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path
import tempfile
from typing import Any

from . import catalog, recovery, resources
from .model import Model, Msg, TurnRequest
from .sandbox import Sandbox, SandboxError

log = logging.getLogger("gamelab.made")

DESCRIBE_TOKENS, DESCRIBE_TIMEOUT_S = 3000, 90.0
WRITE_TOKENS, WRITE_TIMEOUT_S = 16000, 300.0
# rules written for one game before it is left to the model's own inputs: at first, and again after a result
# the written rules refused
MAX_ATTEMPTS, MAX_REWRITES = 3, 2
# a call into the written rules is given this long (a search for a move takes its time)
CALL_TIMEOUT_S = 5.0
# moves really played that the rules are written again from
EXAMPLES_KEPT = 8
MOST_CELLS = 2500
PLAYING, WON, OVER = "playing", "won", "over"
POLICY = "generated"
DIRECTIONS = {"up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight"}
# how one move is written, by the input it is played with (what `adaptation.input_for` plays)
FORMS = {"tap": lambda kind: {"kind": kind, "cell": ["row", "column"]},
         "keys": lambda kind: {"kind": kind, "direction": "up | down | left | right"},
         "drag": lambda kind: {"kind": kind, "cell": ["row", "column"], "to": ["row", "column"]},
         "tap_twice": lambda kind: {"kind": kind, "cell": ["row", "column"]},
         "tap_key": lambda kind: {"kind": kind, "cell": ["row", "column"], "key": "one letter or digit"},
         "tap_to": lambda kind: {"kind": kind, "cell": ["row", "column"], "to": ["row", "column"]}}
# the controls each is explored with; `then`: what follows the tap on `cell` to complete the move (`agent._then`)
CONTROLS = {"tap": {"kind": "tap"}, "keys": {"kind": "keys", "keys": DIRECTIONS},
            "drag": {"kind": "drag", "from": "cell", "to": "to", "duration_ms": 250},
            "tap_twice": {"kind": "tap", "then": "tap"}, "tap_key": {"kind": "tap", "then": "key", "key": "key"},
            "tap_to": {"kind": "tap", "then": "tap_to", "to": "to"}}
# the keys a move may name
KEYS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


def _prompt(name: str) -> str:
    return (resources.FRAGMENT / "prompts" / name).read_text(encoding="utf-8")


def _text(value: Any, most: int) -> str | None:
    return value.strip()[:most] if isinstance(value, str) and value.strip() else None


async def describe(surface: Any, model: Model, site: str, *, hint: str | None = None) -> Game | None:
    """The game on the screen as a model describes it, when it is one that can be played by written rules;
    None when it is not, or the description is not one a family can be made from."""
    shown, _ = recovery.view(await surface.capture())
    context = {"screen": list(recovery._size(shown)), **({"hint": hint} if hint else {})}
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                          "data": base64.b64encode(shown).decode("ascii")}}
    result = await model.complete(TurnRequest(
        system_stable=_prompt("describe_game.md"), max_tokens=DESCRIBE_TOKENS, timeout_s=DESCRIBE_TIMEOUT_S,
        messages=[Msg("user", [{"type": "text", "text": json.dumps(context)}, image])]))
    try:
        answer = json.loads(result.text or "")
    except ValueError:
        return None
    if not isinstance(answer, dict) or answer.get("screen") != "game" or answer.get("fits") is not True:
        return None
    move = answer.get("move") if isinstance(answer.get("move"), dict) else {}
    rows, cols = answer.get("rows"), answer.get("cols")
    told = {"name": _text(answer.get("name"), 80), "summary": _text(answer.get("summary"), 600),
            "cells": _text(answer.get("cells"), 300), "kind": _text(move.get("kind"), 24),
            "description": _text(move.get("description"), 300), "won": _text(answer.get("won"), 200) or "never",
            "over": _text(answer.get("over"), 200) or "never"}
    if (any(value is None for value in told.values()) or move.get("input") not in FORMS or type(rows) is not int
            or type(cols) is not int or rows < 1 or cols < 1 or rows * cols > MOST_CELLS
            or not told["kind"].replace("_", "").isalnum() or not told["kind"].islower()):
        return None
    return Game(site, {**told, "input": move["input"], "rows": rows, "cols": cols})


class Game:
    """One made family: its descriptor, and the written rules once there are any."""

    def __init__(self, site: str, told: dict[str, Any]) -> None:
        self.told = told
        self.id = f"made_{hashlib.sha256(site.encode()).hexdigest()[:8]}"
        self.rules = {"rows": told["rows"], "cols": told["cols"]}
        self.descriptor = self._descriptor()
        if problems := resources.validate(self.descriptor, json.loads(catalog.SCHEMA.read_text(encoding="utf-8"))):
            raise ValueError(f"made family {self.id}: " + "; ".join(problems))
        self.code = Code(self)
        self.attempts = self.rewrites = self.questions = 0
        # taken up again from what was kept of the site, not described and written in this game
        self.restored = False
        self.problems: list[str] = []
        catalog.register(self.descriptor, self.code, Solver(self.code))

    def _descriptor(self) -> dict[str, Any]:
        told, size = self.told, {"type": "integer", "minimum": 1}
        return {
            "id": self.id, "revision": 1, "summary": told["summary"], "state_schema": f"{self.id}.v1",
            "board": {"shape": "grid", "cells": [{"kind": "cell", "looks": told["cells"]}]},
            "state_fields": {
                "grid": f"the grid's rows from the top, each a list of cells from the left: {told['cells']}; null a cell nobody read",
                "score": "the game's own count of progress as the screen shows it (a score, the moves made), or null",
                "scene": "the id of the scene the screen shows"},
            "actions": [{"kind": told["kind"], "input": CONTROLS[told["input"]]["kind"], "description": told["description"]}],
            "scenes": [{"id": PLAYING, "looks": "the board in play"}, {"id": WON, "looks": told["won"]},
                       {"id": OVER, "looks": told["over"]}],
            "meta_actions": [{"id": "new_game", "description": "start another game"}],
            "rule_parameters": {"rows": {"description": "rows of the grid", "values": size},
                                "cols": {"description": "columns of the grid", "values": size}},
            "policies": [POLICY], "baseline_policy": POLICY, "default_policy": POLICY, "default_compute": 1,
            "goal_value": "the game's own score", "verify_every": 1,
            "discovery": {"rules": {"rows": {"minimum": 1}, "cols": {"minimum": 1}}, "controls": CONTROLS[told["input"]],
                          "settling": {"timeout_s": 4, "interval_s": 0.15, "stable_s": 0.25, "animation_s": 0.4, "still": True},
                          "tolerance": 4}}

    def _context(self, states: list[dict[str, Any]], examples: list[dict[str, Any]],
                 previous: tuple[str, list[str]] | None) -> dict[str, Any]:
        game = {"name": self.told["name"], "summary": self.told["summary"], "state_fields": self.descriptor["state_fields"],
                "actions": self.descriptor["actions"], "action_form": FORMS[self.told["input"]](self.told["kind"]),
                "scenes": self.descriptor["scenes"]}
        return {"game": game, "rules": self.rules, "states": states, "examples": examples,
                **({"previous": {"module": previous[0], "problems": previous[1]}} if previous else {})}

    async def write(self, model: Model, states: list[dict[str, Any]], examples: list[dict[str, Any]] = (),
                    refused: dict[str, Any] | None = None, allowed: Any = None) -> bool:
        """Rules written for the game and taken when they hold on everything seen so far: the screens read
        (`states`), the moves really played (`examples`) and, when given, a move whose real result the rules
        in use refused (`refused`). At most MAX_ATTEMPTS questions, each told what was wrong with the last."""
        examples = [*examples][-EXAMPLES_KEPT:] + ([refused] if refused else [])
        previous = None
        if refused is not None and self.code.source:
            previous = (self.code.source, [f"assess refuses the result that was really seen of the last example's move "
                                           f"({json.dumps(refused['action'])})"])
        for _ in range(MAX_ATTEMPTS):
            if allowed is not None and not allowed():
                return False
            self.attempts += 1
            self.questions += 1
            try:
                result = await model.complete(TurnRequest(
                    system_stable=_prompt("write_rules.md"), max_tokens=WRITE_TOKENS, timeout_s=WRITE_TIMEOUT_S,
                    messages=[Msg("user", [{"type": "text", "text": json.dumps(self._context(states, examples, previous))}])]))
            except Exception as error:
                log.info("no rules were written: %s", error)
                self.problems = [f"{type(error).__name__}: {error}"[:300]]
                continue
            source = _unfenced(result.text or "")
            trial = Code(self, source)
            self.problems = trial.check(states, examples)
            if not self.problems:
                self.code.take(trial)
                return True
            trial.close()
            previous = (source, self.problems)
        return False

    async def rewrite(self, model: Model, states: list[dict[str, Any]], examples: list[dict[str, Any]],
                      refused: dict[str, Any], allowed: Any = None) -> bool:
        """The rules written again from a move whose real result they refused; False when they may not be
        (MAX_REWRITES) or none passes."""
        if self.rewrites >= MAX_REWRITES:
            return False
        self.rewrites += 1
        return await self.write(model, states, examples, refused, allowed)

    def record(self) -> dict[str, Any] | None:
        """What is kept of the family for the next game of this site (plan §12, A3): the description it was made
        from and the rules as written; None while no rules are."""
        return {"told": self.told, "source": self.code.source} if self.code.source else None

    @classmethod
    def restore(cls, site: str, kept: Any) -> Game | None:
        """The family as it was kept, with nothing described or written anew; None when what was kept is no
        such record. Whoever takes it up holds its rules to the screen first."""
        if not isinstance(kept, dict) or not isinstance(kept.get("told"), dict) or not isinstance(kept.get("source"), str):
            return None
        told = kept["told"]
        if told.get("input") not in FORMS or any(type(told.get(name)) is not int or told[name] < 1 for name in ("rows", "cols")):
            return None
        try:
            game = cls(site, told)
        except (ValueError, KeyError, TypeError):
            return None
        game.code._load(kept["source"])
        game.restored = True
        return game

    def summary(self) -> dict[str, Any]:
        return {"family": self.id, "name": self.told["name"], **({"restored": True} if self.restored else {}), "summary": self.told["summary"], "input": self.told["input"],
                "rows": self.told["rows"], "cols": self.told["cols"], "attempts": self.attempts, "rewrites": self.rewrites,
                "questions": self.questions, "written": self.code.source is not None, "errors": self.code.errors,
                "digest": hashlib.sha256(self.code.source.encode()).hexdigest() if self.code.source else None}

    def close(self) -> None:
        self.code.close()
        catalog.unregister(self.id)


def _unfenced(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped


class Code:
    """Written rules behind a rule model's interface (`rules.RULE_FUNCTIONS`, as far as the loop calls it). The
    module runs in the sandbox; what it cannot answer (an error, a timeout) is no move, no confirmation."""
    CAPABILITIES = ("hidden",)

    def __init__(self, game: Game, source: str | None = None) -> None:
        self.game, self.source, self.box, self.errors = game, None, None, 0
        self.DEFAULT_RULES = dict(game.rules)
        # the module's text as a file: what is shown to whoever writes this site's adapter (`rule_model`)
        self.__file__ = str(Path(tempfile.gettempdir()) / f"gamelab-{game.id}-{id(self):x}.py")
        if source is not None:
            self._load(source)

    def _load(self, source: str) -> None:
        self.source, self.box = source, Sandbox(source, call_timeout_s=CALL_TIMEOUT_S)
        Path(self.__file__).write_text(source, encoding="utf-8")

    def take(self, other: Code) -> None:
        """Go on with another's module (rules written again), which is given up to this one."""
        self.close()
        self.source, self.box, other.box = other.source, other.box, None
        Path(self.__file__).write_text(self.source, encoding="utf-8")

    def close(self) -> None:
        if self.box is not None:
            self.box.close()
            self.box = None
        Path(self.__file__).unlink(missing_ok=True)

    def _call(self, name: str, **arguments: Any) -> Any:
        if self.box is None:
            raise SandboxError("error", "no rules are written yet")
        try:
            return self.box.call(name, **arguments)
        except SandboxError:
            self.errors += 1
            raise

    # ── what is asked of the written module ───────────────────────────

    def _actions(self, state: dict[str, Any], rules: dict[str, Any]) -> list[dict[str, Any]]:
        found = self._call("legal_actions", state=state, rules=rules)
        if not isinstance(found, list) or not all(self._formed(action, rules) for action in found):
            raise SandboxError("answer", "legal_actions gave something that is no list of moves")
        return found

    def _formed(self, action: Any, rules: dict[str, Any]) -> bool:
        """Whether a move is written as this game's moves are: its kind and, by its input, a cell, a direction
        or two cells, on the grid."""
        told = self.game.told

        def cell(value: Any) -> bool:
            return (isinstance(value, list) and len(value) == 2 and all(type(part) is int for part in value)
                    and 0 <= value[0] < rules["rows"] and 0 <= value[1] < rules["cols"])
        if not isinstance(action, dict) or action.get("kind") != told["kind"]:
            return False
        if told["input"] == "keys":
            return set(action) == {"kind", "direction"} and action["direction"] in DIRECTIONS
        if told["input"] in ("tap", "tap_twice"):
            return set(action) == {"kind", "cell"} and cell(action["cell"])
        if told["input"] == "tap_key":
            return set(action) == {"kind", "cell", "key"} and cell(action["cell"]) and str(action["key"]) in KEYS
        return set(action) == {"kind", "cell", "to"} and cell(action["cell"]) and cell(action["to"])

    def check(self, states: list[dict[str, Any]], examples: list[dict[str, Any]], limit: int = 4) -> list[str]:
        """What is wrong with the module on what was seen: the screens read and the moves really played."""
        rules, problems = self.game.rules, []

        def said(name: str, **arguments: Any) -> tuple[bool, Any]:
            try:
                return True, self._actions(**arguments) if name == "legal_actions" else self._call(name, **arguments)
            except SandboxError as error:
                problems.append(f"{name}: {error.reason}: {error.detail}"[:300])
                return False, None
        for number, state in enumerate([*states, *(example["state"] for example in examples)]):
            if len(problems) >= limit or state.get("scene", PLAYING) != PLAYING:
                break
            where = f"on screen {number}" if number < len(states) else f"on example {number - len(states)}"
            ok, legal = said("legal_actions", state=state, rules=rules)
            ok_status, status = said("status", state=state, rules=rules)
            if ok_status and status != PLAYING:
                problems.append(f"status {where} says {status!r} of a game that is in play")
            if not ok:
                continue
            if not legal:
                problems.append(f"legal_actions {where} offers no move, and the game is in play")
                continue
            ok, picked = said("choose", state=state, rules=rules)
            if ok and picked not in legal:
                problems.append(f"choose {where} gave {json.dumps(picked)[:120]}, which legal_actions does not offer")
            ok, same = said("assess", before=state, action=legal[0], after=state, rules=rules)
            if ok and same is not False:
                problems.append(f"assess {where} takes the unchanged screen as a move's result")
        for number, example in enumerate(examples):
            if len(problems) >= limit:
                break
            ok, legal = said("legal_actions", state=example["state"], rules=rules)
            if ok and example["action"] not in legal:
                problems.append(f"legal_actions on example {number} does not offer the move that was played, "
                                f"{json.dumps(example['action'])}")
            ok, taken = said("assess", before=example["state"], action=example["action"], after=example["after"], rules=rules)
            if ok and taken is not True:
                problems.append(f"assess on example {number} refuses the result that was really seen")
            if self.foresees:
                ok, left = said("result", state=example["state"], action=example["action"], rules=rules)
                if ok and left is not None and (not isinstance(left, dict) or left.get("grid") != example["after"]["grid"]):
                    problems.append(f"result on example {number} gives another state than the one that was really seen "
                                    "(None is the answer when the rules do not decide all of it)")
        return problems[:limit]

    @property
    def foresees(self) -> bool:
        """Whether the module says what a move leaves (`result`): one written before that was asked for does not."""
        return self.source is not None and "def result(" in self.source

    def outcomes(self, state: dict[str, Any], action: dict[str, Any], rules: dict[str, Any]
                 ) -> list[tuple[Any, dict[str, Any]]]:
        """What the move can leave, as far as the written rules decide all of it (PLAN §27, S3): the one state
        they say it leaves, when they take that themselves as the move's result; none when they leave it open,
        do not say, or fail. What a reader could not make out of the next screen is taken from it."""
        if not self.foresees:
            return []
        try:
            left = self._call("result", state=state, action=action, rules=rules)
        except SandboxError:
            return []
        if not isinstance(left, dict) or "grid" not in left:
            return []
        # (the scene as the module gives it, when it gives one: rules that call the last move's result won may
        # take it as nothing else)
        for scene in dict.fromkeys((left.get("scene"), PLAYING)):
            told = {**state, "grid": left["grid"], "scene": scene}
            if scene in (PLAYING, WON, OVER) and not self.validate(told, rules) and not self.unknown_fields(told) \
                    and self.assess(state, action, told, rules)["effect"] == "confirmed":
                return [(None, told)]
        return []

    # ── a rule model, as the loop calls one ───────────────────────────

    def unknown_fields(self, state: dict[str, Any]) -> list[str]:
        if state.get("scene", PLAYING) != PLAYING:
            return []
        return [f"grid[{r}][{c}]" for r, row in enumerate(state["grid"]) for c, cell in enumerate(row) if cell is None]

    def validate(self, state: dict[str, Any], rules: dict[str, Any]) -> list[str]:
        grid = state.get("grid")
        if not isinstance(grid, list) or len(grid) != rules["rows"] or any(
                not isinstance(row, list) or len(row) != rules["cols"] for row in grid):
            return ["grid_shape"]
        problems = []
        if any(cell is not None and type(cell) not in (int, str) for row in grid for cell in row):
            problems.append("cell_value")
        if state.get("scene", PLAYING) not in (PLAYING, WON, OVER):
            problems.append("scene")
        return problems

    def legal_actions(self, state: dict[str, Any], rules: dict[str, Any]) -> list[dict[str, Any]]:
        if state.get("scene", PLAYING) != PLAYING or self.unknown_fields(state):
            return []
        try:
            return self._actions(state, rules)
        except SandboxError:
            return []

    def status(self, state: dict[str, Any], rules: dict[str, Any], goal: dict[str, Any]) -> dict[str, Any]:
        """The game's end is the screen's own message, or what the written rules say of the board. A score
        goal is judged on the score as read."""
        scene = state.get("scene", PLAYING)
        if scene == PLAYING and not self.unknown_fields(state):
            try:
                said = self._call("status", state=state, rules=rules)
                scene = {"won": WON, "lost": OVER}.get(said, PLAYING)
            except SandboxError:
                pass
        reached = "reached" if scene == WON and goal.get("objective") == "play_until_end" else "not_reached"
        if goal.get("objective") == "reach_value":
            score, target = state.get("score"), goal.get("target_value")
            reached = "unknown" if type(score) not in (int, float) or target is None else \
                ("reached" if score >= target else "not_reached")
        return {"terminal": scene != PLAYING, "goal": reached}

    def assess(self, before: dict[str, Any], action: dict[str, Any], after: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
        if self.unknown_fields(after):
            return {"effect": "unknown"}
        if after["grid"] == before["grid"]:
            return {"effect": "absent"}
        try:
            taken = self._call("assess", before=before, action=action, after=after, rules=rules)
        except SandboxError:
            return {"effect": "unknown"}
        return {"effect": "confirmed" if taken is True else "inconsistent"}

    def explain(self, before: dict[str, Any], action: dict[str, Any], after: dict[str, Any], rules: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def ended(self, before: dict[str, Any], action: dict[str, Any], after: dict[str, Any], rules: dict[str, Any]) -> bool:
        return False

    def unread_state(self, rules: dict[str, Any], scene: str) -> dict[str, Any]:
        return {"grid": [[None] * rules["cols"] for _ in range(rules["rows"])], "score": None, "scene": scene}


class Solver:
    """The written rules' own choice of move, behind a solver's interface."""
    POLICIES = (POLICY,)

    def __init__(self, code: Code) -> None:
        self.code = code

    def choose(self, state: dict[str, Any], rules: dict[str, Any], policy: str, budget: int,
               excluded: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        legal = [action for action in self.code.legal_actions(state, rules) if action not in (excluded or [])]
        if not legal:
            return {"action": None}
        try:
            picked = self.code._call("choose", state=state, rules=rules)
        except SandboxError:
            picked = None
        # a move the board did not take, or none it offers: the first that is left
        return {"action": picked if picked in legal else legal[0]}
