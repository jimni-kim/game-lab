"""Measure the harness: `python -m gamelab.eval policies | web | android | offline ...`; `serve` plays a request
in words on a demo page; `models` lists what the model API serves."""
from __future__ import annotations

import argparse
import asyncio
import inspect
import os
from pathlib import Path

from .. import model
from . import android, measure, offline, policies, serve, web


def load_env(path: Path) -> None:
    """Set the variables a `.env` file names (`NAME=value` lines) that the environment does not already have."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        name, found, value = line.strip().removeprefix("export ").partition("=")
        if found and name.strip() and not name.startswith("#"):
            os.environ.setdefault(name.strip(), value.strip().strip("'\""))


async def models(args: argparse.Namespace) -> int:
    key = model.api_key()
    if not key:
        raise SystemExit(f"set {model.KEY_VARIABLE}")
    print("\n".join(await model.available(key, model.api_base())))
    return 0


def main() -> int:
    load_env(measure.ROOT / ".env")
    parser = argparse.ArgumentParser(prog="python -m gamelab.eval", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, module in (("policies", policies), ("web", web), ("android", android), ("offline", offline), ("serve", serve)):
        command = commands.add_parser(name, description=module.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        module.configure(command)
        command.set_defaults(run=module.main)
    listing = commands.add_parser("models", description="The model names the API serves this key.")
    listing.set_defaults(run=models)
    args = parser.parse_args()
    run = args.run
    del args.run
    done = run(args)
    return asyncio.run(done) if inspect.isawaitable(done) else done


if __name__ == "__main__":
    raise SystemExit(main())
