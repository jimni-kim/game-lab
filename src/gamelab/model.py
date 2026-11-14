"""The model the harness asks about a screen: one bounded question, one text answer, no tools.

A question is a `TurnRequest` (a fixed system prompt, one user message of JSON context and a PNG) and its
answer a `TurnResult`. `ChatClient` sends it to an OpenAI-compatible chat completions API (the team's
LiteLLM proxy in front of Azure unless told otherwise) with the key from the environment, and reads the answer
as a stream: a long question (code to write) keeps the model silent for minutes, and a gateway cuts a connection on
which nothing flows. Anything with an async `complete(request)` can stand in for it (a test's canned answers,
`Recorded` around a client).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

# the API asked unless told otherwise (BASE_VARIABLE, --model-base): the LiteLLM proxy in front of Azure
DEFAULT_BASE = "https://litellm.agenticfw.dev/v1"
# the environment's names for the key, the API's base URL and the model, as ARMY names them
KEY_VARIABLE, BASE_VARIABLE, MODEL_VARIABLE = "AGENTIC_OPENAI_KEY", "AGENTIC_OPENAI_BASE", "AGENTIC_OPENAI_MODEL"
# answers of a gateway, not of the model, after which a question is sent again, and how often
RETRIED = frozenset({429, 502, 503, 504})
RETRIES = 2
RETRY_WAIT_S = 2.0
# a whole answer in one Markdown code fence, as some models wrap JSON
_FENCED = re.compile(r"\A\s*```[a-zA-Z]*\n(.*?)\n?```\s*\Z", re.DOTALL)


@dataclass(frozen=True)
class Msg:
    role: str
    # text parts ({"type": "text", "text"}) and images ({"type": "image", "source": {"type": "base64", ...}})
    content: list[dict[str, Any]]


@dataclass(frozen=True)
class TurnRequest:
    system_stable: str
    messages: list[Msg]
    max_tokens: int
    timeout_s: float
    # what the question never is: a tool call, a loop or a stream; kept so a stand-in can check it
    tools: list[Any] = field(default_factory=list)
    answer_only: bool = True
    max_steps: int = 1
    stream: bool = False


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class TurnResult:
    text: str | None
    tool_calls: list[Any] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


class Model(Protocol):
    async def complete(self, request: TurnRequest) -> TurnResult: ...


def api_key() -> str | None:
    return os.environ.get(KEY_VARIABLE, "").strip() or None


def api_base() -> str:
    return os.environ.get(BASE_VARIABLE, "").strip() or DEFAULT_BASE


def default_model() -> str:
    """The model a run asks unless told otherwise: the one the environment names, else none."""
    return os.environ.get(MODEL_VARIABLE, "").strip()


async def available(key: str, base: str) -> list[str]:
    """The model names the API serves this key."""
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}) as http:
        response = await http.get(f"{base.rstrip('/')}/models", timeout=30)
        response.raise_for_status()
        return sorted(entry["id"] for entry in response.json()["data"])


class ChatClient:
    def __init__(self, model: str, key: str, *, base: str = DEFAULT_BASE, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.model, self.base = model, base.rstrip("/")
        self._http = httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, transport=transport)

    async def complete(self, request: TurnRequest) -> TurnResult:
        # the httpx timeout bounds each wait for the next bytes, so a stream that keeps flowing is not cut off;
        # the whole question, retries included, is bounded once more so a trickling answer cannot hang a game
        async with asyncio.timeout(request.timeout_s):
            # a gateway that timed out or is busy is asked again: a long question (code to write) must not end a game
            for attempt in range(1 + RETRIES):
                try:
                    async with self._http.stream("POST", f"{self.base}/chat/completions", json=self.body(request),
                                                 timeout=request.timeout_s) as response:
                        if response.status_code not in RETRIED or attempt == RETRIES:
                            if response.is_error:
                                # a streamed body is not read yet, and the error should carry what the API said
                                await response.aread()
                                response.raise_for_status()
                            return await streamed(response)
                except httpx.TransportError:
                    if attempt == RETRIES:
                        raise
                await asyncio.sleep(RETRY_WAIT_S * (attempt + 1))

    def body(self, request: TurnRequest) -> dict[str, Any]:
        messages = [{"role": "system", "content": request.system_stable},
                    *({"role": message.role, "content": [_part(part) for part in message.content]}
                      for message in request.messages)]
        return {"model": self.model, "messages": messages, "max_completion_tokens": request.max_tokens,
                "stream": True, "stream_options": {"include_usage": True}}

    async def close(self) -> None:
        await self._http.aclose()


def _part(part: dict[str, Any]) -> dict[str, Any]:
    if part["type"] == "image":
        source = part["source"]
        return {"type": "image_url", "image_url": {"url": f"data:{source['media_type']};base64,{source['data']}"}}
    return {"type": "text", "text": part["text"]}


def answer(data: dict[str, Any]) -> TurnResult:
    """A chat completion as the harness reads it: the first choice's text (a fence around the whole of it
    removed), any tool calls it made, and the tokens it cost."""
    message = data["choices"][0]["message"]
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    usage = data.get("usage") or {}
    return TurnResult(text=_unfenced(content), tool_calls=list(message.get("tool_calls") or []),
                      usage=Usage(usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0))


async def streamed(response: httpx.Response) -> TurnResult:
    """A streamed chat completion read as `answer` reads a whole one: the first choice's text pieces joined
    (a fence around the whole of it removed) and the tokens it cost, which come in a chunk of their own."""
    if response.headers.get("content-type", "").startswith("application/json"):
        # a server that answers in one piece anyway (an old proxy, a test's double) is read as before
        await response.aread()
        return answer(response.json())
    pieces: list[str] = []
    usage: dict[str, Any] = {}
    async for line in response.aiter_lines():
        # anything but data (a keep-alive comment, an event name, the blank line between events) says nothing
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        if chunk.get("choices") and isinstance(content := (chunk["choices"][0].get("delta") or {}).get("content"), str):
            pieces.append(content)
        usage = chunk.get("usage") or usage
    return TurnResult(text=_unfenced("".join(pieces)) if pieces else None,
                      usage=Usage(usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0))


def _unfenced(content: Any) -> Any:
    if isinstance(content, str) and (fenced := _FENCED.match(content)):
        return fenced[1]
    return content


class Recorded:
    """A model as the harness asks it, keeping what each question was, what came back and its tokens: a
    run's record keeps only that a question was asked, and a failed look is otherwise explained by guesswork."""

    def __init__(self, model: Model, prompts: dict[str, str] | None = None) -> None:
        # prompts: each known system prompt's text by its name, so an answer says which question it answered
        self.model, self.prompts, self.answers = model, {text: name for name, text in (prompts or {}).items()}, []

    async def complete(self, request: TurnRequest) -> TurnResult:
        asked = {"prompt": self.prompts.get(request.system_stable, "other"), "at_s": round(time.monotonic(), 1)}
        try:
            result = await self.model.complete(request)
        except Exception as error:
            self.answers.append({**asked, "error": f"{type(error).__name__}: {error}"})
            raise
        self.answers.append({**asked, "text": result.text, "input_tokens": result.usage.input_tokens,
                             "output_tokens": result.usage.output_tokens})
        return result

    def tokens(self) -> dict[str, int]:
        return {side: sum(answer.get(f"{side}_tokens", 0) for answer in self.answers) for side in ("input", "output")}


def prompts(folder: Any) -> dict[str, str]:
    """The prompt files of a blueprint's `prompts` folder, by name."""
    return {Path(entry.name).stem: entry.read_text(encoding="utf-8") for entry in folder.iterdir()
            if entry.name.endswith(".md")}
