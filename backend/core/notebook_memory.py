"""What Argus knows about a student in one course, across conversations.

Thread history answers "what did we say ten minutes ago". This answers "what
should you not have to tell me again": the topics they keep getting wrong, the
thing they said they find confusing, when their exam is.

Two reasons it is separate from the chat summary. It outlives a thread -- a
new conversation about the same course starts knowing it -- and most of it is
*derived* rather than said. `grader.py` has computed `weak_topics` on every
attempt since exams existed and written them to a markdown file nobody reads
back; the flashcard store knows exactly which cards keep being graded
``again``. Both were facts about the student that the tutor could not see.

In ``core/`` rather than in a feature because two features need it -- study
writes the derived facts, chat reads them -- and no feature may import
another. It is a plain table accessor with no feature logic in it, which is
what makes that placement honest rather than a loophole.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

#: What a remembered fact is *about*. A small closed set, because the value is
#: in a tutor being able to say "you have missed this three times", and that
#: needs the kinds to mean something rather than be free text.
KINDS = ("weak-topic", "confusion", "goal", "preference")

#: The most facts injected into one prompt. A tutor that recites thirty things
#: it remembers is worse than one that recalls the three that matter, and this
#: text rides in front of every turn.
MAX_RECALLED = 8

#: Below this, a topic missed once is noise rather than a pattern.
MIN_CONFIDENCE = 1


@dataclass(frozen=True)
class Memory:
    """One durable fact about a student in one course."""

    kind: str
    subject: str
    detail: str
    confidence: int

    def render(self) -> str:
        weight = f" (seen {self.confidence}x)" if self.confidence > 1 else ""
        return f"- {self.subject}: {self.detail}{weight}"


def remember(
    conn: sqlite3.Connection,
    course: str,
    kind: str,
    subject: str,
    detail: str,
    *,
    reinforce: bool = True,
) -> None:
    """Record a fact, or strengthen one already known.

    ``reinforce`` is what turns "they got this wrong" into "they keep getting
    this wrong": the same subject seen again bumps its confidence rather than
    writing a second row, so the tutor can tell a slip from a pattern.

    Upsert rather than read-then-write, because the study and chat sides can
    both reach this and a UNIQUE violation would fail whatever they were
    actually doing.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown memory kind {kind!r} — expected one of {', '.join(KINDS)}")
    conn.execute(
        "INSERT INTO notebook_memory (course, kind, subject, detail, confidence) "
        "VALUES (?, ?, ?, ?, 1) "
        "ON CONFLICT(course, kind, subject) DO UPDATE SET "
        "  detail = excluded.detail,"
        "  confidence = confidence + ?,"
        "  updated_at = datetime('now')",
        (course, kind, subject, detail, 1 if reinforce else 0),
    )
    conn.commit()


def forget(conn: sqlite3.Connection, course: str, kind: str, subject: str) -> None:
    """Drop one fact -- a topic that has since been mastered, say."""
    conn.execute(
        "DELETE FROM notebook_memory WHERE course = ? AND kind = ? AND subject = ?",
        (course, kind, subject),
    )
    conn.commit()


def forget_course(conn: sqlite3.Connection, course: str) -> int:
    """Drop everything remembered about one course. Returns the rows removed.

    Called by ``features/study/deletes.py``: nothing in this schema cascades,
    so a deleted course would otherwise leave a tutor still remembering what
    the student struggled with in it.
    """
    cursor = conn.execute("DELETE FROM notebook_memory WHERE course = ?", (course,))
    conn.commit()
    return cursor.rowcount


def recall(conn: sqlite3.Connection, course: str, *, limit: int = MAX_RECALLED) -> list[Memory]:
    """The facts worth telling a tutor about this course, strongest first."""
    rows = conn.execute(
        "SELECT kind, subject, detail, confidence FROM notebook_memory "
        "WHERE course = ? AND confidence >= ? "
        "ORDER BY confidence DESC, updated_at DESC LIMIT ?",
        (course, MIN_CONFIDENCE, limit),
    ).fetchall()
    return [
        Memory(
            kind=str(row["kind"]),
            subject=str(row["subject"]),
            detail=str(row["detail"]),
            confidence=int(row["confidence"]),
        )
        for row in rows
    ]


def as_prompt_block(memories: list[Memory]) -> str:
    """The recalled facts as a block a model can read, or ``''`` for none.

    Phrased as notes the assistant made rather than as instructions, because
    that is what they are -- and because a model told "the student is weak on
    X" tends to announce it, which is a worse tutor than one that simply
    remembers.
    """
    if not memories:
        return ""
    lines = ["What you already know about this student in this course:"]
    lines.extend(memory.render() for memory in memories)
    lines.append(
        "Use it to pitch your answer. Do not recite it back at them, and do "
        "not treat it as more current than what they say now."
    )
    return "\n".join(lines)
