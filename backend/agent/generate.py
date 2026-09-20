"""One-shot text generation, on whichever backend the caller chooses.

Used by the coursework engine, email ingest, and the morning briefing — all of
which need a plain prompt→text call with no tools. Kept separate from the chat
runtime so study code can inject fakes.

Omitting ``model`` keeps the historical Claude Code path (subscription auth,
invariant I5); naming a registry model routes through
:mod:`backend.agent.adapters` like everything else.
"""

from __future__ import annotations

from pathlib import Path

from backend.agent.adapters import (
    PROVIDER_CLAUDE_CLI,
    AgentError,
    Message,
    TextDelta,
    UsageReported,
    resolve_adapter,
)

MODEL = "claude-opus-4-8"
# No tools, and no filesystem access either — this is a pure text call.
DISALLOWED_TOOLS = ("Bash", "Write", "Edit", "Read", "Glob", "Grep", "WebSearch")


async def agent_generate(
    prompt: str,
    feature: str = "generate",
    db_path: Path | None = None,
    model: str | None = None,
    settings: object | None = None,
) -> str:
    """Run one tool-less generation and return the concatenated text.

    ``feature``/``db_path`` only label the fire-and-forget token-usage log
    (§14); when ``db_path`` is None the recorder resolves it best-effort.
    ``settings`` is only needed to look ``model`` up in the registry — with no
    ``model`` there is nothing to look up, which is why it stays optional and
    every existing call site keeps working unchanged.
    """
    from backend.telemetry.usage import record_result_usage

    resolved_settings = settings
    if model and resolved_settings is None:
        from backend.core.config import Settings

        resolved_settings = Settings.load()

    adapter = resolve_adapter(
        resolved_settings,
        model,
        disallowed_tools=DISALLOWED_TOOLS,
        fallback_model=MODEL,
    )
    resolved_model = getattr(adapter, "model", MODEL)

    parts: list[str] = []
    async for event in adapter.run(
        system_prompt="", messages=[Message("user", prompt)], tools=[], max_turns=1
    ):
        if isinstance(event, TextDelta):
            parts.append(event.text)
        elif isinstance(event, UsageReported):
            record_result_usage(db_path, feature, event, model=resolved_model)
    return "".join(parts)


async def agent_describe_image(
    png: bytes,
    prompt: str,
    feature: str = "ocr",
    db_path: Path | None = None,
    model: str | None = None,
    settings: object | None = None,
) -> str:
    """Run one tool-less generation over an image and return its text.

    Used by the OCR escalation in :mod:`backend.rag.extractors.ocr` for pages
    local OCR could not read. It is a separate function rather than a keyword
    on :func:`agent_generate` because its failure mode is different: there is
    no subscription path for this. The Claude Code CLI adapter takes a prompt
    string and has nowhere to put an image, so a caller with no registry model
    configured gets told that plainly here rather than discovering it as a
    provider-side 400 halfway through a 55-page deck.
    """
    from backend.telemetry.usage import record_result_usage

    resolved_settings = settings
    if resolved_settings is None:
        from backend.core.config import Settings

        resolved_settings = Settings.load()

    adapter = resolve_adapter(
        resolved_settings,
        model,
        disallowed_tools=DISALLOWED_TOOLS,
        fallback_model=MODEL,
    )
    if getattr(adapter, "provider", "") == PROVIDER_CLAUDE_CLI:
        raise AgentError(
            "reading a page image needs a registry model with an API key — the "
            "Claude Code subscription path cannot take images. Register one "
            "under /system, or leave OCR on its local engine."
        )
    resolved_model = getattr(adapter, "model", MODEL)

    parts: list[str] = []
    async for event in adapter.run(
        system_prompt="",
        messages=[Message("user", prompt, images=(png,))],
        tools=[],
        max_turns=1,
    ):
        if isinstance(event, TextDelta):
            parts.append(event.text)
        elif isinstance(event, UsageReported):
            record_result_usage(db_path, feature, event, model=resolved_model)
    return "".join(parts)
