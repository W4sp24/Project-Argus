"""Claude Code as agent sources — foreground sessions, and delegated subagents.

Two sources over one directory, split by glob:

* ``claude-code`` — ``<project>/<session>.jsonl``, the sessions you drove.
* ``claude-subagents`` — ``<project>/<session>/subagents/agent-*.jsonl``, the
  background agents those sessions spawned.

The second one exists because the first deliberately excluded it, and that
exclusion was hiding real money. Measured locally when this was written:
1.71B tokens in foreground sessions against **483M in subagents — 22% of all
Claude Code spend**, invisible in a panel whose job is to say where tokens go.
Delegation is a fifth of the answer, so it gets its own line rather than being
folded away or dropped.

They stay two sources rather than one summed total because the whole question
worth asking is how the split moves. ``ALL AGENTS`` adds them back together.

``root()`` reads ``DEFAULT_CLAUDE_HOME`` through the module at call time rather
than binding it at import, so a test that monkeypatches it redirects both
sources at a fixture directory.
"""

from __future__ import annotations

from pathlib import Path

from backend.core.model_registry import FALLBACK_RATE, MODEL_RATES
from backend.telemetry import claude_cli
from backend.telemetry.agents.base import AgentSource

FORMAT_ID = "claude-jsonl"


class _ClaudeSource(AgentSource):
    """Shared root and pricing for anything reading ~/.claude/projects."""

    format_id = FORMAT_ID

    def root(self) -> Path:
        return claude_cli.DEFAULT_CLAUDE_HOME

    def rate_for(self, model: str) -> dict[str, float]:
        """Every model in a Claude Code transcript is an Anthropic one.

        So an unrecognised name here is a *newer* Claude, not a free local
        model — unlike :mod:`backend.telemetry.usage`, pricing it at the
        current agent model is closer to the truth than pricing it at zero.
        """
        return MODEL_RATES.get(model, FALLBACK_RATE)


class ClaudeCodeSource(_ClaudeSource):
    id = "claude-code"
    label = "Claude Code"
    install_hint = "Run a Claude Code session — transcripts land in ~/.claude/projects."
    # One level down only, so a session file is never also counted as a subagent.
    glob = "*/*.jsonl"


class ClaudeSubagentSource(_ClaudeSource):
    id = "claude-subagents"
    label = "Claude Code · subagents"
    install_hint = "No background subagents have run yet — these appear once one does."
    glob = "*/*/subagents/*.jsonl"

    def detect(self) -> bool:
        """Present only once a subagent has actually run.

        The projects directory existing says nothing about delegation, and a
        source that claims to be detected while reporting zero is the confusing
        state this panel is meant to avoid.
        """
        return any(True for _ in self.transcripts())
