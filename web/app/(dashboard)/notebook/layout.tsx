/**
 * The Notebook's own ground (Notebook Redesign v2).
 *
 * One wrapper, deliberately: the redesign repaints the Notebook in a cool slate
 * while the rest of Argus keeps its violet-black terminal surfaces, and the
 * design's own boundary is "Argus's shell is untouched — same top bar, same six
 * modes, same cyan". Scoping the palette to a class here rather than editing the
 * `void`/`panel` tokens is what keeps /dashboard, /research, /code, /system and
 * /automations exactly as they were.
 *
 * `.notebook-skin` (globals.css) carries the ground, the default prose colour and
 * a re-pointed `--ac-bg`; a `body:has()` rule paints the viewport behind a short
 * page. No "use client" — this holds no state, and the dashboard layout above it
 * is a server component for the same reason.
 */
export default function NotebookLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <div className="notebook-skin">{children}</div>;
}
