"""How to read a token count out of one line of a coding agent's log.

Splitting this out of the source classes is what makes user-added agents
possible at all. A source used to hardcode both *where* its logs live and *how*
to read them, so adding an agent meant writing a class. Separate the two and a
source becomes **root + glob + format id** — small enough to be a JSON record
that a dialog can write.

Every parser is best-effort by contract, inherited from the modules these
bodies came from: a missing file, a malformed line, or a shape no released
version ever wrote must yield nothing rather than raise. A usage panel is never
worth breaking a page over.

Formats are scored by *running* them (see :func:`score_format`) rather than by a
separate heuristic. Two ways of recognising a format can disagree; one cannot.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

#: Enough lines to recognise a format without reading a 200MB transcript.
SCORE_LINES = 400


@dataclass(frozen=True)
class LogFormat:
    """One recognisable on-disk shape for agent token usage."""

    id: str
    label: str
    #: Pattern applied relative to a source's root.
    default_glob: str
    #: Yields ``{ts, model, input_tokens, output_tokens, cache_*}`` per turn.
    parse: Callable[[Path], Iterator[dict]]
    #: Shown in the add-agent dialog so a match is explicable, not magic.
    blurb: str


# --- claude-jsonl ---------------------------------------------------------

_SYNTHETIC_MODEL = "<synthetic>"


def parse_claude_jsonl(path: Path) -> Iterator[dict]:
    """Yield one dict per qualifying assistant line in ``path``.

    Skips non-assistant lines, malformed JSON, lines missing usage/model/
    timestamp, and the synthetic ``<synthetic>`` model placeholder. Never
    raises — an unreadable file just yields nothing.

    Moved verbatim from ``backend.telemetry.claude_cli.parse_transcript``,
    which now delegates here. The existing tests are the proof that nothing
    changed in the move.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if obj.get("type") != "assistant":
                    continue
                message = obj.get("message") or {}
                usage = message.get("usage")
                model = message.get("model")
                ts = obj.get("timestamp")
                if not usage or not model or not ts or model == _SYNTHETIC_MODEL:
                    continue
                yield {
                    "ts": ts,
                    "model": model,
                    "input_tokens": int(usage.get("input_tokens") or 0),
                    "output_tokens": int(usage.get("output_tokens") or 0),
                    "cache_creation_input_tokens": int(
                        usage.get("cache_creation_input_tokens") or 0
                    ),
                    "cache_read_input_tokens": int(usage.get("cache_read_input_tokens") or 0),
                }
    except OSError:
        return


# --- codex-rollout --------------------------------------------------------

_CODEX_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")

UNKNOWN_MODEL = "unknown"


def _ints(usage: dict) -> dict[str, int]:
    return {field: max(0, int(usage.get(field) or 0)) for field in _CODEX_FIELDS}


def _delta(current: dict[str, int], previous: dict[str, int] | None) -> dict[str, int]:
    """How much the running total moved since the last reading.

    A counter going *backwards* means the file restarted its accounting (a
    resumed or forked session writing into the same rollout), so the current
    reading is itself the delta.
    """
    if previous is None:
        return dict(current)
    if any(current[field] < previous[field] for field in _CODEX_FIELDS):
        return dict(current)
    return {field: current[field] - previous[field] for field in _CODEX_FIELDS}


def _codex_row(ts: str, model: str, delta: dict[str, int]) -> dict | None:
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


def parse_codex_rollout(path: Path) -> Iterator[dict]:
    """Yield one dict per Codex ``token_count`` event.

    **The trap this handles:** unlike Claude Code, which reports a per-message
    delta on every assistant line, Codex reports ``total_token_usage``
    *cumulatively for the whole session*. Summing it the obvious way inflates a
    session's total by however many turns it ran. So the cumulative reading is
    differenced against the previous one, which is also immune to the same
    event being emitted more than once per turn — ``last_token_usage`` is only
    a fallback for transcripts that omit the running total.

    Version-tolerant by necessity: Codex has moved these fields in and out of a
    ``payload`` wrapper across releases.
    """
    model = UNKNOWN_MODEL
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
                    last = info.get("last_token_usage")
                    if not isinstance(last, dict):
                        continue
                    delta = _ints(last)

                row = _codex_row(ts, model, delta)
                if row is not None:
                    yield row
    except OSError:
        return


# --- registry -------------------------------------------------------------

FORMATS: tuple[LogFormat, ...] = (
    LogFormat(
        id="claude-jsonl",
        label="Claude Code JSONL",
        default_glob="*/*.jsonl",
        parse=parse_claude_jsonl,
        blurb="One JSON object per line; assistant lines carry message.usage.",
    ),
    LogFormat(
        id="codex-rollout",
        label="Codex rollout",
        default_glob="**/rollout-*.jsonl",
        parse=parse_codex_rollout,
        blurb="Session rollouts whose token_count events carry a running total.",
    ),
)

BY_ID = {fmt.id: fmt for fmt in FORMATS}


def find_format(format_id: str | None) -> LogFormat | None:
    return BY_ID.get(format_id or "")


def iter_files(root: Path, glob: str) -> Iterator[Path]:
    """Files under ``root`` matching ``glob``. Empty when the root is absent.

    Never raises: an unreadable or nonexistent directory is an empty one, which
    is the same answer the caller would act on anyway.
    """
    try:
        if not root.is_dir():
            return
        yield from sorted(p for p in root.glob(glob) if p.is_file())
    except OSError:
        return


def score_format(fmt: LogFormat, paths: list[Path]) -> int:
    """How many usage rows ``fmt`` can actually pull out of ``paths``.

    Scoring by running the parser, rather than by a separate signature check,
    means recognition and extraction can never disagree — a format that scores
    well is by definition one that will produce rows.
    """
    found = 0
    for path in paths:
        for index, _ in enumerate(fmt.parse(path)):
            if index >= SCORE_LINES:
                break
            found += 1
    return found


def best_format(paths: list[Path]) -> tuple[LogFormat | None, int]:
    """The format that extracts the most from ``paths``, and how much.

    Returns ``(None, 0)`` when nothing recognises the sample — which the caller
    must report as a failure rather than as an empty success.
    """
    best: LogFormat | None = None
    best_score = 0
    for fmt in FORMATS:
        score = score_format(fmt, paths)
        if score > best_score:
            best, best_score = fmt, score
    return best, best_score
