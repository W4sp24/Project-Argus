# Calendar and tasks

Argus keeps its own calendar and reads your Obsidian tasks. Both work the
moment you open the app — there is nothing to connect, no account to make, and
no OAuth client to register.

To see a calendar you already keep elsewhere (Google, Outlook, iCloud, a
university timetable), you paste **one URL**. That is the whole setup.

---

## 1. Your own events

`/calendar` shows a month grid and the selected day. Click a day to add an
event; click an event to edit or delete it. Events created here are yours to
edit, live in `<vault>/.argus/argus.db`, and appear on the dashboard's
`PLANNER.TIMELINE`, in the 07:00 briefing, and in Insights' meeting-hours.

Recurring events use standard iCalendar rules (`FREQ=WEEKLY;BYDAY=MO,WE`).
Deleting one occurrence of a series cancels just that day and leaves the rest
of the series alone; deleting the series removes all of it.

### Getting your events out

`GET /api/calendar/export.ics` downloads every local event as a standard
`.ics` file that any calendar app will import.

This matters because local events live in SQLite, not in Markdown: they are
**not** visible in Obsidian and they do **not** ride the vault's git
snapshots the way your notes do. The export is the guarantee that they are
still yours to take elsewhere.

---

## 2. Subscribing to a calendar you already have

System → Integrations → **Connect calendar**, or `/calendar` → **+ CALENDAR**.

You need a name and the calendar's **secret iCal URL**. Where to find it:

| Service | Where |
|---|---|
| Google Calendar | Settings → *your calendar* → **Integrate calendar** → *Secret address in iCal format* |
| Outlook / Microsoft 365 | Settings → Calendar → Shared calendars → **Publish a calendar** → ICS link |
| Apple iCloud | Calendar app → right-click a calendar → **Share Calendar** → Public Calendar |
| University timetables | usually an "Export"/"Subscribe" link on the timetable page |

`webcal://` links work — paste them as-is; Argus converts them.

Argus fetches the feed before saving anything, so a wrong URL fails in the
dialog rather than turning into an empty calendar you have to debug later. It
then re-reads every subscribed feed **once an hour**.

### Two things to know

**Subscriptions are read-only.** That is the protocol, not a limitation Argus
chose: an `.ics` feed is a published file, with no way to write back. Events
from a feed render with a read-only marker and Argus refuses to edit them
rather than accepting a change and silently dropping it at the next sync. To
change one, change it in the calendar that publishes it.

**The URL is a password.** Google's "secret address" carries the secret in the
URL itself, so anyone holding it can read that calendar. Argus stores it in
your OS keyring, never in the database, and never shows it again — the
integrations list shows only the host. If you ever paste one somewhere public,
reset it from the same settings page you copied it from.

If a feed stops working — usually because its secret address was reset — the
calendar keeps the events it already had and shows the error, rather than
going quietly empty. A 404 says *there is no calendar at that address*, not
*you have no events*.

---

## 3. Tasks

Tasks live in your vault as ordinary Obsidian
[Tasks](https://publish.obsidian.md/tasks/) checkboxes, so Argus and Obsidian
are always looking at the same file:

```markdown
- [ ] Submit lab report 📅 2026-09-04 ⏫ #cs301
- [ ] Water the plants 🔁 every week 📅 2026-09-01
```

Argus reads due dates (`📅`), scheduled dates (`⏳`), priority (`🔺⏫🔼🔽`),
tags, and recurrence (`🔁`).

**Completing a recurring task creates the next one**, the way the Tasks plugin
does — tick "Water the plants" and next week's instance appears above it with
the date rolled forward. Rules understood: `every day`, `every N days`,
`every week`, `every N weeks`, `every month`, `every N months`, `every year`,
and `every <weekday>`. Anything Argus does not recognise still ticks off
normally; it just does not repeat.

---

## 4. What else can put events on your calendar

Local events are **merged** with any other source, never replaced by one.
Connecting something else adds to your calendar; it never hides what you put
there yourself.

| Source | Setup | Writable |
|---|---|---|
| Argus's own calendar | none | yes |
| A subscribed `.ics` feed | paste one URL | no |
| Google Calendar connector | Cloud Console → OAuth client → consent | no |
| An n8n calendar workflow | run n8n, install the template | via the workflow |

The last two predate this feature and still work unchanged. Most people want
one of the first two.

Approving a schedule block from `/review` writes to Google when the connector
is connected, and to your own calendar otherwise. It used to fail outright
without Google.

---

## 5. Troubleshooting

**A subscribed calendar shows an error.** Read it — the message names the
cause. `404` almost always means the secret address was reset; copy the
current one. `401`/`403` means the feed is not public.

**Events are missing from a feed.** Some feeds publish only a limited window
(Google defaults to a few weeks either side). Argus shows what the feed
contains.

**A recurring event appears once.** Check the rule on the event; an
unparseable rule degrades to a single event rather than failing.

**Nothing syncs.** Subscriptions refresh hourly. Use **SYNC NOW** on the
calendar in System → Integrations to force it.
