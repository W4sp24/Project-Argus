"""One-shot text generation via the agent SDK (subscription auth, I5).

Used by the coursework engine, which needs a plain prompt→text call with no
tools. Kept separate from the chat runtime so study code can inject fakes.
"""

from __future__ import annotations

MODEL = "claude-opus-4-8"


async def agent_generate(prompt: str) -> str:
    """Run one tool-less generation and return the concatenated text."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        TextBlock,
        query,
    )

    options = ClaudeAgentOptions(
        model=MODEL,
        max_turns=1,
        disallowed_tools=["Bash", "Write", "Edit", "Read", "Glob", "Grep", "WebSearch"],
    )
    parts: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            parts.extend(block.text for block in message.content if isinstance(block, TextBlock))
    return "".join(parts)
