import { EngineTrigger } from "@/components/EnginePicker";
import PopOutButton from "@/components/notebook/PopOutButton";

function formatToday(): string {
  return new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

/**
 * `// SYS.NOTEBOOK :: {date}` status line (§4). No typed greeting on Notebook
 * pages — Overview is already stat-tile + two-panel dense, and Flashcards /
 * Practice Exam are workspace pages where a re-typing greeting on every mode
 * switch would just be noise (§10: one typewriter interval at a time is a
 * budget, not a mandate to use one everywhere).
 *
 * Carries the same engine trigger as /chat (§7): study guides and practice
 * exams are model calls too, and generating a whole exam is exactly where
 * someone wants to pick a cheaper — or a local — model deliberately.
 */
export default function NotebookStatusLine({ title }: { title: string }) {
  return (
    <header className="mb-6 animate-rise">
      <p className="eyebrow mb-2">{`// SYS.NOTEBOOK :: ${formatToday()}`}</p>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="min-w-0 flex-1 font-mono text-display font-semibold tracking-tight text-ink-bright">
          {title}
        </h1>
        {/* Renders nothing once this window *is* the pop-out. */}
        <PopOutButton />
        <EngineTrigger />
      </div>
    </header>
  );
}
