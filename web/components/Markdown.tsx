"use client";

import dynamic from "next/dynamic";

/**
 * The one markdown boundary. Every surface that renders markdown goes through
 * here: the chat transcript, the journal note pane, and an automation's text
 * widget.
 *
 * It lived under `components/chat/` while chat was the only caller with
 * plugins. The other two reached for `react-markdown` directly with *no*
 * plugins, which was never a decision — it just meant a note containing a
 * table rendered as a paragraph full of pipes in two places out of three, and
 * that any plugin added for chat reached none of them.
 *
 * `react-markdown`, `remark-gfm`, `rehype-highlight` and the `highlight.js`
 * language grammars it registers MAY ONLY EVER BE IMPORTED INSIDE
 * `MarkdownImpl.tsx`. A convenience import of any of them here, or anywhere
 * outside that file, silently pulls the whole markdown + syntax-highlighting
 * stack into the initial route bundle instead of the lazy chunk this
 * `next/dynamic` boundary creates. `tsc` and `next lint` both stay clean when
 * that happens — the only thing that catches it is `npm run perf:budget`
 * (`web/scripts/check-bundles.mjs`) failing the route's "First Load JS"
 * budget. That matters more now that this boundary is on more routes.
 *
 * Raw HTML stays inert: react-markdown does not render it unless `rehype-raw`
 * is added, which is deliberately not a dependency. Neither an agent's answer
 * nor a workflow author's payload is a trusted source of markup.
 */
const Markdown = dynamic(() => import("./MarkdownImpl"), {
  ssr: false,
  loading: () => <p className="text-label text-ink-faint">Loading…</p>,
});

export default Markdown;
