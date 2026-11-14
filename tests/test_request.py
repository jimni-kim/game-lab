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
