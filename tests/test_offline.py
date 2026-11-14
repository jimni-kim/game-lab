"""Measuring a model on kept screens with no site (`eval offline`): a small store made by the adaptation tests'
fakes, and a model that reads each kept screen as the fake drew it."""
import argparse
import base64
from copy import deepcopy
import json

import pytest

from gamelab import adaptation, agent, recovery
from gamelab.adaptation import Adaptation, Made, Store
from gamelab.eval import measure, offline
from gamelab.model import Recorded, TurnResult
from fake_screen import Screen, render
from test_adaptation import Author, wrong
from test_generated import source


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(adaptation, "SETTLE_S", 0.0)
    # the adapters here are the ones a model writes: the host's plain one is tried in test_plain.py
    monkeypatch.setattr(adaptation, "PLAIN_INSETS", ())
    monkeypatch.setattr(adaptation, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(agent, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)


class Shown(Author):
    """The Author, shown a kept screen: it reads the state that screen was drawn from (`drawn`, by picture),
    says the game is a 2048 when the screen is one of them, and fails on the screens in `failing`."""

    def __init__(self, fake, modules, drawn, failing=()):
        super().__init__(fake, modules)
        self.drawn, self.failing = drawn, set(failing)

    async def complete(self, request):
        content = request.messages[0].content
        context = json.loads(content[0]["text"])
        png = base64.b64decode(content[1]["source"]["data"]) if len(content) > 1 else None
        if png in self.failing:
            raise TimeoutError("the gateway gave up")
        if "families" in context:
            self.asked.append(("identify", context, len(content) - 1))
            known = png in self.drawn
            return TurnResult(text=json.dumps({"screen": "game" if known else "other",
                                               "families": ["slide_merge"] if known else [], "situation": "as drawn"}))
        if png in self.drawn:
            self.fake.board = deepcopy(self.drawn[png])
        return await super().complete(request)


async def kept_store(tmp_path):
    """A store with one 2048 site: seven looks the fake model read, and the adapter that reads them."""
    fake = Screen()
    trace = await Adaptation(Author(fake, []), "slide_merge").explore(fake)
    Store(tmp_path / "store").save(trace, Made(source(fake), 1, False, {"passed": True}))
    return fake, trace


def screens_file(tmp_path, fake):
    """A --screens file: a notice over the board, to identify (no game) and to recover from."""
    (tmp_path / "notice.png").write_bytes(fake._cover(render(fake.board)))
    listed = {"identify": [{"name": "notice", "png": "notice.png", "family": None}],
              "recover": [{"name": "notice", "png": "notice.png", "family": "slide_merge", "reason": "board_not_found",
                           "expected": "a tap on its button"}]}
    (tmp_path / "screens.json").write_text(json.dumps(listed))
    return tmp_path / "screens.json"


async def test_each_kept_screen_read_is_compared_with_the_state_kept_for_it(tmp_path):
    fake, trace = await kept_store(tmp_path)
    drawn = {look.png: look.state for look in trace.looks}
    # the middle screen is read with one tile more than it shows; the last cannot be asked about
    misread = deepcopy(trace.looks[3].state)
    row, col = next((r, c) for r in range(4) for c in range(4) if misread["grid"][r][c] == 0)
    misread["grid"][row][col] = 2
    drawn[trace.looks[3].png] = misread
    model = Shown(fake, [], drawn, failing=[trace.looks[6].png])
    sites = offline.kept([tmp_path / "store"])
    assert len(sites) == 1 and sites[0].name.startswith("unregistered.example-")
    found = await offline.evaluate(sites, offline.screens(None), ["read"], model, None, looks=7, out=tmp_path / "out")
    rows = {(row["look"], row["board_known"]): row for row in found["read"]}
    assert list(rows) == [(0, False), (0, True), (3, True), (6, True)]
    blind, seen = rows[(0, False)], rows[(0, True)]
    assert blind["exact"] and blind["wrong"] == 0 and blind["leaves"] == 16 and blind["rule_valid"]
    assert blind["board_iou"] == 1.0 and blind["rules_agree"] and blind["truth"] == "kept"
    assert seen["exact"] and "board_iou" not in seen
    assert not rows[(3, True)]["exact"] and rows[(3, True)]["wrong"] == 1 and rows[(3, True)]["rule_valid"]
    # a question that failed is that row's error, and the others were still asked
    assert "TimeoutError" in rows[(6, True)]["error"] and rows[(6, True)]["model_errors"]
    # the screen not known is shown alone, the known ones with the grid drawn over the board
    assert [images for kind, _, images in model.asked] == [1, 2, 2]


async def test_an_adapter_is_written_from_the_first_screens_and_checked_on_all_of_them(tmp_path):
    fake, trace = await kept_store(tmp_path)
    model = Shown(fake, [wrong(fake), source(fake)], {})
    sites = offline.kept([tmp_path / "store"])
    found = await offline.evaluate(sites, offline.screens(None), ["write"], Recorded(Author(fake, [])), model,
                                   looks=7, out=tmp_path / "out")
    [row] = found["write"]
    assert row["adopted"] and row["generation"] == 2 and row["checks"][0] is not None and row["checks"][1] is None
    assert row["whole"]["passed"] and row["whole"]["looks"] == 7 and not row["uses_marks"] and row["questions"] == 2
    # the writer was shown the five looks not kept back, and every attempt is kept
    assert [len(context["examples"]) for kind, context, _ in model.asked if kind == "write"] == [5, 5]
    folder = tmp_path / "out" / "write" / sites[0].name
    assert (folder / "attempt-1.py").read_text() == wrong(fake) and (folder / "adopted.py").is_file()
    assert (folder / "attempt-2.py").read_text() == source(fake)


async def test_a_screen_is_identified_with_one_question_and_no_input_and_a_recovery_is_recorded_as_answered(tmp_path):
    fake, trace = await kept_store(tmp_path)
    model = Shown(fake, [], {look.png: look.state for look in trace.looks})
    shown = offline.screens(screens_file(tmp_path, fake))
    found = await offline.evaluate(offline.kept([tmp_path / "store"]), shown, ["identify", "recover"], model, None,
                                   looks=7, out=tmp_path / "out")
    site, notice = found["identify"]
    assert site["expected"] == "slide_merge" and site["families"] == ["slide_merge"] and site["ok"]
    assert notice["expected"] is None and notice["families"] == [] and notice["ok"] and notice["questions"] == 1
    assert [kind for kind, _, _ in model.asked] == ["identify", "identify", "recover"]
    [recovered] = found["recover"]
    assert recovered["action"]["kind"] == "tap" and recovered["situation"] == "a notice"
    assert recovered["expected"] == "a tap on its button" and len(recovered["sent"]) == 1


async def test_the_command_prints_a_table_per_task_and_writes_every_row_and_the_tokens(tmp_path, monkeypatch, capsys):
    fake, trace = await kept_store(tmp_path)
    model = Recorded(Shown(fake, [source(fake)], {look.png: look.state for look in trace.looks}))
    monkeypatch.setattr(measure, "vision_model", lambda args: (None, model))
    parser = argparse.ArgumentParser()
    offline.configure(parser)
    args = parser.parse_args(["--store", str(tmp_path / "store"), "--screens", str(screens_file(tmp_path, fake)),
                              "--vision-model", "fake", "--out", str(tmp_path / "out")])
    assert await offline.main(args) == 0
    printed = capsys.readouterr().out
    assert all(f"\n{task}" in printed for task in offline.TASKS) and "rules ok" in printed
    report = json.loads((tmp_path / "out" / "offline.json").read_text())
    assert [len(report[task]) for task in offline.TASKS] == [4, 1, 2, 1]
    assert report["writer_model"] == "fake" and "not an answer key" in report["truth"]
    assert report["tokens"]["total"] == {"input": 0, "output": 0} and len(report["model_answers"]) == 4 + 1 + 2 + 1


async def test_a_writer_model_named_apart_is_recorded_with_the_readers_questions(monkeypatch):
    monkeypatch.setattr(measure.model, "api_key", lambda: "key")
    args = argparse.Namespace(vision_model="reader", writer_model="writer", model_base="http://models.invalid/v1")
    client, vision = measure.vision_model(args)
    other, writer = measure.writer_model(args, vision)
    apart_client, apart = measure.writer_model(args, vision, apart=True)
    try:
        assert other.model == "writer" and writer.answers is vision.answers and apart.answers is not vision.answers
        # the same model named twice is the reader writing
        assert measure.writer_model(argparse.Namespace(vision_model="reader", writer_model="reader"), vision) == (None, None)
    finally:
        for opened in (client, other, apart_client):
            await opened.close()
