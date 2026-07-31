/**
 * Shared inline-citation rendering for chat surfaces (§7 global chat, §4
 * Course Hub chat). The `/ws/chat` frame protocol carries no structured
 * citations field ({delta|done|error} only — backend/features/chat/router.py),
 * so citations are parsed out of the answer text where the agent emits them
 * as `[path.md]` / `[file p.N]` / `[file slide N]` references (the format
 * backend/agent/prompts/chat.md instructs it to use).
 *
 * Pulled out of components/chat/ChatPanel.tsx so the Course Hub's real chat
 * (components/study/CourseHub.tsx) renders citations identically instead of
 * re-implementing the same regex.
 */
export function renderWithCitations(text: string, vaultName: string) {
  const parts = text.split(/(\[[^\[\]\n]+?\.(?:md|pdf|pptx|docx)(?:\s+(?:p\.|slide\s)?\d+)?\])/g);
  return parts.map((part, i) => {
    const match = part.match(/^\[([^\[\]]+?)(?:\s+(?:p\.|slide\s)?\d+)?\]$/);
    if (!match) return <span key={i}>{part}</span>;
    const path = match[1];
    return (
      <a
        key={i}
        href={`obsidian://open?vault=${encodeURIComponent(vaultName)}&file=${encodeURIComponent(path)}`}
        className="animate-msg-in mx-0.5 inline-block border border-line bg-[var(--ac-bg)] px-1.5 py-0.5 font-mono text-label text-[var(--ac)] transition-colors hover:border-lineHi"
        title={`Open ${path} in Obsidian`}
      >
        ⌗ {path.split("/").pop()}
      </a>
    );
  });
}
