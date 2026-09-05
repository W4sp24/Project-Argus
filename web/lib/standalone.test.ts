import { describe, expect, it } from "vitest";
import { resolveStandalone } from "./standalone";

describe("resolveStandalone", () => {
  it("reads the flag off the opening URL", () => {
    expect(resolveStandalone("?window=standalone", null)).toBe("standalone");
  });

  it("keeps the flag after the query string is gone", () => {
    // Client-side navigation inside the window drops the query, so every
    // render after the first has to read it back from sessionStorage.
    expect(resolveStandalone("", "standalone")).toBe("standalone");
  });

  it("is null in an ordinary window", () => {
    expect(resolveStandalone("", null)).toBeNull();
  });

  it("ignores a value it does not recognise", () => {
    // The flag reaches this from a URL, which anyone can type.
    expect(resolveStandalone("?window=embedded", null)).toBeNull();
    expect(resolveStandalone("", "embedded")).toBeNull();
  });

  it("lets the URL win over a stale stored value", () => {
    // A window reused for a different purpose must not inherit the old mode.
    expect(resolveStandalone("?window=embedded", "standalone")).toBeNull();
  });

  it("tolerates a query string carrying other parameters", () => {
    expect(resolveStandalone("?deck=3&window=standalone", null)).toBe("standalone");
  });
});
