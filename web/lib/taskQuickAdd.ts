/**
 * Todoist-style quick-add parsing for the TASKS panel (§4 General).
 *
 * Turns `review PR p1 #argus tomorrow` into a vault task line the existing
 * Tasks-plugin parser (`backend/tasks/parser.py`) already understands —
 * `review PR #argus 🔺 📅 2026-07-16` — so no backend changes are needed:
 * `POST /api/capture` appends the composed line as-is and the next
 * `/api/agenda` refresh reads priority/due/tags back out of it via regex.
 *
 * Recognized trailing tokens (case-insensitive, matched anywhere, removed
 * from the visible title):
 *   - `p1` / `p2` / `p3`              → 🔺 (highest) / ⏫ (high) / 🔼 (medium)
 *   - `today` / `tomorrow` / ISO date → 📅 YYYY-MM-DD
 * `#project` tags are left inline — the parser already strips them from the
 * displayed text and reads them into `task.tags`.
 */

const PRIORITY_MARK: Record<"1" | "2" | "3", string> = {
  "1": "🔺",
  "2": "⏫",
  "3": "🔼",
};

const PRIORITY_RE = /(?:^|\s)p([123])(?=\s|$)/i;
const DATE_RE = /(?:^|\s)(today|tomorrow|\d{4}-\d{2}-\d{2})(?=\s|$)/i;

function isoDate(offsetDays: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${month}-${day}`;
}

function resolveDate(token: string): string {
  const lower = token.toLowerCase();
  if (lower === "today") return isoDate(0);
  if (lower === "tomorrow") return isoDate(1);
  return token; // already YYYY-MM-DD
}

/** The recognized parts of a quick-add string, before any rendering. */
export interface QuickAddParts {
  /** The title, with the priority and date tokens removed. `#tags` stay inline. */
  text: string;
  /** ISO `YYYY-MM-DD`, or `null` when the input named no date. */
  due: string | null;
  /** `"1" | "2" | "3"`, or `null`. */
  priority: "1" | "2" | "3" | null;
}

/**
 * Split quick-add input into its parts without rendering any of them.
 *
 * Extracted from {@link parseQuickAdd} because the same input now has two
 * destinations with different shapes: the vault wants one markdown line with
 * emoji markers, and an n8n `task.create` workflow wants discrete form fields.
 * Rendering the line and then re-parsing it for the second path would make the
 * emoji vocabulary a wire format between two things that never needed to share
 * one.
 */
export function splitQuickAdd(input: string): QuickAddParts {
  let text = input.trim();
  let priority: "1" | "2" | "3" | null = null;
  let due: string | null = null;

  const priorityMatch = text.match(PRIORITY_RE);
  if (priorityMatch) {
    priority = priorityMatch[1] as "1" | "2" | "3";
    text = (text.slice(0, priorityMatch.index) + text.slice(priorityMatch.index! + priorityMatch[0].length)).trim();
  }

  const dateMatch = text.match(DATE_RE);
  if (dateMatch) {
    due = resolveDate(dateMatch[1]);
    text = (text.slice(0, dateMatch.index) + text.slice(dateMatch.index! + dateMatch[0].length)).trim();
  }

  return { text: text.replace(/\s+/g, " ").trim(), due, priority };
}

/** Parse quick-add input into the exact line text to hand to `/api/capture`. */
export function parseQuickAdd(input: string): string {
  const { text, due, priority } = splitQuickAdd(input);
  const metaParts = [priority ? PRIORITY_MARK[priority] : "", due ? `📅 ${due}` : ""].filter(Boolean);
  return metaParts.length ? `${text} ${metaParts.join(" ")}` : text;
}
