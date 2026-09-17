"""One-shot OpenAI planner for `omarchy-voice say`.

The daemon itself is speech-to-speech over the Realtime API. This module is
the typed equivalent: the same tools, the same policy gate, no microphone.
It talks to Chat Completions over HTTPS so a command can be tried without
opening a websocket.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from . import config as _config

from . import capabilities
from .config import Config
from .persona import PERSONA
from .tools import TOOL_SCHEMAS, Executor, tools_for

DEFAULT_CHAT_BASE = "https://api.openai.com/v1"


@dataclass
class Turn:
    """One request and everything that came of it."""
    text: str
    reply: str = ""
    actions: list[str] = field(default_factory=list)
    error: str = ""
    elapsed: float = 0.0
    tokens: dict = field(default_factory=dict)


def to_chat_tools(schemas: list[dict] | None = None) -> list[dict]:
    converted = []
    for schema in schemas if schemas is not None else TOOL_SCHEMAS:
        converted.append({
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema["description"],
                "parameters": schema["input_schema"],
            },
        })
    return converted


def _system_prompt(config=None) -> str:
    from .tasks import ROUTING
    from .vision import ROUTING as VISION_ROUTING
    return "\n\n".join([
        PERSONA,
        ROUTING if config is not None and config.tasks_enabled else "",
        VISION_ROUTING if config is not None and config.vision_enabled else "",
        capabilities.manifest(),
        "# The desktop right now\n\n" + capabilities.live_state(),
    ])


class PlannerUnavailable(RuntimeError):
    """Something the one-shot planner needs is missing."""


def _history_path() -> Path:
    return _config.STATE_DIR / "planner-history.json"


def _history_load(limit: int) -> list[dict]:
    """Last `limit` turns of conversational memory (oldest first)."""
    if limit <= 0:
        return []
    try:
        data = json.loads(_history_path().read_text())
        turns = [t for t in data if t.get("user") and t.get("reply")]
        return turns[-limit:]
    except Exception:
        return []


def _history_append(user: str, reply: str) -> None:
    """Persist one completed turn. Failures must never break a voice turn."""
    try:
        path = _history_path()
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = []
        data.append({
            "user": user[-500:],
            "reply": reply[-1000:],
            "ts": int(time.time()),
        })
        path.write_text(json.dumps(data[-12:], ensure_ascii=False, indent=1))
    except Exception:
        pass


class Planner:
    def __init__(self, config: Config, executor: Executor):
        self.config = config
        self.executor = executor

    def think(self, text: str, on_phrase: "callable | None" = None) -> Turn:
        turn = Turn(text=text)
        started = time.monotonic()
        try:
            turn.reply = self._loop(text, turn, on_phrase)
        except PlannerUnavailable as exc:
            turn.error = str(exc)
            turn.reply = "My planner isn't configured yet."
        except Exception as exc:  # a voice tool must not die on one bad turn
            turn.error = f"{type(exc).__name__}: {exc}"
            turn.reply = "Something went wrong with that."
        turn.elapsed = time.monotonic() - started
        # Conversational memory: only successful, non-dry-run turns persist.
        if (turn.reply and not turn.error
                and not getattr(self.config, "dry_run", False)):
            _history_append(text, turn.reply)
        return turn

    def _loop(self, text: str, turn: Turn, on_phrase=None) -> str:
        key = os.environ.get(self.config.api_key_env, "")
        if not key:
            raise PlannerUnavailable(
                f"{self.config.api_key_env} is not set — "
                "put it in ~/.config/omarchy-voice/env")

        messages: list[dict] = [
            {"role": "system", "content": _system_prompt(self.config)},
        ]
        # Conversational memory: replay recent turns so follow-ups like
        # "ciérralo" or "¿cómo me llamo?" have a referent.
        if not getattr(self.config, "dry_run", False):
            for prev in _history_load(self.config.history_turns):
                messages.append({"role": "user", "content": prev["user"]})
                messages.append({"role": "assistant", "content": prev["reply"]})
        messages.append({"role": "user", "content": text})
        tools = to_chat_tools(tools_for(self.config))
        reply = ""

        for _ in range(self.config.max_turns):
            data = _chat(messages, tools, self.config, key, on_phrase=on_phrase)
            usage = data.get("usage") or {}
            if usage:
                turn.tokens = {
                    "in": usage.get("prompt_tokens", 0),
                    "out": usage.get("completion_tokens", 0),
                }
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            said = (message.get("content") or "").strip()
            if said:
                reply = said
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return reply or "Done."

            messages.append({
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            })
            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name", "")
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError as exc:
                    outcome_text = f"ERROR: could not parse arguments: {exc}"
                else:
                    outcome = self.executor.call(name, args)
                    turn.actions.append(self.executor.describe(name, args))
                    outcome_text = outcome.as_tool_result()
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": outcome_text,
                })
            if self.executor.pending:
                return reply or "That needs confirmation."

        return reply or "Ran out of steps on that one."


def _chat_base(config: Config) -> str:
    """Validated planner endpoint: HTTPS, or plain HTTP on loopback only.

    Same policy as vision.base_url — the value comes from the user's own
    config file (chmod 600), but we still refuse anything that could turn
    the request into a credentialed or non-loopback plain-HTTP call.
    """
    base = (getattr(config, "base_url", "") or DEFAULT_CHAT_BASE).strip().rstrip("/")
    url = urlsplit(base)
    if url.scheme not in ("https", "http"):
        raise PlannerUnavailable("planner base_url must use https (or http on loopback)")
    if url.scheme == "http" and url.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise PlannerUnavailable("plain http is only allowed on loopback")
    if url.username or url.password or url.query or url.fragment:
        raise PlannerUnavailable("planner base_url must not embed credentials, query or fragment")
    return base


def _chat(messages: list[dict], tools: list[dict], config: Config, key: str,
          on_phrase=None) -> dict:
    if on_phrase is not None:
        return _chat_streamed(messages, tools, config, key, on_phrase)
    body = json.dumps({
        "model": config.planner_model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        **(getattr(config, "extra_body", None) or {}),
    }).encode()
    request = urllib.request.Request(
        _chat_base(config) + "/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:400]
        raise PlannerUnavailable(f"planner HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise PlannerUnavailable(f"could not reach planner endpoint: {exc.reason}") from exc


# Sentence boundaries for speech: a chunk is only spoken once it is a whole
# sentence. A period alone is not enough (it may be a decimal "3.5"), so the
# boundary requires trailing whitespace or end-of-stream. Closing quotes and
# brackets ride along with the sentence ("…profundidades." / "Cada…").
_PHRASE_BOUNDARY = re.compile(r"([\.\!\?\…]+[\)\"''»…]*[ \t\n]+)")


def _split_phrases(buffer: str) -> tuple[list[str], str]:
    """Split `buffer` into complete sentences + the trailing remainder."""
    if not buffer:
        return [], ""
    pieces: list[str] = []
    last = 0
    for match in _PHRASE_BOUNDARY.finditer(buffer):
        pieces.append(buffer[last:match.end()].rstrip())
        last = match.end()
    return pieces, buffer[last:]


def _chat_streamed(messages: list[dict], tools: list[dict], config: Config,
                   key: str, on_phrase) -> dict:
    """Chat Completions with SSE streaming.

    Behaves like `_chat` (same return shape) but emits each completed
    sentence of the reply through `on_phrase(sentence)` as it arrives, so a
    voice front-end can start speaking before the model finishes.
    Tool-call deltas are reassembled into the same `message.tool_calls`
    structure the non-streaming path produces.
    """
    body = json.dumps({
        "model": config.planner_model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "stream": True,
        "stream_options": {"include_usage": True},
        **(getattr(config, "extra_body", None) or {}),
    }).encode()
    request = urllib.request.Request(
        _chat_base(config) + "/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    content_parts: list[str] = []
    tool_calls: dict[int, dict] = {}
    usage: dict = {}

    def _flush(pending: str) -> str:
        sentences, remainder = _split_phrases(pending)
        for sentence in sentences:
            sentence = sentence.strip()
            if sentence:
                try:
                    on_phrase(sentence)
                except Exception:
                    pass  # a voice front-end hiccup must not kill the turn
        return remainder

    pending = ""
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if piece:
                    content_parts.append(piece)
                    pending = _flush(pending + piece)
                for call in delta.get("tool_calls") or []:
                    index = call.get("index", 0)
                    slot = tool_calls.setdefault(index, {
                        "id": "", "type": "function",
                        "function": {"name": "", "arguments": ""},
                    })
                    if call.get("id"):
                        slot["id"] = call["id"]
                    fn = call.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:400]
        raise PlannerUnavailable(f"planner HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise PlannerUnavailable(f"could not reach planner endpoint: {exc.reason}") from exc

    # Whatever is left when the stream ends is the last sentence.
    tail = pending.strip()
    if tail:
        try:
            on_phrase(tail)
        except Exception:
            pass

    message: dict = {"content": "".join(content_parts)}
    if tool_calls:
        message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
    return {"choices": [{"message": message}], "usage": usage}
