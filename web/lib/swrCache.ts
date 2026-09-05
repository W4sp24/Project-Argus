import type { Cache, State } from "swr";

/**
 * A bounded cache provider for SWR.
 *
 * SWR's default cache is an unbounded module-level `Map`, which is fine for a
 * page that reloads between visits and wrong for this app: the dashboard is a
 * long-lived Electron renderer, and a great many of its keys are per-item —
 * `useNotesIn(folder)`, `fetchNoteOrNull(path)`, `useCourseSources(code)`,
 * `useJournalNote(path)`. Reading a hundred notes over an afternoon left a
 * hundred entries behind, and nothing in the app ever deleted one.
 *
 * Least-recently-used, because "recently used" is exactly the property that
 * separates the keys a mounted hook is still reading from the note somebody
 * opened once an hour ago. Evicting a key that *is* still mounted is safe
 * rather than merely tolerable — SWR treats a miss as "no cached value yet"
 * and revalidates — but with the cap well above the number of keys any one
 * screen subscribes to, it should not arise.
 */
export const CACHE_LIMIT = 500;

export function boundedCache(limit = CACHE_LIMIT): Cache {
  const map = new Map<string, State<unknown, unknown>>();

  /** Move a key to the young end. Map iterates in insertion order, so
   *  delete-then-set is the whole LRU mechanism. */
  const touch = (key: string, value: State<unknown, unknown>) => {
    map.delete(key);
    map.set(key, value);
  };

  return {
    get(key: string) {
      const value = map.get(key);
      if (value !== undefined) touch(key, value);
      return value;
    },
    set(key: string, value: State<unknown, unknown>) {
      touch(key, value);
      while (map.size > limit) {
        // `keys().next()` is the oldest entry, and Map guarantees that order.
        const oldest = map.keys().next();
        if (oldest.done) break;
        map.delete(oldest.value);
      }
    },
    delete(key: string) {
      map.delete(key);
    },
    keys() {
      // A copy, not the live iterator: SWR's `mutate(filterFn)` deletes while
      // iterating, and mutating a Map mid-iteration skips entries.
      return [...map.keys()][Symbol.iterator]();
    },
  };
}

/**
 * The value `<SWRConfig provider>` wants: it is handed the existing cache and
 * returns the one to use. Nothing is carried over — this is mounted once, at
 * the dashboard layout, before any request has been made.
 */
export const swrProvider = (): Cache => boundedCache();
