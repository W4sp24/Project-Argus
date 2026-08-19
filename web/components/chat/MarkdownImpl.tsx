"use client";

/**
 * The real renderer, loaded only through `Markdown.tsx`'s dynamic boundary.
 *
 * `react-markdown`, `remark-gfm` and `rehype-highlight` (plus the
 * `highlight.js` language grammars below) MAY ONLY EVER BE IMPORTED HERE. A
 * convenience `import Markdown from "@/components/chat/MarkdownImpl"` — or
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
import rehypeHighlight from "rehype-highlight";

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

const components: Components = {
  pre: CodeBlock as Components["pre"],
};

function MarkdownImpl({ text, className = "text-body" }: { text: string; className?: string }) {
  return (
    <div className={`prose-chat ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, REHYPE_HIGHLIGHT_OPTIONS]]}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

// Re-renders on every batched streaming flush (a new `text` string as the
// assistant's answer grows token by token), so memoising on that one prop
// keeps re-parses down to actual content changes.
export default React.memo(MarkdownImpl);
