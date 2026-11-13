"""The process generated code runs in: one JSON request a line on stdin, one JSON answer a line on stdout.

Started by `host.Sandbox` with the code's file as its argument. A request is `{"call": name, "args": {...}}`;
an argument `{"$picture": "name.png"}` is that file of the working folder, opened as a `helpers.Picture`.
The answer is `{"ok": true, "result": ...}` or `{"ok": false, "error": "..."}`. Whatever the code prints goes
nowhere: its only way out is the value it returns.
"""
from __future__ import annotations

import io
import json
import os
import sys


def main(source_path: str) -> None:
    from gamelab.sandbox import helpers

    answers = os.fdopen(os.dup(1), "w", encoding="utf-8")
    # the code's own prints must not be read as answers
    sys.stdout = sys.stderr = io.StringIO()
    namespace: dict = {"__name__": "adapter"}
    loaded = None
    try:
        with open(source_path, encoding="utf-8") as file:
            exec(compile(file.read(), "adapter.py", "exec"), namespace)
    except BaseException as error:           # a SystemExit at import is a failed load, not the worker's end
        loaded = f"load: {type(error).__name__}: {error}"
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request["call"] == "$ready":
                # the host's first line: the interpreter and its libraries are up (the code itself may not be)
                answers.write('{"ok": true, "result": null}\n')
                answers.flush()
                continue
            if loaded is not None:
                raise RuntimeError(loaded)
            function = namespace.get(request["call"])
            if not callable(function) or request["call"].startswith("_"):
                raise LookupError(f"no function {request['call']}")
            arguments = {name: helpers.Picture.open(value["$picture"]) if isinstance(value, dict) and "$picture" in value
                         else value for name, value in request["args"].items()}
            answer = {"ok": True, "result": function(**arguments)}
            text = json.dumps(answer, allow_nan=False)
        except BaseException as error:
            text = json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"[:500]})
        answers.write(text + "\n")
        answers.flush()


if __name__ == "__main__":
    main(sys.argv[1])
