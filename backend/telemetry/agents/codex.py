"""Codex CLI as an agent source.

Codex persists each session as a rollout transcript under
``~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-<stamp>-<uuid>.jsonl``. Token
counts arrive as ``token_count`` events carrying an ``info`` object.

**The trap this module exists to handle:** unlike Claude Code, which reports a
per-message delta on every assistant line, Codex reports
``total_token_usage`` *cumulatively for the whole session*. Summing it the
obvious way inflates a session's total by a factor of however many turns it
ran. So the cumulative reading is differenced against the previous one, which
is also immune to the same event being emitted more than once per turn —
``last_token_usage`` is only a fallback for transcripts that omit the running
total.

Every field is read defensively and the shape is version-tolerant: Codex has
moved things between the line root and a ``payload`` wrapper across releases,
and this must degrade to "no rows" rather than raise on a shape it has not
seen. Nothing here is verified against a real install on this machine — see
``tests/telemetry/test_codex.py`` for the fixtures that pin the behaviour.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from backend.telemetry.agents.base import AgentSource

DEFAULT_CODEX_HOME = Path.home() / ".codex" / "sessions"

#: Published per-million-token prices for the models Codex actually runs.
#: Anything absent is reported as unpriced rather than guessed at.
CODEX_RATES: dict[str, dict[str, float]] = {
    "gpt-5-codex": {"input": 1.25, "output": 10.0},
    "gpt-5": {"input": 1.25, "output": 10.0},
    "gpt-5-mini": {"input": 0.25, "output": 2.0},
    "gpt-5-nano": {"input": 0.05, "output": 0.40},
    "o3": {"input": 2.0, "output": 8.0},
    "o4-mini": {"input": 1.10, "output": 4.40},
    "codex-mini-latest": {"input": 1.50, "output": 6.0},
}

_USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")

_UNKNOWN_MODEL = "unknown"


def _ints(usage: dict) -> dict[str, int]:
    return {field: max(0, int(usage.get(field) or 0)) for field in _USAGE_FIELDS}


def _delta(current: dict[str, int], previous: dict[str, int] | None) -> dict[str, int]:
    """How much the running total moved since the last reading.

    A counter going *backwards* means the file restarted its accounting (a
    resumed or forked session writing into the same rollout), so the current
    reading is itself the delta.
    """
    if previous is None:
        return dict(current)
    if any(current[field] < previous[field] for field in _USAGE_FIELDS):
        return dict(current)
    return {field: current[field] - previous[field] for field in _USAGE_FIELDS}


def _row(ts: str, model: str, delta: dict[str, int]) -> dict | None:
    """Map Codex's counters onto Argus's four, or None if the turn was empty.

    Codex's ``input_tokens`` is inclusive of ``cached_input_tokens``, and its
    ``output_tokens`` is inclusive of ``reasoning_output_tokens`` — its own
    ``total_tokens`` is just input + output. So cached reads are split back out
    into Argus's cache-read counter and reasoning tokens are left inside
    output, which keeps the two totals equal. Codex publishes no cache-*write*
    counter, so that one is always zero.
    """
    cache_read = min(delta["cached_input_tokens"], delta["input_tokens"])
    fresh_input = delta["input_tokens"] - cache_read
    output = delta["output_tokens"]
    if fresh_input == 0 and cache_read == 0 and output == 0:
        return None
    return {
        "ts": ts,
        "model": model,
        "input_tokens": fresh_input,
        "output_tokens": output,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cache_read,
    }


class CodexSource(AgentSource):
    id = "codex"
    label = "Codex"
    install_hint = "Run a Codex CLI session — rollouts land in ~/.codex/sessions."

    def root(self) -> Path:
        return DEFAULT_CODEX_HOME

    def transcripts(self) -> Iterator[Path]:
        root = self.root()
        if not root.is_dir():
            return
        # Rollouts are nested by date (sessions/YYYY/MM/DD/), so this walks
        # rather than globbing one level like the Claude Code scan does.
        try:
            yield from sorted(root.rglob("rollout-*.jsonl"))
        except OSError:
            return

    def parse(self, path: Path) -> Iterator[dict]:
        model = _UNKNOWN_MODEL
        previous: dict[str, int] | None = None
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if not isinstance(obj, dict):
                        continue

                    # Codex has moved the interesting fields in and out of a
                    # `payload` wrapper across releases; accept either.
                    payload = obj.get("payload")
                    body = payload if isinstance(payload, dict) else obj
                    kind = body.get("type") or obj.get("type")

                    named = body.get("model")
                    if isinstance(named, str) and named:
                        model = named

                    if kind != "token_count":
                        continue

                    info = body.get("info")
                    if not isinstance(info, dict):
                        continue
                    named = info.get("model")
                    if isinstance(named, str) and named:
                        model = named

                    ts = obj.get("timestamp") or body.get("timestamp")
                    if not isinstance(ts, str) or not ts:
                        continue

                    total = info.get("total_token_usage")
                    if isinstance(total, dict):
                        current = _ints(total)
                        delta = _delta(current, previous)
                        previous = current
                    else:
                        # No running total in this transcript — fall back to the
                        # per-turn figure, which is already a delta.
                        last = info.get("last_token_usage")
                        if not isinstance(last, dict):
                            continue
                        delta = _ints(last)

                    row = _row(ts, model, delta)
                    if row is not None:
                        yield row
        except OSError:
            return

    def rate_for(self, model: str) -> dict[str, float] | None:
        return CODEX_RATES.get(model)
