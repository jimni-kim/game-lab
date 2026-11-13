"""Measure the game families' policies on their own simulated games (plan §13, "state → policy").

Each game starts from a seed and is played by a policy through the family's pure core (`choose`,
`next_state`) until no move is left or the move cap is reached; no screen, model or input is
involved, so this measures decision quality and compute only, never perception or delivery. Every
policy plays the same seeds, so policies are compared game by game, not only on averages.

  .venv/bin/python -m gamelab.eval policies --games 100 --out out/policies.json

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

from .. import catalog, solvers
from ..environments.replay import DRAW_BITS
from .measure import revision

# a block game played well can go on for a very long time; a capped game is reported as capped
DEFAULT_MAX_MOVES = 3000
# compute budgets (evaluated boards per decision) worth comparing: the low end is what the first
# live runs used, the high end where a decision still takes well under a second in Python
# (minesweeper's is the layouts its solver may count for one guess)
DEFAULT_COMPUTE = {"slide_merge": (50, 500, 2000), "block_placement": (50, 200, 1000), "minesweeper": (20000,)}
# 2048-style milestones whose reach rate is reported
TILE_MILESTONES = (512, 1024, 2048, 4096)


def play(family_id: str, policy: str, compute: int, seed: int, max_moves: int) -> dict[str, Any]:
    family = catalog.family(family_id)
    rng = random.Random(seed)
    state = family.initial_state(rng.getrandbits(DRAW_BITS))
    moves, took_ms = 0, []
    ended = "capped"
    while moves < max_moves:
        started = time.perf_counter()
        # a policy reads only what the screen would show, never what the game hides (where mines lie)
        action = family.choose(family.shown(copy.deepcopy(state)), policy, compute)
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


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--family", action="append", choices=sorted(catalog.MODELS),
                        help="family to measure (repeatable; default: every family)")
    parser.add_argument("--games", type=int, default=50, help="seeds per policy and budget")
    parser.add_argument("--first-seed", type=int, default=0)
    parser.add_argument("--compute", type=int, action="append",
                        help="compute budget for the searching policy (repeatable; default per family)")
    parser.add_argument("--max-moves", type=int, default=DEFAULT_MAX_MOVES)
    parser.add_argument("--workers", type=int, default=None, help="processes (default: CPU count)")
    parser.add_argument("--out", type=Path, help="write the full result as JSON here")


def main(args: argparse.Namespace) -> int:
    if args.games < 1 or args.max_moves < 1:
        raise SystemExit("--games and --max-moves must be positive")

    seeds = range(args.first_seed, args.first_seed + args.games)
    runs: list[tuple[str, str, int]] = []
    chosen: dict[str, str] = {}
    for family_id in args.family or sorted(catalog.MODELS):
        family = catalog.family(family_id)
        baseline, own = family.baseline_policy, family.descriptor["default_policy"]
        chosen[family_id] = own
        # the baseline ignores the budget; it is played once at the smallest one
        budgets = args.compute or DEFAULT_COMPUTE[family_id]
        runs.append((family_id, baseline, min(budgets)))
        runs += [(family_id, own, budget) for budget in budgets]
        # the general solvers the rule model fits, at the same budgets: what a game with no solver of its own gets
        runs += [(family_id, generic, budget) for generic in solvers.generic_policies(family.model) for budget in budgets]

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {run: [pool.submit(play, *run, seed, args.max_moves) for seed in seeds] for run in runs}
        games = {run: [f.result() for f in fs] for run, fs in futures.items()}

    report: dict[str, Any] = {"revision": revision(), "games_per_run": args.games, "first_seed": args.first_seed,
                              "max_moves": args.max_moves, "families": {}}
    for family_id in dict.fromkeys(run[0] for run in runs):
        family = catalog.family(family_id)
        family_runs = [run for run in runs if run[0] == family_id]
        base = games[family_runs[0]]
        report["families"][family_id] = {
            "family_revision": family.revision, "rules": family.rules,
            "runs": [{"policy": policy, "compute": compute, **summarize(games[run]),
                      **({"vs_baseline": paired(base, games[run])} if run != family_runs[0] else {}),
                      # a general solver against the family's own at the same budget
                      **({"vs_own": paired(games[(family_id, chosen[family_id], run[2])], games[run])}
                         if run[1] in solvers.GENERIC else {}),
                      "per_game": [{k: v for k, v in g.items() if k != "ms_decisions"} for g in games[run]]}
                     for run in family_runs for _, policy, compute in [run]]}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for family_id, data in report["families"].items():
        print(family_id)
        for run in data["runs"]:
            shown = {k: v for k, v in run.items() if k != "per_game"}
            print("  " + json.dumps(shown, ensure_ascii=False))
    return 0
