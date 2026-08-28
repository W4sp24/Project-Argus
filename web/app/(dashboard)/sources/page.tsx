"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import PageHeader from "@/components/PageHeader";
import { useToast } from "@/components/Toast";
import Panel from "@/components/Panel";
import DeleteSourcesDialog from "@/components/sources/DeleteSourcesDialog";
import IngestDialog from "@/components/sources/IngestDialog";
import IngestJobProgress, { jobPanelLabel } from "@/components/sources/IngestJobProgress";
import Button from "@/components/ui/Button";
import { FIELD_CONTROL } from "@/components/ui/Field";
import {
  ApiError,
  latestJobOfKind,
  reindexVault,
  useIngestJob,
  useIngestJobs,
  useSources,
  useVault,
  type SourceInfo,
} from "@/lib/api";
import { obsidianUri } from "@/lib/citations";
import { formatRelativeTime } from "@/lib/relativeTime";

function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

type SortKey = "modified" | "name" | "size" | "chunks";

/** Newest-first by default, matching what the API already returns. */
const SORTS: Record<SortKey, (a: SourceInfo, b: SourceInfo) => number> = {
  modified: (a, b) => b.modified.localeCompare(a.modified),
  name: (a, b) => a.title.localeCompare(b.title),
  size: (a, b) => b.size - a.size,
  // Unindexed files sort last rather than as zero: "not in the index" and
  // "in the index with nothing in it" are different answers, and `chunks` is
  // null for the first precisely so they stay distinguishable.
  chunks: (a, b) => (b.chunks ?? -1) - (a.chunks ?? -1),
};

function SourcesBrowser() {
  const router = useRouter();
  const params = useSearchParams();
  // The view lives in the URL, so "sources in 15-Courses/CS201/materials" can
  // be bookmarked and shared, and Back undoes a folder click instead of
  // leaving the page entirely.
  const folder = params.get("folder");
  const setFolder = useCallback(
    (next: string | null) => {
      const query = new URLSearchParams(params.toString());
      if (next === null) query.delete("folder");
      else query.set("folder", next);
      const search = query.toString();
      // `push`, not `replace`: the audit's complaint was that Back left the
      // page instead of undoing a folder click, and only a history entry
      // fixes that.
      router.push(search ? `/sources?${search}` : "/sources", { scroll: false });
    },
    [params, router],
  );

  const { show } = useToast();
  const [dialogOpen, setDialogOpen] = useState(false);
  // Paths, not indices: the list re-sorts and re-filters underneath a
  // selection, and an index-based one would silently come to mean different
  // rows. Anything no longer on screen is dropped when the delete runs.
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState<SourceInfo[] | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  // Fetched unfiltered, always, and narrowed here. The rail used to be derived
  // from the *filtered* response, so clicking a folder deleted every sibling
  // from the navigation that got you there -- the only way back was "All
  // folders". A navigation control that removes its own siblings when used is
  // a trap, not a filter.
  const { data, error, isLoading, mutate } = useSources();
  const { data: vault } = useVault();
  const { data: job } = useIngestJob(jobId);
  // Adopt a job that is still running from an earlier visit. `jobId` is
  // component state, so leaving the page and coming back lost the readout
  // entirely -- the job carried on, the UI had simply forgotten it, and the
  // only evidence it existed was the files appearing later. `useIngestJobs`
  // has been built and called from nowhere since this route landed; this is
  // what it is for.
  const { data: history } = useIngestJobs();
  useEffect(() => {
    if (jobId) return;
    const running = (history?.jobs ?? []).find(
      (candidate) => candidate.status === "queued" || candidate.status === "running",
    );
    if (running) setJobId(running.id);
  }, [history, jobId]);

  const all = useMemo(() => data?.sources ?? [], [data]);
  // Prefix, matching what `GET /api/sources?folder=` does server-side. The
  // rail counted by *exact* `source.folder` while the API filtered by
  // subtree, so a folder with children could never agree with itself: the
  // rail said 3 and the click produced 18.
  const inFolder = useCallback(
    (path: string) => folder === null || path === folder || path.startsWith(`${folder}/`),
    [folder],
  );
  // Search and sort, which this page has never had. The Course Hub rail has a
  // filter; /sources -- whose entire job is browsing the corpus -- had none,
  // so past a certain count it stopped being a list and became a scroll. The
  // missing search hurts at 60 files; virtualisation not until several
  // hundred, so it is deliberately not here yet.
  const query = (params.get("q") ?? "").trim().toLowerCase();
  const sort = (params.get("sort") ?? "modified") as SortKey;
  const dense = params.get("dense") === "1";
  const setParam = useCallback(
    (key: string, value: string | null) => {
      const next = new URLSearchParams(params.toString());
      if (value === null || value === "") next.delete(key);
      else next.set(key, value);
      const search = next.toString();
      router.replace(search ? `/sources?${search}` : "/sources", { scroll: false });
    },
    [params, router],
  );

  const sources = useMemo(() => {
    const scopedRows = folder === null ? all : all.filter((source) => inFolder(source.folder));
    const matched = query
      ? scopedRows.filter(
          (source) =>
            source.title.toLowerCase().includes(query) ||
            source.path.toLowerCase().includes(query),
        )
      : scopedRows;
    // Copied before sorting: `all` is SWR's cached array and sorting in place
    // would mutate what every other reader of this key sees.
    return [...matched].sort(SORTS[sort] ?? SORTS.modified);
  }, [all, folder, inFolder, query, sort]);
  const folders = useMemo(() => {
    const counts = new Map<string, number>();
    for (const source of all) {
      // Every ancestor counts the file, so a folder's number is what clicking
      // it will actually show.
      const parts = source.folder ? source.folder.split("/") : [""];
      for (let depth = 1; depth <= parts.length; depth += 1) {
        const name = parts.slice(0, depth).join("/");
        counts.set(name, (counts.get(name) ?? 0) + 1);
      }
    }
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [all]);

  const indexAvailable = data?.index_available ?? false;
  const indexed = sources.filter((source) => (source.chunks ?? 0) > 0);
  const chunks = indexed.reduce((total, source) => total + (source.chunks ?? 0), 0);
  const written = sources.filter((source) => source.generated !== null).length;
  const scoped = folder !== null && sources.length !== all.length;
  const unindexed = sources.filter((source) => source.chunks === null);
  const selected = sources.filter((source) => picked.has(source.path));

  function togglePick(path: string) {
    setPicked((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  function afterDelete(summary: { files: number; notes: number; chunks: number }) {
    setDeleting(null);
    setPicked(new Set());
    void mutate();
    const parts = [`${summary.files} file${summary.files === 1 ? "" : "s"}`];
    if (summary.notes) parts.push(`${summary.notes} generated note${summary.notes === 1 ? "" : "s"}`);
    parts.push(`${summary.chunks} chunk${summary.chunks === 1 ? "" : "s"}`);
    // Says what actually went, from the server's own counts -- a delete that
    // reports more than it did is worse than one that reports nothing.
    show(`deleted :: ${parts.join(" · ")}`);
  }
  const [indexing, setIndexing] = useState(false);

  /** Re-embed exactly the files this page is reporting as missing, rather
   * than sending the user to /system to rebuild the whole vault. */
  async function indexMissing() {
    setIndexing(true);
    try {
      await reindexVault(unindexed.map((source) => source.path));
      // The trigger answers IndexStatus, not a job id, so the readout finds
      // its own job -- which then renders through exactly the same segmented
      // progress component an ingest uses.
      const job = await latestJobOfKind("reindex");
      if (job) setJobId(job.id);
    } catch (indexError) {
      const conflict = indexError instanceof ApiError && indexError.status === 409;
      show(
        conflict
          ? "index :: an ingest is using the index — try again when it finishes"
          : `index :: failed — ${indexError instanceof Error ? indexError.message : "backend offline?"}`,
        { tone: "error" },
      );
    } finally {
      setIndexing(false);
    }
  }

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
          // Labelled with the scope when one is active. These are computed
          // over the filtered set, so CHUNKS read 6 unfiltered and 2 filtered
          // with the label unchanged -- which a user reads as the index
          // shrinking rather than as the view narrowing.
          { label: scoped ? `files (of ${all.length})` : "files", value: sources.length },
          { label: "indexed", value: indexAvailable ? indexed.length : "—" },
          { label: "chunks", value: indexAvailable ? chunks : "—" },
          // "written", not "summaries": the tile counts everything Argus
          // produced, and course notes are the larger half of that.
          { label: "written", value: indexAvailable ? written : "—" },
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
        <Panel label={jobPanelLabel(job)} className="mb-4">
          <IngestJobProgress job={job} onDismiss={() => setJobId(null)} />
        </Panel>
      )}

      {/* The page's subtitle is "Everything Argus can search", and it used to
          say "not indexed" on sixteen of eighteen rows while offering no way
          to act on it. The only link to /system was behind the index being
          *unreadable* -- the far more common state, index fine but these
          particular files missing from it, had no affordance at all. That is
          the finding most likely to produce the original complaint that the
          feature "did not feel worth using": nothing was broken, it was
          unfinished at the seam. */}
      {indexAvailable && unindexed.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border border-line bg-panel px-4 py-3">
          <p className="font-mono text-meta text-warn">
            {unindexed.length} file{unindexed.length === 1 ? "" : "s"}{" "}
            {unindexed.length === 1 ? "isn't" : "aren't"} in the search index yet
            {scoped ? " in this folder" : ""} — Argus cannot answer from{" "}
            {unindexed.length === 1 ? "it" : "them"}.
          </p>
          <Button size="sm" variant="primary" onClick={indexMissing} disabled={indexing}>
            {indexing ? "Indexing…" : "Index them"}
          </Button>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[14rem_minmax(0,1fr)]">
        <Panel label="FOLDERS" className="h-fit">
          <ul className="flex flex-col">
            <li>
              <button
                type="button"
                onClick={() => setFolder(null)}
                aria-current={folder === null ? "true" : undefined}
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
                {/* Without `aria-label` the name and the count concatenate, so
                    "15-Courses/CS000" with one file is announced as
                    "CS zero zero zero one" — the count reads as the last digit
                    of the folder. `aria-current` carries the selection, which
                    the styling otherwise conveys with colour and a left border
                    alone. */}
                <button
                  type="button"
                  onClick={() => setFolder(name)}
                  aria-label={`${name || "vault root"}, ${count} ${count === 1 ? "file" : "files"}`}
                  aria-current={folder === name ? "true" : undefined}
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
            <div className="flex items-center gap-2">
              {selected.length > 0 && (
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => setDeleting(selected)}
                >
                  Delete {selected.length}
                </Button>
              )}
              <Button variant="primary" size="sm" onClick={() => setDialogOpen(true)}>
                + Ingest
              </Button>
            </div>
          }
        >
          {all.length > 0 && (
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <input
                type="search"
                defaultValue={query}
                onChange={(event) => setParam("q", event.target.value)}
                placeholder="search this corpus"
                aria-label="Search sources"
                className={`${FIELD_CONTROL} h-7 min-w-0 flex-1 py-0 text-meta`}
              />
              <label className="flex items-center gap-1.5 font-mono text-meta text-ink-muted">
                {/* The one surface that will hold hundreds of rows, so the
                    density toggle earns its place here and nowhere else. One
                    class swap, and it buys back the scroll cost of a large
                    vault -- dense dark interfaces being rather the point of
                    this aesthetic. */}
                <button
                  type="button"
                  onClick={() => setParam("dense", dense ? null : "1")}
                  aria-pressed={dense}
                  className="border border-line px-1.5 py-0.5 uppercase tracking-[0.12em] transition-colors hover:border-lineHi hover:text-ink"
                >
                  {dense ? "compact" : "comfortable"}
                </button>
                sort
                <select
                  value={sort}
                  onChange={(event) => setParam("sort", event.target.value)}
                  aria-label="Sort sources"
                  className={`${FIELD_CONTROL} h-7 py-0 text-meta`}
                >
                  <option value="modified">modified</option>
                  <option value="name">name</option>
                  <option value="size">size</option>
                  <option value="chunks">chunks</option>
                </select>
              </label>
            </div>
          )}
          {error ? (
            <p className="py-8 text-center text-label text-ink-muted">
              Couldn&apos;t reach Argus. Is the backend running?
            </p>
          ) : isLoading ? (
            <p className="py-8 text-center text-label text-ink-faint">Reading your vault…</p>
          ) : sources.length === 0 && query ? (
            // A search that matches nothing is not an empty vault, and
            // offering "+ Ingest" for it would be answering a question the
            // user did not ask.
            <p className="py-10 text-center text-label text-ink-muted">
              Nothing matches &ldquo;{query}&rdquo;
              {folder ? " in this folder" : ""}.
            </p>
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
                <li
                  key={source.path}
                  className={`flex items-baseline gap-3 ${dense ? "py-1" : "py-2"} ${
                    // Argus's own output gets a visual class of its own. It was
                    // indistinguishable from input in every list, which is the
                    // visual half of "what did Argus actually do for me".
                    source.generated !== null ? "border-l-2 border-[var(--ac)] pl-2" : ""
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={picked.has(source.path)}
                    onChange={() => togglePick(source.path)}
                    aria-label={`Select ${source.title}`}
                    className="shrink-0 accent-[var(--ac)]"
                  />
                  <span className="shrink-0 border border-line px-1 py-px font-mono text-micro text-ink-faint">
                    {source.kind}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-label text-ink">
                      {source.title}
                      {source.generated !== null && (
                        <span className="ml-2 font-mono text-micro uppercase tracking-[0.16em] text-[var(--ac)]">
                          {source.generated}
                        </span>
                      )}
                    </p>
                    <p className="mt-0.5 truncate font-mono text-meta text-ink-faint">
                      {source.folder || "vault root"} · {formatRelativeTime(source.modified)} ·{" "}
                      {fileSize(source.size)}
                      {source.chunks !== null &&
                        ` · ${source.chunks} chunk${source.chunks === 1 ? "" : "s"}`}
                      {source.chunks === null && indexAvailable && " · not indexed"}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setDeleting([source])}
                    aria-label={`Delete ${source.title}`}
                    className="shrink-0 font-mono text-meta text-ink-muted transition-colors hover:text-danger"
                  >
                    delete
                  </button>
                  {vault && (
                    /* Every row renders the same "open ↗", so a screen reader
                       listing the page's links gets N identical entries with
                       nothing to choose between them. The visible text stays
                       short; `aria-label` says which file it opens. */
                    <a
                      href={obsidianUri(vault.path, source.path)}
                      aria-label={`Open ${source.title} in Obsidian`}
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

      {deleting && (
        <DeleteSourcesDialog
          sources={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={afterDelete}
        />
      )}

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

/**
 * `useSearchParams` suspends, and Next requires the boundary to be an
 * ancestor rather than the component itself -- without it the whole route
 * silently opts out of static rendering at build time.
 */
export default function SourcesPage() {
  return (
    <Suspense fallback={null}>
      <SourcesBrowser />
    </Suspense>
  );
}
