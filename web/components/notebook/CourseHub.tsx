"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import NotebookPanel from "@/components/notebook/NotebookPanel";
import { useToast } from "@/components/Toast";
import ChatPanel from "@/components/chat/ChatPanel";
import {
  mutateJSON,
  useCourseSources,
  useFlashcardDecks,
  useStudyExams,
  useVault,
} from "@/lib/api";
import { ChatProvider, useChatActions, useChatMessages, useChatMeta } from "@/lib/chat";
import { obsidianUri } from "@/lib/citations";
import GenerateDialog, { type GenerateKind } from "@/components/notebook/GenerateDialog";
import { useCourseSelection } from "@/lib/courseSelection";
import { useJobs } from "@/lib/jobs";
import { selectedModel, useSelectedModel } from "@/lib/models";
import { useWeakTopics } from "@/lib/useStudySignals";

const SUGGESTIONS = [
  "Summarize this week's material",
  "What's likely to be on the exam?",
  "Explain the hardest concept so far",
];

/**
 * "New conversation", and a note saying which one you are in.
 *
 * Must live *inside* the ChatProvider, because that is where the thread is.
 * Without it a hub that now resumes yesterday's conversation would have no
 * way to deliberately start a fresh one — and a resumed transcript with no
 * label looks like the old bug in reverse, where a course's whole history
 * appears unasked.
 */
function CourseThreadBar() {
  const { threadTitle, busy } = useChatMeta();
  const { newThread } = useChatActions();
  const messages = useChatMessages();
  if (messages.length === 0) return null;
  return (
    <div className="mb-2 flex items-center justify-between gap-2">
      <p className="truncate text-label text-nb-faint">{threadTitle || "this conversation"}</p>
      <button
        type="button"
        onClick={newThread}
        disabled={busy}
        className="shrink-0 rounded-ctl px-2 py-1 text-label text-nb-faint hover:text-nb-ink disabled:opacity-40"
      >
        New conversation
      </button>
    </div>
  );
}

/**
 * Course Hub center pane — the shared chat surface, scoped to one course.
 *
 * This used to be a second, near-complete copy of `lib/chat.tsx`'s WebSocket
 * client: its own frame handling, its own `ChatMessage` type saying
 * "assistant" where the original said "argus", its own error path popping two
 * pending bubbles where the original popped one. Every protocol change needed
 * two edits, and the two had already drifted apart.
 *
 * A nested `ChatProvider` shadows the app-wide one from the dashboard layout,
 * so the course still keeps its own thread — "separate from the global chat",
 * as the original spec asked — while there is now one implementation of the
 * protocol instead of two, and every later chat fix lands on both surfaces at
 * once.
 *
 * `course` rides on every outbound frame, where `build_vault_tools`
 * (backend/agent/runtime.py) turns it into a *forced* `search_vault` filter
 * rather than leaving the scope to the model's discretion — the whole point
 * of asking from inside a course's hub. The SOURCES rail's ticks ride along
 * beside it and narrow that filter further, so "what does lecture 3 say"
 * asked with only lecture 3 selected reads only lecture 3.
 *
 * The line under the input states the scope. A user who has narrowed to two
 * files and forgotten needs to be able to see why the answer looks thin,
 * without opening the rail to count checkboxes.
 */

export function CourseChat({ code }: { code: string }) {
  const model = useSelectedModel();
  const { paths, available } = useCourseSelection();
  const scoped = paths.length < available.length;
  return (
    <ChatProvider course={code} sources={paths}>
      <NotebookPanel heading="Ask this course" scale="body" className="flex h-full flex-col">
        <CourseThreadBar />
        <ChatPanel
          variant="dock"
          suggestions={SUGGESTIONS}
          placeholder={
            paths.length === 0
              ? "Ask about this course — no sources selected"
              : scoped
                ? `Ask about the ${paths.length} file${paths.length === 1 ? "" : "s"} you have selected`
                : "Ask about this course's materials and notes"
          }
        />
        {/* Neither "reading N of M files" nor the word "sources": the first is
            the Sources rail's own status line word for word, and the second is
            its heading. Two panels on one screen saying the same sentence about
            different things is confusing to read and ambiguous to address. */}
        <p className="mt-2.5 text-label text-nb-faint">
          {model} · grounded in {paths.length} of {available.length} files
        </p>
      </NotebookPanel>
    </ChatProvider>
  );
}

/**
 * One STUDIO button, with an honest running state.
 *
 * These used to change their own label to "writing…" and then go silent for
 * however long a provider takes — minutes, for a guide over a whole course.
 * The blinking bar is the same idiom `IngestJobProgress` uses, and for the
 * same reason: it moves only while something is actually running, so a stall
 * looks like a stall rather than like progress.
 */
function StudioAction({
  label,
  running,
  runningLabel,
  disabled,
  onClick,
  note,
}: {
  label: string;
  running: boolean;
  runningLabel: string;
  disabled: boolean;
  onClick: () => void;
  note?: string;
}) {
  return (
    <div>
      <button
        onClick={onClick}
        disabled={disabled}
        aria-busy={running}
        className={`w-full rounded-ctl border px-3.5 py-2.5 text-left text-ctl transition-colors disabled:cursor-not-allowed disabled:opacity-70 ${
          running
            ? "border-[var(--ac)] bg-nb-acBg text-[var(--ac)]"
            : "border-nb-line bg-nb-raised text-nb-ink hover:border-nb-lineHi"
        }`}
      >
        {running ? `${runningLabel}…` : label}
      </button>
      {running && (
        <div className="mt-1.5 h-[3px] w-full overflow-hidden rounded-bar bg-nb-track" aria-hidden>
          <span className="block h-full w-1/3 animate-blink bg-[var(--ac)]" />
        </div>
      )}
      {note && !running && <p className="mt-1 text-label text-nb-faint">{note}</p>}
    </div>
  );
}

/** An in-app route goes through `next/link`; an `obsidian://` target cannot. */
function LinkOrAnchor({
  href,
  external,
  className,
  children,
}: {
  href: string;
  external?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  if (external) {
    return (
      <a href={href} className={className}>
        {children}
      </a>
    );
  }
  return (
    <Link href={href} className={className}>
      {children}
    </Link>
  );
}

/** How many rows each STUDIO list shows before it offers the rest.
 *
 * These used to be bare `.slice(0, 8)` calls with no count and no way past
 * them, so after one semester a course's artifacts simply disappeared from
 * the only list that names them -- silently, which is the part that matters:
 * the list looked complete. */
const CAP = 8;

interface GeneratedItem {
  key: string;
  label: string;
  date: string;
  /** No DECK: decks have their own panel. They were drowning here -- mixed with
   * guides and exams under one heading capped at eight rows, so a course's
   * decks fell off the end of the only list that named them -- and every deck
   * row pointed at `/notebook/flashcards?deck=<id>`, a parameter nothing in the
   * app has ever read. */
  kind: "GUIDE" | "EXAM";
  href?: string;
  /** An obsidian:// target, which `next/link` must not try to route. */
  external?: boolean;
}

/**
 * Course Hub right rail STUDIO (§4 Course Hub) — generation actions now hit
 * the real endpoints `CoursesPanel` already uses (`/api/study/guide`,
 * `/api/study/exam`, `/api/flashcards/decks`), instead of every button just
 * toasting `generation :: preview`. "Generated" lists real artifacts:
 * exams (`GET /api/study/exams?course=`) and study guides — the latter read
 * off `GET /api/study/courses/<code>/sources` (the `study` zone), filtered to
 * `guide-*` files so exam markdown (already covered by the exams list) isn't
 * double-counted. Decks were listed here too and now have their own
 * `CourseDecksPanel`: a deck is the one artifact you come back to daily, and a
 * due count belongs somewhere it cannot be pushed off the end of a shared list.
 */
export function CourseStudio({ code }: { code: string }) {
  const { show } = useToast();
  const { data: exams, mutate: refreshExams } = useStudyExams(code);
  // Read only to refresh: decks have their own panel now, so this holds the
  // shared SWR key open and re-fetches it when this course's work lands.
  const { mutate: refreshDecks } = useFlashcardDecks(code);
  const { data: sources, mutate: refreshSources } = useCourseSources(code);
  const { data: vault } = useVault();
  const { paths, available, refresh: refreshSelection } = useCourseSelection();
  const { jobs, track, isBusy } = useJobs();
  const [generating, setGenerating] = useState<GenerateKind | null>(null);
  const [showAllGenerated, setShowAllGenerated] = useState(false);
  const [showAllTopics, setShowAllTopics] = useState(false);
  const scoped = paths.length < available.length;
  // A guide or an exam built from nothing is not a request worth sending —
  // the backend refuses it, and disabling the button says so a round trip
  // earlier.
  const nothingSelected = paths.length === 0 && available.length > 0;

  const guides = (sources ?? []).filter(
    (source) => source.zone === "study" && /^guide-/.test(source.path.split("/").pop() ?? ""),
  );

  const generated: GeneratedItem[] = [
    ...(exams ?? []).map((exam) => ({
      key: `exam-${exam.id}`,
      label: exam.title,
      date: exam.created_at,
      kind: "EXAM" as const,
      // Carries its id. Every EXAM row used to point at the bare route, so
      // clicking "EXAM · Midterm review" opened whatever exam the page
      // happened to load rather than that one.
      href: `/notebook/exam?id=${exam.id}`,
    })),
    // A guide that took minutes to write used to render as unclickable text,
    // with its path announced only in a toast that had since auto-dismissed.
    // It is a real file in the vault, so the obsidian link is the honest
    // destination -- there is no in-app reader for it.
    ...guides.map((guide) => ({
      key: guide.path,
      label: guide.title,
      date: guide.modified,
      kind: "GUIDE" as const,
      href: vault ? obsidianUri(vault.path, guide.path) : undefined,
      external: true,
    })),
  ].sort((a, b) => (a.date < b.date ? 1 : -1));

  const weakTopics = useWeakTopics().filter((topic) => topic.course === code);

  /**
   * Is a generation of this kind running *for this course*?
   *
   * This used to be one `busyAction: string | null` shared by all three
   * buttons, so a running guide disabled the exam and the deck as well —
   * across every course, since the flag lived in one component. Nothing in the
   * backend asks for that: study generation takes no single-flight slot and
   * contends with nothing (backend/features/study/router.py, `_accept`).
   */
  const busy = (kind: string) =>
    isBusy((job) => job.kind === kind && job.params?.course === code);

  /**
   * Queue a generation and hand its id to the registry.
   *
   * `background: true` is the whole fix for work vanishing on navigation. The
   * backend has accepted it since the job store was generalised; this was the
   * caller that never sent it, so every generation was a fetch held open for
   * minutes with its only progress state in a component local.
   */
  /**
   * Queue a study guide. Still one click: a guide has nothing to configure,
   * unlike a deck or an exam.
   *
   * `background: true` is the whole fix for work vanishing on navigation. The
   * backend has accepted it since the job store was generalised; this was the
   * caller that never sent it.
   */
  async function startGuide() {
    try {
      const { job_id } = await mutateJSON<{ job_id: string }>("/api/study/guide", {
        course: code,
        model: selectedModel(),
        sources: paths,
        background: true,
      });
      track(job_id);
      show("study guide queued — it keeps running if you leave this tab");
    } catch (error) {
      // A 422 here is about the *request* (nothing selected, nothing indexed)
      // and is answered before a job row exists, which is why it still
      // arrives as a rejected promise rather than as a failed job.
      show(`study guide could not start: ${error instanceof Error ? error.message : "backend offline?"}`, {
        tone: "error",
      });
    }
  }

  /**
   * Reload the GENERATED list when this course's work finishes.
   *
   * Refreshing in a promise's resolved branch is no longer possible — there is
   * no promise, by design. The count of this course's in-flight jobs falling
   * to zero is the signal instead, and it works no matter which window or
   * which route started the job.
   */
  const inFlight = jobs.filter((job) => job.params?.course === code).length;
  useEffect(() => {
    if (inFlight > 0) return;
    void refreshSources();
    void refreshExams();
    void refreshDecks();
    refreshSelection();
  }, [inFlight, refreshSources, refreshExams, refreshDecks, refreshSelection]);

  return (
    <>
      <NotebookPanel
        heading="Make something"
        scale="body"
        pad="md"
        // The scope moves here from the button labels. It was appended to all
        // three ("study guide · 3 sources"), which said the same thing three
        // times and made every accessible name depend on how many files
        // happened to be ticked.
        subheading={
          nothingSelected
            ? undefined
            : scoped
              ? `From the ${paths.length} file${paths.length === 1 ? "" : "s"} you have ticked.`
              : "From everything in this course."
        }
      >
        {nothingSelected && (
          <p className="mb-3.5 text-label text-warn">
            Nothing is selected — tick a source to generate from it.
          </p>
        )}

        <div className="flex flex-col gap-2">
          <StudioAction
            label="Study guide"
            running={busy("guide")}
            runningLabel="Writing the guide"
            disabled={busy("guide") || nothingSelected}
            onClick={() => void startGuide()}
          />
          <StudioAction
            label="Flashcard deck"
            running={busy("deck")}
            runningLabel="Writing cards"
            disabled={busy("deck") || nothingSelected}
            onClick={() => setGenerating("deck")}
          />
          <StudioAction
            label="Practice exam"
            running={busy("exam")}
            runningLabel="Generating questions"
            disabled={busy("exam") || nothingSelected}
            onClick={() => setGenerating("exam")}
          />
        </div>

        {weakTopics.length > 0 && (
          <div className="mt-4 border-t border-nb-line pt-3.5">
            <p className="mb-2 text-label text-nb-faint">Worth another look</p>
            <div className="flex flex-wrap gap-1.5">
              {weakTopics.slice(0, showAllTopics ? undefined : CAP).map((topic) => (
                <span
                  key={topic.topic}
                  className="rounded-full border border-nb-line px-2.5 py-0.5 text-label text-nb-body"
                >
                  {topic.topic}
                </span>
              ))}
            </div>
            {weakTopics.length > CAP && !showAllTopics && (
              <button
                type="button"
                onClick={() => setShowAllTopics(true)}
                className="mt-2 text-label text-nb-body underline underline-offset-2 transition-colors hover:text-nb-ink"
              >
                {CAP} of {weakTopics.length} · show all
              </button>
            )}
          </div>
        )}

        {generating && (
          <GenerateDialog
            kind={generating}
            course={code}
            sources={paths}
            onClose={() => setGenerating(null)}
          />
        )}
      </NotebookPanel>

      {/* A second panel, per the artboard: what you can make and what you have
          already made are different questions, and the deck panel sits between
          them because a deck is the one artifact with a number that changes
          every day. Rendered from here rather than as its own export so the
          post-job refresh stays in one place. */}
      <NotebookPanel heading="Already made" scale="body" pad="md" className="order-last">
        {generated.length === 0 ? (
          <p className="text-label text-nb-faint">Nothing generated for {code} yet.</p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {generated.slice(0, showAllGenerated ? undefined : CAP).map((item) =>
              item.href ? (
                <li key={item.key}>
                  {/* `next/link` for an in-app route, so opening an exam no
                      longer costs a full page load that discards the course
                      chat thread; a plain anchor for `obsidian://`, which the
                      router must not try to handle. */}
                  <LinkOrAnchor
                    href={item.href}
                    external={item.external}
                    className="block text-ctl text-nb-body transition-colors hover:text-[var(--ac)]"
                  >
                    <span className="truncate">{item.label}</span>
                    <span className="text-nb-faint">
                      {" · "}
                      {item.kind.toLowerCase()} · {item.date.slice(0, 10)}
                    </span>
                  </LinkOrAnchor>
                </li>
              ) : (
                <li key={item.key} className="text-ctl text-nb-body">
                  <span className="truncate">{item.label}</span>
                  <span className="text-nb-faint">
                    {" · "}
                    {item.kind.toLowerCase()} · {item.date.slice(0, 10)}
                  </span>
                </li>
              ),
            )}
          </ul>
        )}
        {generated.length > CAP && !showAllGenerated && (
          <button
            type="button"
            onClick={() => setShowAllGenerated(true)}
            className="mt-2 text-label text-nb-body underline underline-offset-2 transition-colors hover:text-nb-ink"
          >
            {CAP} of {generated.length} · show all
          </button>
        )}
      </NotebookPanel>
    </>
  );
}
