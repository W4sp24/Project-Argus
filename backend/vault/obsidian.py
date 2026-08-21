"""Deep links into Obsidian, and the vault settings they have to agree with.

Every ``obsidian://`` link Argus produced used to be built as
``open?vault=<folder basename>&file=<path>``, in five separate places. That is
wrong in a way that only shows up on someone else's machine: ``vault=`` is
matched against the vault's *registered* name in Obsidian, which is set when
the vault is added and is independent of what the directory is called
afterwards. Rename the folder, point ``VAULT_PATH`` at a differently-cased
path, or register the vault under any other name, and every link in the product
fails with Obsidian's own "Vault not found" dialog — which was reported against
a vault called Second Brain.

``open?path=<absolute path>`` has no such failure mode: Obsidian resolves the
vault from the path itself. That is what everything builds now, and it is why
``/api/vault`` grows an absolute ``path`` field.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

#: Obsidian's own config directory inside a vault. Its presence is also the
#: only reliable signal that a folder has ever been opened as a vault.
CONFIG_DIR = ".obsidian"

#: Where the Daily Notes core plugin keeps its folder/format settings.
DAILY_NOTES_CONFIG = "daily-notes.json"

#: What Obsidian's Daily Notes plugin uses when the user has not chosen.
DEFAULT_DAILY_FORMAT = "YYYY-MM-DD"


def note_uri(vault_path: Path, rel_path: str) -> str:
    """A deep link to one note, immune to the vault's registered name.

    ``quote`` with the default ``safe="/"`` keeps path separators readable and
    escapes the characters that would otherwise terminate the query string.
    """
    absolute = (vault_path / rel_path).as_posix()
    return f"obsidian://open?path={quote(absolute, safe='')}"


def is_obsidian_vault(vault_path: Path) -> bool:
    """Whether this folder has ever been opened in Obsidian.

    A vault Obsidian has never seen cannot be deep-linked into by any URL,
    which is worth telling the user *before* they click a link and get an
    error dialog with no explanation. See ``backend.features.system.doctor``.
    """
    return (vault_path / CONFIG_DIR).is_dir()


def _read_config(vault_path: Path, name: str) -> dict:
    """One of Obsidian's own JSON config files, or ``{}``.

    Never raises. These files belong to Obsidian, not to Argus: they may be
    absent, half-written while Obsidian saves, or hand-edited into something
    invalid, and none of that is a reason for an Argus request to fail.
    """
    config = vault_path / CONFIG_DIR / name
    try:
        parsed = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _to_strftime(moment_format: str) -> str | None:
    """Translate a Moment.js date format to ``strftime``, or ``None``.

    Obsidian stores the Daily Notes format in Moment.js tokens. Only the
    date-shaped subset is translated — a format using anything else is one
    Argus should not try to reproduce, and ``None`` sends the caller back to
    its own default rather than to a filename built from a half-understood
    pattern.
    """
    tokens = [
        ("YYYY", "%Y"),
        ("MMMM", "%B"),
        ("dddd", "%A"),
        ("MMM", "%b"),
        ("ddd", "%a"),
        ("MM", "%m"),
        ("DD", "%d"),
        ("YY", "%y"),
    ]
    out = []
    index = 0
    while index < len(moment_format):
        for token, replacement in tokens:
            if moment_format.startswith(token, index):
                out.append(replacement)
                index += len(token)
                break
        else:
            char = moment_format[index]
            # Separators are fine; a letter we did not translate is a token we
            # do not understand, and guessing would silently misname the file.
            if char.isalpha():
                return None
            out.append("%%" if char == "%" else char)
            index += 1
    return "".join(out)


def daily_note_settings(vault_path: Path, fallback_folder: str) -> tuple[str, str]:
    """``(folder, strftime_format)`` for this vault's daily notes.

    Argus used to write ``<taxonomy.daily>/<ISO date>.md`` unconditionally. A
    vault whose Daily Notes plugin is configured differently — a ``Journal/``
    folder, or ``YYYY-MM-DD dddd`` filenames — therefore ended up with a second,
    parallel set of daily notes that the user never opens, while the note they
    *do* open stays empty. Reading Obsidian's own setting is what makes "add
    this to today's note" mean the same thing in both applications.

    Falls back to the taxonomy folder and ISO dates whenever the config is
    missing, empty, or in a format this cannot faithfully reproduce.
    """
    config = _read_config(vault_path, DAILY_NOTES_CONFIG)
    raw_folder = config.get("folder")
    folder = raw_folder.strip("/") if isinstance(raw_folder, str) and raw_folder.strip() else ""
    raw_format = config.get("format")
    translated = (
        _to_strftime(raw_format.strip())
        if isinstance(raw_format, str) and raw_format.strip()
        else None
    )
    return folder or fallback_folder, translated or "%Y-%m-%d"
