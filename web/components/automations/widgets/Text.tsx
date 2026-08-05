"use client";

import dynamic from "next/dynamic";

/**
 * Markdown, rendered by react-markdown exactly as the journal does.
 *
 * Two layers keep pushed markup inert, and both matter. The backend has
 * already run the body through the one allowlist sanitiser
 * (`backend/features/automations/sanitize.py`), and react-markdown does not
 * render raw HTML unless `rehype-raw` is added — which is deliberately not a
 * dependency here. A workflow author is not a trusted source of markup.
 */
const ReactMarkdown = dynamic(() => import("react-markdown"), {
  ssr: false,
  loading: () => <p className="text-label text-ink-faint">Loading…</p>,
});

export default function Text({ payload }: { payload: unknown }) {
  const raw = (payload ?? {}) as { body?: unknown };
  const body = typeof raw.body === "string" ? raw.body : null;

  if (body === null) {
    return <p className="text-label text-ink-faint">This text widget&rsquo;s payload has no body.</p>;
  }
  if (!body.trim()) {
    return <p className="text-label text-ink-faint">Nothing to report.</p>;
  }

  return (
    <div className="prose-journal text-body">
      <ReactMarkdown>{body}</ReactMarkdown>
    </div>
  );
}
