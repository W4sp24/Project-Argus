import GlassCard from "@/components/GlassCard";
import PageHeader from "@/components/PageHeader";

export default function ReviewPage() {
  return (
    <>
      <PageHeader
        label="REVIEW"
        title="Approval queue"
        subtitle="Nothing touches your vault, calendar, or Todoist without your click."
      />
      <GlassCard label="QUEUE" title="No suggestions yet">
        <p className="text-sm text-ink-muted">
          When FRIDAY proposes a schedule block, task change, or note edit, it shows up here as a
          card with a diff you can approve or dismiss (Phase P3).
        </p>
      </GlassCard>
    </>
  );
}
