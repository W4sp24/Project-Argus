import { EngineTrigger } from "@/components/EnginePicker";
import PopOutButton from "@/components/notebook/PopOutButton";

/**
 * "Monday, 14 September". Built by hand rather than with a locale string
 * because the design fixes the order — weekday, day, month, no year — and
 * `toLocaleDateString` picks day/month order from the locale, so en-US would
 * silently render "Monday, September 14" instead.
 */
function formatToday(now: Date = new Date()): string {
  const weekday = now.toLocaleDateString("en-US", { weekday: "long" });
  const month = now.toLocaleDateString("en-US", { month: "long" });
  return `${weekday}, ${now.getDate()} ${month}`;
}

/**
 * The Notebook's page header (Notebook Redesign v2).
 *
 * Replaces the `// SYS.NOTEBOOK :: {date}` mono status line: inside the
 * Notebook, mono is reserved for numbers and keys, and a date is a phrase.
 *
 * Carries the same engine trigger as /chat: study guides, decks and practice
 * exams are model calls too, and generating a whole exam is exactly where
 * someone wants to pick a cheaper — or a local — model deliberately.
 */
export default function NotebookStatusLine({ title }: { title: string }) {
  return (
    <header className="mb-6 flex animate-rise flex-wrap items-end gap-4">
      <div className="min-w-0 flex-1">
        <p className="mb-1.5 text-label text-nb-faint">{formatToday()}</p>
        <h1 className="min-w-0 font-body text-display font-semibold tracking-tight text-nb-ink">
          {title}
        </h1>
      </div>
      {/* Renders nothing once this window *is* the pop-out. */}
      <PopOutButton />
      <EngineTrigger />
    </header>
  );
}
