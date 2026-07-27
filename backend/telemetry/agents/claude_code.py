"""Claude Code as an agent source.

A thin adapter over :mod:`backend.telemetry.claude_cli`, which still owns the
parsing. The indirection is deliberate: ``DEFAULT_CLAUDE_HOME`` is read through
the module at call time rather than bound at import, so a test that
monkeypatches it points this source at a fixture directory too.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from backend.core.model_registry import FALLBACK_RATE, MODEL_RATES
from backend.telemetry import claude_cli
from backend.telemetry.agents.base import AgentSource


class ClaudeCodeSource(AgentSource):
    id = "claude-code"
    label = "Claude Code"
    install_hint = "Run a Claude Code session — transcripts land in ~/.claude/projects."

    def root(self) -> Path:
        return claude_cli.DEFAULT_CLAUDE_HOME

    def transcripts(self) -> Iterator[Path]:
        return claude_cli.scan_projects(self.root())

    def parse(self, path: Path) -> Iterator[dict]:
        return claude_cli.parse_transcript(path)

    def rate_for(self, model: str) -> dict[str, float]:
        """Every model in a Claude Code transcript is an Anthropic one.

        So an unrecognised name here is a *newer* Claude, not a free local
        model — unlike :mod:`backend.telemetry.usage`, pricing it at the
        current agent model is closer to the truth than pricing it at zero.
        """
        return MODEL_RATES.get(model, FALLBACK_RATE)
