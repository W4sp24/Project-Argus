"""Tests for the Codex rollout parser.

Codex is not installed on the machine this was written on, so these fixtures
are the specification: they pin the cumulative-vs-delta handling that is the
whole reason the module exists, and the version tolerance that keeps a shape
change from raising.
"""

import json
from pathlib import Path

from backend.telemetry.agents.codex import CodexSource


def _token_count(
    ts: str,
    *,
    input_tokens: int,
    cached: int,
    output: int,
    reasoning: int = 0,
    cumulative: bool = True,
) -> str:
    """One ``token_count`` event, cumulative by default (Codex's real shape)."""
    usage = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output,
        "reasoning_output_tokens": reasoning,
        "total_tokens": input_tokens + output,
    }
    key = "total_token_usage" if cumulative else "last_token_usage"
    return json.dumps(
        {
            "timestamp": ts,
            "type": "event_msg",
            "payload": {"type": "token_count", "info": {key: usage, "model_context_window": 272000}},
        }
    )


def _turn_context(ts: str, model: str) -> str:
    return json.dumps(
        {"timestamp": ts, "type": "turn_context", "payload": {"model": model, "cwd": "/tmp"}}
    )


def _write(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_cumulative_totals_are_differenced(tmp_path: Path) -> None:
    """The trap: total_token_usage is a running total, not a per-turn figure.

    Summing it directly would report 3850 for a session that actually spent
    2750. The rows must instead add up to the final cumulative reading.
    """
    path = _write(
        tmp_path / "rollout-1.jsonl",
        [
            _turn_context("2026-07-20T09:00:00.000Z", "gpt-5-codex"),
            _token_count("2026-07-20T09:00:01.000Z", input_tokens=1000, cached=800, output=100),
            _token_count("2026-07-20T09:01:00.000Z", input_tokens=2500, cached=2000, output=250),
        ],
    )

    rows = list(CodexSource().parse(path))
    assert len(rows) == 2

    totals = [
        r["input_tokens"] + r["output_tokens"] + r["cache_read_input_tokens"] for r in rows
    ]
    assert totals == [1100, 1650]
    assert sum(totals) == 2750, "rows must sum to the final cumulative total, not 3850"


def test_cached_tokens_split_out_of_input(tmp_path: Path) -> None:
    """Codex's input_tokens includes cached reads; Argus counts them apart."""
    path = _write(
        tmp_path / "rollout-2.jsonl",
        [
            _turn_context("2026-07-20T09:00:00.000Z", "gpt-5-codex"),
            _token_count("2026-07-20T09:00:01.000Z", input_tokens=1000, cached=800, output=100),
        ],
    )

    row = next(iter(CodexSource().parse(path)))
    assert row["input_tokens"] == 200
    assert row["cache_read_input_tokens"] == 800
    assert row["output_tokens"] == 100
    assert row["cache_creation_input_tokens"] == 0, "Codex publishes no cache-write counter"


def test_model_comes_from_turn_context(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "rollout-3.jsonl",
        [
            _turn_context("2026-07-20T09:00:00.000Z", "gpt-5-codex"),
            _token_count("2026-07-20T09:00:01.000Z", input_tokens=10, cached=0, output=5),
        ],
    )
    assert next(iter(CodexSource().parse(path)))["model"] == "gpt-5-codex"


def test_model_unknown_without_a_turn_context(tmp_path: Path) -> None:
    """An unnamed model is reported as unknown and priced at zero, not guessed."""
    path = _write(
        tmp_path / "rollout-4.jsonl",
        [_token_count("2026-07-20T09:00:01.000Z", input_tokens=10, cached=0, output=5)],
    )
    row = next(iter(CodexSource().parse(path)))
    assert row["model"] == "unknown"
    assert CodexSource().rate_for("unknown") is None


def test_counter_going_backwards_is_treated_as_a_reset(tmp_path: Path) -> None:
    """A resumed session restarting its accounting must not produce a negative."""
    path = _write(
        tmp_path / "rollout-5.jsonl",
        [
            _turn_context("2026-07-20T09:00:00.000Z", "gpt-5-codex"),
            _token_count("2026-07-20T09:00:01.000Z", input_tokens=2500, cached=2000, output=250),
            _token_count("2026-07-20T09:05:00.000Z", input_tokens=500, cached=0, output=50),
        ],
    )
    rows = list(CodexSource().parse(path))
    assert rows[1]["input_tokens"] == 500
    assert rows[1]["output_tokens"] == 50
    assert all(value >= 0 for row in rows for value in row.values() if isinstance(value, int))


def test_falls_back_to_last_token_usage(tmp_path: Path) -> None:
    """Transcripts without a running total use the per-turn figure as-is."""
    path = _write(
        tmp_path / "rollout-6.jsonl",
        [
            _turn_context("2026-07-20T09:00:00.000Z", "gpt-5-codex"),
            _token_count(
                "2026-07-20T09:00:01.000Z", input_tokens=300, cached=100, output=40, cumulative=False
            ),
            _token_count(
                "2026-07-20T09:01:00.000Z", input_tokens=300, cached=100, output=40, cumulative=False
            ),
        ],
    )
    rows = list(CodexSource().parse(path))
    assert len(rows) == 2
    assert sum(r["input_tokens"] for r in rows) == 400  # (300 - 100) twice


def test_malformed_and_irrelevant_lines_are_skipped(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "rollout-7.jsonl",
        [
            "not json at all {{{",
            json.dumps({"timestamp": "2026-07-20T09:00:00.000Z", "type": "session_meta"}),
            json.dumps(["a list, not an object"]),
            json.dumps({"type": "token_count", "payload": {"type": "token_count"}}),  # no info
            _turn_context("2026-07-20T09:00:00.000Z", "gpt-5-codex"),
            _token_count("2026-07-20T09:00:01.000Z", input_tokens=10, cached=0, output=5),
        ],
    )
    assert len(list(CodexSource().parse(path))) == 1


def test_zero_usage_turns_produce_no_row(tmp_path: Path) -> None:
    """A repeated identical cumulative reading is a no-op, not a zero row."""
    path = _write(
        tmp_path / "rollout-8.jsonl",
        [
            _turn_context("2026-07-20T09:00:00.000Z", "gpt-5-codex"),
            _token_count("2026-07-20T09:00:01.000Z", input_tokens=100, cached=0, output=10),
            _token_count("2026-07-20T09:00:02.000Z", input_tokens=100, cached=0, output=10),
        ],
    )
    assert len(list(CodexSource().parse(path))) == 1


def test_missing_file_and_missing_root_yield_nothing(tmp_path: Path) -> None:
    source = CodexSource()
    assert list(source.parse(tmp_path / "ghost.jsonl")) == []

    source.root = lambda: tmp_path / "no-such-codex"  # type: ignore[method-assign]
    assert list(source.transcripts()) == []
    assert source.detect() is False


def test_transcripts_walks_the_date_nested_layout(tmp_path: Path) -> None:
    """Rollouts live under sessions/YYYY/MM/DD/, not one flat level."""
    day = tmp_path / "2026" / "07" / "20"
    day.mkdir(parents=True)
    (day / "rollout-2026-07-20T09-00-00-abc.jsonl").write_text("", encoding="utf-8")
    (day / "not-a-rollout.jsonl").write_text("", encoding="utf-8")

    source = CodexSource()
    source.root = lambda: tmp_path  # type: ignore[method-assign]
    found = [p.name for p in source.transcripts()]
    assert found == ["rollout-2026-07-20T09-00-00-abc.jsonl"]
