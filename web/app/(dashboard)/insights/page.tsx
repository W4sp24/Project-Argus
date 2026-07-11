import GlassCard from "@/components/GlassCard";
import PageHeader from "@/components/PageHeader";

export default function InsightsPage() {
  return (
    <>
      <PageHeader
        label="INSIGHTS"
        title="How you're doing"
        subtitle="Trends from your tasks, calendar, and study sessions."
      />
      <div className="grid gap-4 md:grid-cols-2">
        <GlassCard label="TASKS" title="Completion trend">
          <p className="text-sm text-ink-muted">
            A 14-day completion trend renders here once there&apos;s task history (Phase P4).
          </p>
        </GlassCard>
        <GlassCard label="STUDY" title="Study streak">
          <p className="text-sm text-ink-muted">
            Practice-exam scores and study streaks per course land here (Phase P4).
          </p>
        </GlassCard>
      </div>
    </>
  );
}
