"""Syllabus import: dated items become task *suggestions*, never direct tasks (I1)."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from backend.rag.extract import extract_blocks

ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
WORDY_DATE_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?",
    re.IGNORECASE,
)
DEADLINE_HINTS = ("exam", "quiz", "midterm", "final", "due", "assignment", "project", "deadline")
MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}


def _dates_in(line: str, default_year: int) -> list[str]:
    dates = list(ISO_DATE_RE.findall(line))
    for month, day, year in WORDY_DATE_RE.findall(line):
        dates.append(
            f"{int(year or default_year):04d}-{MONTHS[month.lower()[:3]]:02d}-{int(day):02d}"
        )
    return dates


def parse_syllabus(conn: sqlite3.Connection, file_path: Path, course: str) -> int:
    """Extract dated deadlines from a syllabus file into suggestion rows.

    Returns the number of suggestions created. Reads the file, writes nothing
    to the vault — approval (P3) is what turns suggestions into tasks.
    """
    text = "\n".join(block.text for block in extract_blocks(file_path))
    default_year = datetime.now().year
    created = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or not any(hint in stripped.lower() for hint in DEADLINE_HINTS):
            continue
        for found in _dates_in(stripped, default_year):
            payload = {
                "task": f"{course}: {stripped[:140]}",
                "due": found,
                "course": course,
                "source": file_path.name,
                "lead_time_days": 3,
            }
            conn.execute(
                "INSERT INTO suggestions (kind, payload_json, rationale) VALUES (?, ?, ?)",
                (
                    "task",
                    json.dumps(payload, ensure_ascii=False),
                    f"Found in {file_path.name}: “{stripped[:180]}”",
                ),
            )
            created += 1
    conn.commit()
    return created
