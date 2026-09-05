import { describe, expect, it } from "vitest";
import { boundedCache } from "./swrCache";

const state = (value: unknown) => ({ data: value }) as never;

describe("boundedCache", () => {
  it("reads back what it stored", () => {
    const cache = boundedCache(3);
    cache.set("a", state(1));
    expect(cache.get("a")).toEqual({ data: 1 });
    expect(cache.get("missing")).toBeUndefined();
  });

  it("evicts the least recently used key once it is over the limit", () => {
    const cache = boundedCache(2);
    cache.set("a", state(1));
    cache.set("b", state(2));
    cache.set("c", state(3));

    expect(cache.get("a")).toBeUndefined();
    expect(cache.get("b")).toEqual({ data: 2 });
    expect(cache.get("c")).toEqual({ data: 3 });
  });

  it("counts a read as a use, so a key a mounted hook keeps reading survives", () => {
    // This is the whole reason it is LRU rather than insertion-ordered: the
    // keys still being rendered are exactly the ones being read.
    const cache = boundedCache(2);
    cache.set("a", state(1));
    cache.set("b", state(2));
    cache.get("a");
    cache.set("c", state(3));

    expect(cache.get("a")).toEqual({ data: 1 });
    expect(cache.get("b")).toBeUndefined();
  });

  it("counts a rewrite as a use", () => {
    const cache = boundedCache(2);
    cache.set("a", state(1));
    cache.set("b", state(2));
    cache.set("a", state(9));
    cache.set("c", state(3));

    expect(cache.get("a")).toEqual({ data: 9 });
    expect(cache.get("b")).toBeUndefined();
  });

  it("deletes", () => {
    const cache = boundedCache(3);
    cache.set("a", state(1));
    cache.delete("a");
    expect(cache.get("a")).toBeUndefined();
  });

  it("survives deleting through the iterator keys() hands out", () => {
    // SWR's `mutate(filterFn)` iterates keys and deletes as it goes; a live
    // Map iterator skips entries when the Map is mutated mid-iteration, so a
    // filtered revalidation would silently miss half its matches.
    const cache = boundedCache(10);
    for (const key of ["a", "b", "c", "d"]) cache.set(key, state(key));

    const seen: string[] = [];
    for (const key of cache.keys()) {
      seen.push(key);
      cache.delete(key);
    }

    expect(seen).toEqual(["a", "b", "c", "d"]);
    expect([...cache.keys()]).toEqual([]);
  });

  it("never grows past the limit, however many keys go through it", () => {
    const cache = boundedCache(50);
    for (let i = 0; i < 500; i += 1) cache.set(`note-${i}`, state(i));
    expect([...cache.keys()]).toHaveLength(50);
  });
});
