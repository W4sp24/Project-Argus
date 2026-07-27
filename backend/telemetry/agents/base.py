"""What every local coding-agent usage source has to provide.

An "agent source" is one CLI that keeps local session transcripts Argus can
read: Claude Code today, Codex alongside it, whatever comes next. The source
owns three things nobody else can know — where its transcripts live, how to
read a token count out of one, and what its models cost — and gets the
incremental scanning and aggregation for free from
:mod:`backend.telemetry.scan`.

Every method is best-effort by contract: a missing home directory, a malformed
line, or an unreadable file must yield nothing rather than raise. A usage panel
is never worth breaking a page over.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path


class AgentSource(ABC):
    """One local coding-agent CLI whose transcripts Argus can read."""

    #: Stable key stored in ``cli_usage.agent`` — never change it in place.
    id: str
    #: What the UI calls this agent.
    label: str
    #: Shown when :meth:`detect` is False, so an empty card says what to do.
    install_hint: str

    @abstractmethod
    def root(self) -> Path:
        """Directory this agent writes its session transcripts under."""

    @abstractmethod
    def transcripts(self) -> Iterator[Path]:
        """Every transcript file worth parsing. Empty when the root is absent."""

    @abstractmethod
    def parse(self, path: Path) -> Iterator[dict]:
        """Yield ``{ts, model, input_tokens, output_tokens, cache_*}`` per turn.

        Never raises — an unreadable or malformed file yields nothing.
        """

    @abstractmethod
    def rate_for(self, model: str) -> dict[str, float] | None:
        """Per-million-token ``{input, output}`` price, or None if unpriced.

        None is a real answer, not a failure: a local model is free and a
        provider whose prices Argus does not track is the user's to know.
        Callers name unpriced models rather than inventing a number for them.
        """

    def detect(self) -> bool:
        """Whether this agent is installed and has written anything locally."""
        try:
            return self.root().is_dir()
        except OSError:
            return False

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Estimated spend for one model's totals. Unpriced models cost zero.

        Cache tokens are deliberately excluded, matching
        :mod:`backend.telemetry.usage`: they are billed at different rates per
        provider and the estimate is honest about being an estimate.
        """
        rate = self.rate_for(model)
        if rate is None:
            return 0.0
        return (input_tokens * rate["input"] + output_tokens * rate["output"]) / 1_000_000
