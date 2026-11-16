"""Light judgements on small pictures (PLAN §19): pictures sized as Jev takes them, its questions and answers, and
a chat model asked in parts no larger than its pictures allow. No network."""
import base64
from io import BytesIO
import json

import httpx
from PIL import Image

from gamelab import checker
from gamelab.model import TurnResult, Usage


def png(width, height, colour=(10, 20, 30)):
    out = BytesIO()
    Image.new("RGB", (width, height), colour).save(out, format="PNG")
    return out.getvalue()


def size(data):
    with Image.open(BytesIO(data)) as image:
        return image.size


def test_a_picture_is_sized_as_jev_takes_it_whatever_it_was():
    least, most = checker.JEV_SIDES
    for width, height in ((127, 128), (30, 30), (60, 200), (1080, 2400), (5000, 300)):
        got = size(checker.fitted(png(width, height)))
        assert all(least <= side <= most for side in got), (width, height, got)
        assert checker.JEV_AREA[0] <= got[0] * got[1] <= checker.JEV_AREA[1], (width, height, got)


async def test_jev_is_asked_every_question_with_its_picture_named_in_it_and_its_answers_read():
    sent = {}

    def answer(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"answers": {"a": {"type": "noul", "noul": 0.93},
                                                     "b": {"type": "choice", "choice": "s1", "probabilities": {"s0": 0.1, "s1": 0.9}}},
                                         "usage": {"input_tokens": 500, "output_tokens": 0, "cost": 0.00002}})
    jev = checker.Jev("key")
    jev._http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    got = await jev.ask([checker.Check("a", checker.NOUL, "Is there a block in {image}?", png(50, 50)),
                         checker.Check("b", checker.CHOICE, "Which shape is {image}?", png(80, 40), options={"s0": "#", "s1": "##"})],
                        "a game")
    assert got == {"a": {"p": 0.93}, "b": {"choice": "s1", "p": 0.9}}
    # every picture an asset, named in its own question; a choice's options as its criteria
    references = list(sent["assets"])
    assert len(references) == 2 and references[0] in sent["questions"]["a"]["instructions"]
    assert references[1] in sent["questions"]["b"]["instructions"] and sent["questions"]["b"]["criteria"] == {"s0": "#", "s1": "##"}
    picture = base64.b64decode(sent["assets"][references[0]]["data_base64"])
    assert min(size(picture)) >= checker.JEV_SIDES[0]
    assert jev.spent.cost == 0.00002 and jev.spent.checks == 2


class Canned:
    """A chat model that says yes to every key it is asked, and counts the pictures of each question."""

    def __init__(self):
        self.pictures = []

    async def complete(self, request):
        parts = request.messages[0].content
        self.pictures.append(sum(part["type"] == "image" for part in parts))
        keys = [part["text"].split()[1] for part in parts if part["type"] == "text" and part["text"].startswith("key ")]
        return TurnResult(text=json.dumps({key: 1.0 for key in keys}), usage=Usage(100, 10))

    async def close(self):
        pass


async def test_a_chat_model_is_asked_in_parts_no_larger_than_the_pictures_it_takes():
    chat = checker.Chat("light", "key", base="http://proxy")
    chat.client = Canned()
    checks = [checker.Check(f"c{index}", checker.NOUL, "Is there a block in {image}?", png(20, 20)) for index in range(64)]
    got = await chat.ask(checks, "a game")
    assert len(got) == 64 and all(answer == {"p": 1.0} for answer in got.values())
    assert chat.client.pictures == [checker.CHAT_PICTURES, 64 - checker.CHAT_PICTURES]
