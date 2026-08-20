# Setting up Argus

This guide assumes no programming knowledge. It takes about 20 minutes, most
of which is waiting for downloads.

Argus is a personal assistant that reads your own notes. It keeps everything in
a folder on your computer as ordinary text files, so you can open them in
[Obsidian](https://obsidian.md), in Notepad, or anywhere else — and if you ever
delete Argus, your notes are still just sitting there.

---

## Step 1 — Install Argus

**If you were given an installer** (`Argus-Setup.exe`), double-click it.

Windows will probably show a blue box saying *"Windows protected your PC"*.
That appears because the installer isn't signed with a paid certificate, not
because anything is wrong. Click **More info**, then **Run anyway**.

**If you were given the source code instead**, see
[Running from source](#running-from-source) at the end.

## Step 2 — Install Git

Argus takes a snapshot of your notes before it changes anything, so any edit
can be undone. It uses a free tool called **Git** to do that, and it can't work
without it.

Download it from **<https://git-scm.com/download/win>** and click Next through
the installer — every default is fine.

This is the only thing Argus genuinely requires.

## Step 3 — Choose how the AI runs

Argus needs an AI model to answer questions about your notes, plan your day,
and build practice exams. **You need one of these three. You do not need all
three,** and you can change your mind later.

| | Cost | Speed | Your notes |
|---|---|---|---|
| **A model on your own PC** | Free | Depends on your PC | Never leave your computer |
| **A hosted API key** | A few dollars a month | Fast on any PC | Excerpts sent to that company |
| **Claude Code** | Uses a Claude subscription | Fast | Excerpts sent to Anthropic |

### Which should you pick?

- **A newer PC with a graphics card, and you care about privacy?**
  Run models on your own PC. It's free and nothing leaves your machine.
- **An older or lighter laptop?** Use a hosted API key. Running AI locally
  needs *more* computing power, not less — a hosted key is what makes Argus
  work well on a modest machine.
- **Already paying for Claude?** Use Claude Code and skip the extra bill.

<details>
<summary><strong>Option A — a model on your own PC (free)</strong></summary>

1. Download **Ollama** from <https://ollama.com/download> and install it.
   It runs quietly in the background; you never need to open it.
2. Open Argus, click **System** in the sidebar.
3. Find the **LOCAL.MODELS** panel. Argus has already checked your computer and
   labelled each model:
   - **FITS** — runs well here.
   - **SLOW** — will work, but replies take a while.
   - **TOO BIG** — won't run on this machine.
   One model is marked **BEST FIT**. That's Argus's recommendation.
4. Click **DOWNLOAD** next to it and wait. These files are large (2–9 GB), so
   this is a good moment to make tea.
5. When it says **✓ added**, you're done — it's ready to use.

</details>

<details>
<summary><strong>Option B — a hosted API key (works on any PC)</strong></summary>

1. Make an account with any of these and create an API key. **Groq** has a
   generous free tier and is a good place to start.
   - Groq — <https://console.groq.com/keys>
   - DeepSeek — <https://platform.deepseek.com/api_keys>
   - Together — <https://api.together.xyz/settings/api-keys>
   - OpenRouter — <https://openrouter.ai/keys>
   - Fireworks — <https://fireworks.ai/account/api-keys>
   - Google (for Gemini) — <https://aistudio.google.com/apikey>
   - Anthropic (for Claude) — <https://console.anthropic.com/settings/keys>
2. Copy the key. It usually starts with `gsk_`, `sk-`, or similar.
   **Treat it like a password.**
3. In Argus, go to **System → MODELS** and click **+ ADD MODEL**.
4. Choose **Hosted API** — or **Google Gemini** / **Anthropic API key** if you
   picked one of those, which need no address, only the key.
5. Click your provider's name to fill in the address automatically, then paste
   your key into the **api key** box.
6. Click **TEST CONNECTION**. Argus checks it can reach them and lists the
   models they offer.
7. Pick a model from the dropdown, click **TEST CONNECTION** once more so it
   goes green, give it a short name like `groq-llama`, and click **SAVE MODEL**.

On DeepSeek, pick `deepseek-chat`. `deepseek-reasoner` cannot call tools, and
Argus refuses a model that cannot — every answer about your vault is grounded in
a tool call, so a model without them would look like it was working while making
things up.

Your key is stored in Windows' own password manager, not in any file Argus
writes.

</details>

<details>
<summary><strong>Option C — Claude Code (uses your Claude subscription)</strong></summary>

1. Install Claude Code from <https://claude.com/code>.
2. Open a terminal, type `claude`, and sign in when prompted. You only do this
   once.
3. In Argus, go to **System → MODELS**. `claude-sonnet-5` is already listed and
   ready.

</details>

## Step 4 — Point Argus at your notes

The first time Argus opens, it asks where your notes should live.

- **Already use Obsidian?** Choose *Use an existing vault* and pick your vault
  folder. Argus never deletes your notes — it only adds folders and files it
  needs.
- **New to this?** Choose *Create a new vault*, pick a location (Documents is
  fine), and give it a name. Argus builds a starter structure for you.

Then click through the health check. Green and amber are both fine — amber
just means an optional extra isn't set up.

## Step 5 — Ask it something

Click **Chat** and ask a question about your notes. Every answer links back to
the note it came from, and if the answer isn't in your notes, Argus says so
rather than making something up.

---

## Choosing a model day to day

The button in the top-right of **Chat** and **Study** switches models. Each one
is tagged:

- **LOCAL** — runs on this computer; your notes never leave it.
- **HOSTED** — note excerpts are sent to that provider.

A sensible pattern is a small local model for everyday questions and a hosted
one for heavy work like generating a practice exam.

To change which model is used by default, hover its row in **System → MODELS**
and click **MAKE DEFAULT**.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| *"could not reach that endpoint"* when testing | Ollama isn't running. Open it from the Start menu and try again. |
| *"check the API key"* | The key was copied wrong, or has been revoked. Make a new one. |
| *"did not pass the check: … never called the test tool"* | That model can't use tools, so it couldn't cite your notes. Pick a different one — everything in LOCAL.MODELS works. |
| Replies are very slow | A local model too big for your PC. Try one marked **FITS**, or add a hosted key. |
| Chat says the backend is offline | Close Argus completely and reopen it. |
| Anything else | **System → DOCTOR → RUN AGAIN** lists what's healthy and what isn't. |

---

## Optional extras

None of these are needed for Argus to work.

- **Google Calendar / Todoist** — under **System → INTEGRATIONS**, so the day
  planner can see your real schedule.
- **Use your notes from a coding assistant** — if you use Claude Code, Codex
  CLI, or Gemini CLI, they can read your vault while you work on other
  projects. See [Connect your coding agent](../README.md#connect-your-coding-agent).
  Requires the source install.

---

## Using an existing vault with different folder names

Argus organizes new vaults with nine top-level folders (`00-Inbox`,
`10-Daily`, `15-Courses`, `20-Projects`, `30-Areas`, `40-People`,
`50-Reference`, `90-Meta`, `99-Private`). If you already have an Obsidian
vault with your own names for these — or no equivalent for some of them —
you don't need to rename anything. Add the matching keys to your env file
(the `.env` next to a source checkout, or the config file the desktop app
already writes `VAULT_PATH` into) and Argus will read and write there instead:

```
VAULT_INBOX_DIR=Quick Notes
VAULT_DAILY_DIR=Journal
VAULT_COURSES_DIR=School
VAULT_PROJECTS_DIR=Projects
VAULT_AREAS_DIR=Areas
VAULT_PEOPLE_DIR=People
VAULT_REFERENCE_DIR=Reference
VAULT_JOURNAL_DIR=Argus Journal
VAULT_PRIVATE_DIR=Do Not Index
VAULT_INDEXABLE_SUFFIXES=.md,.pdf,.pptx,.docx
```

Every key is optional and independent — set only the ones you need. Each
value must be a plain folder name (no `/`, no `..`), and all nine must be
different from each other; **`VAULT_PRIVATE_DIR` in particular is the folder
Argus never indexes and never sends to any model**, so double-check it before
saving. `argus doctor` (or **System → DOCTOR**) reports which configured
folders don't exist in your vault yet — that's expected for a folder you
haven't used, not a broken install.

Two things this does **not** do: it does not move any of your existing files
(create the folder yourself, or let Argus create it on first use), and if you
change these keys on a vault Argus has already indexed, run `argus reindex`
(or **System → REINDEX**) afterward so search and chat see the new locations.

---

## Running from source

For anyone comfortable with a terminal. Needs Python 3.12+, Node 18+, and git.

```bash
git clone <this-repo> && cd Project-Argus

python -m venv .venv
.venv/Scripts/activate          # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pip install -e ".[rag]"         # the notes-search stack — needed for chat

argus init ./my-vault           # or set VAULT_PATH in .env to an existing vault
cd web && npm install && cd ..

argus web                       # dashboard on :3000, API on :8000
```

Then open <http://localhost:3000>, go to **System**, and follow Step 3 above to
add a model. `argus doctor` checks the install at any time.

---

## What Argus sends where

- Indexing and searching your notes happens entirely on your computer, always.
- Anything in your private folder (`99-Private/` unless you've renamed it —
  see [above](#using-an-existing-vault-with-different-folder-names)), and any
  note tagged `#no-ai`, is never indexed and never sent to any model.
- With a **LOCAL** model, nothing at all leaves your computer.
- With a **HOSTED** model, only the excerpts relevant to your question are sent
  to that provider — never your whole vault.
- API keys are stored in your operating system's password manager, never in a
  file and never in your notes.
