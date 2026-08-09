"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import WidgetShell from "@/components/automations/WidgetShell";
import { useConfirm } from "@/components/ui/useConfirm";
import { useToast } from "@/components/Toast";
import {
  deleteAutomationWidget,
  patchAutomationWidget,
  resetAutomationLayout,
  useAutomationInstances,
  useAutomationWidgets,
  type AutomationWidget,
} from "@/lib/api";

/**
 * Automation-fed panels on the dashboard, laid out as an auto-placed grid the
 * user can take control of one widget at a time.
 *
 * Auto-place, then take control (Home Assistant's model): a new widget
 * appends automatically at its default span, so an automation is useful the
 * moment it first pushes — no configuration step between installing and
 * benefiting. Dragging or resizing ONE widget marks only that one as
 * `layout_locked`; everything else keeps auto-placing regardless of how many
 * widgets are already under manual control.
 *
 * Renders nothing at all when there are no widgets. An empty panel announcing
 * that you have no automations is worse than the space it occupies, and every
 * existing install starts in exactly that state.
 */

const WIDE_COLUMNS = 4;
const NARROW_COLUMNS = 2;
const NARROW_BREAKPOINT = "(min-width: 1280px)";
const MAX_SPAN = 4;
const MIN_SPAN = 1;
// Matches `grid-auto-rows: minmax(124px, auto)` below. Resize math converts
// pixel drag distance into whole rows off this floor — a widget whose actual
// rendered row is taller (a long list under STALE, say) makes the drag feel
// "sticky" past that point. A known approximation, not a bug: the corner
// handle always has the cols×rows button as an exact, discrete fallback.
const ROW_PX = 124;
/** Dismissal of the zero-state hint. Scoped to this one line, not a general
 * "hide automations" preference — the AUTO tab is unaffected either way. */
const HINT_KEY = "argus-automations-hint-dismissed";
const GAP_PX = 16; // gap-4, matching the dashboard's rhythm elsewhere on the page

function widgetKey(w: Pick<AutomationWidget, "instance_id" | "slug">): string {
  return `${w.instance_id}:${w.slug}`;
}

/**
 * The width a renderer asks for, in grid cells.
 *
 * The renderer proposes and the user disposes: a metric is one number and
 * reads fine narrow, while a timeline, table or paragraph gets truncated into
 * uselessness at one column ("Team s…", "1:1 with …"). So each kind carries a
 * sensible default and the size control overrides it — which is why this is
 * applied only while `layout_locked` is false. Once someone has sized a
 * widget by hand, their choice wins and never gets silently re-proposed.
 *
 * Deliberately not a stored default: writing it at push time would bake one
 * build's opinion into the database, and changing it later would then only
 * affect widgets created afterwards.
 */
const PROPOSED_COLS: Record<string, number> = {
  metric: 1,
  list: 1,
  timeline: 2,
  table: 2,
  text: 2,
  chart: 2,
};

function proposedSpan(widget: AutomationWidget): { cols: number; rows: number } {
  if (widget.layout_locked) return { cols: widget.grid_cols, rows: widget.grid_rows };
  return { cols: PROPOSED_COLS[widget.kind] ?? 1, rows: widget.grid_rows };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export default function AutomationWidgets() {
  const { data: widgets, mutate } = useAutomationWidgets();
  const { data: instances } = useAutomationInstances();
  const { show } = useToast();
  const { confirm, confirmDialog } = useConfirm();

  const gridRef = useRef<HTMLDivElement>(null);
  const [columns, setColumns] = useState(WIDE_COLUMNS);
  const [draggingKey, setDraggingKey] = useState<string | null>(null);
  const [liveResize, setLiveResize] = useState<{ key: string; cols: number; rows: number } | null>(
    null,
  );
  // Read after mount, never during render: localStorage is unavailable on the
  // server, so branching on it while rendering is a hydration mismatch. Both
  // flags start false so the server renders nothing and the client decides —
  // that costs one frame of absence rather than a flash of content that then
  // disappears for someone who already dismissed it.
  const [hintReady, setHintReady] = useState(false);
  const [hintDismissed, setHintDismissed] = useState(false);

  useEffect(() => {
    try {
      setHintDismissed(window.localStorage.getItem(HINT_KEY) === "1");
    } catch {
      // Private-browsing/embedded contexts throw. Showing the hint is the
      // safe failure: worst case it reappears, which is recoverable — a
      // silently hidden entry point is not.
    }
    setHintReady(true);
  }, []);

  function dismissHint() {
    setHintDismissed(true);
    try {
      window.localStorage.setItem(HINT_KEY, "1");
    } catch {
      // Best-effort, exactly like ModeProvider's own persistence.
    }
  }

  useEffect(() => {
    const mq = window.matchMedia(NARROW_BREAKPOINT);
    const update = () => setColumns(mq.matches ? WIDE_COLUMNS : NARROW_COLUMNS);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);

  // Display order. `position` is nullable (auto-placed widgets never had one
  // written), so a widget's index in the array it arrived in stands in for it
  // — that's also the order the backend already put it in.
  const sorted = useMemo(() => {
    const source = widgets ?? [];
    return source
      .map((w, i) => ({ w, i }))
      .sort((a, b) => (a.w.position ?? a.i) - (b.w.position ?? b.i))
      .map((entry) => entry.w);
  }, [widgets]);

  // Origin is only information once there is a second instance to tell apart.
  // With one registered, every chip would read the same and say nothing, so
  // the name is withheld and OriginChip renders null.
  const showOrigin = (instances?.length ?? 0) > 1;
  const byId = new Map((instances ?? []).map((i) => [i.id, i]));

  const controlledWidgets = sorted.filter((w) => w.layout_locked);

  /**
   * Apply a layout patch (resize, cycle-width, or reorder) to one widget.
   * Optimistic first — the grid does not visibly snap back while the request
   * is in flight — then always ends with a bare revalidate, which both
   * confirms success (server-clamped values win) and rolls back a failure
   * (server truth overwrites the optimistic guess). Toasts the "you're now
   * controlling this" mode-change exactly once, the first time a widget
   * that wasn't already locked gets a patch.
   */
  async function applyPatch(
    widget: AutomationWidget,
    patch: { position?: number; grid_cols?: number; grid_rows?: number },
    optimisticList?: AutomationWidget[],
  ) {
    const wasLocked = widget.layout_locked;
    const key = widgetKey(widget);
    const base = optimisticList ?? sorted;
    const nextList = base.map((w) => (widgetKey(w) === key ? { ...w, ...patch, layout_locked: true } : w));
    void mutate(nextList, false);
    try {
      await patchAutomationWidget(widget.slug, widget.instance_id, patch);
      if (!wasLocked) {
        show(
          `automations :: you're now controlling ${widget.title || widget.slug} — drag or resize any widget any time, the rest still auto-place`,
        );
      }
    } catch (error) {
      show(
        `automations :: could not update ${widget.title || widget.slug} — ${
          error instanceof Error ? error.message : "unknown error"
        }`,
        { tone: "error" },
      );
    } finally {
      void mutate();
    }
  }

  /** Move `widget` to `targetIndex` in display order and persist just its own
   * `position`, computed as the midpoint between its new neighbours — so
   * reordering ONE widget never has to write to the widgets on either side of
   * it, matching the "only that one gets locked" contract. */
  function reorderTo(widget: AutomationWidget, targetIndex: number) {
    const key = widgetKey(widget);
    const withoutMoved = sorted.filter((w) => widgetKey(w) !== key);
    const insertAt = clamp(targetIndex, 0, withoutMoved.length);
    const reordered = withoutMoved.slice();
    reordered.splice(insertAt, 0, widget);

    const prevEff = insertAt > 0 ? (reordered[insertAt - 1].position ?? insertAt - 1) : null;
    const nextEff =
      insertAt < reordered.length - 1 ? (reordered[insertAt + 1].position ?? insertAt + 1) : null;
    let newPosition: number;
    if (prevEff === null && nextEff === null) newPosition = 0;
    else if (prevEff === null) newPosition = nextEff! - 1;
    else if (nextEff === null) newPosition = prevEff + 1;
    else newPosition = (prevEff + nextEff) / 2;

    void applyPatch(widget, { position: newPosition }, reordered);
  }

  function handleDrop(targetWidget: AutomationWidget) {
    const key = draggingKey;
    setDraggingKey(null);
    if (!key || key === widgetKey(targetWidget)) return;
    const moved = sorted.find((w) => widgetKey(w) === key);
    if (!moved) return;
    const withoutMoved = sorted.filter((w) => widgetKey(w) !== key);
    const insertAt = withoutMoved.findIndex((w) => widgetKey(w) === widgetKey(targetWidget));
    reorderTo(moved, insertAt === -1 ? withoutMoved.length : insertAt);
  }

  /** Keyboard-reachable equivalent of dragging a widget one slot over — same
   * `reorderTo`, so keyboard and pointer reordering always agree on the
   * result. Documented here rather than left as a silent gap: these two
   * buttons are the full keyboard story for reorder, alongside the
   * cols×rows button below for resize. */
  function moveBy(widget: AutomationWidget, direction: -1 | 1) {
    const idx = sorted.findIndex((w) => widgetKey(w) === widgetKey(widget));
    if (idx === -1) return;
    reorderTo(widget, idx + direction);
  }

  function cycleWidth(widget: AutomationWidget) {
    // Cycle from what is on screen, not from the stored value — otherwise the
    // first press on an unlocked two-column widget jumps it to 2 and looks
    // like nothing happened.
    const nextCols = (proposedSpan(widget).cols % MAX_SPAN) + 1;
    void applyPatch(widget, { grid_cols: nextCols });
  }

  function startResize(event: ReactPointerEvent<HTMLDivElement>, widget: AutomationWidget) {
    event.preventDefault();
    event.stopPropagation();
    const grid = gridRef.current;
    if (!grid) return;
    const cellWidth = (grid.clientWidth - GAP_PX * (columns - 1)) / columns;
    const startX = event.clientX;
    const startY = event.clientY;
    const startCols = widget.grid_cols;
    const startRows = widget.grid_rows;
    const key = widgetKey(widget);
    const maxCols = Math.min(MAX_SPAN, columns);

    function nextSize(clientX: number, clientY: number) {
      const dCols = Math.round((clientX - startX) / (cellWidth + GAP_PX));
      const dRows = Math.round((clientY - startY) / (ROW_PX + GAP_PX));
      return {
        cols: clamp(startCols + dCols, MIN_SPAN, maxCols),
        rows: clamp(startRows + dRows, MIN_SPAN, MAX_SPAN),
      };
    }

    function onMove(ev: PointerEvent) {
      const size = nextSize(ev.clientX, ev.clientY);
      setLiveResize({ key, cols: size.cols, rows: size.rows });
    }

    function onUp(ev: PointerEvent) {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      const size = nextSize(ev.clientX, ev.clientY);
      setLiveResize(null);
      if (size.cols !== startCols || size.rows !== startRows) {
        void applyPatch(widget, { grid_cols: size.cols, grid_rows: size.rows });
      }
    }

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp, { once: true });
  }

  /**
   * Hand every widget back to auto-placement.
   *
   * One call, not a PATCH per widget: every layout write implicitly locks the
   * widget it touches, so unlocking through that route is impossible by
   * construction — the act of sending the patch re-locks. The backend
   * therefore owns this as a single operation, which also makes it atomic
   * (no half-restored dashboard if one request fails) and keeps sizes
   * returning to the renderers' own defaults rather than to whatever this
   * session happened to observe first.
   */
  async function restoreAutoPlace() {
    if (!widgets || controlledWidgets.length === 0) return;
    const nextList = widgets.map((w) =>
      w.layout_locked ? { ...w, grid_cols: 1, grid_rows: 1, layout_locked: false } : w,
    );
    void mutate(nextList, false);
    try {
      await resetAutomationLayout();
      show("automations :: restored auto-place");
    } catch (error) {
      show(
        `automations :: could not restore auto-place — ${
          error instanceof Error ? error.message : "unknown error"
        }`,
        { tone: "error" },
      );
    } finally {
      void mutate();
    }
  }

  // Nothing pushed yet. The original rule here was to render nothing at all,
  // on the grounds that an empty panel announcing you have no automations is
  // worse than the space it costs — which is still true of a *panel*. But
  // rendering nothing meant the dashboard gave no hint the feature existed,
  // and a widget can only appear after an n8n instance is registered *and*
  // the inbound surface is switched on, so the zero state is where most
  // people will sit. So: one muted line, the same weight as the layout line
  // it replaces, and dismissible — because for someone who will never run
  // n8n, a permanent hint is exactly the noise the original rule guarded
  // against.
  if (!widgets || widgets.length === 0) {
    if (!hintReady || hintDismissed) return null;
    return (
      <div className="flex items-center gap-2 py-0.5">
        <p className="min-w-0 font-mono text-micro uppercase tracking-[0.16em] text-ink-faint">
          automations · no live panels yet
        </p>
        <Link
          href="/automations"
          className="font-mono text-micro uppercase tracking-[0.14em] text-[var(--ac)] underline underline-offset-2 hover:text-ink-bright"
        >
          set one up →
        </Link>
        <button
          type="button"
          aria-label="Hide the automations hint"
          title="Hide this — /automations stays in the top bar"
          onClick={dismissHint}
          className="ml-auto px-1 font-mono text-micro text-ink-faint hover:text-ink"
        >
          ✕
        </button>
      </div>
    );
  }

  async function remove(slug: string, instanceId: string, title: string) {
    // useConfirm resolves null on cancel and "" on a plain confirm.
    const answer = await confirm({
      label: "Remove widget",
      message: `Remove ${title} from the dashboard?`,
      detail:
        "The automation keeps running in n8n. Its next push will bring the widget back.",
      confirmLabel: "Remove",
      tone: "danger",
    });
    if (answer === null) return;
    try {
      await deleteAutomationWidget(slug, instanceId);
      show(`automations :: removed ${slug}`);
      void mutate();
    } catch (error) {
      show(
        `automations :: could not remove ${slug} — ${
          error instanceof Error ? error.message : "unknown error"
        }`,
        { tone: "error" },
      );
    }
  }

  const controlledCount = controlledWidgets.length;

  return (
    <>
      <p className="mb-2 font-mono text-meta text-ink-faint">
        {controlledCount === 0 ? (
          "auto-placed · drag a widget to reorder, drag its corner to resize"
        ) : (
          <>
            {`▣ ${controlledCount} widget${controlledCount === 1 ? "" : "s"} under your control · the rest still auto-place`}{" "}
            <button
              type="button"
              onClick={() => void restoreAutoPlace()}
              className="text-[var(--ac)] hover:underline"
            >
              restore auto-place
            </button>
          </>
        )}
      </p>

      <div
        ref={gridRef}
        className="grid gap-4"
        style={{
          gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
          gridAutoRows: `minmax(${ROW_PX}px, auto)`,
        }}
      >
        {sorted.map((widget, index) => {
          const key = widgetKey(widget);
          const instance = byId.get(widget.instance_id);
          const live = liveResize?.key === key ? liveResize : null;
          const proposed = proposedSpan(widget);
          const displayCols = Math.min(live?.cols ?? proposed.cols, columns);
          const displayRows = live?.rows ?? proposed.rows;
          const title = widget.title || widget.slug;

          return (
            <div
              key={key}
              // HTML5 drag-and-drop reorder. `moveBy` below is the keyboard
              // equivalent — this is not the only way to reorder.
              draggable
              onDragStart={() => setDraggingKey(key)}
              onDragEnd={() => setDraggingKey(null)}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                handleDrop(widget);
              }}
              className={`relative min-w-0 transition-opacity ${
                draggingKey === key ? "opacity-50" : ""
              }`}
              style={{ gridColumn: `span ${displayCols}`, gridRow: `span ${displayRows}` }}
            >
              <WidgetShell
                widget={widget}
                instanceName={showOrigin ? instance?.name : null}
                instanceBaseUrl={instance?.base_url}
                controlled={widget.layout_locked}
                actions={
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      aria-label={`Move ${title} earlier`}
                      disabled={index === 0}
                      onClick={() => moveBy(widget, -1)}
                      className="px-0.5 font-mono text-micro text-ink-faint hover:text-ink disabled:opacity-30"
                    >
                      ◀
                    </button>
                    <button
                      type="button"
                      aria-label={`Move ${title} later`}
                      disabled={index === sorted.length - 1}
                      onClick={() => moveBy(widget, 1)}
                      className="px-0.5 font-mono text-micro text-ink-faint hover:text-ink disabled:opacity-30"
                    >
                      ▶
                    </button>
                    <button
                      type="button"
                      // Reports the span actually on screen, which is the
                      // renderer's proposal until the user overrides it —
                      // showing the stored 1×1 under a two-column widget
                      // would just be wrong.
                      aria-label={`Resize ${title}, currently ${proposed.cols} columns by ${proposed.rows} rows. Activates to cycle width.`}
                      title="Cycle width — keyboard-reachable resize"
                      onClick={() => cycleWidth(widget)}
                      className="border border-line px-1 py-px font-mono text-micro text-ink-faint hover:border-lineHi hover:text-ink"
                    >
                      {`${proposed.cols}×${proposed.rows}`}
                    </button>
                  </div>
                }
                menuItems={[
                  {
                    label: "REMOVE",
                    run: () =>
                      void remove(widget.slug, widget.instance_id, widget.title || widget.slug),
                  },
                ]}
              />
              {/* Pointer-drag resize. Decorative/mouse-only by design — the
                  cols×rows button above is the keyboard- and screen-reader-
                  reachable way to do the same thing, so this handle is
                  excluded from the tab order rather than faking operability
                  it doesn't have. */}
              <div
                aria-hidden="true"
                title="Drag to resize"
                onPointerDown={(event) => startResize(event, widget)}
                className="absolute bottom-0.5 right-0.5 h-4 w-4 cursor-nwse-resize touch-none select-none font-mono text-micro leading-none text-ink-faint hover:text-ink"
              >
                ⋰
              </div>
            </div>
          );
        })}
      </div>

      {confirmDialog}
    </>
  );
}
