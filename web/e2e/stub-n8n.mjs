/**
 * A stub n8n for the e2e suite.
 *
 * The automations feature is defined by how Argus behaves against n8n, and
 * there is no n8n in CI — so the alternative to this file is not testing the
 * feature end to end at all. It speaks just enough of n8n's public API for
 * discovery, install and firing to be exercised for real through the UI:
 *
 *   GET  /api/v1/workflows            list, honouring ?tags=
 *   GET  /api/v1/workflows/:id        one definition
 *   POST /api/v1/workflows            create (template install)
 *   POST /api/v1/workflows/:id/activate
 *   POST /form/:webhookId             fire a form trigger
 *   POST /webhook/:path               fire a webhook trigger
 *
 * Firing returns a widget payload, so a run exercises the widget path rather
 * than only the acknowledgement path — the acknowledgement path would pass
 * even if rendering were broken.
 *
 * Deliberately dependency-free (node:http only): the e2e harness already
 * spawns a Python backend and a Next server, and a third moving part with its
 * own install step would be one more thing that can fail before a test runs.
 */
import http from "node:http";

const PORT = Number(process.env.STUB_N8N_PORT || 5679);
const API_KEY = process.env.STUB_N8N_KEY || "e2e-n8n-key";

/** A form-trigger workflow, tagged so Argus discovers it. */
const FORM_WORKFLOW = {
  id: "wf-echo",
  name: "E2E Echo",
  active: true,
  tags: [{ id: "1", name: "argus" }],
  nodes: [
    {
      name: "On form submission",
      type: "n8n-nodes-base.formTrigger",
      webhookId: "echo-hook",
      parameters: {
        // Workflow Finishes, never the Immediately default — the same rule
        // the shipped templates follow, for the same reason.
        responseMode: "lastNode",
        formFields: {
          values: [
            {
              fieldLabel: "topic",
              fieldType: "text",
              requiredField: true,
              placeholder: "what to look up",
            },
          ],
        },
      },
    },
  ],
  connections: {},
};

/** A webhook-trigger workflow: renders as a bare button, no inputs. */
const BUTTON_WORKFLOW = {
  id: "wf-button",
  name: "E2E Button",
  active: true,
  tags: [{ id: "1", name: "argus" }],
  nodes: [
    {
      name: "Webhook",
      type: "n8n-nodes-base.webhook",
      parameters: { path: "button-hook", responseMode: "responseNode" },
    },
  ],
  connections: {},
};

/** Untagged: must never appear in Argus, since tagging is the registration. */
const UNTAGGED_WORKFLOW = {
  id: "wf-hidden",
  name: "E2E Untagged Should Not Appear",
  active: true,
  tags: [{ id: "9", name: "internal" }],
  nodes: [{ name: "Webhook", type: "n8n-nodes-base.webhook", parameters: { path: "nope" } }],
  connections: {},
};

const workflows = new Map(
  [FORM_WORKFLOW, BUTTON_WORKFLOW, UNTAGGED_WORKFLOW].map((w) => [w.id, w]),
);
let created = 0;

function send(res, status, body) {
  const payload = body === undefined ? "" : JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(payload),
  });
  res.end(payload);
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (!chunks.length) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    return {};
  }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
  const { pathname } = url;

  // --- trigger URLs: public on the n8n side, so no API key here ---
  if (req.method === "POST" && (pathname.startsWith("/form/") || pathname.startsWith("/webhook/"))) {
    const body = await readBody(req);
    const topic = body?.topic ?? "nothing";
    return send(res, 200, {
      widget: "text",
      title: "E2E RESULT",
      body: `looked up **${topic}**`,
    });
  }

  // --- everything under /api/v1 needs the key ---
  if (pathname.startsWith("/api/v1")) {
    if (req.headers["x-n8n-api-key"] !== API_KEY) {
      return send(res, 401, { message: "unauthorized" });
    }
  }

  if (req.method === "GET" && pathname === "/api/v1/workflows") {
    const tag = url.searchParams.get("tags");
    const all = [...workflows.values()];
    // Filtered here as the real API claims to — Argus re-filters client-side
    // anyway, because whether this works is version-dependent.
    const data = tag ? all.filter((w) => w.tags?.some((t) => t.name === tag)) : all;
    return send(res, 200, { data });
  }

  const one = pathname.match(/^\/api\/v1\/workflows\/([^/]+)$/);
  if (req.method === "GET" && one) {
    const workflow = workflows.get(one[1]);
    return workflow ? send(res, 200, workflow) : send(res, 404, { message: "not found" });
  }

  if (req.method === "POST" && pathname === "/api/v1/workflows") {
    const body = await readBody(req);
    const id = `wf-installed-${++created}`;
    const workflow = { ...body, id, active: false, tags: body.tags ?? [{ id: "1", name: "argus" }] };
    workflows.set(id, workflow);
    return send(res, 200, workflow);
  }

  const activate = pathname.match(/^\/api\/v1\/workflows\/([^/]+)\/activate$/);
  if (req.method === "POST" && activate) {
    const workflow = workflows.get(activate[1]);
    if (!workflow) return send(res, 404, { message: "not found" });
    workflow.active = true;
    return send(res, 200, workflow);
  }

  // The credential-type schema has moved between n8n releases, so Argus has a
  // manual-paste fallback for exactly this. Refusing here keeps that fallback
  // on the tested path rather than the hypothetical one.
  if (req.method === "POST" && pathname === "/api/v1/credentials") {
    return send(res, 400, { message: "credential type schema not supported" });
  }

  return send(res, 404, { message: `no stub route for ${req.method} ${pathname}` });
});

server.listen(PORT, "127.0.0.1", () => {
  process.stdout.write(`[stub-n8n] listening on 127.0.0.1:${PORT}\n`);
});
