"""The harness's side of the sandbox: start the worker, hand it pictures and JSON, bound every call."""
from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import selectors
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
from typing import Any

# one call's time, the worker's memory, one answer's size, and the files it may leave in its folder
CALL_TIMEOUT_S = 5.0
START_TIMEOUT_S = 20.0
MEMORY_MB = 512
ANSWER_BYTES = 1_000_000
FILE_BYTES = 16_000_000
# how often a waiting call looks at the worker's memory
WATCH_INTERVAL_S = 0.1
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
# no network, no writes but its folder, nothing of the home folder but the Python it runs on, no children
PROFILE = """(version 1)
(allow default)
(deny network*)
(deny process-fork)
(deny file-write*)
(allow file-write* (subpath "{folder}"))
(deny file-read-data (subpath "{home}"))
(allow file-read-data {readable})
"""


class SandboxError(Exception):
    """The generated code did not answer as it must: `reason` says how (timeout, memory, answer, error)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


def _limits() -> None:
    """In the child, before the worker starts: no core file, bounded files, and no more CPU than a runaway
    loop would need to be noticed."""
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_BYTES, FILE_BYTES))
    os.setsid()


class Sandbox:
    def __init__(self, source: str, *, call_timeout_s: float = CALL_TIMEOUT_S, memory_mb: int = MEMORY_MB) -> None:
        self.source, self.call_timeout_s, self.memory_mb = source, call_timeout_s, memory_mb
        self.folder = Path(tempfile.mkdtemp(prefix="gamelab-sandbox-")).resolve()
        (self.folder / "adapter.py").write_text(source, encoding="utf-8")
        self.confined = sys.platform == "darwin" and os.access(SANDBOX_EXEC, os.X_OK)
        self.process: subprocess.Popen | None = None
        self.calls = self.restarts = 0
        self._pictures = 0

    def _command(self) -> list[str]:
        package_root = str(Path(__file__).resolve().parents[2])
        # -I: no environment variables, no user site, no script folder on the path
        python = [sys.executable, "-I", "-c",
                  f"import sys; sys.path.insert(0, {package_root!r}); from gamelab.sandbox import worker; "
                  f"worker.main('adapter.py')"]
        if not self.confined:
            return python
        readable = {package_root, sys.prefix, sys.base_prefix, sysconfig.get_paths()["purelib"],
                    str(Path(sys.executable).resolve().parent.parent), str(self.folder)}
        profile = PROFILE.format(folder=self.folder, home=Path.home().resolve(),
                                 readable=" ".join(f'(subpath "{Path(path).resolve()}")' for path in sorted(readable)))
        return [SANDBOX_EXEC, "-p", profile, *python]

    def _start(self) -> None:
        self.process = subprocess.Popen(self._command(), cwd=self.folder, env={}, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, preexec_fn=_limits)
        self.restarts += 1
        # starting the interpreter is not the code's time: a call's bound starts once the worker answers
        self._send("$ready", {})
        self._answer(START_TIMEOUT_S)

    def _send(self, name: str, arguments: dict[str, Any]) -> None:
        try:
            self.process.stdin.write((json.dumps({"call": name, "args": arguments}) + "\n").encode())
            self.process.stdin.flush()
        except OSError as error:
            self._kill()
            raise SandboxError("died", str(error)) from None

    def picture(self, png: bytes) -> dict[str, str]:
        """`png` put in the worker's folder, as the argument that hands it over as a `helpers.Picture`."""
        self._pictures += 1
        name = f"picture-{self._pictures}.png"
        (self.folder / name).write_bytes(png)
        return {"$picture": name}

    def forget(self, argument: dict[str, str]) -> None:
        (self.folder / argument["$picture"]).unlink(missing_ok=True)

    def call(self, name: str, **arguments: Any) -> Any:
        """The code's function `name` called with JSON arguments (and pictures from `picture`); its JSON result.
        A call that fails by the code's own error leaves the worker running; one that hangs, grows too large
        or answers with something that is no answer ends it, and the next call starts it afresh."""
        if self.process is None or self.process.poll() is not None:
            self._start()
        self.calls += 1
        self._send(name, arguments)
        line = self._answer(self.call_timeout_s)
        try:
            answer = json.loads(line)
            if not isinstance(answer, dict) or type(answer.get("ok")) is not bool:
                raise ValueError("not an answer")
        except ValueError as error:
            self._kill()
            raise SandboxError("answer", str(error)) from None
        if not answer["ok"]:
            raise SandboxError("error", str(answer.get("error")))
        return answer.get("result")

    def _answer(self, timeout_s: float) -> bytes:
        deadline = time.monotonic() + timeout_s
        chunks, size = [], 0
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while True:
                if time.monotonic() >= deadline:
                    self._kill()
                    raise SandboxError("timeout")
                if self._memory_mb() > self.memory_mb:
                    self._kill()
                    raise SandboxError("memory")
                if not selector.select(WATCH_INTERVAL_S):
                    continue
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    self._kill()
                    raise SandboxError("died")
                chunks.append(chunk)
                size += len(chunk)
                if size > ANSWER_BYTES:
                    self._kill()
                    raise SandboxError("answer", "too large")
                if chunk.endswith(b"\n"):
                    return b"".join(chunks)

    def _memory_mb(self) -> float:
        """The worker's resident memory, asked of the system: macOS enforces no address-space limit."""
        try:
            out = subprocess.run(["/bin/ps", "-o", "rss=", "-g", str(self.process.pid)], capture_output=True, text=True, timeout=2)
            return sum(int(value) for value in out.stdout.split()) / 1024
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return 0.0

    def _kill(self) -> None:
        if self.process is not None:
            try:
                os.killpg(self.process.pid, 9)
            except OSError:
                self.process.kill()
            self.process.wait()
            self.process = None

    def close(self) -> None:
        self._kill()
        shutil.rmtree(self.folder, ignore_errors=True)

    def __enter__(self) -> Sandbox:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
