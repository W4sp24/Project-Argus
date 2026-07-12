---
description: Write a narrative of this session into the vault's dev journal (90-Meta/)
---

Log this session to the Obsidian vault's dev journal. Follow these rules exactly:

## Where

- Vault root: `$ARGUS_VAULT` if set, else `C:\Users\ethan\Documents\Scientia`.
- Project slug = lowercased basename of the current working directory.
- Session note: `90-Meta/sessions/<yyyy>/<yyyy-MM-dd>-<slug>.md` (today's date).
  If it does not exist yet, create it with frontmatter
  (`type: dev-session`, `project`, `date`, `tags: [dev-journal]`) and an H1.
- Project note: `90-Meta/projects/<slug>.md`. If it does not exist, create it from
  `90-Meta/_templates/project.md`.

## What to write

1. Append to the session note a `## Narrative — HH:mm` section covering, briefly and
   concretely: what was done this session, decisions made (and why), problems hit and
   how they were resolved, and next steps. Write for future-you reading in Obsidian:
   prose over fragments, `[[wikilinks]]` where they help.
2. Update the project note: refresh **Current state**, add any new **Key decisions**
   (newest first), tick or add **Open threads**, and prepend a
   `[[<yyyy-MM-dd>-<slug>]]` link under **Recent sessions**.

## How

- Prefer the Obsidian MCP tools (`obsidian` server) if connected; otherwise write the
  files directly with your file tools. Both are acceptable (the stub hook has already
  guaranteed the deterministic record).
- NEVER write outside `90-Meta/`. Never touch a note tagged `#no-ai`.
- When done, report the vault-relative path(s) you wrote, e.g.
  `Logged -> 90-Meta/sessions/2026/2026-07-12-project-argus.md`.
