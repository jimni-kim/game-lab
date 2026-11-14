"""The model client: a question as an OpenAI-compatible chat completion, its answer read as a stream into plain text."""
import json

import httpx
import pytest

from gamelab.model import ChatClient, Msg, Recorded, TurnRequest, answer

QUESTION = TurnRequest(system_stable="Say what the screen shows.", max_tokens=64, timeout_s=2, messages=[Msg("user", [
    {"type": "text", "text": '{"observation_id": "o1"}'},
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}])])


def events(*chunks: dict, split: int = 7) -> httpx.Response:
    """A streamed answer as the API sends it, its bytes cut at arbitrary places as a network delivers them."""
    wire = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks).encode() + b": keep-alive\n\ndata: [DONE]\n\n"

    async def pieces():
        for start in range(0, len(wire), split):
            yield wire[start:start + split]
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=pieces())


def delta(content: str | None) -> dict:
    return {"choices": [{"index": 0, "delta": {} if content is None else {"content": content}}]}


USAGE = {"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 7}}


async def test_a_question_is_sent_as_one_streamed_chat_completion_with_its_image_and_key():
    seen = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return events({"choices": [{"index": 0, "delta": {"role": "assistant"}}]}, delta('{"screen"'), delta(None),
                      delta(': "over"}'), USAGE)

    client = ChatClient("vendor/model", "secret", base="https://models.test/v1/", transport=httpx.MockTransport(reply))
    result = await Recorded(client, {"classify": QUESTION.system_stable}).complete(QUESTION)
    await client.close()
    request, = seen
    body = json.loads(request.content)
    assert str(request.url) == "https://models.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer secret"
    assert body["model"] == "vendor/model" and body["max_completion_tokens"] == 64 and "tools" not in body
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert body["messages"][0] == {"role": "system", "content": "Say what the screen shows."}
    assert body["messages"][1]["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}
    assert result.text == '{"screen": "over"}' and not result.tool_calls
    assert (result.usage.input_tokens, result.usage.output_tokens) == (900, 7)


async def test_a_streamed_answer_in_one_fence_reads_as_its_text():
    client = ChatClient("m", "k", base="https://models.test/v1",
                        transport=httpx.MockTransport(lambda r: events(delta("```json\n{\"a\""), delta(": 1}\n```"), USAGE)))
    result = await client.complete(QUESTION)
    await client.close()
    assert result.text == '{"a": 1}' and (result.usage.input_tokens, result.usage.output_tokens) == (900, 7)


async def test_an_answer_sent_in_one_piece_is_still_read():
    """An old proxy or a test's double may ignore the request to stream and answer with plain JSON."""
    def reply(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "```\nok\n```"}}],
                                         "usage": {"prompt_tokens": 3, "completion_tokens": 1}})
    client = ChatClient("m", "k", base="https://models.test/v1", transport=httpx.MockTransport(reply))
    result = await client.complete(QUESTION)
    await client.close()
    assert result.text == "ok" and (result.usage.input_tokens, result.usage.output_tokens) == (3, 1)


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


async def test_a_gateway_that_timed_out_is_asked_again_and_a_refusal_is_not(monkeypatch):
    """Regression: one 504 from the proxy while the adapter's code was being written ended a game with no record."""
    from gamelab import model
    monkeypatch.setattr(model, "RETRY_WAIT_S", 0.0)
    seen = []

    def reply(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(504)
        return events(delta("o"), delta("k"))
    client = ChatClient("m", "k", base="https://models.test/v1", transport=httpx.MockTransport(reply))
    request = TurnRequest(system_stable="s", messages=[Msg("user", [{"type": "text", "text": "q"}])], max_tokens=5, timeout_s=5)
    assert (await client.complete(request)).text == "ok" and len(seen) == 2
    refused = ChatClient("m", "k", base="https://models.test/v1",
                         transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "bad image"})))
    with pytest.raises(httpx.HTTPStatusError) as raised:
        await refused.complete(request)
    assert raised.value.response.status_code == 400 and "bad image" in raised.value.response.text


async def test_a_gateway_that_keeps_timing_out_ends_the_question(monkeypatch):
    from gamelab import model
    monkeypatch.setattr(model, "RETRY_WAIT_S", 0.0)
    seen = []
    client = ChatClient("m", "k", base="https://models.test/v1",
                        transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(504)))
    with pytest.raises(httpx.HTTPStatusError):
        await client.complete(QUESTION)
    assert len(seen) == 1 + model.RETRIES


async def test_an_answer_that_never_ends_is_cut_off_at_the_question_s_timeout():
    """The time a question may take bounds the whole of it; running out is not a gateway's fault to ask again for."""
    import asyncio
    seen = []

    def reply(request):
        seen.append(request)

        async def trickle():
            while True:
                yield b"data: " + json.dumps(delta("x")).encode() + b"\n\n"
                await asyncio.sleep(0.01)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=trickle())
    client = ChatClient("m", "k", base="https://models.test/v1", transport=httpx.MockTransport(reply))
    request = TurnRequest(system_stable="s", messages=[Msg("user", [{"type": "text", "text": "q"}])], max_tokens=5, timeout_s=0.1)
    with pytest.raises(TimeoutError):
        await client.complete(request)
    assert len(seen) == 1
