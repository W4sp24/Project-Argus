import { describe, expect, it } from "vitest";
import { nextReview } from "./schedule";

// A Wednesday, mid-afternoon, so "later today" and the weekday names have
// somewhere unambiguous to land.
const NOW = new Date("2026-09-16T15:00:00");

describe("nextReview", () => {
  it("calls a card with no review due now", () => {
    // Null is what the API sends for a card nobody has graded. It is new, and
    // new is due immediately — inventing the deck's creation time instead would
    // make this a lie the editor has to decode.
    expect(nextReview(null, false, NOW)).toEqual({ label: "Due now", tone: "due" });
  });

  it("calls a past date due now", () => {
    expect(nextReview("2026-09-14T09:00:00", false, NOW).label).toBe("Due now");
  });

  it("counts by calendar day, not by 24-hour blocks", () => {
    // 23:00 tonight is eight hours away but still today; 09:00 tomorrow is
    // eighteen hours away and is not. Rounding the gap would swap them.
    expect(nextReview("2026-09-16T23:00:00", false, NOW).label).toBe("Later today");
    expect(nextReview("2026-09-17T09:00:00", false, NOW).label).toBe("Tomorrow");
  });

  it("names the weekday inside the coming week", () => {
    expect(nextReview("2026-09-18T10:00:00", false, NOW).label).toBe("Friday");
    expect(nextReview("2026-09-21T10:00:00", false, NOW).label).toBe("Monday");
  });

  it("switches to a date once a weekday would be ambiguous", () => {
    // Seven days out is the same weekday as today — "Wednesday" would read as
    // tomorrow-ish rather than a week away.
    expect(nextReview("2026-09-23T10:00:00", false, NOW).label).toBe("Sep 23");
    expect(nextReview("2026-11-02T10:00:00", false, NOW).label).toBe("Nov 2");
  });

  it("lets suspended outrank any date the card still carries", () => {
    // Suspending does not clear the schedule, so a suspended card keeps a
    // due_at. It still never comes up, and the column has to say so.
    expect(nextReview("2026-09-18T10:00:00", true, NOW)).toEqual({
      label: "Suspended",
      tone: "muted",
    });
    expect(nextReview(null, true, NOW).label).toBe("Suspended");
  });

  it("does not throw on a timestamp it cannot parse", () => {
    expect(nextReview("not a date", false, NOW)).toEqual({ label: "—", tone: "muted" });
  });
});
