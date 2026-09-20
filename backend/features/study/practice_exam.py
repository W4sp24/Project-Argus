"""Practice exams grounded in real course materials.

Every question must cite a verbatim quote from the course corpus; questions
whose citation cannot be verified are dropped (invariant I6). Exam files are
NEW files under each course's ``study/`` subfolder (see
:mod:`backend.core.taxonomy`) — the one direct-write exemption to the
single-writer rule (I1).
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.agent.formatting import compose, json_math_contract
from backend.agent.generate import Generator
from backend.agent.itemflaws import Item as FlawItem
from backend.agent.itemflaws import check_set, item_writing_rules, rejected
from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.rag.select import MAX_PROMPT_CHARS, pack_excerpts

# Re-exported: this module was where `Generator` and the prompt budget lived,
# and several callers (and their tests) still import them from here.
__all__ = ["MAX_PROMPT_CHARS", "Exam", "Generator", "Question", "StudyError"]


class StudyError(RuntimeError):
    """Raised when study content cannot be generated."""


class Citation(BaseModel):
    """Verbatim pointer into the course corpus."""

    path: str
    page: int | None = None
    slide: int | None = None
    quote: str

    def label(self) -> str:
        name = self.path.rsplit("/", 1)[-1]
        if self.page is not None:
            return f"{name} p.{self.page}"
        if self.slide is not None:
            return f"{name} slide {self.slide}"
        return name


class Question(BaseModel):
    """One validated exam question."""

    q: str
    type: Literal["mcq", "short", "problem"]
    options: list[str] | None = None
    answer: str
    explanation: str = ""
    citation: Citation


class Exam(BaseModel):
    """A validated, citable practice exam."""

    course: str
    title: str
    questions: list[Question] = Field(default_factory=list)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _strip_fences(raw: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    return match.group(1) if match else raw


#: Hex digits, for recognising a well-formed ``\uXXXX``.
_HEX = frozenset("0123456789abcdefABCDEF")

#: Escapes a model writing this schema plausibly *means*. Everything else
#: following a backslash is read as LaTeX and the backslash is kept literal.
#:
#: ``\n`` is in here and ``\t``/``\b``/``\f``/``\r`` are not, and that split is
#: the whole judgement call. A line break inside an explanation is something a
#: model really does want; a tab, backspace, formfeed or carriage return is
#: not, while ``\times``, ``\begin``, ``\frac`` and ``\rho`` are among the most
#: common commands in the language. So ``\n`` keeps its meaning and the other
#: four are treated as notation. ``\neq`` is the one case this gets wrong, and
#: the exam contract tells the model to double its backslashes because of it.
_KEPT_ESCAPES = frozenset('"\\/n')

#: Every C0 control character except LF. Finding one in a parsed payload is
#: taken as proof that a LaTeX backslash was decoded as an escape.
#:
#: TAB is the one arguable member: a quote lifted verbatim out of an extracted
#: PDF could contain a real one, and repairing such a payload would leave a
#: literal ``\t`` in the quote, failing ``_citation_verified`` and costing that
#: question. Included anyway, because ``\text`` is far more likely than a tab
#: in a cited sentence, and losing one question beats writing a tab into the
#: middle of an explanation in the vault permanently.
_DECODED_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f]")


def _repair_lone_backslashes(text: str) -> str:
    r"""Double any backslash inside a JSON string that isn't starting an escape.

    Walks the document tracking whether it is inside a string literal, because
    a backslash outside one is not an escape and must not be touched.
    """
    out: list[str] = []
    index, end = 0, len(text)
    in_string = False
    while index < end:
        char = text[index]
        if not in_string:
            in_string = char == '"'
            out.append(char)
            index += 1
        elif char != "\\":
            in_string = char != '"'
            out.append(char)
            index += 1
        else:
            following = text[index + 1 : index + 2]
            unicode_escape = following == "u" and len(text[index + 2 : index + 6]) == 4
            if unicode_escape:
                unicode_escape = set(text[index + 2 : index + 6]) <= _HEX
            if following in _KEPT_ESCAPES or unicode_escape:
                out.append(char + following)
                index += 2
            else:
                out.append("\\\\")
                index += 1
    return "".join(out)


def _has_decoded_control(value: Any) -> bool:
    """Did anything in this payload decode to a control character?"""
    if isinstance(value, str):
        return _DECODED_CONTROL.search(value) is not None
    if isinstance(value, dict):
        return any(_has_decoded_control(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_decoded_control(item) for item in value)
    return False


def _decode_payload(raw: str) -> Any:
    r"""``json.loads``, tolerant of a model that wrote LaTeX into a JSON string.

    Asking for mathematical notation and asking for JSON are in tension: a
    backslash means one thing to LaTeX and another to JSON, and a model
    reliably writes ``\frac`` where the format needs ``\\frac``. That has two
    outcomes and no third. ``\alpha``, ``\sum``, ``\left``, ``\cdot``, ``\pi``
    are not valid escapes, so the parse raises and the whole exam is lost.
    ``\frac``, ``\text``, ``\begin`` and ``\rho`` *are*, so they decode
    silently to a formfeed, a tab, a backspace and a carriage return -- past
    ``_citation_verified``, which normalises to ``[a-z0-9]`` and so cannot
    see them, into ``exams.questions_json``, and into the vault for good.

    So a clean parse is not sufficient evidence of a clean payload. Both the
    raised error and the tell-tale control character route to the same repair,
    and the repair is only ever applied to text that has already failed one of
    those two checks -- a payload the model escaped correctly is returned
    exactly as ``json.loads`` produced it.
    """
    text = _strip_fences(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as first_error:
        try:
            return json.loads(_repair_lone_backslashes(text))
        except json.JSONDecodeError:
            # Report the original failure: the repaired text is not what the
            # generator sent, so its error offsets would point at nothing.
            raise StudyError(f"generator returned invalid JSON: {first_error}") from first_error

    if not _has_decoded_control(payload):
        return payload
    try:
        return json.loads(_repair_lone_backslashes(text))
    except json.JSONDecodeError:
        return payload  # Mangled, but parseable — better than losing the exam.


def _citation_verified(citation: Citation, corpus: list[dict[str, Any]]) -> bool:
    quote = _normalize(citation.quote)
    if not quote:
        return False
    for chunk in corpus:
        if chunk["meta"].get("path") != citation.path:
            continue
        if quote in _normalize(chunk["text"]):
            return True
    return False


def _as_flaw_item(question: Question) -> FlawItem:
    """A question in the shape the validator understands."""
    return FlawItem(
        stem=question.q,
        options=tuple(question.options or ()),
        answer=question.answer,
        explanation=question.explanation,
    )


def build_exam(
    course: str, raw: str, corpus: list[dict[str, Any]]
) -> tuple[Exam, int, dict[str, int]]:
    """Parse generator output; keep only questions worth sitting.

    Two gates, and they are different in kind. The citation check is I6 --
    a question whose quote cannot be found in the corpus is unsupported and
    never ships whatever else is true of it. The flaw check is quality: it
    drops a question that is about the document rather than the subject, or
    that a test-wise student could answer without knowing anything.

    Returns the exam, the number dropped, and a count per reason -- because
    "I asked for 20 and got 6" was previously unexplained. `dropped` was
    computed and thrown away at every call site.
    """
    payload = _decode_payload(raw)

    parsed: list[Question] = []
    dropped = 0
    reasons: dict[str, int] = {}
    for raw_question in payload.get("questions", []):
        try:
            question = Question.model_validate(raw_question)
        except Exception:
            dropped += 1
            reasons["malformed"] = reasons.get("malformed", 0) + 1
            continue
        if not _citation_verified(question.citation, corpus):
            dropped += 1  # I6: uncited questions never ship
            reasons["uncited"] = reasons.get("uncited", 0) + 1
            continue
        parsed.append(question)

    # Checked as a set, not one at a time: some flaws only exist in company.
    # A key in position A six times out of ten is a paper a guesser scores
    # well on, and no single question shows it.
    kept: list[Question] = []
    flaws_by_index = check_set([_as_flaw_item(question) for question in parsed])
    for index, question in enumerate(parsed):
        flaws = flaws_by_index[index]
        if not rejected(flaws):
            kept.append(question)
            continue
        dropped += 1
        for flaw in flaws:
            if flaw.severity == "reject":
                reasons[flaw.code] = reasons.get(flaw.code, 0) + 1

    return (
        Exam(
            course=course,
            title=str(payload.get("title") or f"{course} practice exam"),
            questions=kept,
        ),
        dropped,
        reasons,
    )


def unique_base(directory: Path, base: str) -> str:
    """A filename stem in `directory` that no `<stem>.md` already uses.

    Generated study output is named after the course, the day and the shape of
    the request, so two runs on the same day collide by construction. The exam
    path has always counted past a collision; the guide used to write straight
    over the earlier file, which is why this lives here rather than inline.
    """
    if not (directory / f"{base}.md").exists():
        return base
    suffix = 1
    while (directory / f"{base}-{suffix}.md").exists():
        suffix += 1
    return f"{base}-{suffix}"


#: Rough minutes per question by type, for the time estimate on the paper.
#: A real exam tells you how long you have; a list of questions does not.
_MINUTES = {"mcq": 1.5, "short": 3.0, "problem": 6.0}


def _frontmatter(exam: Exam, course: str, kind: str, extra: dict[str, Any]) -> list[str]:
    """YAML header so an exam is a note rather than a loose file.

    Exams used to open on a bare ``# H1``. That made them invisible to
    ``relink`` (which keys on ``generated_by: argus``), to the WRITTEN badge
    in the SOURCES rail, and to Obsidian's graph -- while the guide in the
    same folder carried a full header.
    """
    import frontmatter as _fm

    post = _fm.Post(
        "",
        title=f"{exam.title}{' — answer key' if kind == 'key' else ''}",
        type=kind,
        generated_by="argus",
        course=course,
        questions=len(exam.questions),
        tags=["argus/exam", f"course/{course}"],
        **extra,
    )
    return _fm.dumps(post).rstrip().splitlines()


def _answer_space(question: Question) -> list[str]:
    """Somewhere to write. A short question used to render as a heading alone."""
    if question.type == "short":
        return ["_Answer:_ ", ""]
    return ["_Working:_", "", "> ", "> ", ""]


def render_exam_md(
    exam: Exam, *, course: str = "", rel_path: str = "", difficulty: str = ""
) -> str:
    """The question paper, shaped like one.

    Every field is run through ``_escape_dollars``, not just the citation
    quote. A bare ``$`` in a question, an option or an answer opens a maths
    span that runs to the next ``$`` -- which is usually several questions
    further down the page.
    """
    minutes = sum(_MINUTES.get(question.type, 2.0) for question in exam.questions)
    stem = rel_path.rsplit("/", 1)[-1][:-3]
    key_link = f"[[{stem}-key|answer key]]" if rel_path else "answer key"
    lines = _frontmatter(
        exam,
        course or exam.course,
        "exam",
        {"difficulty": difficulty} if difficulty else {},
    )
    lines += [
        "",
        f"# {_safe_maths(exam.title)}",
        "",
        f"**{len(exam.questions)} questions** · about {round(minutes)} minutes"
        + (f" · {difficulty}" if difficulty else ""),
        "",
        "Answer every question. Working is marked where it is asked for.",
        "",
        "---",
        "",
    ]
    for number, question in enumerate(exam.questions, start=1):
        lines += [f"## {number}. {_safe_maths(question.q)}", ""]
        if question.type == "mcq" and question.options:
            for letter, option in zip("ABCDEFGH", question.options, strict=False):
                lines.append(f"- {letter}) {_safe_maths(option)}")
            lines.append("")
        else:
            lines += _answer_space(question)
    lines += ["---", "", f"Answers and citations: {key_link}."]
    return "\n".join(lines)


def _escape_dollars(text: str) -> str:
    r"""Neutralise a bare ``$`` so it cannot open a maths span.

    Applied to the *verbatim* strings -- a citation quote is lifted straight
    out of course material, so it can carry a price, a shell variable or a
    stray delimiter that Argus and Obsidian would both read as the start of an
    equation running to the next ``$`` somewhere further down the key. A ``$``
    that is already escaped is left alone.
    """
    return re.sub(r"(?<!\\)\$", r"\\$", text)


def _safe_maths(text: str) -> str:
    r"""Escape ``$`` only when the field cannot be valid maths anyway.

    ``q``, ``answer`` and ``explanation`` legitimately carry LaTeX -- the exam
    contract asks for it -- so escaping them unconditionally would turn every
    inline equation into literal dollar signs. But an *odd* number of
    unescaped delimiters cannot be balanced maths, and one stray ``$`` opens a
    span that swallows the page down to the next one, usually several
    questions later. So: balanced, leave it; unbalanced, neutralise it.
    """
    if len(re.findall(r"(?<!\\)\$", text)) % 2 == 0:
        return text
    return _escape_dollars(text)


def render_key_md(exam: Exam, *, course: str = "", rel_path: str = "") -> str:
    paper = f"[[{rel_path.rsplit('/', 1)[-1][:-3]}|the paper]]" if rel_path else "the paper"
    lines = _frontmatter(exam, course or exam.course, "exam-key", {})
    lines += ["", f"# {_safe_maths(exam.title)} — answer key", "", f"Questions: {paper}.", ""]
    for number, question in enumerate(exam.questions, start=1):
        # Blank lines between the three fields, not just newlines. Consecutive
        # lines are one paragraph with soft breaks, which puts "**Why:**" and
        # anything the explanation contains mid-line -- so a display block
        # never starts a line and renders as a literal $$ instead of maths.
        quote = _escape_dollars(question.citation.quote)
        lines += [
            f"## {number}. {_safe_maths(question.q)}",
            "",
            f"**Answer:** {_safe_maths(question.answer)}",
            "",
            f"**Why:** {_safe_maths(question.explanation)}",
            "",
            f"**Source:** {question.citation.label()} — “{quote}”",
            "",
        ]
    return "\n".join(lines)


def exam_prompt(
    course: str, corpus: list[dict[str, Any]], topics: str | None, n: int, difficulty: str
) -> str:

    topic_line = f"Focus on: {topics}." if topics else "Cover the material broadly."
    task = f"""Create a {difficulty} practice exam with exactly {n} questions for course {course},
grounded ONLY in the source excerpts below. {topic_line}

Return ONLY JSON (no prose) with this exact schema:
{{"title": str, "questions": [{{"q": str, "type": "mcq"|"short"|"problem",
"options": [str, ...] (mcq only, 4 options), "answer": str, "explanation": str,
"citation": {{"path": str, "page": int|null, "slide": int|null, "quote": str}}}}]}}

Citation rules (questions violating them will be discarded):
- "path" must be one of the SOURCE paths verbatim.
- "quote" must be a short VERBATIM substring copied from that source excerpt.
- Do not ask about anything not present in the excerpts.

The citation records where a question came from. It is not what the question
is about: never mention the excerpt, the document, the deck, a slide number
or a page number in "q", in an option, or in "answer"."""

    # json_math_contract(), not the markdown math_contract() the notes and the
    # study guide get. Three of those rules invert once the reply is JSON --
    # backslashes double, $$ blocks are out, and `answer` must stay plain
    # because the grader compares it to what a person typed. Handing over the
    # markdown contract here would instruct the model to produce exactly what
    # this feature cannot consume.
    return compose(
        task,
        item_writing_rules(),
        json_math_contract(),
        f"SOURCES:\n{pack_excerpts(corpus)}",
    )


async def generate_practice_exam(
    vault_path: Path,
    conn: sqlite3.Connection,
    generator: Generator,
    corpus: list[dict[str, Any]],
    course: str,
    topics: str | None = None,
    n: int = 10,
    difficulty: str = "medium",
    *,
    taxonomy: Taxonomy | None = None,
) -> tuple[int, Exam, str]:
    """Generate, validate, persist, and write one practice exam.

    Returns (exam_id, exam, vault-relative exam path).
    """
    tax = taxonomy or active_taxonomy()
    if not corpus:
        raise StudyError(f"no indexed material for course {course} — upload to materials/ first")

    from backend.telemetry.audit import log_prompt_conn

    log_prompt_conn(
        conn,
        "study",
        "claude-opus-4-8",
        [str(chunk["meta"].get("path")) for chunk in corpus if chunk["meta"].get("path")],
    )
    prompt = exam_prompt(course, corpus, topics, n, difficulty)
    raw = await generator(prompt)
    exam, dropped, reasons = build_exam(course, raw, corpus)

    # One retry, and only for what was actually wrong. A model told "you wrote
    # four questions about the document; write four more about the subject"
    # usually complies; the same model asked again from scratch usually
    # repeats itself. Bounded at one because a second retry has never been
    # worth the wait, and because the caller is holding a job open.
    if dropped and len(exam.questions) < n:
        retry = await generator(_retry_prompt(prompt, exam, dropped, reasons, n))
        extra, _extra_dropped, extra_reasons = build_exam(course, retry, corpus)
        seen = {_normalize(question.q) for question in exam.questions}
        for question in extra.questions:
            if len(exam.questions) >= n:
                break
            if _normalize(question.q) in seen:
                continue
            seen.add(_normalize(question.q))
            exam.questions.append(question)
        for code, count in extra_reasons.items():
            reasons[code] = reasons.get(code, 0) + count

    if not exam.questions:
        raise StudyError(
            f"all {dropped} generated questions were rejected "
            f"({', '.join(f'{code}: {count}' for code, count in sorted(reasons.items()))})"
        )

    study_dir = vault_path / tax.course_study(course)
    study_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    base = unique_base(study_dir, f"exam-{stamp}-{len(exam.questions)}q")
    rel_path = f"{tax.course_study(course)}/{base}.md"

    # The row before the files: an INSERT that fails after the writes leaves
    # two markdown files the quiz UI can never reach.
    cursor = conn.execute(
        "INSERT INTO exams (course, title, questions_json) VALUES (?, ?, ?)",
        (course, exam.title, exam.model_dump_json()),
    )
    conn.commit()

    (study_dir / f"{base}.md").write_text(
        render_exam_md(exam, course=course, rel_path=rel_path, difficulty=difficulty),
        encoding="utf-8",
    )
    (study_dir / f"{base}-key.md").write_text(
        render_key_md(exam, course=course, rel_path=rel_path), encoding="utf-8"
    )
    return int(cursor.lastrowid), exam, rel_path


def _retry_prompt(
    original: str, exam: Exam, dropped: int, reasons: dict[str, int], wanted: int
) -> str:
    """Ask for replacements for what was rejected, naming the reasons.

    Deliberately carries the original prompt: the model needs the same source
    excerpts to write a grounded question, and re-deriving them would be a
    second selection pass over the same corpus.
    """
    missing = max(1, wanted - len(exam.questions))
    named = ", ".join(f"{code} ({count})" for code, count in sorted(reasons.items()))
    return compose(
        original,
        f"""RETRY

{dropped} of your questions were rejected: {named}.

Write {missing} replacement question(s) in the same JSON schema, fixing those
faults. Do not repeat any question you have already written. Return only the
JSON, with just the replacements in "questions".""",
    )
