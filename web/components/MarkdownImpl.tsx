"use client";

/**
 * The real renderer, loaded only through `Markdown.tsx`'s dynamic boundary.
 *
 * `react-markdown`, `remark-gfm` and `rehype-highlight` (plus the
 * `highlight.js` language grammars below) MAY ONLY EVER BE IMPORTED HERE. A
 * convenience `import Markdown from "@/components/MarkdownImpl"` — or
 * any other file reaching past the dynamic boundary straight into this
 * module — silently pulls the whole markdown + syntax-highlighting stack
 * into the initial route bundle. `tsc` and `next lint` both stay green when
 * that happens; nothing catches it except `npm run perf:budget` (see
 * `web/scripts/check-bundles.mjs`) against the route's "First Load JS"
 * figure. If you need markdown somewhere else, import `./Markdown` (the
 * `next/dynamic` wrapper), never this file.
 */

import React, { useRef, useState, type ComponentPropsWithoutRef } from "react";
import type { Element as HastElement } from "hast";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";

// KaTeX's own stylesheet, self-hosted. The CSP in `web/next.config.mjs` is
// `font-src 'self' data:` with no CDN, so this cannot be a <link> to
// jsdelivr — Next's css-loader rewrites the `url(fonts/KaTeX_*.woff2)`
// references inside it to same-origin `/_next/static/media/*`, which is what
// makes it work at all. See the CSP comment in next.config.mjs.
import "katex/dist/katex.min.css";

// rehype-highlight v7's `subset` option only narrows which registered
// languages `detect: true` is allowed to *guess between* — it does nothing
// to trim the bundle, because the default `languages` map is lowlight's
// `common` set (37 grammars) regardless of `subset`. The only way to ship
// fewer grammars (per its own readme, "Example: registering") is to pass an
// explicit `languages` map built from individual `highlight.js/lib/languages/*`
// imports, which tree-shakes down to just what's listed here. Confirmed by
// reading `node_modules/rehype-highlight/lib/index.js` and its readme.
import bash from "highlight.js/lib/languages/bash";
import python from "highlight.js/lib/languages/python";
import typescript from "highlight.js/lib/languages/typescript";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import yaml from "highlight.js/lib/languages/yaml";
import sql from "highlight.js/lib/languages/sql";
import markdown from "highlight.js/lib/languages/markdown";
import diff from "highlight.js/lib/languages/diff";

import { useToast } from "@/components/Toast";

const CURATED_LANGUAGES = {
  bash,
  python,
  typescript,
  javascript,
  json,
  yaml,
  sql,
  markdown,
  diff,
};

const REHYPE_HIGHLIGHT_OPTIONS = {
  detect: true,
  languages: CURATED_LANGUAGES,
  // `subset` still matters even with a trimmed `languages` map: without it,
  // `detect: true` would guess across all nine curated grammars (including
  // `diff` and `markdown`, which false-positive easily on ordinary prose),
  // rather than just the code-shaped ones worth guessing between.
  subset: ["bash", "python", "typescript", "javascript", "json", "yaml", "sql"],
};
// Note: rehype-highlight v7 has no `ignoreMissing` option (checked
// `node_modules/rehype-highlight/lib/index.d.ts` — its `Options` type lists
// only aliases/detect/languages/plainText/prefix/subset). It doesn't need
// one: an explicit ```lang fence naming something outside `languages` above
// already degrades gracefully — `lowlight.highlight` throws only on truly
// unregistered names, which rehype-highlight catches and turns into a
// `file.message` warning (dropped here; there's no build step consuming the
// VFile), leaving the code block emitted unhighlighted rather than crashing
// the render.

/**
 * One fenced code block, with a copy-to-clipboard button.
 *
 * This overrides `pre`, not `code`, and that distinction is the whole point.
 * By the time these components run, rehype-highlight has already replaced the
 * `code` element's single text child with a tree of `<span class="hljs-*">`
 * elements — so a `code` override that reads `String(children)` gets
 * "[object Object]" rather than source, throws away every highlight span it
 * was added to produce, and nests a second `<pre>` inside react-markdown's
 * own. Wrapping `pre` leaves the highlighted tree completely untouched.
 *
 * The copy text comes off the rendered node's `textContent` for the same
 * reason: walking the hast tree back into a string would have to re-implement
 * what the DOM already knows, and would drift the moment a plugin changes the
 * shape of that tree.
 */
function CodeBlock({
  children,
  node,
  ...rest
}: ComponentPropsWithoutRef<"pre"> & { node?: HastElement }) {
  const { show } = useToast();
  const [copied, setCopied] = useState(false);
  const preRef = useRef<HTMLPreElement>(null);

  // `pre > code.language-python` — the class rehype-highlight sets, whether
  // the fence named the language or `detect` inferred it.
  const firstChild = node?.children?.[0];
  const classes =
    firstChild && firstChild.type === "element"
      ? ((firstChild.properties?.className as string[] | undefined) ?? [])
      : [];
  const language =
    classes.map((name) => /^language-([\w-]+)$/.exec(name)?.[1]).find(Boolean) ?? null;

  // Honest-failure copy: only claim success once `writeText` actually
  // resolves (matches `web/components/system/ConnectN8nDialog.tsx:88`).
  // `navigator.clipboard` is undefined outside a secure context and can
  // reject when the document isn't focused — toasting "copied" regardless
  // would send the user off to paste stale or absent clipboard content.
  async function copy() {
    try {
      await navigator.clipboard.writeText(preRef.current?.textContent ?? "");
      setCopied(true);
      show("copied :: code block on the clipboard");
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      show("copy failed :: select the code and copy it by hand", { tone: "error" });
    }
  }

  return (
    <div className="group relative">
      {language && (
        <span className="pointer-events-none absolute left-3 top-2 font-mono text-micro uppercase tracking-[0.12em] text-ink-faint">
          {language}
        </span>
      )}
      <button
        type="button"
        aria-label={copied ? "Code copied" : "Copy code"}
        onClick={() => void copy()}
        className="absolute right-2 top-2 border border-line bg-panel px-2 py-1 font-mono text-micro uppercase tracking-[0.12em] text-ink-faint opacity-0 transition-colors hover:border-lineHi hover:text-ink focus-visible:opacity-100 group-hover:opacity-100"
      >
        {copied ? "copied" : "copy"}
      </button>
      <pre ref={preRef} {...rest}>
        {children}
      </pre>
    </div>
  );
}

/**
 * KaTeX options, every one of them load-bearing.
 *
 * `trust: false` is the security-relevant one. `next.config.mjs` states
 * outright that the chat surface renders agent output, so a prompt-injected
 * response must not be able to reach a remote origin with vault content —
 * and `\href`, `\url` and `\includegraphics` are precisely that hole. False
 * is KaTeX's default; it is set explicitly because the default changing
 * silently is not a risk worth carrying here.
 *
 * `throwOnError: false` + `strict: "ignore"`: a model writing an unsupported
 * command should cost the reader that one expression, rendered in red, not
 * the whole message. And `strict` left at its default warns to the console
 * for every Unicode character in a formula, which a maths-heavy answer emits
 * by the hundred.
 *
 * `output: "htmlAndMathml"` is the accessibility answer. KaTeX emits a
 * visual `.katex-html` tree marked `aria-hidden` itself, plus a
 * `.katex-mathml` sibling carrying real MathML for a screen reader. Dropping
 * to `"html"` would leave assistive tech reading the visual tree's
 * characters one at a time, in visual rather than semantic order.
 *
 * `maxExpand` bounds macro recursion. The contract forbids `\newcommand`,
 * but a contract is a request, and an expansion bomb in an answer would hang
 * the render on the main thread.
 */
const REHYPE_KATEX_OPTIONS = {
  trust: false,
  strict: "ignore" as const,
  throwOnError: false,
  output: "htmlAndMathml" as const,
  maxExpand: 1000,
};

const components: Components = {
  pre: CodeBlock as Components["pre"],
};

/**
 * Hide a display block that has opened but not yet closed.
 *
 * micromark's flow-math construct behaves like an unterminated code fence: an
 * unmatched `$$` consumes everything to the end of the input. Mid-stream that
 * is the entire rest of the message, which KaTeX is then asked to parse as one
 * expression — it fails, and `throwOnError: false` paints the result red.
 *
 * So from the moment `$$` arrives until its closer lands, every frame renders
 * a growing red block where the answer should be. That is the whole tail of a
 * message flashing red on *every* display equation, several times a second.
 *
 * Withholding the incomplete block instead costs the reader nothing they can
 * perceive — the equation appears when it is complete rather than assembling
 * itself in red — and skips a KaTeX parse that was going to fail anyway.
 *
 * Only for text still streaming. A settled message with an odd `$$` is a model
 * that wrote a stray delimiter, and showing that honestly is right.
 */
function hideIncompleteDisplayMath(text: string): string {
  const opener = text.lastIndexOf("$$");
  if (opener === -1) return text;
  // Count from the start: an even number of delimiters before this one means
  // this one opens a block that has not been closed.
  const before = text.slice(0, opener).split("$$").length - 1;
  return before % 2 === 0 ? text.slice(0, opener) : text;
}

function MarkdownImpl({
  text,
  className = "text-body",
  streaming = false,
}: {
  text: string;
  className?: string;
  streaming?: boolean;
}) {
  const body = streaming ? hideIncompleteDisplayMath(text) : text;
  return (
    <div className={`prose-md ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[
          [rehypeHighlight, REHYPE_HIGHLIGHT_OPTIONS],
          [rehypeKatex, REHYPE_KATEX_OPTIONS],
        ]}
        components={components}
      >
        {body}
      </ReactMarkdown>
    </div>
  );
}

// Re-renders on every batched streaming flush (a new `text` string as the
// assistant's answer grows token by token), so memoising on that one prop
// keeps re-parses down to actual content changes.
//
// Considered and rejected: splitting the message on blank lines into
// separately-memoised blocks, so a flush re-parses only the last one. It is
// the standard trick and it would bound the per-flush cost, but a blank line
// is not a safe cut in markdown — it separates the items of a loose list, sits
// inside fenced blocks, and precedes the link definitions of a reference-style
// link. Cutting there changes how a settled message renders, permanently, to
// speed up the seconds it was arriving. The parse is memoised per distinct
// string; the expensive part that was actually visible is handled above.
export default React.memo(MarkdownImpl);
