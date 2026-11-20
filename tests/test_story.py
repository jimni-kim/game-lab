"""A story played by the model's own inputs (PLAN §27, session T): the lines are moved on by the loop without
asking, the model looks only where the story asks for a choice, and a choice that cannot be taken back is made
only as the person said such choices are to be made; where they said nothing the work is handed back there.
The story here is a toy: a box of lines at the bottom that a tap moves on, and at one line two options in the
middle, one of which must be pressed before the lines go on."""
from io import BytesIO
import json

import pytest
from PIL import Image, ImageDraw

from gamelab import adaptation, agent, direct, recovery, request
from gamelab.agent import play
from gamelab.contracts import Budget, GoalSpec
from gamelab.model import TurnResult

SIZE = (600, 400)
BOX = [20, 300, 560, 80]
OPTIONS = ([150, 100, 300, 50], [150, 180, 300, 50])
LINE = {"kind": "tap", "at": [300, 340], "advance": True}
ASKS_AT, ENDS_AT = 5, 9


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(direct, "SETTLE_S", 0.0)
    monkeypatch.setattr(direct, "STILL_GAP_S", 0.0)
    monkeypatch.setattr(recovery, "SETTLE_S", 0.0)
    monkeypatch.setattr(agent, "LINE_WAIT_S", 0.0)
    monkeypatch.setattr(adaptation, "IDENTIFY_WAIT_S", 0.0)


def inside(box, x, y):
    return box[0] <= x < box[0] + box[2] and box[1] <= y < box[1] + box[3]


class Story:
    """A surface showing the toy as a picture alone: `line` lines told, `picked` the option pressed (None before)."""
    name = "fake"
    reads_dom = False
    kinds = frozenset({"keys", "tap", "drag"})

    def __init__(self):
        self.line, self.picked, self.taps = 0, None, []

    @property
    def asking(self):
        return self.line == ASKS_AT and self.picked is None

    async def size(self):
        return list(SIZE)

    async def address(self):
        return "https://story.example/"

    async def capture(self, region=None):
        image = Image.new("RGB", SIZE, (30, 30, 60))
        draw = ImageDraw.Draw(image)
        x, y, w, h = BOX
        draw.rectangle((x, y, x + w, y + h), fill=(240, 240, 240))
        # the line's words: a bar whose length and shade are the line's own
        draw.rectangle((x + 20, y + 20, x + 60 + self.line * 50, y + 50), fill=(20 + self.line * 20, 20, 20))
        if self.asking:
            for left, top, width, height in OPTIONS:
                draw.rectangle((left, top, left + width, top + height), fill=(40, 120, 220))
        if self.line >= ENDS_AT:
            draw.rectangle((100, 20, 500, 70), fill=(30, 160, 60))
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    async def tap(self, x, y, duration_ms):
        self.taps.append((x, y))
        if self.asking:
            for index, box in enumerate(OPTIONS):
                if inside(box, x, y):
                    self.picked = index
                    self.line += 1
        elif self.line < ENDS_AT:
            self.line += 1

    async def key(self, key):
        pass

    async def drag(self, *args):
        raise AssertionError("nothing here is dragged")

    async def release(self):
        pass


class Page(Story):
    """The toy as a page that can be read: the box of lines, and the two options while the story asks."""
    reads_dom = True

    async def outline(self):
        def thing(box, text, **more):
            return {"at": [box[0] + box[2] // 2, box[1] + box[3] // 2], "size": box[2:], "tag": "div", "text": text,
                    "label": "", "classes": [], "bg": "", "flags": [], **more}
        items = [thing(BOX, f"line {self.line}", pressed=True), thing([20, 270, 100, 24], "Mina"),
                 thing([540, 10, 50, 20], "Menu", tag="button")]
        if self.asking:
            items += [thing(OPTIONS[0], "Tell her the truth", tag="button"), thing(OPTIONS[1], "Say nothing", tag="button")]
        return {"size": list(SIZE), "drawn": 0.0, "cut": False, "items": items}


class Teller:
    """A model that moves the story on with `advance`, presses the first option where it asks (and says whether
    that choice lasts), and reads the end."""

    def __init__(self, fake, lasting, box=True):
        self.fake, self.lasting, self.box, self.asked, self.named, self.named_first = fake, lasting, box, [], 0, None

    async def complete(self, request):
        context = json.loads(request.messages[0].content[0]["text"])
        if "families" in context:
            self.named += 1
            return TurnResult(text=json.dumps({"screen": "other", "families": [], "situation": "a story"}))
        if not context.get("play"):
            return TurnResult(text=json.dumps({"screen": "other", "board": None, "rules": None, "state": None}))
        self.asked.append(context)
        self.named_first = self.named if self.named_first is None else self.named_first
        answer = {"situation": "a story's line", "scene": "story", "guided": False, "score": None, "expect": "the next line"}
        if self.fake.line >= ENDS_AT:
            answer["action"] = {"kind": "done", "end": "won"}
        elif self.fake.asking:
            answer["action"] = {"kind": "tap", "at": [300, 125]}
            answer["choice"] = {"asks": "What do you tell Mina?", "options": ["Tell her the truth", "Say nothing"],
                                "picked": "Tell her the truth", "lasting": self.lasting}
        else:
            answer["action"] = {**LINE, **({"lines": BOX} if self.box else {})}
        return TurnResult(text=json.dumps(answer))


def ending(played):
    return played.record["outcome"], played.record["reason"]


async def told(fake, lasting, goal=GoalSpec("play_until_end"), box=True, **options):
    model = Teller(fake, lasting, box)
    played, made = await play(fake, model, None, goal=goal, budget=Budget(40, 1, 60), **options)
    return model, played, made


async def test_a_storys_lines_are_moved_on_by_the_loop_and_the_model_looks_only_at_the_choice():
    fake = Page()
    model, played, made = await told(fake, lasting=False)
    assert ending(played) == ("goal_reached", "won"), (played.record, made)
    # the first line, the choice, the line after it, the end: four looks for nine lines
    assert len(model.asked) == 4 and fake.line == ENDS_AT and fake.picked == 0
    assert made["story"]["stretches"][0] == {"at_input": 1, "sent": ASKS_AT, "stopped": "other_to_press", "by": "page"}
    assert made["story"]["by_code"] == (ASKS_AT - 1) + (ENDS_AT - ASKS_AT - 1) - 1 + 1
    # the model is told how many lines were sent and why the sending stopped, and nothing was sent onto the options
    assert model.asked[1]["history"][-1]["repeated"] == {"sent": ASKS_AT, "stopped": "other_to_press"}
    assert fake.taps[:ASKS_AT] == [(300, 340)] * ASKS_AT and fake.taps[ASKS_AT] == (300, 125)
    # which game this is was asked before the play only: a story is not asked about again on the way (the asking
    # gets itself past screens with inputs of its own, and would make the story's choice)
    assert model.named == model.named_first
    # a choice that can be made again is made, and kept for the record
    assert made["choices"] == [{"asks": "What do you tell Mina?", "options": ["Tell her the truth", "Say nothing"],
                                "picked": "Tell her the truth", "lasting": False, "at_input": ASKS_AT + 1}]


async def test_a_choice_that_cannot_be_taken_back_is_not_made_and_the_work_is_handed_back_with_what_is_asked():
    fake = Page()
    model, played, made = await told(fake, lasting=True)
    assert ending(played) == ("stopped", "choice"), (played.record, made)
    # no option was pressed: the story stands at its question
    assert fake.picked is None and fake.asking and len(fake.taps) == ASKS_AT
    assert made["choice"] == {"asks": "What do you tell Mina?", "options": ["Tell her the truth", "Say nothing"],
                              "picked": "Tell her the truth", "lasting": True, "at_input": ASKS_AT + 1, "sent": False}
    assert "Tell her the truth / Say nothing" in played.record["detail"]


async def test_such_a_choice_is_made_as_the_person_said_choices_are_to_be_made():
    fake = Page()
    model, played, made = await told(fake, lasting=True, goal=GoalSpec("play_until_end", choices="the kind way"))
    assert ending(played) == ("goal_reached", "won") and fake.picked == 0
    assert model.asked[0]["goal"]["choices"] == "the kind way"
    assert made["choices"][0]["as_asked"] == "the kind way" and "choice" not in made
    # an answer that says nothing of whether the choice lasts is one that does
    assert direct._choice({"options": ["a", "b"]})["lasting"] and direct._choice({"options": ["a"]}) is None


async def test_on_a_picture_alone_a_line_is_one_that_changes_only_where_lines_are_shown():
    fake = Story()
    model, played, made = await told(fake, lasting=True)
    assert ending(played) == ("stopped", "choice"), (played.record, made)
    # the model said where the lines are shown: the loop sends them for as long as nothing changes elsewhere; the
    # options coming up are a change elsewhere, and nothing is sent onto them
    assert made["story"]["stretches"] == [{"at_input": 1, "sent": ASKS_AT, "stopped": "elsewhere", "by": "picture"}]
    assert len(model.asked) == 2 and fake.taps == [(300, 340)] * ASKS_AT and fake.picked is None
    # where the lines are shown is not known: the model looks at what each line changed before the next is sent
    fake = Story()
    model, played, made = await told(fake, lasting=True, box=False)
    assert ending(played) == ("stopped", "choice") and fake.picked is None
    assert {stretch["sent"] for stretch in made["story"]["stretches"]} == {1}


async def test_a_storys_line_is_kept_as_no_step_of_a_routine_and_nothing_is_sent_onto_its_choice_unasked():
    """A game asked for from its start keeps how its first screens were passed as a routine; a line of the story
    is none of that: the choice's screen looks like a line's, and a step would be sent on it."""
    fake = Page()
    model, played, made = await told(fake, lasting=True, anew=True)
    assert ending(played) == ("stopped", "choice"), (played.record, made)
    assert len(fake.taps) == ASKS_AT and all(move["level"] == "L0" for move in played.moves)
    assert [routine["learned"] for routine in made.get("routines", [])] in ([], [0])


class Asker:
    def __init__(self, answer):
        self.answer = answer

    async def complete(self, request):
        return TurnResult(text=json.dumps(self.answer))


async def test_a_request_says_how_a_storys_choices_are_to_be_made():
    answer = {"url": "https://story.example/", "objective": "play_until_end", "choices": " 착한 쪽으로 "}
    asked = await request.understand(Asker(answer), "story.example 이야기를 착한 쪽으로 진행해줘")
    assert asked["goal"].choices == "착한 쪽으로" and asked["goal"].to_json()["choices"] == "착한 쪽으로"
    asked = await request.understand(Asker({**answer, "choices": None}), "story.example 이야기를 진행해줘")
    assert asked["goal"].choices is None and "choices" not in asked["goal"].to_json()
