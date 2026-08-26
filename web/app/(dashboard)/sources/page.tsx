"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import PageHeader from "@/components/PageHeader";
import Panel from "@/components/Panel";
import IngestDialog from "@/components/sources/IngestDialog";
import IngestJobProgress from "@/components/sources/IngestJobProgress";
import Button from "@/components/ui/Button";
import { useIngestJob, useSources, useVault, type SourceInfo } from "@/lib/api";
import { obsidianUri } from "@/lib/citations";

function relativeTime(iso: string): string {
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 90) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** A summary Argus wrote is named for its source; pair them in the listing. */
function isSummary(source: SourceInfo): boolean {
  return source.path.endsWith(".summary.md");
}

export default function SourcesPage() {
  const [folder, setFolder] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);

  const { data, error, isLoading, mutate } = useSources(folder ?? undefined);
  const { data: vault } = useVault();
  const { data: job } = useIngestJob(jobId);

  const sources = useMemo(() => data?.sources ?? [], [data]);
  const folders = useMemo(() => {
    const counts = new Map<string, number>();
    for (const source of data?.sources ?? []) {
      counts.set(source.folder, (counts.get(source.folder) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [data]);

  const indexAvailable = data?.index_available ?? false;
  const indexed = sources.filter((source) => (source.chunks ?? 0) > 0);
  const chunks = indexed.reduce((total, source) => total + (source.chunks ?? 0), 0);
  const summaries = sources.filter(isSummary).length;

  // Refetch once a job settles: the files it wrote are new rows in this list.
  // In an effect, not during render -- `mutate` is a side effect, and clearing
  // `jobId` mid-render would drop the finished job's summary before the user
  // has seen it.
  const status = job?.status;
  useEffect(() => {
    if (status === "ok" || status === "partial" || status === "failed") void mutate();
  }, [status, mutate]);

  return (
    <>
      <PageHeader
        label="sources"
        title="Sources"
        subtitle="Everything Argus can search. Add files, choose where they land, and see exactly what happened to each one."
      />

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          { label: "files", value: sources.length },
          { label: "indexed", value: indexAvailable ? indexed.length : "—" },
          { label: "chunks", value: indexAvailable ? chunks : "—" },
          { label: "summaries", value: summaries },
        ].map((tile) => (
          <div
            key={tile.label}
            className="flex min-w-0 flex-col gap-1.5 border border-line bg-panel px-4 py-3"
          >
            <span className="font-mono text-micro uppercase tracking-[0.16em] text-ink-faint">
              {tile.label}
            </span>
            <span className="font-mono text-2xl font-semibold text-ink-bright">{tile.value}</span>
          </div>
        ))}
      </div>

      {!indexAvailable && !isLoading && sources.length > 0 && (
        <p className="mb-4 border border-line bg-panel px-4 py-3 font-mono text-meta text-warn">
          These files are in your vault, but the search index can&apos;t be read, so chunk counts
          are unknown. Check the index on{" "}
          <Link href="/system" className="underline hover:text-ink-bright">
            System
          </Link>
          .
        </p>
      )}

      {job && (
        <Panel label="INGESTING" className="mb-4">
          <IngestJobProgress job={job} />
        </Panel>
      )}

      <div className="grid gap-4 lg:grid-cols-[14rem_minmax(0,1fr)]">
        <Panel label="FOLDERS" className="h-fit">
          <ul className="flex flex-col">
            <li>
              <button
                type="button"
                onClick={() => setFolder(null)}
                className={`w-full truncate border-l-2 py-1 pl-2 text-left text-label transition-colors ${
                  folder === null
                    ? "border-[var(--ac)] text-ink-bright"
                    : "border-transparent text-ink-muted hover:text-ink"
                }`}
              >
                All folders
              </button>
            </li>
            {folders.map(([name, count]) => (
              <li key={name}>
                <button
                  type="button"
                  onClick={() => setFolder(name)}
                  className={`flex w-full items-baseline justify-between gap-2 border-l-2 py-1 pl-2 text-left transition-colors ${
                    folder === name
                      ? "border-[var(--ac)] text-ink-bright"
                      : "border-transparent text-ink-muted hover:text-ink"
                  }`}
                >
                  <span className="min-w-0 truncate text-label">{name || "vault root"}</span>
                  <span className="shrink-0 font-mono text-meta text-ink-faint">{count}</span>
                </button>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel
          label={folder ? `SOURCES · ${folder}` : "SOURCES"}
          headerRight={
            <Button variant="primary" size="sm" onClick={() => setDialogOpen(true)}>
              + Ingest
            </Button>
          }
        >
          {error ? (
            <p className="py-8 text-center text-label text-ink-muted">
              Couldn&apos;t reach Argus. Is the backend running?
            </p>
          ) : isLoading ? (
            <p className="py-8 text-center text-label text-ink-faint">Reading your vault…</p>
          ) : sources.length === 0 ? (
            <div className="py-10 text-center">
              <p className="text-label text-ink-muted">
                {folder ? "Nothing in this folder yet." : "No sources yet."}
              </p>
              <p className="mx-auto mt-1 max-w-sm font-mono text-meta text-ink-faint">
                Add a PDF, slide deck or note and Argus can answer from it — with a citation back to
                the page it came from.
              </p>
              <Button variant="primary" size="md" className="mt-4" onClick={() => setDialogOpen(true)}>
                + Ingest
              </Button>
            </div>
          ) : (
            <ul className="flex flex-col divide-y divide-line">
              {sources.map((source) => (
                <li key={source.path} className="flex items-baseline gap-3 py-2">
                  <span className="shrink-0 border border-line px-1 py-px font-mono text-micro text-ink-faint">
                    {source.kind}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-label text-ink">
                      {source.title}
                      {isSummary(source) && (
                        <span className="ml-2 font-mono text-micro uppercase tracking-[0.16em] text-[var(--ac)]">
                          summary
                        </span>
                      )}
                    </p>
                    <p className="mt-0.5 truncate font-mono text-meta text-ink-faint">
                      {source.folder || "vault root"} · {relativeTime(source.modified)} ·{" "}
                      {fileSize(source.size)}
                      {source.chunks !== null && ` · ${source.chunks} chunks`}
                      {source.chunks === null && indexAvailable && " · not indexed"}
                    </p>
                  </div>
                  {vault && (
                    <a
                      href={obsidianUri(vault.path, source.path)}
                      className="shrink-0 font-mono text-meta text-ink-faint transition-colors hover:text-[var(--ac)]"
                    >
                      open ↗
                    </a>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      {dialogOpen && (
        <IngestDialog
          onClose={() => setDialogOpen(false)}
          onStarted={setJobId}
          initialTarget={folder ?? undefined}
        />
      )}
    </>
  );
}
