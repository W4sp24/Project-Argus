"use client";

import Markdown from "@/components/Markdown";

/**
 * An automation's text payload, rendered through the one shared markdown
 * boundary (`web/components/Markdown.tsx`).
 *
 * This used to reach for `react-markdown` directly, with no plugins at all —
 * so a workflow that pushed a table rendered it as a paragraph full of pipes,
 * and a fenced block came out unhighlighted. That was never a decision, just
 * the cost of a second renderer existing.
 *
 * Two layers keep pushed markup inert, and both still matter. The backend has
 * already run the body through the one allowlist sanitiser
 * (`backend/features/automations/sanitize.py`), and react-markdown does not
 * render raw HTML unless `rehype-raw` is added — which is deliberately not a
 * dependency. A workflow author is not a trusted source of markup.
 */
export default function Text({ payload }: { payload: unknown }) {
  const raw = (payload ?? {}) as { body?: unknown };
  const body = typeof raw.body === "string" ? raw.body : null;

  if (body === null) {
    return <p className="text-label text-ink-faint">This text widget&rsquo;s payload has no body.</p>;
  }
  if (!body.trim()) {
    return <p className="text-label text-ink-faint">Nothing to report.</p>;
  }

  return <Markdown text={body} />;
}
