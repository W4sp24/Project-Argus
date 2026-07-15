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

/** Parse quick-add input into the exact line text to hand to `/api/capture`. */
export function parseQuickAdd(input: string): string {
  let text = input.trim();
  let priorityMark = "";
  let dueMeta = "";

  const priorityMatch = text.match(PRIORITY_RE);
  if (priorityMatch) {
    priorityMark = PRIORITY_MARK[priorityMatch[1] as "1" | "2" | "3"];
    text = (text.slice(0, priorityMatch.index) + text.slice(priorityMatch.index! + priorityMatch[0].length)).trim();
  }

  const dateMatch = text.match(DATE_RE);
  if (dateMatch) {
    dueMeta = `📅 ${resolveDate(dateMatch[1])}`;
    text = (text.slice(0, dateMatch.index) + text.slice(dateMatch.index! + dateMatch[0].length)).trim();
  }

  text = text.replace(/\s+/g, " ").trim();
  const metaParts = [priorityMark, dueMeta].filter(Boolean);
  return metaParts.length ? `${text} ${metaParts.join(" ")}` : text;
}
