"""A request in words turned into what a game is asked for (PLAN §10, G12): one question, and the host
checks the answer."""
import json

from gamelab import request
from gamelab.model import TurnResult


class Says:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    async def complete(self, turn):
        self.asked.append(json.loads(turn.messages[0].content[0]["text"]))
        return TurnResult(text=self.answer if isinstance(self.answer, str) else json.dumps(self.answer))


async def test_a_request_gives_the_page_the_goal_and_what_it_says_of_the_game():
    words = "play 2048 at https://ovolve.github.io/2048-AI/ until the 512 tile"
    model = Says({"url": "https://ovolve.github.io/2048-AI/", "objective": "reach_value", "target_value": 512, "game": "2048"})
    asked = await request.understand(model, words)
    assert asked["url"] == "https://ovolve.github.io/2048-AI/" and asked["hint"] == "2048"
    assert (asked["goal"].objective, asked["goal"].target_value) == ("reach_value", 512)
    assert model.asked[0] == {"request": words, "objectives": ["play_until_end", "reach_value", "actions"]}


async def test_an_answer_the_request_does_not_bear_out_is_not_taken():
    # an address the words do not carry, a number with no objective for it, an answer that is none
    asked = await request.understand(Says({"url": "https://elsewhere.example/", "objective": "actions", "target_value": 0, "game": ""}),
                                     "play the game on the page for a while")
    assert asked["url"] is None and asked["hint"] is None
    assert (asked["goal"].objective, asked["goal"].target_value) == ("play_until_end", None)
    asked = await request.understand(Says("no json"), "play minesweeper")
    assert asked["url"] is None and asked["goal"].objective == "play_until_end"
    asked = await request.understand(Says({"url": None, "objective": "actions", "target_value": 30, "game": "minesweeper"}), "30 moves of minesweeper")
    assert (asked["goal"].objective, asked["goal"].target_value, asked["hint"]) == ("actions", 30, "minesweeper")


APPS = [{"package": "name.boyle.chris.sgtpuzzles", "label": "Puzzles"}, {"package": "com.tpcstld.twozerogame", "label": "2048"}]


async def test_an_app_is_taken_when_it_is_one_of_the_devices_and_its_label_goes_to_the_model():
    model = Says({"url": None, "app": "name.boyle.chris.sgtpuzzles", "objective": "play_until_end", "target_value": None,
                  "game": "Fifteen"})
    asked = await request.understand(model, "퍼즐 앱에서 Fifteen 끝까지", APPS)
    assert (asked["url"], asked["app"], asked["hint"]) == (None, "name.boyle.chris.sgtpuzzles", "Fifteen")
    assert model.asked[0]["apps"] == APPS and model.asked[0]["request"] == "퍼즐 앱에서 Fifteen 끝까지"


async def test_an_app_not_installed_or_not_shown_is_not_taken():
    answer = {"url": None, "app": "com.android.settings", "objective": "play_until_end", "game": None}
    assert (await request.understand(Says(answer), "open the settings app", APPS))["app"] is None
    # no apps were shown: whatever package the model names is none of the device's
    answer["app"] = "com.tpcstld.twozerogame"
    model = Says(answer)
    asked = await request.understand(model, "2048 앱에서 30수")
    assert asked["app"] is None and "apps" not in model.asked[0]


async def test_a_request_with_a_page_is_the_web_even_when_an_app_is_named_and_with_neither_it_is_not_taken():
    words = "play https://example.com/2048 or the 2048 app"
    asked = await request.understand(Says({"url": "https://example.com/2048", "app": "com.tpcstld.twozerogame",
                                           "objective": "play_until_end"}), words, APPS)
    assert (asked["url"], asked["app"]) == ("https://example.com/2048", None)
    asked = await request.understand(Says({"url": None, "app": None, "objective": "play_until_end"}), "play something", APPS)
    assert (asked["url"], asked["app"]) == (None, None)
