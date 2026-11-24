"""Play a game app on an Android device or emulator through adb and measure the harness.

The device must already be attached (`adb devices`); the emulator this was set up for is the AVD
`army-game` (`emulator -avd army-game`). The game is installed and signed in to by the person.

  .venv/bin/python -m gamelab.eval android --check --out out/device
      one capture, the screen's size and the app in front: is the device reachable, and what does the
      game look like

  .venv/bin/python -m gamelab.eval android --family slide_merge --package com.example.game \\
      --objective reach_value --target 2048 --compute 500 --out out/app
      the agent's loop (`agent.play`) on the app: a model reads the first moves and writes its adapter;
      without `--family` the game is found from the screen, and one no family is registered for is
      played by a model or by a family made for it

  .venv/bin/python -m gamelab.eval android --ask "퍼즐 앱에서 Fifteen 끝까지" --compute 200 --out out/ask
      a request in words: the app is one of those installed (`pm list packages -3` with a launcher
      activity), named to the model by its label; the goal and what the words say of the game come
      from the request too, and so does how far the work goes (`--objective levels`, `--stop`, `--allow`)

  .venv/bin/python -m gamelab.eval android --ask "꿈의 집 하던 판 이어서 레벨 3개" --compute 200 --out out/ask
      a request that goes on from the screen as it stands (or any run with `--resume`): the app is not
      stopped and started again, only brought to the front when another is there (PLAN §25, R4)

`--setup FILE` names a JSON list of steps run before each game, standing in for the person starting it:
`{"force_stop": PACKAGE}`, `{"clear": PACKAGE}` (`pm clear`: the app's data and progress gone),
`{"launch": PACKAGE}`, `{"tap": [x, y]}`, `{"swipe": [x0, y0, x1, y1]}`, `{"key": KEYCODE}`, `{"wait_ms": N}`.
With `--package` alone the app is brought to the front once. An app a request chose is stopped and started
again before each game, unless the work goes on from the screen as it stands (`resumed`); `apps.json` may give
an app its name (`label`) and have its data cleared first (`clear: true`).

An app's label is read once from its APK (`aapt2 dump badging`) and kept by package and version under
`out/apps/`; an app whose label cannot be read is named by its package alone.

A direction is swiped across the board unless `--keys` sends D-pad key events instead. An app has no
answer key: the run reports what the harness itself confirmed, and the screen it ended on.
"""
from __future__ import annotations

import argparse
import asyncio
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Awaitable, Callable

from .. import agent, request, work
from ..adaptation import Store
from ..contracts import Scope
from ..environments.android import COMMAND_TIMEOUT_S, SDK, AndroidSurface, Run
from . import measure

# the apps a person named by hand: a label in place of the APK's, and whether a game starts from no data
APPS = files(__package__) / "apps.json"
# each app's label as its APK gives it, by package and version
LABELS = measure.ROOT / "out" / "apps"
AAPT_VARIABLE = "GAMELAB_AAPT2"
_PACKAGE = re.compile(r"^package:(\S+)(?:\s+versionCode:(\d+))?", re.M)
_LABEL = re.compile(r"^application-label(-ko)?:'(.*)'$", re.M)

# a setup swipe takes this long
SETUP_SWIPE_MS = 300
# an app brought to the front draws its first screen: wait this long before the harness looks
LAUNCH_WAIT_MS = 3000


def aapt_path() -> str | None:
    """The aapt2 to read an APK with: GAMELAB_AAPT2, else the newest the SDK's build tools have."""
    if configured := os.environ.get(AAPT_VARIABLE, "").strip():
        return configured
    found = sorted((SDK / "build-tools").glob("*/aapt2"),
                   key=lambda path: [int(part) if part.isdigit() else 0 for part in path.parent.name.split(".")])
    return str(found[-1]) if found else None


async def aapt(args: list[str]) -> bytes:
    tool = aapt_path()
    if tool is None:
        raise OSError("no aapt2 in the SDK's build tools")
    process = await asyncio.create_subprocess_exec(tool, *args, stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(COMMAND_TIMEOUT_S):
            out, err = await process.communicate()
    except TimeoutError:
        process.kill()
        raise RuntimeError("aapt2 timed out") from None
    if process.returncode:
        raise RuntimeError(f"aapt2: {err.decode(errors='replace').strip() or process.returncode}")
    return out


def chosen() -> dict[str, dict[str, Any]]:
    """`apps.json`: by package, what the person said of an app."""
    return {name: entry for name, entry in json.loads(APPS.read_text(encoding="utf-8")).items() if not name.startswith("_")}


async def label(run: Run, package: str, version: str | None, *, read: Callable[[list[str]], Awaitable[bytes]] = aapt,
                kept: Path | None = None) -> str | None:
    """The app's name as its APK gives it (the Korean one first when it has one), read once for each version
    and kept; None when it cannot be read."""
    path = (kept or LABELS) / f"{package}-{version or 'unknown'}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))["label"]
    found = None
    try:
        paths = [line.removeprefix("package:").strip() for line in
                 (await run(["shell", "pm", "path", package])).decode(errors="replace").splitlines() if line.startswith("package:")]
        base = next((one for one in paths if one.endswith("/base.apk")), paths[0] if paths else None)
        if base is not None:
            with tempfile.TemporaryDirectory() as folder:
                local = str(Path(folder) / "base.apk")
                await run(["pull", base, local])
                labels = dict(_LABEL.findall((await read(["dump", "badging", local])).decode(errors="replace")))
            names = list(dict.fromkeys(name for name in (labels.get("-ko"), labels.get("")) if name))
            found = " / ".join(names) or None
    except (RuntimeError, OSError):
        found = None
    # an app whose APK cannot be read is not tried again until another version is installed
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"package": package, "version": version, "label": found}, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return found


async def installed(run: Run, *, read: Callable[[list[str]], Awaitable[bytes]] = aapt, kept: Path | None = None,
                    named: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The apps a person installed that can be opened from the launcher, each `{"package", "label"}`: the
    label `apps.json` gives, else the APK's, else the package's own name. One `apps.json` hides is left out."""
    named = chosen() if named is None else named
    listed = _PACKAGE.findall((await run(["shell", "pm", "list", "packages", "-3", "--show-versioncode"])).decode(errors="replace"))
    launchers = (await run(["shell", "cmd", "package", "query-activities", "--brief", "-a", "android.intent.action.MAIN",
                            "-c", "android.intent.category.LAUNCHER"])).decode(errors="replace")
    opened = {token.split("/")[0] for token in launchers.split() if "/" in token}
    apps = []
    for package, version in sorted(listed):
        if package in opened and not named.get(package, {}).get("hide"):
            given = named.get(package, {}).get("label")
            apps.append({"package": package, "label": given or await label(run, package, version or None, read=read, kept=kept) or package})
    return apps


def prepared(package: str, named: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The steps before each game of an app a request chose: stopped, its data cleared when `apps.json` says
    so, and started again."""
    clear = (chosen() if named is None else named).get(package, {}).get("clear")
    return [{"force_stop": package}, *([{"clear": package}] if clear else []), {"launch": package}, {"wait_ms": LAUNCH_WAIT_MS}]


async def resumed(surface: AndroidSurface, package: str) -> list[dict[str, Any]]:
    """The steps before work that goes on from the screen as it stands (PLAN §25, R4): none when the app is in
    front, else it is brought there as it was left (`am start` on a running app's task does not start it again)."""
    try:
        if await surface.foreground() == package:
            return []
    except RuntimeError:
        pass
    return [{"launch": package}, {"wait_ms": LAUNCH_WAIT_MS}]


async def steps_for(args: argparse.Namespace, surface: AndroidSurface) -> list[dict[str, Any]]:
    """What is done before the first game: the steps of `--setup`, else the app a request chose started again
    (or only brought to the front when the work goes on from the screen), else the app brought to the front."""
    goal, _ = measure.goal_and_budget(args)
    if args.setup:
        return json.loads(args.setup.read_text(encoding="utf-8"))
    if args.package and goal.scope == Scope.CURRENT_GAME:
        return await resumed(surface, args.package)
    if args.asked:
        return prepared(args.package)
    return [{"launch": args.package}, {"wait_ms": LAUNCH_WAIT_MS}] if args.package else []


async def device(surface: AndroidSurface, serial: str | None) -> str:
    """The device's serial, the name its lock is held by."""
    try:
        return (await surface.run(["get-serialno"])).decode(errors="replace").strip() or serial or "device"
    except RuntimeError:
        return serial or "device"


async def setup(surface: AndroidSurface, steps: list[dict[str, Any]]) -> None:
    for step in steps:
        if "force_stop" in step:
            await surface.run(["shell", "am", "force-stop", step["force_stop"]])
        if "clear" in step:
            await surface.run(["shell", "pm", "clear", step["clear"]])
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


def stoppable() -> asyncio.Event:
    """An event set by the first interrupt (Ctrl-C) or termination: the game then ends at its next move, as
    `cancelled`, and is written down like any other. A second one stops at once, writing nothing."""
    import signal

    cancel, loop = asyncio.Event(), asyncio.get_running_loop()

    def stop(number: int) -> None:
        if cancel.is_set():
            # the second: as if no handler had been set
            loop.remove_signal_handler(number)
            os.kill(os.getpid(), number)
            return
        print(json.dumps({"stopping": "the game ends at its next move and is written down; again to stop at once"}),
              flush=True)
        cancel.set()
    for number in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(number, stop, number)
        except (NotImplementedError, RuntimeError):
            pass
    return cancel


async def check(surface: AndroidSurface, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    png = await surface.capture()
    (out / "check.png").write_bytes(png)
    print(json.dumps({"size": await surface.size(), "app": await surface.foreground(),
                      "capture": str(out / "check.png"), "png_kib": round(len(png) / 1024, 1)}))
    return 0


async def play_one(args: argparse.Namespace, number: int, surface: AndroidSurface, base: str, model: Any,
                   writer: Any = None, *, steps: list[dict[str, Any]] = (), cancel: asyncio.Event | None = None,
                   watching: Callable[[measure.Recorder], agent.Watch] | None = None) -> dict[str, Any]:
    """One game on the device after `steps`, its files written; `cancel` ends it at the next input, and
    `watching` may hang another watcher around the game's recorder."""
    goal, budget = measure.goal_and_budget(args)
    # a request in words set the goal and says what it can of the game
    hint = (args.asked["hint"] or args.hint) if getattr(args, "asked", None) else args.hint
    await setup(surface, steps)
    name = f"{base}-{number:02d}"
    recorder = measure.Recorder(name)
    answered = len(model.answers)
    started = time.monotonic()
    # read by an adapter a model writes for this app, by the agent's own loop (as `eval web`), as many games of
    # it as the work asks for; with no family named the agent finds it from the screen, or plays a game it has none for
    run, adapted = await work.play(surface, model, None if args.identify else args.family, goal=goal,
                                    budget=budget, policy=args.policy, store=Store(args.adapter_store),
                                    fresh=args.regenerate, cancel=cancel, watch=watching(recorder) if watching else recorder,
                                    accept_consent=args.accept_consent, writer=writer, hint=hint,
                                    game=measure.game_within(args),
                                    task=measure.task_named(args), wait=measure.task_waits(args), way=measure.way_named(args), research=measure.researched(args), review=measure.reviewed(args),
                                    light=measure.light_model(args), turn=measure.turn_judges(args))
    result = measure.result(name, run, recorder, model, seconds=time.monotonic() - started, **measure.handed(adapted))
    request = {"family": args.family, "app": await surface.address(),
               "goal": goal.to_json(), "budget": budget.to_json(), "policy": args.policy}
    try:
        screen = await surface.capture()
    except ValueError:
        screen = run.last_screen
    measure.write_game(args.out, name, result, request, run, recorder, model.answers[answered:], screen)
    return result


async def main(args: argparse.Namespace) -> int:
    surface = AndroidSurface(serial=args.serial, package=args.package, keys=args.keys)
    try:
        await surface.size()
    except (RuntimeError, ValueError, OSError) as error:
        raise SystemExit(f"no Android device answers adb ({error}): start the emulator or attach a device") from None
    if args.check:
        return await check(surface, args.out)
    if args.compute is None:
        raise SystemExit("--compute is required to play")
    client, model = measure.vision_model(args)
    if model is None:
        raise SystemExit("a game is played with a model: name one with --vision-model")
    args.asked = None
    if args.ask:
        # the app is the request's to say, among those installed; what to play for too. Nobody is here to be asked
        # back: a question the request leaves open is only told (`unclear`), and the game is played as read
        args.asked = await request.understand(model, args.ask, await installed(surface.run))
        print(json.dumps({"asked": {"url": args.asked["url"], "app": args.asked["app"],
                                    "goal": measure.goal_and_budget(args)[0].to_json(), "hint": args.asked["hint"],
                                    "within": args.asked["within"], "task": args.asked["task"],
                                    **({"unclear": args.asked["unclear"]} if args.asked.get("unclear") else {})}},
                         ensure_ascii=False), flush=True)
        if args.asked["url"] is not None:
            raise SystemExit("the request names a page: play it with eval web --ask")
        if args.asked["app"] is None:
            raise SystemExit("the request names no app installed on the device")
        args.package = surface.package = args.asked["app"]
    steps = await steps_for(args, surface)
    # a game after the first starts again as the first did, unless the work goes on from where the last one stopped
    again = bool(args.setup) or (bool(args.asked) and measure.goal_and_budget(args)[0].scope != Scope.CURRENT_GAME)
    writer_client, writer = measure.writer_model(args, model)
    base = args.package or args.family or "app"
    results = []
    cancel = stoppable()
    try:
        with measure.lock(f"android-{await device(surface, args.serial)}"):
            for number in range(1, args.games + 1):
                if cancel.is_set():
                    break
                result = await play_one(args, number, surface, base, model, writer, cancel=cancel,
                                        steps=steps if number == 1 or again else [])
                results.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
    except measure.Busy as busy:
        raise SystemExit(f"{busy} is in use by another run") from None
    finally:
        for opened in (client, writer_client):
            if opened is not None:
                await opened.close()
        await measure.close_light(args)
        if results:
            measure.write_summary(args.out, args, results, family=args.family, package=args.package)
    return 0


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--check", action="store_true", help="capture one screen and report the device; play nothing")
    parser.add_argument("--family", help="the game's family; without it the harness finds it from the screen")
    parser.add_argument("--hint", help="what the person said of the game, told to the model that finds what it is")
    parser.add_argument("--ask", metavar="WORDS",
                        help="a request in words (\"the puzzles app, Fifteen to its end\"): it names the app among those "
                             "installed and sets the goal in place of --package, --objective and --target")
    parser.add_argument("--package", help="the app to bring to the front before the first game")
    parser.add_argument("--serial", help="the device (adb -s), when more than one is attached")
    parser.add_argument("--keys", action="store_true", help="send D-pad key events for directions, not swipes")
    parser.add_argument("--setup", type=Path, help="a JSON list of steps run before each game (see above)")
    parser.add_argument("--generate", action="store_true",
                        help="accepted for old commands: the game is now always read by an adapter a model writes")
    parser.add_argument("--identify", action="store_true",
                        help="do not tell the harness the game's family; it is found from the screen")
    parser.add_argument("--adapter-store", type=Path, default=measure.ROOT / "out" / "adapters",
                        help="where generated adapters are kept by app and reused")
    parser.add_argument("--regenerate", action="store_true", help="write a new adapter even when one is kept for the app")
    measure.game_arguments(parser)
    # --check plays nothing and needs no budget
    next(action for action in parser._actions if action.dest == "compute").required = False
