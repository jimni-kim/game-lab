"""The model the harness asks about a screen: one bounded question, one text answer, no tools.

A question is a `TurnRequest` (a fixed system prompt, one user message of JSON context and a PNG) and its
answer a `TurnResult`. `ChatClient` sends it to an OpenAI-compatible chat completions API (OpenRouter
unless told otherwise) with the key from the environment; anything with an async `complete(request)`
can stand in for it (a test's canned answers, `Recorded` around a client).
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
# where the key is looked for, in this order
KEY_VARIABLES = ("AGENTIC_OPENROUTER_KEY", "OPENROUTER_API_KEY")
# the model a run asks unless told otherwise: the reader asks only at the start and on screens the code
# cannot read, and a weaker model's misreading is what a run would then measure
DEFAULT_MODEL = "anthropic/claude-opus-5.5"
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
    return next((value for name in KEY_VARIABLES if (value := os.environ.get(name, "").strip())), None)


class ChatClient:
    def __init__(self, model: str, key: str, *, base: str = OPENROUTER_BASE, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.model, self.base = model, base.rstrip("/")
        self._http = httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, transport=transport)

    async def complete(self, request: TurnRequest) -> TurnResult:
        response = await self._http.post(f"{self.base}/chat/completions", json=self.body(request),
                                         timeout=request.timeout_s)
        response.raise_for_status()
        return answer(response.json())

    def body(self, request: TurnRequest) -> dict[str, Any]:
        messages = [{"role": "system", "content": request.system_stable},
                    *({"role": message.role, "content": [_part(part) for part in message.content]}
                      for message in request.messages)]
        return {"model": self.model, "messages": messages, "max_tokens": request.max_tokens, "stream": False}

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
    if isinstance(content, str) and (fenced := _FENCED.match(content)):
        content = fenced[1]
    usage = data.get("usage") or {}
    return TurnResult(text=content, tool_calls=list(message.get("tool_calls") or []),
                      usage=Usage(usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0))


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
