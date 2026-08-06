# Automations (n8n)

Argus can discover workflows in your own n8n instances, render them as forms,
fire them, and display what they push back. It works in both directions:

- **Outbound** — Argus calls n8n to find and run workflows.
- **Inbound** — workflows call Argus to capture notes and tasks, and to push
  dashboard widgets.

The two directions use **two different credentials flowing opposite ways**, and
mixing them up is the most common setup mistake. Argus labels every step with
its direction for that reason.

| Credential | Direction | Where it lives |
|---|---|---|
| n8n API key | `ARGUS → N8N` | Argus's OS keyring |
| Argus bearer token | `N8N → ARGUS` | Argus's OS keyring; you paste it into n8n once |

---

## 1. Connect your n8n instances (outbound)

System → Integrations → **Connect n8n**, or `/automations` → **+ ADD INSTANCE**.

You need a name, the base URL (e.g. `http://localhost:5678`) and an n8n API key
(n8n → Settings → n8n API → Create an API key).

### More than one instance

You can register several — typically a local one for anything that touches the
vault, and an always-on remote one for schedules that must keep running when
your laptop is shut. Each instance carries its own API key **and its own
inbound bearer token**, so revoking one never silences the others.

Everything fed by an automation names its origin: widgets on the dashboard,
rows in the command palette, entries in the ACTIVE list and lines in the
ACTIVITY log all carry an instance chip, and `/automations` filters by
instance. The chip is hidden while only one instance is registered, because a
label that always reads the same is noise.

**The same workflow name on two instances is two automations, not one.** n8n
workflow ids are only unique within a single instance, so Argus keys its cache
and its widgets on `(instance, id)` and `(instance, slug)`. Two instances can
push `weather.now` and you get two widgets, not a fight over one.

Argus tests the connection before saving anything, and shows you **how many
workflows it found tagged `argus`** rather than a green tick — the count is the
proof that the key works and that discovery will find something.

> **If the test fails with "the public API appears to be disabled"**, your n8n
> is a Community build with the public API turned off. Enable it in n8n's
> settings; Argus cannot work around it.

### Tagging is the registration

Argus shows workflows tagged **`argus`** and nothing else. Tag a workflow in
n8n and it appears on the next refresh; untag it and the card disappears.
Nothing is registered a second time inside Argus, so connecting an instance
does not dump forty unrelated internal workflows onto your dashboard.

Three further tags change behaviour:

| Tag | Effect |
|---|---|
| `argus:async` | Argus does not wait for the result. It issues a run id and the workflow pushes its result back later. Use this for anything multi-step or LLM-driven — those routinely exceed any sane synchronous timeout. |
| `argus:confirm` | The card asks before firing. Use it for anything that sends, deletes, or spends. |

---

## 2. The one setting that silently breaks feedback

**This is the most important thing on this page.**

An n8n Webhook node's *Respond* setting defaults to **Immediately**, which
returns HTTP 200 *before the workflow has done anything*. Left alone, **every
action reports success no matter what actually happened** — the email that
bounced, the API that rejected the request, the node that errored.

Argus cannot detect this. On the wire, "responded instantly because it
succeeded quickly" and "responded instantly because it did not wait" are the
same response.

So set it explicitly:

- **Webhook node** → *Respond*: `Using Respond to Webhook node`
- **Form Trigger** → *Respond When*: `Workflow Finishes`

Every template Argus ships already does this. If you build your own workflow,
this is the setting to check first when a run says it worked and nothing
happened.

---

## 3. What a workflow returns decides what you see

You configure nothing extra. The **shape of the response** picks the mode:

| Workflow returns | Card shows |
|---|---|
| nothing / an empty 200 | `FIRED 12:04` |
| `{"ok": true, "message": "Email sent to 3 recipients"}` | ✓ with the message |
| `{"ok": false, "message": "Gmail: invalid recipient"}` | ✗ with the reason |
| `{"widget": "metric", ...}` | the widget, rendered inline |

Runs are synchronous with a **30-second bound**. Past that the card reports
`TIMED OUT` and links to the live execution in n8n — **the workflow is not
cancelled**, because n8n owns it. Tag it `argus:async` to move it off the
waiting path entirely.

---

## 4. The inbound surface

Workflows push data back to `/api/external/*`, which runs on **its own port**
(default `8787`), bound to loopback, behind a bearer token. It is off until you
enable it.

```
ARGUS_EXTERNAL_ENABLED=true
ARGUS_EXTERNAL_PORT=8787
ARGUS_EXTERNAL_BASE_URL=https://your-tunnel-hostname
```

`ARGUS_EXTERNAL_BASE_URL` is the public URL your tunnel forwards **from**.
Argus substitutes it into installed templates, and refuses to install a
template while it is empty — a workflow posting to nowhere fails silently
hours later, which is worse than refusing now.

### Reaching Argus from outside

Argus does not manage a tunnel for you. Point any of cloudflared, Tailscale
Funnel, or ngrok at `127.0.0.1:8787` and set the resulting hostname as
`ARGUS_EXTERNAL_BASE_URL`.

> **Prefer a stable hostname.** A Cloudflare *quick tunnel* gets a new
> `*.trycloudflare.com` name on every restart, and that URL is baked into your
> workflows — so a restart silently breaks all of them. They keep running and
> keep failing to reach anything. Use a named tunnel for anything you rely on.

### The endpoints

| Endpoint | Purpose |
|---|---|
| `POST /api/external/capture` | `{title?, body, tags?}` → a note |
| `POST /api/external/tasks` | `{text, due?, priority?, tags?}` → a task |
| `POST /api/external/widget/{slug}` | push a dashboard widget |
| `GET /api/external/agenda?date=` | today's agenda |
| `GET /api/external/tasks?status=open` | open tasks |
| `GET /api/external/briefing?date=` | a generated briefing |
| `GET /api/external/ping` | 204, no auth — tunnel liveness only |

**Deliberately absent: agent invocation and action triggers.** A workflow
cannot ask Argus a question grounded in your vault. It can `GET` the agenda and
feed that to its own LLM node, but it cannot do retrieval over your notes. That
keeps model spend off a public URL — and it is the main thing this design
cannot grow into without reopening the decision.

Everything on this surface is limited to **60 requests/minute** and a **256 KB**
body, and every read applies the same privacy rules as the rest of Argus:
`99-Private/` and anything tagged `#no-ai` never appear in a response.

---

## 5. Widgets

A pushed payload names its own renderer. Unknown kinds are rejected **at push
time** with a message naming the valid ones, so a typo fails loudly instead of
rendering as an empty box hours later.

| Kind | Payload |
|---|---|
| `metric` | `label`, `value`, `sub?` |
| `list` | `items[]` of `{text, sub?, href?}` |
| `table` | `columns[]`, `rows[][]` |
| `timeline` | `entries[]` of `{at, text, sub?}` |
| `text` | `body` (markdown) |
| `chart` | `kind: line\|bar`, `series[]` of `{label, points[][]}` |

```http
POST /api/external/widget/weather
Authorization: Bearer <token>

{ "widget": "metric",
  "title": "WEATHER",
  "label": "Manila",
  "value": "31°C",
  "sub": "feels like 38° · 70% humidity",
  "expected_interval_seconds": 1800 }
```

### `expected_interval_seconds` is the important field

Declare how often the workflow pushes. Past **2.5×** that interval, the widget
goes **STALE** — dimmed, labelled with its true age, still showing the last good
data.

This matters more than it looks. A push model fails *silently*: if a workflow
breaks, nobody calls anything and the panel just shows yesterday's data forever.
Without a declared cadence Argus cannot tell "quiet because nothing happened"
from "quiet because it died".

Four states, and **three of them are not failures**:

| State | Meaning |
|---|---|
| `LIVE` | fresh within its interval |
| `STALE` | past 2.5× the interval — old data, labelled as old |
| `EMPTY` | pushed fine, genuinely zero items |
| `WAITING` | installed, nothing has arrived yet |

`WAITING` links straight into n8n, because the two likely causes are a workflow
that was never activated and a credential that was never granted — both fixed
there, not here.

---

## 6. Templates

`/automations` → GALLERY installs a bundled workflow into your n8n with the
`argus` tag, the callback URL and the token already filled in, then opens n8n at
that workflow.

**Granting the credential is the one manual step, by design.** Argus does not
create your Google or Todoist credential: OAuth needs a browser round trip, and
doing it for API-key types would mean handing Argus the secret again — which is
the thing this whole arrangement exists to avoid. After migration Argus's
keyring holds one n8n API key and one bearer token **per registered instance**,
and no third-party secrets at all.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Every run reports success, nothing happens | *Respond: Immediately*. See §2. |
| Cards show `DISCONNECTED` | Argus cannot reach n8n. Cards still render from the last known schema; RUN is disabled. |
| A workflow does not appear | It is not tagged `argus`, or it has not been refreshed yet. |
| `WAITING` forever | The workflow was never activated, or its credential was never granted. Open it in n8n. |
| A panel goes `STALE` | The workflow stopped pushing. Check its executions in n8n. |
| Push returns 422 | The payload's `widget` kind is unknown or a field is malformed. The message names it. The previous good payload keeps rendering. |
| Push returns 401 | Wrong or rotated bearer token. Tokens are per instance — check you pasted the one issued for *that* instance. Re-issue it and update the n8n credential. |
| Push returns 413 | Body over 256 KB. |
| Template install returns 409 | `ARGUS_EXTERNAL_BASE_URL` is not set, or you have several instances registered and used the unscoped route — install from the instance's own card. |
| Run returns 409 "ambiguous" | Two instances have a workflow with the same n8n id. Run it from `/automations`, which knows which instance you meant. |
| Instances show `UNREACHABLE` but n8n is up | Argus could not read the stored API key. A locked or unavailable OS keyring reports this rather than claiming the key is missing — run `argus doctor`. |
