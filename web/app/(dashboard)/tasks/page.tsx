import GlassCard from "@/components/GlassCard";
import PageHeader from "@/components/PageHeader";

const BUCKETS = ["Overdue", "Today", "This week", "Someday"] as const;

export default function TasksPage() {
  return (
    <>
      <PageHeader
        label="TASKS"
        title="Task board"
        subtitle="Every task from your vault and Todoist, in one board."
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {BUCKETS.map((bucket) => (
          <GlassCard key={bucket} label={bucket.toUpperCase()} title={bucket}>
            <p className="text-sm text-ink-muted">
              Tasks appear here once the parser is online (Phase P2).
            </p>
          </GlassCard>
        ))}
      </div>
    </>
  );
}
