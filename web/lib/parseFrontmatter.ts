/**
 * Minimal client-side markdown parsing for PROJECTS.VAULT (§4 Code): pulls
 * `status:`/`tags:` frontmatter, checkbox progress, and the first open task
 * out of a note's raw content. No YAML/markdown dependency — the frontmatter
 * shape written by the vault template is simple enough for a small regex
 * parser, and pulling in a library here would cost real bytes against the
 * 135kB per-route budget (§10) for a feature this narrow.
 */

export interface ParsedProject {
  status: string | null;
  tags: string[];
  /** First non-empty line of body text after frontmatter + any leading H1. */
  description: string | null;
  doneCount: number;
  totalCount: number;
  /** Text of the first unchecked `- [ ]` task, if any. */
  nextTask: string | null;
}

const FRONTMATTER_RE = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/;
const CHECKBOX_RE = /^\s*[-*]\s+\[([ xX])\]\s+(.*)$/gm;

function stripQuotes(value: string): string {
  const trimmed = value.trim();
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

function parseTags(frontmatter: string): string[] {
  const inline = frontmatter.match(/^tags:\s*\[(.*)\]\s*$/m);
  if (inline) {
    return inline[1]
      .split(",")
      .map((tag) => stripQuotes(tag))
      .filter(Boolean);
  }
  const block = frontmatter.match(/^tags:\s*\n((?:[ \t]*-\s*.+\n?)+)/m);
  if (block) {
    return [...block[1].matchAll(/-\s*(.+)/g)].map((match) => stripQuotes(match[1]));
  }
  return [];
}

export function parseProjectNote(content: string): ParsedProject {
  const match = content.match(FRONTMATTER_RE);
  const frontmatter = match?.[1] ?? "";
  const body = match ? content.slice(match[0].length) : content;

  const statusMatch = frontmatter.match(/^status:\s*(.+)$/m);
  const status = statusMatch ? stripQuotes(statusMatch[1]) : null;
  const tags = parseTags(frontmatter);

  const description =
    body
      .split("\n")
      .map((line) => line.trim())
      .find((line) => line.length > 0 && !line.startsWith("#")) ?? null;

  let doneCount = 0;
  let totalCount = 0;
  let nextTask: string | null = null;
  for (const checkbox of body.matchAll(CHECKBOX_RE)) {
    totalCount += 1;
    const checked = checkbox[1].toLowerCase() === "x";
    if (checked) doneCount += 1;
    else nextTask ??= checkbox[2].trim();
  }

  return { status, tags, description, doneCount, totalCount, nextTask };
}
