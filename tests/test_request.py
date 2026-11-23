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
    assert model.asked[0]["request"] == words and model.asked[0]["objectives"] == ["play_until_end", "reach_value", "actions", "levels",
                                                                                                "until_level"]
    assert set(model.asked[0]["meta_actions"]) == {"boosters", "currency", "continue", "retry"}
    # nothing said of where to start, when to stop or what may be spent: the game started again, nothing allowed
    goal = asked["goal"]
    assert (goal.scope, goal.stop_conditions, goal.allowed_meta_actions) == ("new_game", (), frozenset())


async def test_a_request_for_levels_from_the_screen_as_it_stands_with_its_stops_and_permissions():
    words = "하던 판 이어서 레벨 3개 깨줘. 부스터는 써도 되고, 지면 멈춰"
    model = Says({"url": None, "objective": "levels", "target_value": 3, "scope": "current_game",
                  "stop": ["lost", " 하트가 없으면 ", "lost"], "allowed": ["boosters", "buy_gems", {"x": 1}], "game": None})
    goal = (await request.understand(model, words))["goal"]
    assert (goal.objective, goal.target_value, goal.scope) == ("levels", 3, "current_game")
    # the same condition once; a permission the host does not know is none
    assert goal.stop_conditions == ("lost", "하트가 없으면") and goal.allowed_meta_actions == frozenset({"boosters"})
    # levels with no number are not levels; a scope the host does not know starts the game again
    goal = (await request.understand(Says({"objective": "levels", "target_value": None, "scope": "everything",
                                           "stop": "lost", "allowed": "all"}), "레벨 깨줘"))["goal"]
    assert (goal.objective, goal.scope, goal.stop_conditions, goal.allowed_meta_actions) == ("play_until_end", "new_game", (), frozenset())


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


async def test_an_errand_beside_the_levels_is_taken_by_a_plain_name_and_nothing_else_is_one():
    async def task(said):
        answer = {"url": None, "app": "com.tpcstld.twozerogame", "objective": "play_until_end",
                  **({"task": said} if said is not ... else {})}
        return (await request.understand(Says(answer), "일일 보상 받아줘", APPS))["task"]
    assert await task("daily_reward") == "daily_reward" and await task(" Daily Reward ") == "daily_reward"
    assert await task("claim-gifts") == "claim_gifts"
    # a name the routine keeps for itself, one that is no name, one too long, none at all
    assert await task("next_level") is None and await task("New Game") is None
    assert await task(3) is None and await task("") is None and await task("보상") is None and await task("x" * 25) is None
    assert await task(None) is None and await task(...) is None


async def test_levels_up_to_one_and_a_number_of_levels_are_taken_as_the_model_tells_them_apart():
    asked = await request.understand(Says({"objective": "until_level", "target_value": 10, "game": "candy"}), "캔디 10단계까지 깨줘")
    assert (asked["goal"].objective, asked["goal"].target_value) == ("until_level", 10)
    asked = await request.understand(Says({"objective": "levels", "target_value": 3}), "레벨 3개 깨줘")
    assert (asked["goal"].objective, asked["goal"].target_value) == ("levels", 3)
    # up to a level with no number is not taken
    asked = await request.understand(Says({"objective": "until_level", "target_value": None}), "다리까지 깨줘")
    assert (asked["goal"].objective, asked["goal"].target_value) == ("play_until_end", None)


LAST = {"words": "캔디크러스사가 다리까지 레벨 깨줭", "url": None, "app": "com.king.candycrushsaga", "label": "Candy Crush Saga",
        "game": None, "within": None, "goal": {"objective": "play_until_end"},
        "stopped": {"screen": "popup", "situation": "Level 9 start dialog", "level": 9}}
CANDY = [*APPS, {"package": "com.king.candycrushsaga", "label": "Candy Crush Saga"}]


async def test_a_request_that_goes_on_with_the_last_game_takes_its_app_and_name_when_the_host_still_has_them():
    model = Says({"same": True, "app": None, "objective": "until_level", "target_value": 10, "scope": "current_game"})
    asked = await request.understand(model, "10단계까지 깨줘", CANDY, last=LAST)
    assert asked["same"] and asked["app"] == "com.king.candycrushsaga" and asked["hint"] == "Candy Crush Saga"
    assert (asked["goal"].objective, asked["goal"].target_value, asked["goal"].scope) == ("until_level", 10, "current_game")
    # the model is shown the last game, where it stopped included
    shown = model.asked[0]["last"]
    assert shown["app"] == "com.king.candycrushsaga" and shown["stopped"]["level"] == 9 and shown["request"] == LAST["words"]
    # its own words about the game and its within are kept over the last game's
    asked = await request.understand(Says({"same": True, "game": "candy", "within": "fifteen"}), "candy 더 해줘", CANDY,
                                     last={**LAST, "game": "old", "within": "other"})
    assert (asked["hint"], asked["within"]) == ("candy", "fifteen")
    asked = await request.understand(Says({"same": True}), "더 해줘", CANDY, last={**LAST, "game": "old", "within": "other"})
    assert (asked["hint"], asked["within"]) == ("old", "other")
    # the last game's app no longer installed is not taken; before the apps are shown neither (the hint is)
    asked = await request.understand(Says({"same": True}), "10단계까지 깨줘", APPS, last=LAST)
    assert asked["app"] is None and asked["same"]
    asked = await request.understand(Says({"same": True}), "10단계까지 깨줘", last=LAST)
    assert asked["app"] is None and asked["hint"] == "Candy Crush Saga"


async def test_the_last_games_address_is_taken_only_when_it_is_the_same_address():
    last = {**LAST, "app": None, "label": None, "url": "https://mines.example/play", "game": "mines"}
    asked = await request.understand(Says({"same": True, "url": "https://mines.example/play"}), "한 판 더", last=last)
    assert asked["url"] == "https://mines.example/play" and asked["hint"] == "mines"
    # another address the words do not carry is not taken, same or not
    asked = await request.understand(Says({"same": True, "url": "https://elsewhere.example/"}), "한 판 더", last=last)
    assert asked["url"] is None
    # not going on with the last game: its address is one the words do not carry
    asked = await request.understand(Says({"same": False, "url": "https://mines.example/play"}), "한 판 더", last=last)
    assert asked["url"] is None and not asked["same"] and asked["hint"] is None


async def test_with_no_last_game_nothing_is_the_same_and_a_question_to_ask_back_is_taken_cut_short():
    asked = await request.understand(Says({"same": True, "app": "com.tpcstld.twozerogame", "objective": "play_until_end"}),
                                     "10단계까지 깨줘", APPS)
    assert not asked["same"] and asked["hint"] is None and asked["app"] == "com.tpcstld.twozerogame"
    model = Says({"objective": "play_until_end", "unclear": "  '다리까지'는 몇 단계까지인가요?  "})
    asked = await request.understand(model, "캔디크러스사가 다리까지 레벨 깨줭")
    assert asked["unclear"] == "'다리까지'는 몇 단계까지인가요?" and "last" not in model.asked[0]
    assert (await request.understand(Says({"unclear": "x" * 500}), "?"))["unclear"] == "x" * request.UNCLEAR_MOST
    for none in ("", "   ", None, 3, ["?"]):
        assert (await request.understand(Says({"unclear": none}), "play 2048"))["unclear"] is None
