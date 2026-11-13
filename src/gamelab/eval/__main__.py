"""Measure the harness: `python -m gamelab.eval policies | web | android ...`."""
from __future__ import annotations

import argparse
import asyncio
import inspect

from . import android, policies, web


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m gamelab.eval", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, module in (("policies", policies), ("web", web), ("android", android)):
        command = commands.add_parser(name, description=module.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        module.configure(command)
        command.set_defaults(run=module.main)
    args = parser.parse_args()
    run = args.run
    del args.run
    done = run(args)
    return asyncio.run(done) if inspect.isawaitable(done) else done


if __name__ == "__main__":
    raise SystemExit(main())
