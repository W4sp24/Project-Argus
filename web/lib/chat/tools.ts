/**
 * Folding `tool` frames into the step list a trace renders from.
 *
 * Split out of `lib/chat.tsx` for the reason `lib/jobs/reducer.ts` was split
 * out of `lib/jobs.tsx`: this is the decision-making, it is pure, and it is the
 * part worth unit-testing. What stays in the provider is React state and IO.
 *
 * The frames streamed over the socket and the `tools_json` rows a thread
 * restores from are the same objects — `_tool_frame()` (backend) produces both,
 * and the router appends every frame it sends — so they must fold through one
 * place, or a reloaded trace would quietly differ from the one you watched
 * being built.
 */

/** One dispatch of one tool, assembled from the `tool` start and end frames. */
export interface ToolStep {
  callId: string;
  name: string;
  args?: Record<string, unknown>;
  /** Present once the matching `phase:"end"` frame lands. */
  label?: string;
  detail?: string;
  paths?: string[];
  ok?: boolean;
  startedAt: number;
  endedAt?: number;
}

/** The fields an end frame contributes, whether or not a start was seen. */
function finishedFields(frame: Record<string, unknown>, name: string, now: number) {
  return {
    label: String(frame.label ?? name),
    detail: String(frame.detail ?? ""),
    paths: Array.isArray(frame.paths) ? (frame.paths as string[]) : [],
    ok: frame.ok !== false,
    endedAt: now,
  };
}

/**
 * Fold one live frame into a step list, upserting by `call_id`.
 *
 * Copies, because this is called from a `setState` updater and the previous
 * list must not be mutated. Scanning for the call id is fine here: one frame at
 * a time, against the handful of steps in a single turn.
 */
export function applyToolFrame(
  steps: ToolStep[],
  frame: Record<string, unknown>,
  now: number = Date.now(),
): ToolStep[] {
  const callId = String(frame.call_id ?? "");
  const name = String(frame.name ?? "");
  const next = [...steps];
  const at = next.findIndex((step) => step.callId === callId);
  if (frame.phase === "start") {
    const started: ToolStep = {
      callId,
      name,
      args: (frame.args as Record<string, unknown>) ?? undefined,
      startedAt: now,
    };
    if (at === -1) next.push(started);
    else next[at] = { ...next[at], ...started };
    return next;
  }
  const finished = finishedFields(frame, name, now);
  // An end frame with no matching start can only come from a truncated
  // persisted trace; keep the summary rather than dropping the step.
  if (at === -1) next.push({ callId, name, startedAt: now, ...finished });
  else next[at] = { ...next[at], ...finished };
  return next;
}

/**
 * Fold a whole persisted trace in one pass.
 *
 * Same result as reducing `applyToolFrame` over the frames, without the copy
 * and the linear scan per frame — that is quadratic in the trace length, and
 * `openThread` pays it once per message in the thread.
 */
export function foldToolFrames(frames: unknown[], now: number = Date.now()): ToolStep[] {
  const steps: ToolStep[] = [];
  const at = new Map<string, number>();
  for (const raw of frames) {
    if (!raw || typeof raw !== "object") continue;
    const frame = raw as Record<string, unknown>;
    const callId = String(frame.call_id ?? "");
    const name = String(frame.name ?? "");
    const index = at.get(callId);
    if (frame.phase === "start") {
      const started: ToolStep = {
        callId,
        name,
        args: (frame.args as Record<string, unknown>) ?? undefined,
        startedAt: now,
      };
      if (index === undefined) {
        at.set(callId, steps.length);
        steps.push(started);
      } else {
        steps[index] = { ...steps[index], ...started };
      }
      continue;
    }
    const finished = finishedFields(frame, name, now);
    if (index === undefined) {
      at.set(callId, steps.length);
      steps.push({ callId, name, startedAt: now, ...finished });
    } else {
      steps[index] = { ...steps[index], ...finished };
    }
  }
  return steps;
}
