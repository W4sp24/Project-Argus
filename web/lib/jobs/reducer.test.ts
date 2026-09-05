import { describe, expect, it } from "vitest";
import { type JobLike, reconcile } from "./reducer";

const job = (id: string, status: string, kind = "guide"): JobLike => ({
  id,
  status,
  kind,
  params: null,
});

describe("reconcile", () => {
  it("keeps tracking a job that is still running", () => {
    const out = reconcile(["a"], [job("a", "running")]);
    expect(out.tracked).toEqual(["a"]);
    expect(out.finished).toEqual([]);
  });

  it("reports a job that reached a terminal status, and stops tracking it", () => {
    const out = reconcile(["a"], [job("a", "ok")]);
    expect(out.tracked).toEqual([]);
    expect(out.finished.map((entry) => entry.id)).toEqual(["a"]);
  });

  it("treats partial and failed as terminal too", () => {
    expect(reconcile(["a"], [job("a", "partial")]).finished).toHaveLength(1);
    expect(reconcile(["a"], [job("a", "failed")]).finished).toHaveLength(1);
  });

  it("adopts a running job it was not tracking", () => {
    // This is recovery: a reload, a crash, or a second window opening
    // mid-job. The server is the source of truth, not localStorage.
    const out = reconcile([], [job("b", "queued")]);
    expect(out.tracked).toEqual(["b"]);
  });

  it("does not adopt a job that already finished", () => {
    // Otherwise every mount would re-announce the whole history.
    expect(reconcile([], [job("b", "ok")]).tracked).toEqual([]);
    expect(reconcile([], [job("b", "ok")]).finished).toEqual([]);
  });

  it("drops a tracked id the server no longer knows about", () => {
    // A vanished row cannot finish, so holding its id would leak a
    // permanently-pending entry into the tray.
    expect(reconcile(["gone"], []).tracked).toEqual([]);
  });

  it("preserves tracking order and does not duplicate on re-adoption", () => {
    const out = reconcile(["a"], [job("a", "running"), job("b", "running")]);
    expect(out.tracked).toEqual(["a", "b"]);
  });
});
