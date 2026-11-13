"""Play a game app on an Android device or emulator through adb and measure the harness.

The device must already be attached (`adb devices`); the emulator this was set up for is the AVD
`army-game` (`emulator -avd army-game`). The game is installed and signed in to by the person.

  .venv/bin/python -m gamelab.eval android --check --out out/device
      one capture, the screen's size and the app in front: is the device reachable, and what does the
      game look like (a profile is written from such a capture)

  .venv/bin/python -m gamelab.eval android --family slide_merge --package com.example.game \\
      --objective reach_value --target 2048 --compute 500 --out out/app
      a game no profile registers: its reading is made from a model's look the code confirms

  .venv/bin/python -m gamelab.eval android --profile some_app_profile --compute 500 --out out/app

`--setup FILE` names a JSON list of steps run before each game, standing in for the person starting it:
`{"force_stop": PACKAGE}`, `{"launch": PACKAGE}`, `{"tap": [x, y]}`, `{"swipe": [x0, y0, x1, y1]}`,
`{"key": KEYCODE}`, `{"wait_ms": N}`. With `--package` alone the app is brought to the front once.

A direction is swiped across the board unless `--keys` sends D-pad key events instead. An app has no
answer key: the run reports what the harness itself confirmed, and the screen it ended on.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from .. import profiles
from ..environments.android import AndroidSurface
from ..play import play
from . import measure

# a setup swipe takes this long
SETUP_SWIPE_MS = 300
# an app brought to the front draws its first screen: wait this long before the harness looks
LAUNCH_WAIT_MS = 3000


async def setup(surface: AndroidSurface, steps: list[dict[str, Any]]) -> None:
    for step in steps:
        if "force_stop" in step:
            await surface.run(["shell", "am", "force-stop", step["force_stop"]])
        if "launch" in step:
            await surface.launch(step["launch"])
        if "tap" in step:
            await surface.tap(*step["tap"], 0)
        if "swipe" in step:
            await surface.drag(*step["swipe"], SETUP_SWIPE_MS)
        if "key" in step:
            await surface.key(step["key"])
        if "wait_ms" in step:
            await asyncio.sleep(step["wait_ms"] / 1000)


async def check(surface: AndroidSurface, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    png = await surface.capture()
    (out / "check.png").write_bytes(png)
    print(json.dumps({"size": await surface.size(), "app": await surface.foreground(),
                      "capture": str(out / "check.png"), "png_kib": round(len(png) / 1024, 1)}))
    return 0


async def main(args: argparse.Namespace) -> int:
    surface = AndroidSurface(serial=args.serial, package=args.package, keys=args.keys)
    try:
        await surface.size()
    except (RuntimeError, ValueError, OSError) as error:
        raise SystemExit(f"no Android device answers adb ({error}): start the emulator or attach a device") from None
    if args.check:
        return await check(surface, args.out)
    if bool(args.profile) == bool(args.family):
        raise SystemExit("give --profile or --family")
    if args.compute is None:
        raise SystemExit("--compute is required to play")
    profile = profiles.load(args.profile) if args.profile else None
    steps = json.loads(args.setup.read_text(encoding="utf-8")) if args.setup else \
        ([{"launch": args.package}, {"wait_ms": LAUNCH_WAIT_MS}] if args.package else [])
    goal, budget = measure.goal_and_budget(args)
    client, model = measure.vision_model(args)
    base = args.profile or args.package or args.family
    results = []
    try:
        for number in range(1, args.games + 1):
            if number == 1 or args.setup:
                await setup(surface, steps)
            name = f"{base}-{number:02d}"
            recorder = measure.Recorder(name)
            answered = len(model.answers) if model else 0
            started = time.monotonic()
            run = await play(surface, **({"profile": profile} if profile else {"family_id": args.family}),
                             goal=goal, budget=budget, policy=args.policy, model=model, watch=recorder,
                             accept_consent=args.accept_consent, run_id=name)
            result = measure.result(name, run, recorder, model, seconds=time.monotonic() - started)
            request = {"profile_id": args.profile, "family": args.family, "app": await surface.address(),
                       "goal": goal.to_json(), "budget": budget.to_json(), "policy": args.policy}
            try:
                screen = await surface.capture()
            except ValueError:
                screen = run.last_screen
            measure.write_game(args.out, name, result, request, run, recorder,
                               model.answers[answered:] if model else [], screen)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        if client is not None:
            await client.close()
        if results:
            measure.write_summary(args.out, args, results, profile=args.profile, family=args.family, package=args.package)
    return 0


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--check", action="store_true", help="capture one screen and report the device; play nothing")
    parser.add_argument("--profile", help="the registered app profile to play")
    parser.add_argument("--family", help="a game no profile registers: its family (its reading is made from a look)")
    parser.add_argument("--package", help="the app to bring to the front before the first game")
    parser.add_argument("--serial", help="the device (adb -s), when more than one is attached")
    parser.add_argument("--keys", action="store_true", help="send D-pad key events for directions, not swipes")
    parser.add_argument("--setup", type=Path, help="a JSON list of steps run before each game (see above)")
    measure.game_arguments(parser)
    # --check plays nothing and needs no budget
    next(action for action in parser._actions if action.dest == "compute").required = False
