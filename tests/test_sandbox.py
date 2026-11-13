"""Generated code runs apart from the harness: what it is given, and what it cannot do (adaptive perception plan §7)."""
from io import BytesIO
import os
from pathlib import Path
import time

from PIL import Image
import pytest

from gamelab.sandbox import Sandbox, SandboxError

needs_confinement = pytest.mark.skipif(not Sandbox("").confined, reason="no sandbox-exec on this system")


def png(colour=(10, 200, 30), size=(40, 30)):
    out = BytesIO()
    Image.new("RGB", size, colour).save(out, format="PNG")
    return out.getvalue()


def run(source, call="f", timeout=3.0, **arguments):
    with Sandbox(source, call_timeout_s=timeout) as sandbox:
        return sandbox.call(call, **arguments)


def test_it_is_handed_json_and_pictures_and_gives_json_back():
    source = """
from gamelab.sandbox.helpers import nearest
def read(picture, table):
    return {"size": [picture.width, picture.height], "colour": list(picture.pixel(3, 4)),
            "named": nearest(picture.mean([0, 0, 10, 10]), table)[0], "box": picture.box_of((10, 200, 30))}
"""
    with Sandbox(source) as sandbox:
        seen = sandbox.call("read", picture=sandbox.picture(png()), table={"green": [0, 255, 0], "red": [255, 0, 0]})
        assert seen == {"size": [40, 30], "colour": [10, 200, 30], "named": "green", "box": [0, 0, 40, 30]}
        # the worker lives for the game: a second call starts no new process
        sandbox.call("read", picture=sandbox.picture(png()), table={"a": [1, 1, 1]})
        assert sandbox.restarts == 1 and sandbox.calls == 2
    assert not sandbox.folder.exists()


def test_its_own_error_is_an_answer_and_the_worker_goes_on():
    with Sandbox("def f(x):\n    return 1 // x\n") as sandbox:
        with pytest.raises(SandboxError, match="ZeroDivisionError") as failed:
            sandbox.call("f", x=0)
        assert failed.value.reason == "error" and sandbox.call("f", x=1) == 1 and sandbox.restarts == 1
        with pytest.raises(SandboxError, match="no function"):
            sandbox.call("_private")


@pytest.mark.parametrize(("source", "reason"), [
    ("def f():\n    while True:\n        pass\n", "timeout"),
    ("import os\ndef f():\n    os.write(1, b'not json\\n')\n    return 1\n", "answer"),
    ("import os\ndef f():\n    os.write(1, b'x' * 2_000_000)\n", "answer"),
    ("def f():\n    return float('nan')\n", "error"),
    ("def f():\n    return {1, 2}\n", "error"),
    ("import os\ndef f():\n    os._exit(0)\n", "died"),
    ("raise SystemExit(3)\n", "error"),
    ("def f(:\n", "error"),
], ids=["an endless loop", "a line that is no JSON", "an answer too large", "a number JSON has none for",
        "a value that is no JSON", "the process ended", "it exits while loading", "code that does not parse"])
def test_code_that_does_not_answer_as_it_must_is_refused(source, reason):
    started = time.monotonic()
    with pytest.raises(SandboxError) as failed:
        run(source, timeout=1.0)
    assert failed.value.reason == reason and time.monotonic() - started < 15


def test_what_it_prints_is_not_read_as_an_answer():
    assert run('def f():\n    print(\'{"ok": true, "result": "forged"}\')\n    return "real"\n') == "real"


def test_memory_it_takes_beyond_its_bound_ends_it():
    source = "kept = []\ndef f():\n    while True:\n        kept.append(bytearray(20_000_000))\n"
    with Sandbox(source, call_timeout_s=20, memory_mb=200) as sandbox:
        with pytest.raises(SandboxError) as failed:
            sandbox.call("f")
        assert failed.value.reason in ("memory", "error")      # ended from outside, or its own MemoryError


def test_it_has_no_environment_variable_of_the_harness(monkeypatch):
    monkeypatch.setenv("AGENTIC_OPENAI_KEY", "secret")
    # only what the system itself sets for any process
    assert set(run("import os\ndef f():\n    return sorted(os.environ)\n")) <= {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}


@needs_confinement
def test_it_reads_nothing_of_the_home_folder_and_writes_only_in_its_own(tmp_path):
    secret = Path.home() / ".gamelab-sandbox-test"
    secret.write_text("secret")
    try:
        source = """
def read(path):
    return open(path).read()
def write(path):
    open(path, "w").write("x")
    return True
"""
        with Sandbox(source) as sandbox:
            with pytest.raises(SandboxError, match="PermissionError"):
                sandbox.call("read", path=str(secret))
            with pytest.raises(SandboxError, match="PermissionError"):
                sandbox.call("write", path=str(tmp_path / "out.txt"))
            with pytest.raises(SandboxError, match="PermissionError"):
                sandbox.call("write", path=str(secret))
            assert sandbox.call("write", path="scratch.txt") and (sandbox.folder / "scratch.txt").exists()
        assert secret.read_text() == "secret" and not (tmp_path / "out.txt").exists()
    finally:
        secret.unlink()


@needs_confinement
def test_it_reaches_no_network_and_starts_no_process():
    source = """
import socket, subprocess, os
def connect():
    socket.create_connection(("1.1.1.1", 53), timeout=2)
def spawn():
    return subprocess.run(["/bin/echo", "x"], capture_output=True).stdout.decode()
def fork():
    return os.fork()
"""
    with Sandbox(source) as sandbox:
        for call in ("connect", "spawn", "fork"):
            with pytest.raises(SandboxError) as failed:
                sandbox.call(call)
            assert failed.value.reason == "error" and ("Error" in failed.value.detail), (call, failed.value.detail)
