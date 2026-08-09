"use client";

import { useMemo, useState } from "react";
import Field, { FIELD_CONTROL } from "@/components/ui/Field";
import Button from "@/components/ui/Button";
import SegmentedControl from "@/components/ui/SegmentedControl";
import type { AutomationCard, AutomationField } from "@/lib/api";

/**
 * A workflow's Form Trigger, rendered as a native Argus form.
 *
 * n8n is the single source of truth for the schema — rename a field there and
 * this changes on the next refresh, because there is no second schema to
 * maintain. Field order is the backend's (required before optional, per
 * Raycast's guideline) and is deliberately not re-sorted here.
 *
 * Values are `Record<string, unknown>` rather than `Record<string, string>`
 * because two field types don't fit a bare string: `checkboxes` with
 * `multiple` submits a `string[]`, and `file` submits a data URL (a string,
 * but distinct enough in how it's produced to call out).
 *
 * Fields needing care beyond a plain `<input>`:
 *
 * - **Dropdown / radio / checkboxes** all carry `options`, but are three
 *   different controls, not one collapsed `<select>` — n8n's Form Trigger
 *   distinguishes them and a submitter picking between them expects the
 *   control they configured, not always a dropdown.
 * - **File** has no server-side upload endpoint here — the payload is
 *   forwarded verbatim as the form/webhook body (see `runAutomation`), so a
 *   selected file is read client-side into a data URL and submitted as that
 *   string, the same representation `IconPicker` already uses for images.
 * - **Password** is masked, held only for the life of the request, and wiped
 *   from state on submit. It is never persisted, never logged and never
 *   written into run history. Supporting it at all puts Argus in the
 *   secret-relay business, which it was not in before, so the handling is
 *   explicit rather than incidental.
 * - **Custom HTML** has already been through the one allowlist sanitiser on
 *   the server (`backend/features/automations/sanitize.py`). The trust
 *   boundary is server-side; this is the only place in the app that renders
 *   pushed markup, and it renders what the server already vetted.
 * - **Hidden** is never rendered but its value (default or otherwise) is
 *   still carried in `values` and submitted.
 */

function initialValueFor(field: AutomationField): unknown {
  if (field.type === "checkboxes" && field.multiple) {
    // A default for a multi-checkbox field, if n8n ever sends one, is a
    // single string — split it into the array shape this control edits.
    return typeof field.default === "string" && field.default
      ? field.default.split(",").map((v) => v.trim()).filter(Boolean)
      : [];
  }
  return typeof field.default === "string" ? field.default : "";
}

function initialValues(fields: readonly AutomationField[]): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const field of fields) {
    values[field.name] = initialValueFor(field);
  }
  return values;
}

function isFilled(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "string") return value.trim() !== "";
  return value !== undefined && value !== null && value !== "";
}

/** Reads a `File` into a data URL — the same representation `IconPicker`
 * uses for images, generalised to any file type since a Form Trigger's file
 * field has no declared MIME constraint. */
function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("could not read file"));
    reader.readAsDataURL(file);
  });
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
  onSubmit: (payload: Record<string, unknown>) => void;
}) {
  const fields = useMemo(() => card.fields ?? [], [card.fields]);
  const [values, setValues] = useState<Record<string, unknown>>(() => initialValues(fields));
  const [fileNames, setFileNames] = useState<Record<string, string>>({});
  const [fileError, setFileError] = useState<string | null>(null);

  const missingRequired = fields.some(
    (field) => field.required && !field.hidden && !isFilled(values[field.name]),
  );

  function set(name: string, value: unknown) {
    setValues((previous) => ({ ...previous, [name]: value }));
  }

  function toggleCheckbox(field: AutomationField, option: string, checked: boolean) {
    if (field.multiple) {
      setValues((previous) => {
        const current = Array.isArray(previous[field.name])
          ? (previous[field.name] as string[])
          : [];
        const next = checked
          ? [...current, option]
          : current.filter((v) => v !== option);
        return { ...previous, [field.name]: next };
      });
      return;
    }
    // Not `multiple` but still a checkboxes-typed field — a single-select
    // scalar rendered with checkbox controls (see the docstring above).
    set(field.name, checked ? option : "");
  }

  async function pickFile(field: AutomationField, fileList: FileList | null) {
    const file = fileList?.[0];
    setFileError(null);
    if (!file) {
      set(field.name, "");
      setFileNames((previous) => ({ ...previous, [field.name]: "" }));
      return;
    }
    try {
      const dataUrl = await fileToDataUrl(file);
      set(field.name, dataUrl);
      setFileNames((previous) => ({ ...previous, [field.name]: file.name }));
    } catch {
      setFileError(`Could not read ${file.name}.`);
    }
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

        if (field.type === "radio" && field.options && field.options.length > 0) {
          const options = field.options;
          const current = typeof values[field.name] === "string" ? (values[field.name] as string) : "";
          return (
            <Field key={field.name} label={field.label || field.name} hint={field.required ? undefined : "optional"}>
              {() => (
                <SegmentedControl
                  options={options}
                  value={current}
                  onChange={(next) => set(field.name, next)}
                  labels={Object.fromEntries(options.map((o) => [o, o]))}
                />
              )}
            </Field>
          );
        }

        if (field.type === "checkboxes" && field.options && field.options.length > 0) {
          const options = field.options;
          const selected = field.multiple
            ? Array.isArray(values[field.name])
              ? (values[field.name] as string[])
              : []
            : typeof values[field.name] === "string"
              ? [values[field.name] as string]
              : [];
          return (
            <Field key={field.name} label={field.label || field.name} hint={field.required ? undefined : "optional"}>
              {() => (
                <div className="flex flex-col gap-1.5 border border-line bg-sunken px-3 py-2">
                  {options.map((option) => (
                    <label
                      key={option}
                      className="flex cursor-pointer items-center gap-2 text-label text-ink"
                    >
                      <input
                        type="checkbox"
                        checked={selected.includes(option)}
                        onChange={(event) => toggleCheckbox(field, option, event.target.checked)}
                        className="h-3.5 w-3.5 accent-[var(--ac)]"
                      />
                      {option}
                    </label>
                  ))}
                </div>
              )}
            </Field>
          );
        }

        if (field.type === "file") {
          const fileName = fileNames[field.name];
          return (
            <Field
              key={field.name}
              label={field.label || field.name}
              hint={field.required ? undefined : "optional"}
              error={fileError ?? undefined}
            >
              {(props) => (
                <div className="flex items-center gap-2">
                  <input
                    {...props}
                    type="file"
                    onChange={(event) => void pickFile(field, event.target.files)}
                    className="w-full border border-line bg-sunken px-3 py-2 text-label text-ink file:mr-3 file:border-0 file:bg-[var(--ac-bg)] file:px-2 file:py-1 file:font-mono file:text-micro file:uppercase file:tracking-[0.12em] file:text-[var(--ac)]"
                  />
                  {fileName && (
                    <span className="shrink-0 truncate font-mono text-meta text-ink-faint">
                      {fileName}
                    </span>
                  )}
                </div>
              )}
            </Field>
          );
        }

        return (
          <Field
            key={field.name}
            label={field.label || field.name}
            hint={field.required ? undefined : "optional"}
          >
            {(props) => {
              const stringValue = typeof values[field.name] === "string" ? (values[field.name] as string) : "";

              if (field.type === "textarea") {
                return (
                  <textarea
                    {...props}
                    rows={3}
                    value={stringValue}
                    onChange={(event) => set(field.name, event.target.value)}
                    placeholder={field.placeholder ?? ""}
                    className={`${FIELD_CONTROL} resize-none`}
                  />
                );
              }
              // "dropdown" (and any unrecognised option-bearing type, so a
              // form still renders something usable) falls back to a select.
              if (field.options && field.options.length > 0) {
                return (
                  <select
                    {...props}
                    value={stringValue}
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
                  value={stringValue}
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
