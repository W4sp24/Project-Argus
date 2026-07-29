"use client";

import { useRef, useState } from "react";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Field, { FIELD_CONTROL } from "@/components/ui/Field";

/**
 * Replaces the four `window.confirm` / `window.prompt` calls. Those bypassed
 * the design system entirely — an OS chrome dialog in the middle of a terminal
 * HUD — and `window.prompt` in particular was collecting the planner's
 * dismissal reason, a real feedback form, through a browser box.
 *
 * Pass `reason` to collect free text along with the confirmation; `onConfirm`
 * receives it (trimmed, possibly empty). Cancelling never calls `onConfirm`.
 */
export default function ConfirmDialog({
  label,
  message,
  detail,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "danger",
  reason,
  busy = false,
  onConfirm,
  onCancel,
}: {
  /** Accessible name for the dialog. */
  label: string;
  message: string;
  /** Secondary line — the reassurance, the consequence, the path. */
  detail?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "danger" | "primary";
  reason?: { label: string; placeholder?: string; required?: boolean };
  busy?: boolean;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState("");
  const reasonRef = useRef<HTMLTextAreaElement>(null);

  const blocked = busy || (reason?.required === true && text.trim() === "");

  return (
    <Dialog
      label={label}
      onClose={onCancel}
      className="w-[30rem] max-w-[calc(100vw-2rem)] p-5"
      // With a reason field, land in it. Without one, initial focus falls to
      // Cancel (first in DOM order below) so Enter on a destructive prompt
      // backs out rather than committing.
      initialFocusRef={reason ? reasonRef : undefined}
    >
      <p className="eyebrow mb-3">{`▍${label.toUpperCase()}`}</p>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!blocked) onConfirm(text.trim());
        }}
      >
        <p className="text-body text-ink">{message}</p>
        {detail && <p className="mt-1 text-label text-ink-muted">{detail}</p>}

        {reason && (
          <Field label={reason.label} className="mt-4">
            {(props) => (
              <textarea
                {...props}
                ref={reasonRef}
                rows={3}
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder={reason.placeholder}
                className={`${FIELD_CONTROL} resize-none leading-relaxed`}
              />
            )}
          </Field>
        )}

        <div className="mt-5 flex items-center gap-2 border-t border-line pt-4">
          <Button size="md" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button
            type="submit"
            size="md"
            variant={tone}
            disabled={blocked}
            className="ml-auto"
          >
            {confirmLabel}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
