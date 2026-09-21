"""JEVAL router: cheap typed triage of user text via TypeSafe Jev.

One System One evaluation classifies an utterance as system_command /
question / conversation and scores whether a short social reply fully
serves it. The planner uses this to decide whether the full tool loop is
worth its latency (see Planner.think). Modes:

  off     never runs
  shadow  classify fire-and-forget, log, never change behaviour
  local   classify in-line; trivial conversation gets a fixed short reply

Design rules this module lives by:

- Stdlib only (urllib), like the rest of the planner path — no new deps.
- Fail open: every entry point either succeeds or reports through the
  JSONL log. Nothing here may ever break a voice turn.
- The API key is read from $TYPESAFE_API_KEY or ~/.config/typesafe/env
  and is never printed, logged, or embedded in error strings.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
KEY_ENV = "TYPESAFE_API_KEY"
KEY_FILE = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "typesafe"
    / "env"
)
LOG_DIR = (
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    / "jev-router"
)
LOG_FILE = LOG_DIR / "voice.jsonl"
TIMEOUT = 8.0          # seconds; bounded so a hang cannot pile up threads
STATE_TRUNC = 160      # input text kept in the log, for debugging only

# A trivial conversational turn is one a fixed short reply fully serves.
TRIVIAL_INSTRUCTIONS = (
    "A short social reply with no information or action fully serves this: "
    "greeting, thanks, goodbye"
)

QUESTIONS = {
    "intent": {
        "type": "choice",
        "instructions": (
            "What does this user utterance need from a desktop voice assistant?"
        ),
        "criteria": {
            "system_command": (
                "Asks for an action on the desktop: open, close, launch, run, "
                "change a setting, control media or hardware"
            ),
            "question": (
                "Asks for information: a fact, how to do something, a status, "
                "a lookup, advice that needs content in the answer"
            ),
            "conversation": (
                "Pure social exchange: greeting, thanks, goodbye, small talk; "
                "no information or action is being requested"
            ),
        },
    },
    "is_trivial": {
        "type": "noul",
        "instructions": TRIVIAL_INSTRUCTIONS,
    },
}


def load_key() -> str:
    """$TYPESAFE_API_KEY, else the export line in ~/.config/typesafe/env.

    The value never reaches a log or an exception message.
    """
    key = os.environ.get(KEY_ENV, "").strip()
    if key:
        return key
    try:
        lines = KEY_FILE.read_text().splitlines()
    except OSError:
        return ""
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export "):].strip()
        if name == KEY_ENV:
            return value.strip().strip('"').strip("'")
    return ""


def classify(text: str, mode: str = "shadow", timeout: float = TIMEOUT) -> dict:
    """One Jev evaluation of `text`. Raises on failure — caller decides.

    Returns a verdict dict: intent, probs, noul_trivial, ms, usage.
    """
    key = load_key()
    if not key:
        raise RuntimeError(f"{KEY_ENV} is not set (env or {KEY_FILE})")
    body = json.dumps({
        "state": text,
        "model": MODEL,
        "questions": QUESTIONS,
    }).encode()
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read())
    answers = data.get("answers") or {}
    intent = answers.get("intent") or {}
    trivial = answers.get("is_trivial") or {}
    return {
        "intent": intent.get("choice", ""),
        "probs": intent.get("probabilities") or {},
        "noul_trivial": trivial.get("noul"),
        "ms": round((time.monotonic() - started) * 1000),
        "usage": data.get("usage") or {},
    }


def _append_log(record: dict) -> None:
    """One JSONL line. Dir 700, file 600. Logging must never raise."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(LOG_DIR, 0o700)
        fd = os.open(LOG_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.chmod(LOG_FILE, 0o600)
    except Exception:
        pass


def classify_and_log(text: str, mode: str = "shadow") -> dict:
    """Classify `text` and append one JSONL record. Never raises.

    Record fields: ts, consumer, mode, state (truncated), intent, probs,
    noul_trivial, ms, usage, error.
    """
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "consumer": "voice-router",
        "mode": mode,
        "state": text[:STATE_TRUNC],
        "intent": "",
        "probs": {},
        "noul_trivial": None,
        "ms": None,
        "usage": {},
        "error": "",
    }
    try:
        record.update(classify(text, mode=mode))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode(errors="replace")[:200]
        except Exception:
            detail = ""
        record["error"] = f"HTTP {exc.code}: {detail}"
    except Exception as exc:  # fail open — the router is never load-bearing
        record["error"] = f"{type(exc).__name__}: {exc}"
    _append_log(record)
    return record


def launch_background(text: str, mode: str = "shadow") -> None:
    """Fire-and-forget classify_and_log in a short-lived thread.

    Zero added latency for the caller: the request happens off-thread while
    the planner loop runs. Non-daemon on purpose — a fast `say` must not
    exit before the log line is written; TIMEOUT bounds the wait. Never
    raises, and safe where no asyncio loop exists (the CLI path).
    """
    def _run() -> None:
        try:
            classify_and_log(text, mode=mode)
        except Exception:
            pass
    try:
        threading.Thread(target=_run, name="jeval-router", daemon=False).start()
    except Exception:
        pass
