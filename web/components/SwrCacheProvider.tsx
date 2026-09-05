"use client";

import type { ReactNode } from "react";
import { SWRConfig } from "swr";
import { swrProvider } from "@/lib/swrCache";

/** Module-level so the config object keeps one identity for the life of the
 *  app; SWRConfig reads `provider` once, on mount. */
const SWR_CONFIG = { provider: swrProvider };

/**
 * Mounts the bounded SWR cache (see `lib/swrCache.ts`).
 *
 * A component of its own rather than `<SWRConfig>` written straight into
 * `(dashboard)/layout.tsx`, because `provider` is a function and that would
 * make the layout a client component — shipping the whole layout tree in the
 * shared bundle for every route. `perf:budget` measured the difference: 1 kB
 * on every route's first load, against a budget two routes are already over.
 * The layout stays a server component and renders this instead.
 */
export default function SwrCacheProvider({ children }: { children: ReactNode }) {
  return <SWRConfig value={SWR_CONFIG}>{children}</SWRConfig>;
}
