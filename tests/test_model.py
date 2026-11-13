"""The model client: a question as an OpenAI-compatible chat completion, its answer as plain text."""
import json

import httpx
import pytest

from gamelab.model import ChatClient, Msg, Recorded, TurnRequest, answer

QUESTION = TurnRequest(system_stable="Say what the screen shows.", max_tokens=64, timeout_s=2, messages=[Msg("user", [
    {"type": "text", "text": '{"observation_id": "o1"}'},
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}])])


async def test_a_question_is_sent_as_one_chat_completion_with_its_image_and_key():
    seen = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"screen": "over"}'}}],
                                         "usage": {"prompt_tokens": 900, "completion_tokens": 7}})

    client = ChatClient("vendor/model", "secret", base="https://models.test/v1/", transport=httpx.MockTransport(reply))
    result = await Recorded(client, {"classify": QUESTION.system_stable}).complete(QUESTION)
    await client.close()
    request, = seen
    body = json.loads(request.content)
    assert str(request.url) == "https://models.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer secret"
    assert body["model"] == "vendor/model" and body["max_completion_tokens"] == 64 and "tools" not in body
    assert body["messages"][0] == {"role": "system", "content": "Say what the screen shows."}
    assert body["messages"][1]["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}
    assert result.text == '{"screen": "over"}' and not result.tool_calls
    assert (result.usage.input_tokens, result.usage.output_tokens) == (900, 7)


@pytest.mark.parametrize("content", ['```json\n{"a": 1}\n```', '```\n{"a": 1}\n```', [{"type": "text", "text": '{"a": 1}'}]])
def test_a_fenced_or_split_answer_reads_as_its_text(content):
    assert answer({"choices": [{"message": {"content": content}}]}).text == '{"a": 1}'


def test_a_tool_call_is_reported_not_dropped():
    """The reader refuses an answer that calls a tool; the client must not hide one."""
    result = answer({"choices": [{"message": {"content": None, "tool_calls": [{"id": "c", "type": "function"}]}}]})
    assert result.text is None and result.tool_calls


async def test_a_recorder_keeps_each_answer_and_a_failure():
    class Failing:
        async def complete(self, request):
            raise RuntimeError("no network")

    recorded = Recorded(Failing())
    with pytest.raises(RuntimeError):
        await recorded.complete(QUESTION)
    assert recorded.answers[0]["prompt"] == "other" and "no network" in recorded.answers[0]["error"]
    assert recorded.tokens() == {"input": 0, "output": 0}
