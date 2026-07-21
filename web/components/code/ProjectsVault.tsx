"use client";

import useSWR from "swr";
import Panel from "@/components/Panel";
import { apiFetch, useNotes, useVault, type NoteInfo } from "@/lib/api";
import { formatRelativeTime } from "@/lib/relativeTime";
import { parseProjectNote } from "@/lib/parseFrontmatter";

const MAX_PROJECTS = 12;

const STATUS_CLASS: Record<string, string> = {
  ACTIVE: "border-[var(--ac)] text-[var(--ac)]",
  PAUSED: "border-amber-400 text-amber-400",
  SHIPPED: "border-ok text-ok",
};

function normalizeStatus(raw: string | null): string {
  const upper = (raw ?? "ACTIVE").toUpperCase();
  return upper in STATUS_CLASS ? upper : "ACTIVE";
}

/** Picks the projects folder that actually has files — 40-Projects per spec, else 20-Projects. */
function selectProjectFolder(notes: NoteInfo[]): { folder: string | null; notes: NoteInfo[] } {
  const primary = notes.filter((note) => note.folder.startsWith("40-Projects"));
  if (primary.length > 0) return { folder: "40-Projects", notes: primary };
  const fallback = notes.filter((note) => note.folder.startsWith("20-Projects"));
  if (fallback.length > 0) return { folder: "20-Projects", notes: fallback };
  return { folder: null, notes: [] };
}

interface NoteContentPayload {
  content: string;
}

/** Batches the per-project `GET /api/note?path=` fetches behind one SWR key. */
function useProjectContents(paths: string[]) {
  const key = paths.length > 0 ? ["project-notes", ...paths].join("|") : null;
  return useSWR(key, async () => {
    const entries = await Promise.all(
      paths.map(async (path) => {
        const response = await apiFetch(`/api/note?path=${encodeURIComponent(path)}`);
        if (!response.ok) return [path, null] as const;
        const payload = (await response.json()) as NoteContentPayload;
        return [path, payload.content] as const;
      }),
    );
    return Object.fromEntries(entries) as Record<string, string | null>;
  });
}

function ProjectCard({ note, content, vaultName }: { note: NoteInfo; content: string | undefined; vaultName: string | undefined }) {
  const parsed = content ? parseProjectNote(content) : null;
  const status = normalizeStatus(parsed?.status ?? null);
  const uri = vaultName
    ? `obsidian://open?vault=${encodeURIComponent(vaultName)}&file=${encodeURIComponent(note.path)}`
    : undefined;
  const pct = parsed && parsed.totalCount > 0 ? Math.round((parsed.doneCount / parsed.totalCount) * 100) : 0;

  const Card = (
    <div className="flex h-full flex-col border border-line bg-panel p-4 text-left transition-colors hover:border-lineHi">
      <p className="truncate font-mono text-[10.5px] text-ink-faint">{note.path}</p>
      <div className="mt-1.5 flex items-start justify-between gap-2">
        <p className="min-w-0 flex-1 truncate text-[14px] font-medium text-ink-bright">{note.title}</p>
        <span className={`shrink-0 border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.12em] ${STATUS_CLASS[status]}`}>
          {status}
        </span>
      </div>
      {parsed?.description && (
        <p className="mt-1.5 line-clamp-2 text-[12.5px] leading-relaxed text-ink-muted">{parsed.description}</p>
      )}
      {parsed && parsed.tags.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {parsed.tags.map((tag) => (
            <span key={tag} className="font-mono text-[10px] text-ink-faint">
              #{tag}
            </span>
          ))}
        </div>
      )}
      <div className="mt-auto pt-3">
        {parsed && parsed.totalCount > 0 && (
          <>
            <div className="mb-1 flex items-center justify-between font-mono text-[10px] text-ink-faint">
              <span>
                {parsed.doneCount}/{parsed.totalCount} tasks
              </span>
              <span>{pct}%</span>
            </div>
            <div className="h-1 w-full bg-sunken">
              <div className="h-1 bg-[var(--ac)]" style={{ width: `${pct}%` }} />
            </div>
          </>
        )}
        {parsed?.nextTask && (
          <p className="mt-1.5 truncate font-mono text-[10.5px] text-[var(--ac)]">→ next: {parsed.nextTask}</p>
        )}
        <p className="mt-1.5 font-mono text-[10px] text-ink-faint">✎ {formatRelativeTime(note.modified)}</p>
      </div>
    </div>
  );

  if (!uri) return Card;
  return (
    <a href={uri} className="block h-full">
      {Card}
    </a>
  );
}

/**
 * PROJECTS.VAULT (§4 Code) — real data: `GET /api/notes` filtered to the
 * projects zone, then `GET /api/note?path=` per project (capped at
 * {@link MAX_PROJECTS}) parsed client-side for frontmatter/checkboxes (§14
 * lean-fetch note: this is runtime data, not bundle weight). Backlink counts
 * are skipped — no endpoint reports them, and faking a number would be worse
 * than omitting the chip.
 */
export default function ProjectsVault() {
  const { data: notes } = useNotes();
  const { data: vault } = useVault();
  const { folder, notes: projectNotes } = selectProjectFolder(notes ?? []);
  const capped = projectNotes.slice(0, MAX_PROJECTS);
  const { data: contents } = useProjectContents(capped.map((note) => note.path));

  return (
    <Panel
      label="PROJECTS.VAULT"
      headerRight={<span className="font-mono text-[10px] uppercase tracking-wide text-ok">OBSIDIAN MCP: WIRED</span>}
    >
      {!notes ? (
        <p className="text-sm text-ink-faint">Loading…</p>
      ) : capped.length === 0 ? (
        <p className="text-sm text-ink-muted">
          No project notes found in <span className="font-mono text-xs">40-Projects/</span> or{" "}
          <span className="font-mono text-xs">20-Projects/</span>.
        </p>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            {capped.map((note) => (
              <ProjectCard key={note.path} note={note} content={contents?.[note.path] ?? undefined} vaultName={vault?.name} />
            ))}
          </div>
          {projectNotes.length > MAX_PROJECTS && (
            <p className="mt-3 font-mono text-[10px] text-ink-faint">
              showing {MAX_PROJECTS} of {projectNotes.length} in {folder}/
            </p>
          )}
        </>
      )}
    </Panel>
  );
}
