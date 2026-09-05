"use client";

/** Small accent circle — the assistant's avatar (§7).
 *
 * Its own file because both the transcript's empty state (`ChatPanel`) and
 * every assistant turn (`MessageRow`) draw one, and `MessageRow` is memoised —
 * importing it from there would tie the empty state to the memo boundary.
 */
export default function Orb({ size = "h-6 w-6" }: { size?: string }) {
  return (
    <span
      aria-hidden
      className={`flex shrink-0 items-center justify-center rounded-full border border-[var(--ac)] ${size}`}
    >
      <span className="h-[35%] w-[35%] rounded-full bg-[var(--ac)]" />
    </span>
  );
}
