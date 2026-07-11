import GlassCard from "@/components/GlassCard";
import PageHeader from "@/components/PageHeader";

export default function ChatPage() {
  return (
    <>
      <PageHeader
        label="CHAT"
        title="Ask your second brain"
        subtitle="Every answer cites the note it came from — click a citation to open it in Obsidian."
      />
      <GlassCard label="OFFLINE" title="FRIDAY isn't listening yet">
        <p className="mb-4 text-sm text-ink-muted">
          Chat comes online in Phase P1, once your vault is indexed. You&apos;ll be able to ask
          things like:
        </p>
        <ul className="space-y-2">
          {[
            "What did I write about pointers last week?",
            "Summarize my CS201 lecture notes since the midterm.",
            "When did I last talk to Mom about the trip?",
          ].map((example) => (
            <li
              key={example}
              className="rounded-xl border border-white/5 bg-white/[0.03] px-4 py-2.5 text-sm text-ink-muted"
            >
              {example}
            </li>
          ))}
        </ul>
      </GlassCard>
    </>
  );
}
