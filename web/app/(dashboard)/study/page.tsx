import GlassCard from "@/components/GlassCard";
import PageHeader from "@/components/PageHeader";

export default function StudyPage() {
  return (
    <>
      <PageHeader
        label="STUDY"
        title="Course hub"
        subtitle="Drop slides and syllabi into a course folder — FRIDAY turns them into study guides and practice exams."
      />
      <div className="grid gap-4 md:grid-cols-2">
        <GlassCard label="COURSES" title="Your courses">
          <p className="text-sm text-ink-muted">
            Courses in <span className="font-mono text-xs">15-Courses/</span> appear here as
            cards with deadlines and weak topics (Phase P1.5).
          </p>
        </GlassCard>
        <GlassCard label="EXAMS" title="Practice exams">
          <p className="text-sm text-ink-muted">
            Generate a cited practice exam from your real course materials, then take it right
            here in quiz mode (Phase P1.5).
          </p>
        </GlassCard>
      </div>
    </>
  );
}
