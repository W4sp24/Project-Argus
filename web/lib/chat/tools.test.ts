import { describe, expect, it } from "vitest";
import { applyToolFrame, foldToolFrames, type ToolStep } from "./tools";

const START = { type: "tool", phase: "start", call_id: "c1", name: "search_vault", args: { q: "x" } };
const END = {
  type: "tool",
  phase: "end",
  call_id: "c1",
  name: "search_vault",
  label: "Search vault",
  detail: "dijkstra",
  paths: ["15-Courses/CS000/course.md"],
  ok: true,
};

/** A fixed clock, so a step's identity is its content and not the wall time. */
const NOW = 1_700_000_000_000;

describe("applyToolFrame", () => {
  it("adds a step for a start frame", () => {
    const steps = applyToolFrame([], START, NOW);
    expect(steps).toEqual([
      { callId: "c1", name: "search_vault", args: { q: "x" }, startedAt: NOW },
    ]);
  });

  it("folds the end frame into the step its call_id names", () => {
    const steps = applyToolFrame(applyToolFrame([], START, NOW), END, NOW);
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({
      callId: "c1",
      label: "Search vault",
      detail: "dijkstra",
      paths: ["15-Courses/CS000/course.md"],
      ok: true,
      endedAt: NOW,
    });
    // The start frame's args must survive the end frame folding over it.
    expect(steps[0].args).toEqual({ q: "x" });
  });

  it("keeps an end frame that has no matching start", () => {
    // Only reachable from a truncated persisted trace — keep the summary
    // rather than dropping the step and under-reporting what the turn did.
    const steps = applyToolFrame([], END, NOW);
    expect(steps).toHaveLength(1);
    expect(steps[0].label).toBe("Search vault");
  });

  it("does not mutate the list it was given", () => {
    const before: ToolStep[] = [];
    applyToolFrame(before, START, NOW);
    expect(before).toEqual([]);
  });

  it("reads ok:false as a failure, and anything else as success", () => {
    expect(applyToolFrame([], { ...END, ok: false }, NOW)[0].ok).toBe(false);
    expect(applyToolFrame([], { ...END, ok: undefined }, NOW)[0].ok).toBe(true);
  });
});

describe("foldToolFrames", () => {
  it("agrees with applyToolFrame, frame for frame", () => {
    // The guarantee that matters: the trace you reload is the trace you
    // watched being built. One function per path would be two that drift.
    const frames = [
      START,
      END,
      { ...START, call_id: "c2", name: "read_note", args: { path: "a.md" } },
      { ...END, call_id: "c2", name: "read_note", label: "Read note", detail: "a.md" },
    ];
    const folded = foldToolFrames(frames, NOW);
    const reduced = frames.reduce<ToolStep[]>(
      (steps, frame) => applyToolFrame(steps, frame, NOW),
      [],
    );
    expect(folded).toEqual(reduced);
  });

  it("keeps the order the frames arrived in", () => {
    const frames = [
      { ...START, call_id: "a" },
      { ...START, call_id: "b" },
      { ...END, call_id: "a" },
      { ...START, call_id: "c" },
    ];
    expect(foldToolFrames(frames, NOW).map((step) => step.callId)).toEqual(["a", "b", "c"]);
  });

  it("ignores anything that is not a frame object", () => {
    // `row.tools` is whatever JSON was persisted; a null or a string in there
    // must not take out the whole transcript.
    expect(foldToolFrames([null, "nope", 7, START], NOW)).toHaveLength(1);
  });

  it("folds a long trace without rescanning it per frame", () => {
    // 400 call ids, start and end each. The old reduce copied the list and
    // scanned it once per frame, so this was ~320k comparisons for one message
    // and openThread paid it per message in the thread.
    const frames = [];
    for (let i = 0; i < 400; i += 1) frames.push({ ...START, call_id: `c${i}` });
    for (let i = 0; i < 400; i += 1) frames.push({ ...END, call_id: `c${i}` });

    const steps = foldToolFrames(frames, NOW);

    expect(steps).toHaveLength(400);
    expect(steps.every((step) => step.endedAt === NOW)).toBe(true);
    expect(steps[399].callId).toBe("c399");
  });
});
