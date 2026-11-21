"""Measure the game families' policies on their own simulated games (plan §13, "state → policy").

Each game starts from a seed and is played by a policy through the family's pure core (`choose`,
`next_state`) until no move is left or the move cap is reached; no screen, model or input is
involved, so this measures decision quality and compute only, never perception or delivery. Every
policy plays the same seeds, so policies are compared game by game, not only on averages.

  .venv/bin/python -m gamelab.eval policies --games 100 --out out/policies.json
  .venv/bin/python -m gamelab.eval policies --family minesweeper --games 400 --hide mines

`--rule NAME=VALUE` (a JSON value) plays every family that has that rule by it instead of its standard one
(a larger grid, another count of mines, three cards a turn of the stock), and `--policy NAME` plays only the
policies named:

  .venv/bin/python -m gamelab.eval policies --family minesweeper --policy solver --games 10000 \
      --rule rows=16 --rule cols=30 --rule mines=99 --rule first_open=cell

`--hide RULE` adds a run of each family's own policy told nothing of that rule value (null), where the family
lets it be unknown: the game is still played by the real value, so the run says what not knowing it costs.

The replay games are the cores' own simplified games: block_placement's replay score (`LINE_POINTS`)
is not any site's scoring, and its pieces come from `STANDARD_PIECES`. Results say how the policies
compare on those rules; a site with other pieces or scoring may rank them differently.
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import statistics
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from .. import catalog, resources, solvers
from .measure import revision

# the bits of each seeded draw a simulated game's chance (a new tile, a tray) is made from
DRAW_BITS = 32
# a block game played well can go on for a very long time; a capped game is reported as capped
DEFAULT_MAX_MOVES = 3000
# compute budgets (evaluated boards per decision) worth comparing: the low end is what the first
# live runs used, the high end where a decision still takes well under a second in Python
# (minesweeper's is the layouts its solver may count for one guess)
DEFAULT_COMPUTE = {"slide_merge": (50, 500, 2000), "block_placement": (50, 200, 1000), "minesweeper": (20000,),
                   "match3": (200,), "klondike": (200,),
                   "falling": (2000,)}
# 2048-style milestones whose reach rate is reported
TILE_MILESTONES = (512, 1024, 2048, 4096)


def bound(family_id: str, values: dict[str, Any] | None):
    """The family playing by `values` where it has those rules (`--rule`), else by its standard ones."""
    family = catalog.family(family_id)
    own = {name: value for name, value in (values or {}).items() if name in family.rules}
    if problems := resources.validate(own, catalog.rules_schema(family.descriptor)):
        raise SystemExit(f"--rule: {family_id}: {'; '.join(problems)}")
    return family.bind({"rules": own}) if own else family


def play(family_id: str, policy: str, compute: int, seed: int, max_moves: int, hidden: tuple[str, ...] = (),
         values: dict[str, Any] | None = None) -> dict[str, Any]:
    family = bound(family_id, values)
    # the rules the policy is told: the game's own, without the values hidden from it
    told = family.bind({"rules": {**family.rules, **{name: None for name in hidden}}}) if hidden else family
    rng = random.Random(seed)
    state = family.initial_state(rng.getrandbits(DRAW_BITS))
    moves, took_ms = 0, []
    # what a policy keeps of the screens it saw in this game (a solver that keeps nothing has none)
    memory = None
    ended = "capped"
    while moves < max_moves:
        started = time.perf_counter()
        # a policy reads only what the screen would show, never what the game hides (where mines lie)
        # a real game takes no move once it has ended, whatever a policy told less would still try
        over = bool(hidden) and family.model.status(family.shown(copy.deepcopy(state)), family.rules,
                                                    {"objective": "play_until_end"})["terminal"]
        seen = family.shown(copy.deepcopy(state))
        memory = told.recall(memory, seen)
        action = None if over else told.choose(seen, policy, compute, memory=memory)
        took_ms.append((time.perf_counter() - started) * 1000)
        if action is None:
            ended = "no_move"
            break
        state = family.next_state(state, action, rng.getrandbits(DRAW_BITS))
        if action["kind"] in family.meta_actions:
            continue                       # continuing after a win is not a move on the board
        moves += 1
    result = {"seed": seed, "moves": moves, "score": state.get("score"), "ended": ended,
              "ms_per_move": round(sum(took_ms) / max(moves, 1), 3), "max_ms": round(max(took_ms), 3),
              "ms_decisions": took_ms}
    if state.get("score") is None:
        # a game with no score is won or lost
        seen = family.model.status(family.shown(copy.deepcopy(state)), family.rules, {"objective": "play_until_end"})
        result["won"] = seen["goal"] == "reached"
    elif "grid" in state:
        result["max_tile"] = max(max(row) for row in state["grid"])
    won = getattr(family.model, "won", None)
    if callable(won) and (cleared := won(state, family.rules)) is not None:
        # a game with a score and a level's goal besides (rows within so many pieces): met or not
        result["won"] = cleared
    return result


def summarize(games: list[dict[str, Any]]) -> dict[str, Any]:
    scores = sorted(g["score"] if g["score"] is not None else int(g["won"]) for g in games)
    # §13.1 bounds the 95th percentile of every decision in the run, not of per-game averages
    decisions = sorted(ms for g in games for ms in g["ms_decisions"])
    summary = {"games": len(games), "score_mean": round(statistics.fmean(scores), 1),
               "score_median": statistics.median(scores), "score_p10": _quantile(scores, 0.1),
               "score_p90": _quantile(scores, 0.9), "moves_mean": round(statistics.fmean(g["moves"] for g in games), 1),
               "capped": sum(g["ended"] == "capped" for g in games),
               "ms_per_move_mean": round(statistics.fmean(g["ms_per_move"] for g in games), 3),
               "ms_p95": round(_quantile(decisions, 0.95), 3), "max_ms": max(g["max_ms"] for g in games)}
    if all("won" in g for g in games):
        summary["win_rate"] = round(sum(g["won"] for g in games) / len(games), 3)
    if all("max_tile" in g for g in games):
        summary["reach_rate"] = {str(tile): round(sum(g["max_tile"] >= tile for g in games) / len(games), 3)
                                 for tile in TILE_MILESTONES}
    return summary


def paired(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> dict[str, Any]:
    """How often the second policy scored above, equal to or below the first on the same seed."""
    pairs = list(zip(first, second, strict=True))
    def score(game: dict[str, Any]) -> int:
        return game["score"] if game["score"] is not None else int(game["won"])
    return {"better": sum(score(b) > score(a) for a, b in pairs),
            "equal": sum(score(b) == score(a) for a, b in pairs),
            "worse": sum(score(b) < score(a) for a, b in pairs)}


def _quantile(ordered: list[float], q: float) -> float:
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def rule_value(text: str) -> tuple[str, Any]:
    name, mark, value = text.partition("=")
    if not mark or not name:
        raise argparse.ArgumentTypeError("NAME=VALUE")
    try:
        return name, json.loads(value)
    except ValueError:
        return name, value                 # a bare word is a string


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--family", action="append", choices=sorted(catalog.MODELS),
                        help="family to measure (repeatable; default: every family)")
    parser.add_argument("--games", type=int, default=50, help="seeds per policy and budget")
    parser.add_argument("--first-seed", type=int, default=0)
    parser.add_argument("--compute", type=int, action="append",
                        help="compute budget for the searching policy (repeatable; default per family)")
    parser.add_argument("--max-moves", type=int, default=DEFAULT_MAX_MOVES)
    parser.add_argument("--rule", action="append", default=[], metavar="NAME=VALUE", type=rule_value,
                        help="play by this rule value (JSON) in every family that has the rule (repeatable)")
    parser.add_argument("--policy", action="append", default=[], metavar="NAME",
                        help="play only these policies (repeatable; default: the baseline, the family's own, the general)")
    parser.add_argument("--hide", action="append", default=[], metavar="RULE",
                        help="also play each family's own policy told nothing of this rule value (repeatable)")
    parser.add_argument("--workers", type=int, default=None, help="processes (default: CPU count)")
    parser.add_argument("--out", type=Path, help="write the full result as JSON here")


def main(args: argparse.Namespace) -> int:
    if args.games < 1 or args.max_moves < 1:
        raise SystemExit("--games and --max-moves must be positive")

    seeds = range(args.first_seed, args.first_seed + args.games)
    runs: list[tuple[str, str, int, tuple[str, ...]]] = []
    chosen: dict[str, str] = {}
    values = dict(args.rule)
    for family_id in args.family or sorted(catalog.MODELS):
        family = bound(family_id, values)
        baseline, own = family.baseline_policy, family.descriptor["default_policy"]
        chosen[family_id] = own
        # the baseline ignores the budget; it is played once at the smallest one
        budgets = args.compute or DEFAULT_COMPUTE[family_id]
        runs.append((family_id, baseline, min(budgets), ()))
        runs += [(family_id, own, budget, ()) for budget in budgets]
        # the general solvers the rule model fits, at the same budgets: what a game with no solver of its own gets
        runs += [(family_id, generic, budget, ()) for generic in solvers.generic_policies(family.model) for budget in budgets]
        # the family's own policy told nothing of the hidden rule values it may be played without
        hidden = tuple(name for name in args.hide if name in family.rules and not resources.validate(
            None, family.descriptor["rule_parameters"][name]["values"]))
        runs += [(family_id, own, budget, hidden) for budget in budgets if hidden]
        if args.policy:
            # only the policies named, any the family offers: the baseline first and once, as above
            named = [name for name in dict.fromkeys([baseline, *family.policies()]) if name in args.policy]
            runs = [run for run in runs if run[0] != family_id]
            runs += [(family_id, name, budget, ()) for name in named
                     for budget in ([min(budgets)] if name == baseline else budgets)]
    if not runs:
        raise SystemExit("--policy: no family has a policy of that name")
    runs = list(dict.fromkeys(runs))

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {run: [pool.submit(play, *run[:3], seed, args.max_moves, run[3], values) for seed in seeds] for run in runs}
        games = {run: [f.result() for f in fs] for run, fs in futures.items()}

    report: dict[str, Any] = {"revision": revision(), "games_per_run": args.games, "first_seed": args.first_seed,
                              "max_moves": args.max_moves, "families": {}}
    for family_id in dict.fromkeys(run[0] for run in runs):
        family = bound(family_id, values)
        family_runs = [run for run in runs if run[0] == family_id]
        # the first run is the baseline unless `--policy` left it out: then the runs are compared with nothing
        base = games[family_runs[0]] if family_runs[0][1] == family.baseline_policy else None
        own_runs = {run[2]: run for run in family_runs if run[1] == chosen[family_id] and not run[3]}
        report["families"][family_id] = {
            "family_revision": family.revision, "rules": family.rules,
            "runs": [{"policy": policy, "compute": compute, **({"hidden": list(hidden)} if hidden else {}),
                      **summarize(games[run]),
                      **({"vs_baseline": paired(base, games[run])} if base is not None and run != family_runs[0] else {}),
                      # a general solver against the family's own at the same budget
                      **({"vs_own": paired(games[own_runs[run[2]]], games[run])}
                         if (run[1] in solvers.GENERIC or hidden) and run[2] in own_runs else {}),
                      "per_game": [{k: v for k, v in g.items() if k != "ms_decisions"} for g in games[run]]}
                     for run in family_runs for _, policy, compute, hidden in [run]]}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for family_id, data in report["families"].items():
        print(family_id)
        for run in data["runs"]:
            shown = {k: v for k, v in run.items() if k != "per_game"}
            print("  " + json.dumps(shown, ensure_ascii=False))
    return 0
