"use client";

import { useMemo, useState } from "react";
import Field, { FIELD_CONTROL } from "@/components/ui/Field";
import Button from "@/components/ui/Button";
import type { AutomationCard, AutomationField } from "@/lib/api";

/**
 * A workflow's Form Trigger, rendered as a native Argus form.
 *
 * n8n is the single source of truth for the schema — rename a field there and
 * this changes on the next refresh, because there is no second schema to
 * maintain. Field order is the backend's (required before optional, per
 * Raycast's guideline) and is deliberately not re-sorted here.
 *
 * Two field types need care:
 *
 * - **Password** is masked, held only for the life of the request, and wiped
 *   from state on submit. It is never persisted, never logged and never
 *   written into run history. Supporting it at all puts Argus in the
 *   secret-relay business, which it was not in before, so the handling is
 *   explicit rather than incidental.
 * - **Custom HTML** has already been through the one allowlist sanitiser on
 *   the server (`backend/features/automations/sanitize.py`). The trust
 *   boundary is server-side; this is the only place in the app that renders
 *   pushed markup, and it renders what the server already vetted.
 */

function initialValues(fields: readonly AutomationField[]): Record<string, string> {
  const values: Record<string, string> = {};
  for (const field of fields) {
    values[field.name] = typeof field.default === "string" ? field.default : "";
  }
  return values;
}

export default function AutomationForm({
  card,
  busy,
  onCancel,
  onSubmit,
}: {
  card: AutomationCard;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (payload: Record<string, string>) => void;
}) {
  const fields = useMemo(() => card.fields ?? [], [card.fields]);
  const [values, setValues] = useState<Record<string, string>>(() => initialValues(fields));

  const missingRequired = fields.some(
    (field) => field.required && !field.hidden && !values[field.name]?.trim(),
  );

  function set(name: string, value: string) {
    setValues((previous) => ({ ...previous, [name]: value }));
  }

  function submit() {
    if (busy || missingRequired) return;
    const payload = { ...values };
    // Wipe every secret from component state the moment it leaves. The
    // request still carries it; nothing here keeps a copy.
    setValues((previous) => {
      const cleared = { ...previous };
      for (const field of fields) {
        if (field.secret) cleared[field.name] = "";
      }
      return cleared;
    });
    onSubmit(payload);
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      className="flex flex-col gap-3 px-4 py-3"
    >
      <p className="font-mono text-meta uppercase tracking-[0.14em] text-[var(--ac)]">
        {card.name}
      </p>

      {fields.length === 0 && (
        <p className="text-label text-ink-faint">This automation takes no input.</p>
      )}

      {fields.map((field) => {
        // Hidden fields are never shown but their value is still submitted.
        if (field.hidden) return null;

        if (field.type === "custom_html") {
          return (
            <div
              key={field.name}
              className="text-label text-ink-muted"
              // Sanitised server-side before it ever reached this build.
              dangerouslySetInnerHTML={{ __html: field.html ?? "" }}
            />
          );
        }

        return (
          <Field
            key={field.name}
            label={field.label || field.name}
            hint={field.required ? undefined : "optional"}
          >
            {(props) => {
              if (field.type === "textarea") {
                return (
                  <textarea
                    {...props}
                    rows={3}
                    value={values[field.name] ?? ""}
                    onChange={(event) => set(field.name, event.target.value)}
                    placeholder={field.placeholder ?? ""}
                    className={`${FIELD_CONTROL} resize-none`}
                  />
                );
              }
              if (field.options && field.options.length > 0) {
                return (
                  <select
                    {...props}
                    value={values[field.name] ?? ""}
                    onChange={(event) => set(field.name, event.target.value)}
                    className={FIELD_CONTROL}
                  >
                    <option value="">—</option>
                    {field.options.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                );
              }
              const inputType =
                field.type === "password"
                  ? "password"
                  : field.type === "number"
                    ? "number"
                    : field.type === "date"
                      ? "date"
                      : field.type === "email"
                        ? "email"
                        : "text";
              return (
                <input
                  {...props}
                  type={inputType}
                  value={values[field.name] ?? ""}
                  onChange={(event) => set(field.name, event.target.value)}
                  placeholder={field.placeholder ?? ""}
                  className={FIELD_CONTROL}
                />
              );
            }}
          </Field>
        );
      })}

      <div className="flex items-center gap-2 border-t border-line pt-3">
        <Button variant="quiet" onClick={onCancel} disabled={busy}>
          BACK
        </Button>
        <Button
          type="submit"
          variant="primary"
          className="ml-auto"
          disabled={busy || missingRequired}
        >
          {busy ? "RUNNING…" : "RUN"}
        </Button>
      </div>
    </form>
  );
}
