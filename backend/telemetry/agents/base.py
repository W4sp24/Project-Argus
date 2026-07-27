"""What every local coding-agent usage source has to provide.

An "agent source" is one CLI whose local session logs Argus can read. Since the
parsers moved to :mod:`backend.telemetry.formats`, a source is now just three
facts — **root, glob, format** — plus whatever it knows about its own pricing.
That is small enough to be a JSON record, which is what lets a user register an
agent from the UI instead of shipping a Python class.

Only :meth:`root` is abstract. Built-in sources override it because their
directories are computed (and, in Claude Code's case, monkeypatched by tests);
everything else has a working default.

Every method is best-effort by contract: a missing home directory, a malformed
line, or an unreadable file must yield nothing rather than raise.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

from backend.telemetry.formats import find_format, iter_files


class AgentSource(ABC):
    """One local coding-agent CLI whose transcripts Argus can read."""

    #: Stable key stored in ``cli_usage.agent`` — never change it in place.
    id: str
    #: What the UI calls this agent.
    label: str
    #: Shown when :meth:`detect` is False, so a disabled row says what to do.
    install_hint: str
    #: Which :mod:`backend.telemetry.formats` entry reads these logs.
    format_id: str
    #: Pattern under :meth:`root`. Empty means "the format's default".
    glob: str = ""
    #: False for sources the user registered and may delete.
    builtin: bool = True

    @abstractmethod
    def root(self) -> Path:
        """Directory this agent writes its session transcripts under."""

    def pattern(self) -> str:
        fmt = find_format(self.format_id)
        return self.glob or (fmt.default_glob if fmt else "*.jsonl")

    def transcripts(self) -> Iterator[Path]:
        """Every transcript worth parsing. Empty when the root is absent."""
        return iter_files(self.root(), self.pattern())

    def parse(self, path: Path) -> Iterator[dict]:
        """Yield ``{ts, model, input_tokens, output_tokens, cache_*}`` per turn."""
        fmt = find_format(self.format_id)
        return fmt.parse(path) if fmt else iter(())

    def rate_for(self, model: str) -> dict[str, float] | None:
        """Per-million-token ``{input, output}`` price, or None if unpriced.

        None is a real answer, not a failure: a local model is free and a
        provider whose prices Argus does not track is the user's to know.
        Callers name unpriced models rather than inventing a number for them —
        which is also why a user-registered source returns None for everything.
        """
        return None

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


class GenericSource(AgentSource):
    """A source the user registered from the UI.

    One class for every custom agent rather than one class each — the whole
    point of splitting parsing out of sources. Everything it needs came out of
    a dialog and lives in ``.argus/agent-sources.json``.
    """

    builtin = False

    def __init__(self, config: dict) -> None:
        self.id = str(config["id"])
        self.label = str(config.get("label") or config["id"])
        self.format_id = str(config.get("format") or "")
        self.glob = str(config.get("glob") or "")
        self._root = Path(str(config.get("path") or ""))
        self.install_hint = f"No matching logs under {self._root}."

    def root(self) -> Path:
        return self._root
