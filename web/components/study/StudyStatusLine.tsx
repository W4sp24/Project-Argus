function formatToday(): string {
  return new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

/**
 * `// SYS.STUDY :: {date}` status line (§4). No typed greeting on Study
 * pages — Overview is already stat-tile + two-panel dense, and Flashcards /
 * Practice Exam are workspace pages where a re-typing greeting on every mode
 * switch would just be noise (§10: one typewriter interval at a time is a
 * budget, not a mandate to use one everywhere).
 */
export default function StudyStatusLine({ title }: { title: string }) {
  return (
    <header className="mb-6 animate-rise">
      <p className="eyebrow mb-2">{`// SYS.STUDY :: ${formatToday()}`}</p>
      <h1 className="font-mono text-[23px] font-semibold tracking-tight text-ink-bright">
        {title}
      </h1>
    </header>
  );
}
