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

from backend.agent.adapters import TextDelta, UsageReported, resolve_adapter

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
    from backend.usage import record_result_usage

    resolved_settings = settings
    if model and resolved_settings is None:
        from backend.config import Settings

        resolved_settings = Settings.load()

    adapter = resolve_adapter(
        resolved_settings,
        model,
        disallowed_tools=DISALLOWED_TOOLS,
        fallback_model=MODEL,
    )
    resolved_model = getattr(adapter, "model", MODEL)

    parts: list[str] = []
    async for event in adapter.run(system_prompt="", user_message=prompt, tools=[], max_turns=1):
        if isinstance(event, TextDelta):
            parts.append(event.text)
        elif isinstance(event, UsageReported):
            record_result_usage(db_path, feature, event, model=resolved_model)
    return "".join(parts)
