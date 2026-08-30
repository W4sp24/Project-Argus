/**
 * Todoist-style quick-add parsing for the TASKS panel (§4 General).
 *
 * Turns `review PR !! #argus every monday next friday` into a vault task line
 * the existing Tasks-plugin parser (`backend/vault/tasks.py::parse_task_line`)
 * already understands — `review PR #argus ⏫ 🔁 every monday 📅 2026-09-04` —
 * so no backend changes are needed: `POST /api/capture` appends the composed
 * line as-is and the next `/api/agenda` refresh reads priority/due/recurrence/
 * tags back out of it via regex.
 *
 * Recognized tokens (case-insensitive, matched anywhere as whole words, and
 * removed from the visible title unless noted):
 *
 *   - priority   `p1`/`p2`/`p3`, or `!!!`/`!!`/`!` — more marks, more urgent
 *                → 🔺 (highest) / ⏫ (high) / 🔼 (medium)
 *   - due        `today`, `tomorrow`, `2026-09-04`, `friday`/`fri`,
 *                `next friday`, `next week`, `in 3 days`, `in 2 weeks` → 📅
 *   - recurrence `every day`, `every 2 weeks`, `every monday` → 🔁
 *   - time       `3pm`, `3:30pm`, `15:00`, `at 9am` → reported as `time`, and
 *                **left in the title** (see below)
 *
 * Three rules hold this together:
 *
 * **A token this cannot parse stays in the title.** `every 2 mondays` is not
 * something `advance_date` can roll forward, so it is not matched at all and
 * the words remain visible, rather than being eaten into a 🔁 rule the vault
 * would silently never repeat. The grammar here is deliberately the same
 * grammar `RECUR_RULE_RE` accepts, no wider.
 *
 * **A time is found but not stripped.** The Tasks line has no marker for a
 * time of day — 📅 and ⏳ are dates — so removing `3pm` from the title would
 * be the only thing here that loses what the user typed. It is reported in
 * `time` for the preview chip and left where it was written.
 *
 * **Weeks start on Monday.** `friday` is the next Friday (1–7 days out);
 * `next friday` is the Friday of *next* week; `next week` is next Monday. A
 * rule you can compute in your head beats one that is merely clever.
 */

export type Priority = "1" | "2" | "3";

const PRIORITY_MARK: Record<Priority, string> = {
  "1": "🔺",
  "2": "⏫",
  "3": "🔼",
};

/** Weekday words → `Date.getDay()` (0 = Sunday). */
const WEEKDAYS: Record<string, number> = {
  sun: 0,
  sunday: 0,
  mon: 1,
  monday: 1,
  tue: 2,
  tues: 2,
  tuesday: 2,
  wed: 3,
  weds: 3,
  wednesday: 3,
  thu: 4,
  thur: 4,
  thurs: 4,
  thursday: 4,
  fri: 5,
  friday: 5,
  sat: 6,
  saturday: 6,
};

/** Longest first, so `monday` is preferred over `mon` at the same position. */
const WEEKDAY_WORDS = Object.keys(WEEKDAYS)
  .sort((a, b) => b.length - a.length)
  .join("|");

/** The full names `RECUR_RULE_RE` accepts — an abbreviation is normalised to
 *  one of these before it is written, or the backend would not match it. */
const WEEKDAY_FULL = [
  "sunday",
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
];

/**
 * A recurrence rule, restricted to what `backend/vault/tasks.py::advance_date`
 * can actually advance. `every 2 mondays` is intentionally absent: the backend
 * returns None for it, so matching it here would write a 🔁 that never repeats.
 */
const RECUR_RE = new RegExp(
  `(?:^|\\s)every\\s+(?:(\\d+)\\s+)?(day|week|month|year|${WEEKDAY_WORDS})s?(?=\\s|$)`,
  "i",
);

/** One date token. Leftmost match wins, as the single-pattern version did. */
const DATE_RE = new RegExp(
  "(?:^|\\s)(" +
    [
      "\\d{4}-\\d{2}-\\d{2}",
      "today",
      "tomorrow",
      "next\\s+week",
      `next\\s+(?:${WEEKDAY_WORDS})`,
      WEEKDAY_WORDS,
      "in\\s+\\d+\\s+(?:days?|weeks?)",
    ].join("|") +
    ")(?=\\s|$)",
  "i",
);

/** `3pm`, `3:30 pm`, `at 9am`, `15:00`. Found, never removed — see the module
 *  docstring. `\d{4}-\d{2}-\d{2}` cannot reach here: it carries no colon and
 *  no meridiem. */
const TIME_RE =
  /(?:^|\s)(?:at\s+)?(?:(\d{1,2})(?::([0-5]\d))?\s*(am|pm)|([01]?\d|2[0-3]):([0-5]\d))(?=\s|$)/i;

/** `p1`–`p3`, or a standalone run of 1–3 `!`. Standalone matters: "Ship it!"
 *  is an exclamation, not a priority, and only a `!` with whitespace on both
 *  sides is the token. */
const PRIORITY_RE = /(?:^|\s)(?:p([123])|(!{1,3}))(?=\s|$)/i;

function isoDate(offsetDays: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${month}-${day}`;
}

/** Days from today to the start of next week (Monday). */
function daysToNextMonday(): number {
  const today = new Date().getDay();
  return (8 - today) % 7 || 7;
}

/** Days from today to the next `weekday`, always at least one — "friday" on a
 *  Friday means the one coming, not the one you are standing in. */
function daysToWeekday(weekday: number): number {
  return (weekday - new Date().getDay() + 7) % 7 || 7;
}

/** Where a weekday sits in a Monday-first week. */
function weekIndex(weekday: number): number {
  return (weekday + 6) % 7;
}

function resolveDate(token: string): string {
  const lower = token.toLowerCase().replace(/\s+/g, " ").trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(lower)) return lower;
  if (lower === "today") return isoDate(0);
  if (lower === "tomorrow") return isoDate(1);
  if (lower === "next week") return isoDate(daysToNextMonday());

  const relative = lower.match(/^in (\d+) (days?|weeks?)$/);
  if (relative) {
    return isoDate(Number(relative[1]) * (relative[2].startsWith("week") ? 7 : 1));
  }

  const next = lower.match(/^next (.+)$/);
  if (next) {
    return isoDate(daysToNextMonday() + weekIndex(WEEKDAYS[next[1]]));
  }
  return isoDate(daysToWeekday(WEEKDAYS[lower]));
}

/** `every 2 week` / `every mon` as the backend spells it: `every 2 weeks`,
 *  `every monday`. `RECUR_RULE_RE` is anchored, so a rule it cannot match
 *  would tick off but never repeat. */
function normalizeRule(count: string | undefined, unit: string): string {
  const lower = unit.toLowerCase();
  const word = lower in WEEKDAYS ? WEEKDAY_FULL[WEEKDAYS[lower]] : lower;
  if (!count) return `every ${word}`;
  return `every ${count} ${Number(count) === 1 ? word : `${word}s`}`;
}

/** Remove one whole match from the input, leaving the spacing tidy. */
function cut(text: string, match: RegExpMatchArray): string {
  return (text.slice(0, match.index) + " " + text.slice(match.index! + match[0].length)).trim();
}

/** The recognized parts of a quick-add string, before any rendering. */
export interface QuickAddParts {
  /** The title, with the recognized tokens removed. `#tags` — and any time of
   *  day, which the Tasks line cannot carry — stay inline. */
  text: string;
  /** ISO `YYYY-MM-DD`, or `null` when the input named no date. */
  due: string | null;
  /** 24-hour `HH:MM`, or `null`. Still present in `text` too: this is what the
   *  input said, not a field the vault line has anywhere to put. */
  time: string | null;
  /** `"1" | "2" | "3"`, or `null`. */
  priority: Priority | null;
  /** A Tasks-plugin rule (`every 2 weeks`), normalised to the spelling
   *  `advance_date` accepts, or `null`. */
  recurrence: string | null;
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
  let recurrence: string | null = null;
  let due: string | null = null;
  let priority: Priority | null = null;

  // Recurrence first: `every monday` contains a weekday, and the date matcher
  // would otherwise cut the day out from under the rule and leave `every`
  // stranded in the title.
  const recurMatch = text.match(RECUR_RE);
  if (recurMatch && !(recurMatch[1] && recurMatch[2].toLowerCase() in WEEKDAYS)) {
    recurrence = normalizeRule(recurMatch[1], recurMatch[2]);
    text = cut(text, recurMatch);
  }

  const dateMatch = text.match(DATE_RE);
  if (dateMatch) {
    due = resolveDate(dateMatch[1]);
    text = cut(text, dateMatch);
  }

  const priorityMatch = text.match(PRIORITY_RE);
  if (priorityMatch) {
    priority = (priorityMatch[1] ?? String(4 - priorityMatch[2].length)) as Priority;
    text = cut(text, priorityMatch);
  }

  // Read last and never cut: the time stays in the title, so removing it from
  // the string the other patterns see would change nothing but the risk.
  const timeMatch = text.match(TIME_RE);

  return {
    text: text.replace(/\s+/g, " ").trim(),
    due,
    time: timeMatch ? normalizeTime(timeMatch) : null,
    priority,
    recurrence,
  };
}

function normalizeTime(match: RegExpMatchArray): string {
  const [, hour12, minutes12, meridiem, hour24, minutes24] = match;
  if (hour24) return `${hour24.padStart(2, "0")}:${minutes24}`;
  let hour = Number(hour12) % 12;
  if (meridiem.toLowerCase() === "pm") hour += 12;
  return `${String(hour).padStart(2, "0")}:${minutes12 ?? "00"}`;
}

/**
 * Parse quick-add input into the exact line text to hand to `/api/capture`.
 *
 * Marker order follows the Tasks plugin's own: description, priority, 🔁, 📅.
 * It is load-bearing for the rule, which `RECUR_RE` reads as everything up to
 * the next marker — a rule written after the date would run to end of line and
 * swallow whatever a later feature appends.
 */
export function parseQuickAdd(input: string): string {
  const { text, due, priority, recurrence } = splitQuickAdd(input);
  const metaParts = [
    priority ? PRIORITY_MARK[priority] : "",
    recurrence ? `🔁 ${recurrence}` : "",
    due ? `📅 ${due}` : "",
  ].filter(Boolean);
  return metaParts.length ? `${text} ${metaParts.join(" ")}` : text;
}
