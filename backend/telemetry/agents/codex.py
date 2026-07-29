"""Codex CLI as an agent source.

Codex persists each session as a rollout transcript under
``~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-<stamp>-<uuid>.jsonl``. The parser
itself is the ``codex-rollout`` entry in :mod:`backend.telemetry.formats` —
including the cumulative-vs-delta handling that is the whole reason it needs
special care. This module supplies only what is Codex-specific about *where*
the logs are and *what its models cost*.

Nothing here is verified against a real install on the machine this was written
on; ``tests/telemetry/test_codex.py`` holds the fixtures that pin the
behaviour instead.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from backend.telemetry.agents.base import AgentSource
from backend.telemetry.formats import find_format, iter_files, parse_codex_rollout

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

FORMAT_ID = "codex-rollout"


class CodexSource(AgentSource):
    id = "codex"
    label = "Codex"
    install_hint = "Run a Codex CLI session — rollouts land in ~/.codex/sessions."
    format_id = FORMAT_ID

    def root(self) -> Path:
        return DEFAULT_CODEX_HOME

    def transcripts(self) -> Iterator[Path]:
        # Rollouts are nested by date (sessions/YYYY/MM/DD/), so this walks
        # rather than globbing one level like the Claude Code scan does.
        fmt = find_format(FORMAT_ID)
        glob = fmt.default_glob if fmt else "**/rollout-*.jsonl"
        return iter_files(self.root(), glob)

    def parse(self, path: Path) -> Iterator[dict]:
        return parse_codex_rollout(path)

    def rate_for(self, model: str) -> dict[str, float] | None:
        return CODEX_RATES.get(model)
