"use client";

import { useCallback, useRef, useState } from "react";
import ConfirmDialog from "@/components/ui/ConfirmDialog";

export interface ConfirmRequest {
  /** Accessible name for the dialog, e.g. "Delete task". */
  label: string;
  message: string;
  detail?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "danger" | "primary";
  /** Collect free text alongside the confirmation (replaces window.prompt). */
  reason?: { label: string; placeholder?: string; required?: boolean };
}

/**
 * Promise-based confirmation, shaped to drop into the places that called
 * `window.confirm` / `window.prompt`.
 *
 * Resolves to `null` when cancelled, and to the reason string otherwise —
 * which is `""` for a plain confirmation. Check `=== null`, not truthiness:
 * "confirmed with no reason given" and "cancelled" are different answers.
 *
 *   const reason = await confirm({ label: "Delete task", message: "…" });
 *   if (reason === null) return;
 *
 * Render `confirmDialog` somewhere in the component's tree.
 */
export function useConfirm() {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const resolver = useRef<((value: string | null) => void) | null>(null);

  const confirm = useCallback((next: ConfirmRequest) => {
    setRequest(next);
    return new Promise<string | null>((resolve) => {
      // A second call while one is open abandons the first as cancelled rather
      // than leaving its caller awaiting forever.
      resolver.current?.(null);
      resolver.current = resolve;
    });
  }, []);

  const settle = useCallback((value: string | null) => {
    setRequest(null);
    const resolve = resolver.current;
    resolver.current = null;
    resolve?.(value);
  }, []);

  const confirmDialog = request ? (
    // Closes on confirm, matching what window.confirm did — the caller's own
    // busy state (button labels flipping to DELETING… etc.) covers the wait.
    <ConfirmDialog
      {...request}
      onConfirm={(reason) => settle(reason)}
      onCancel={() => settle(null)}
    />
  ) : null;

  return { confirm, confirmDialog };
}
