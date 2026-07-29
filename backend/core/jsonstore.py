"""Durable reads and writes for the small JSON registries in ``.argus/``.

Every registry beside the sqlite db — ``models.json``, ``model-prefs.json``,
``agent-sources.json``, ``mcp-servers.json`` — used to be written with
``Path.write_text``, which truncates the file to zero bytes *before* writing the
new contents. A crash, power loss or full disk in that window left a truncated
file, and every loader returned an empty list on any parse error. The next save
then overwrote the wreckage, so a single bad moment permanently deleted every
model, endpoint and custom agent the user had added — silently, with the OS
keyring entry left orphaned behind it.

Two rules fix that, and both matter:

**Write via a temp file and rename.** ``os.replace`` is atomic within a
filesystem, so a reader either sees the whole old file or the whole new one,
never a half. The temp file is created in the *same directory* for that reason.

**Never overwrite something unreadable.** An absent file is normal and stays
silent, but a file that exists and does not parse is quarantined to
``<name>.corrupt`` before the default is returned. The bytes survive for
recovery, the next save writes a fresh file rather than burying the evidence,
and :func:`corrupt_files` lets ``argus doctor`` tell the user it happened
instead of leaving them to notice their models are gone.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Appended to a file that exists but could not be parsed.
CORRUPT_SUFFIX = ".corrupt"


def save_json(path: Path, payload: Any) -> None:
    """Write ``payload`` to ``path`` atomically, creating parents as needed.

    The temp file lives in the target's own directory so the final
    :func:`os.replace` stays within one filesystem, where it is atomic. The
    ``fsync`` before it is what makes the guarantee survive a power loss rather
    than only a process crash.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        # A failed write must not leave a stray .tmp behind to confuse the next
        # one; the original file is still intact either way.
        tmp.unlink(missing_ok=True)
        raise


def load_json[T](path: Path, default: T) -> T:
    """Read ``path``, or return ``default``.

    Absent is silent — a registry nobody has written yet is not a problem.
    Unreadable is not: the file is renamed to ``<name>.corrupt`` and logged, so
    the next :func:`save_json` cannot bury it.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except OSError as exc:
        # Locked or unreadable, but still there — do not quarantine a file we
        # may simply lack permission to read right now.
        logger.warning("could not read %s: %s", path, exc)
        return default

    try:
        return json.loads(text)  # type: ignore[no-any-return]
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        _quarantine(path, exc)
        return default


def _quarantine(path: Path, exc: Exception) -> None:
    """Move an unparseable file aside so the next save cannot overwrite it."""
    target = path.with_name(path.name + CORRUPT_SUFFIX)
    try:
        os.replace(path, target)
    except OSError as move_exc:  # pragma: no cover - unwritable dir
        logger.warning("%s is corrupt (%s) and could not be moved: %s", path, exc, move_exc)
        return
    logger.warning("%s was corrupt (%s) — moved to %s", path, exc, target)


def corrupt_files(directory: Path) -> list[Path]:
    """Quarantined files in ``directory``, for the doctor's config-files check."""
    try:
        return sorted(p for p in directory.glob(f"*{CORRUPT_SUFFIX}") if p.is_file())
    except OSError:
        return []
