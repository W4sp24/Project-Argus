"""Look at a folder and work out whether Argus can read agent usage from it.

This is what makes "add an agent" a folder path instead of a Python class. It
is also, deliberately, the same shape as the model registry's
``POST /api/models/test``: run the real thing against the real input and report
what actually came back, rather than asking the user to know their tool's log
format and finding out later that they were wrong.

Everything is bounded. A user can point this at ``C:\\`` and it must return, so
format detection samples a handful of small files and the preview stats read at
most :data:`STAT_FILES` — reporting ``sampled`` rather than passing a partial
count off as a total.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from backend.telemetry.formats import FORMATS, LogFormat, iter_files, score_format

#: Files read while deciding *which* format matches. Detection needs a few
#: examples, not the corpus.
SCORE_FILES = 8

#: A parser that recognises nothing still reads to EOF looking, so oversized
#: files are skipped during detection only.
SCORE_MAX_BYTES = 32 * 1024 * 1024

#: Files read for the preview totals shown in the dialog.
STAT_FILES = 25

#: Tried in addition to each format's own default, because a user's folder need
#: not be laid out the way the tool that inspired the format lays its own out.
FALLBACK_GLOBS = ("*.jsonl", "**/*.jsonl", "**/*.json")


@dataclass
class Discovery:
    """What a scan found. ``ok`` is False when nothing recognised the folder."""

    ok: bool = False
    detail: str = ""
    fmt: LogFormat | None = None
    glob: str = ""
    files: int = 0
    turns: int = 0
    total_tokens: int = 0
    models: list[str] = field(default_factory=list)
    first_ts: str | None = None
    last_ts: str | None = None
    sampled: bool = False
    sample_paths: list[str] = field(default_factory=list)


def expand(raw: str) -> Path:
    """``~/.codex/sessions`` -> an absolute path. Never raises on nonsense."""
    try:
        return Path(raw.strip()).expanduser()
    except (OSError, RuntimeError, ValueError):
        return Path(raw.strip())


def _candidate_globs(explicit: str | None) -> list[str]:
    if explicit and explicit.strip():
        return [explicit.strip()]
    seen: list[str] = []
    for pattern in [fmt.default_glob for fmt in FORMATS] + list(FALLBACK_GLOBS):
        if pattern not in seen:
            seen.append(pattern)
    return seen


def _scorable(paths: list[Path]) -> list[Path]:
    picked: list[Path] = []
    for path in paths:
        try:
            if path.stat().st_size > SCORE_MAX_BYTES:
                continue
        except OSError:
            continue
        picked.append(path)
        if len(picked) >= SCORE_FILES:
            break
    return picked


def discover(root: Path, glob: str | None = None) -> Discovery:
    """Find the best (format, glob) for ``root`` and preview what it yields."""
    if not root.exists():
        return Discovery(detail=f"{root} does not exist")
    if not root.is_dir():
        return Discovery(detail=f"{root} is a file, not a folder")

    best: tuple[LogFormat, str, list[Path]] | None = None
    best_score = 0
    any_files = False

    for pattern in _candidate_globs(glob):
        matches = list(iter_files(root, pattern))
        if not matches:
            continue
        any_files = True
        sample = _scorable(matches)
        for fmt in FORMATS:
            score = score_format(fmt, sample)
            if score > best_score:
                best, best_score = (fmt, pattern, matches), score

    if best is None:
        return Discovery(
            detail=(
                "found files, but none of them carry token counts Argus recognises"
                if any_files
                else f"no .jsonl or .json files under {root}"
            )
        )

    fmt, pattern, matches = best
    return _preview(fmt, pattern, matches)


def _preview(fmt: LogFormat, pattern: str, matches: list[Path]) -> Discovery:
    """Real totals over a bounded slice of the matched files."""
    read = matches[:STAT_FILES]
    turns = total = 0
    models: set[str] = set()
    first = last = None

    for path in read:
        for row in fmt.parse(path):
            turns += 1
            total += (
                row["input_tokens"]
                + row["output_tokens"]
                + row["cache_creation_input_tokens"]
                + row["cache_read_input_tokens"]
            )
            models.add(str(row["model"]))
            ts = str(row["ts"])
            if first is None or ts < first:
                first = ts
            if last is None or ts > last:
                last = ts

    sampled = len(matches) > len(read)
    counted = f"{turns:,} turn{'' if turns == 1 else 's'}"
    where = (
        f"across {len(read)} of {len(matches)} files" if sampled else f"across {len(matches)} files"
    )
    return Discovery(
        ok=turns > 0,
        detail=(
            f"{fmt.label} — {counted} {where}"
            if turns
            else f"matched {fmt.label} files, but no token counts were in them"
        ),
        fmt=fmt,
        glob=pattern,
        files=len(matches),
        turns=turns,
        total_tokens=total,
        models=sorted(models),
        first_ts=first,
        last_ts=last,
        sampled=sampled,
        sample_paths=[str(path) for path in read[:3]],
    )
