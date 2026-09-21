"use client";

import { useMemo } from "react";
import useSWR from "swr";
import Markdown from "@/components/Markdown";
import NotebookPanel from "@/components/notebook/NotebookPanel";
import { fetcher, useVault, type NoteContent } from "@/lib/api";
import { obsidianUri } from "@/lib/citations";

/** Frontmatter, and the body without it.
 *
 * `js-yaml` is not a dependency and this does not need one: the header is
 * read for display — a doc type, a coverage figure, a list of topics — and a
 * line-oriented read of `key: value` plus `- item` covers everything Argus
 * itself writes. Anything it cannot parse simply does not render, which is
 * the right failure for a header that is decoration here and data in
 * Obsidian.
 */
function split(raw: string): { meta: Record<string, string[]>; body: string } {
  if (!raw.startsWith("---")) return { meta: {}, body: raw };
  const end = raw.indexOf("\n---", 3);
  if (end === -1) return { meta: {}, body: raw };

  const meta: Record<string, string[]> = {};
  let key = "";
  for (const line of raw.slice(4, end).split("\n")) {
    const item = /^\s*-\s+(.*)$/.exec(line);
    if (item && key) {
      meta[key] = [...(meta[key] ?? []), item[1].replace(/^['"]|['"]$/g, "")];
      continue;
    }
    const pair = /^([A-Za-z_][\w-]*):\s*(.*)$/.exec(line);
    if (!pair) continue;
    key = pair[1];
    const value = pair[2].trim().replace(/^['"]|['"]$/g, "");
    meta[key] = value ? [value] : [];
  }
  return { meta, body: raw.slice(end + 4).replace(/^\n/, "") };
}

/** The `<!-- argus:relations --> … ` fence, which the Related rail renders. */
const RELATIONS = /<!-- argus:relations:start -->[\s\S]*?<!-- argus:relations:end -->/;

/**
 * Read a generated note without leaving Argus.
 *
 * There was no in-app reader at all: `CourseHub` linked a guide to an
 * `obsidian://` URI because that was the only honest destination, and a note
 * the app had just written could not be read in the app that wrote it. The
 * Obsidian link stays — the file is real and that is where you annotate it —
 * but checking what came out should not require an app switch.
 *
 * Rendering goes through `components/Markdown`, which is the one boundary:
 * KaTeX for the maths these notes are now full of, `remark-gfm` for the
 * concept tables, highlighting for code. The Related fence is stripped
 * because the links in it are `[[wikilinks]]`, which mean nothing here.
 */
export default function NoteReader({ path }: { path: string }) {
  // `obsidianUri` needs the vault root: an obsidian:// link addresses an
  // absolute path, not a vault-relative one.
  const { data: vault } = useVault();
  const { data, error, isLoading } = useSWR<NoteContent>(
    `/api/note?path=${encodeURIComponent(path)}`,
    fetcher,
  );

  const { meta, body } = useMemo(() => split(data?.content ?? ""), [data?.content]);
  const name = path.split("/").pop() ?? path;
  const title = meta.title?.[0] ?? name.replace(/\.md$/, "");

  if (error) {
    return (
      <NotebookPanel heading="Note">
        <p className="text-body text-danger">
          Could not open {name}: {error instanceof Error ? error.message : "unknown error"}
        </p>
      </NotebookPanel>
    );
  }

  return (
    <NotebookPanel
      heading={title}
      subheading={path}
      headerRight={
        <a
          href={obsidianUri(vault?.path ?? "", path)}
          className="rounded-ctl border border-nb-line px-3 py-1.5 text-label text-nb-body transition-colors hover:border-nb-lineHi hover:text-nb-ink"
        >
          Open in Obsidian
        </a>
      }
    >
      {isLoading ? (
        <p className="text-body text-nb-faint">Opening…</p>
      ) : (
        <>
          {/* What the generator knew about its own coverage. A note written
              from a third of a deck has to be able to say so, and this is
              where a reader would look. */}
          <dl className="mb-5 flex flex-wrap gap-x-6 gap-y-1.5 text-label text-nb-faint">
            {(["doc_type", "coverage", "equations", "scope", "course"] as const).map((field) =>
              meta[field]?.[0] ? (
                <div key={field} className="flex gap-1.5">
                  <dt>{field.replace("_", " ")}</dt>
                  <dd className="text-nb-body">{meta[field][0]}</dd>
                </div>
              ) : null,
            )}
          </dl>
          <article className="max-w-none">
            <Markdown text={body.replace(RELATIONS, "").trimEnd()} />
          </article>
          {meta.topics?.length ? (
            <p className="mt-6 border-t border-nb-line pt-4 text-label text-nb-faint">
              Topics: <span className="text-nb-body">{meta.topics.join(" · ")}</span>
            </p>
          ) : null}
        </>
      )}
    </NotebookPanel>
  );
}
