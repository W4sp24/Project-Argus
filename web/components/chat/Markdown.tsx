"use client";

import dynamic from "next/dynamic";

/**
 * Markdown, rendered by react-markdown exactly as the journal does — see
 * `web/components/automations/widgets/Text.tsx:14` for the sibling boundary
 * this mirrors.
 *
 * `react-markdown`, `remark-gfm`, `rehype-highlight` and the
 * `highlight.js` language grammars it registers MAY ONLY EVER BE IMPORTED
 * INSIDE `MarkdownImpl.tsx`. A convenience import of any of them here, or
 * anywhere outside that file, silently pulls the whole markdown +
 * syntax-highlighting stack into the initial route bundle instead of the
 * lazy chunk this `next/dynamic` boundary creates. `tsc` and `next lint`
 * both stay clean when that happens — the only thing that catches it is
 * `npm run perf:budget` (`web/scripts/check-bundles.mjs`) failing the
 * route's "First Load JS" budget.
 *
 * Two layers keep pushed markup inert, and both matter, same as the
 * journal's widget: react-markdown does not render raw HTML unless
 * `rehype-raw` is added — which is deliberately not a dependency here.
 * Assistant/agent output is not a trusted source of markup.
 */
const Markdown = dynamic(() => import("./MarkdownImpl"), {
  ssr: false,
  loading: () => <p className="text-label text-ink-faint">Loading…</p>,
});

export default Markdown;
