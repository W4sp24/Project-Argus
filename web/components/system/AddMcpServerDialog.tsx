"use client";

import { useMemo, useState } from "react";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Field, { FIELD_CONTROL } from "@/components/ui/Field";
import { addMcpServer, testMcpServer, type McpProbeResult } from "@/lib/api";

/**
 * Register an external MCP server.
 *
 * The panel this replaces listed two servers that existed nowhere in the
 * backend and offered a CONNECT button that toasted `argus mcp add gmail` — a
 * command that was never implemented. This one really connects: it starts the
 * server (or opens the HTTP session), completes the MCP handshake, and lists
 * the tools it found, and SAVE stays locked until that succeeds. The tool list
 * is the proof, which is why it is shown rather than a green tick.
 */

const TRANSPORTS = [
  {
    id: "stdio" as const,
    title: "Local command",
    blurb: "Argus runs the server as a subprocess and talks over stdio.",
  },
  {
    id: "http" as const,
    title: "Remote URL",
    blurb: "A hosted server speaking streamable HTTP.",
  },
];

/** `npx -y @foo/bar --flag` → `["npx", ["-y", "@foo/bar", "--flag"]]`. */
function splitCommand(raw: string): { command: string; args: string[] } {
  const parts = raw.trim().split(/\s+/).filter(Boolean);
  return { command: parts[0] ?? "", args: parts.slice(1) };
}

export default function AddMcpServerDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: () => void;
}) {
  const { show } = useToast();
  const [transport, setTransport] = useState<"stdio" | "http">("stdio");
  const [name, setName] = useState("");
  const [commandLine, setCommandLine] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [result, setResult] = useState<McpProbeResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  /** Any edit un-proves a previous green light. */
  function invalidate() {
    setResult(null);
    setSaveError(null);
  }

  const body = useMemo(() => {
    const { command, args } = splitCommand(commandLine);
    return {
      name: name.trim() || "probe",
      transport,
      ...(transport === "stdio" ? { command, args } : { url: url.trim() }),
      ...(token.trim() ? { token: token.trim() } : {}),
    };
  }, [name, transport, commandLine, url, token]);

  const canTest =
    transport === "stdio" ? Boolean(commandLine.trim()) : url.trim().startsWith("http");
  const canSave = Boolean(name.trim()) && result?.ok === true;

  async function runTest() {
    if (!canTest || testing) return;
    setTesting(true);
    setSaveError(null);
    try {
      setResult(await testMcpServer(body));
    } catch (error) {
      setResult({
        ok: false,
        detail: error instanceof Error ? error.message : "the test could not run",
        latency_ms: 0,
        tools: [],
      });
    } finally {
      setTesting(false);
    }
  }

  async function save() {
    if (!canSave || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      await addMcpServer({ ...body, name: name.trim() });
      show(`mcp :: ${name.trim()} connected`);
      onAdded();
      onClose();
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "could not add that server");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      label="Add an MCP server"
      onClose={onClose}
      align="center"
      className="w-[38rem] max-w-[calc(100vw-2rem)]"
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (canSave) void save();
          else if (canTest) void runTest();
        }}
        className="p-5"
      >
        <p className="eyebrow mb-3">▍ADD.MCP.SERVER</p>

        <div className="mb-4 grid gap-2 sm:grid-cols-2">
          {TRANSPORTS.map((option) => {
            const active = option.id === transport;
            return (
              <button
                key={option.id}
                type="button"
                aria-pressed={active}
                onClick={() => {
                  setTransport(option.id);
                  invalidate();
                }}
                className={`border p-3 text-left transition-colors ${
                  active ? "border-[var(--ac)] bg-[var(--ac-bg)]" : "border-line hover:border-lineHi"
                }`}
              >
                <span className="block text-body text-ink">{option.title}</span>
                <span className="mt-1 block text-meta leading-relaxed text-ink-muted">
                  {option.blurb}
                </span>
              </button>
            );
          })}
        </div>

        <div className="flex flex-col gap-3">
          {transport === "stdio" ? (
            <Field label="command" hint="Exactly what you would type in a terminal.">
              {(props) => (
                <input
                  {...props}
                  value={commandLine}
                  onChange={(event) => {
                    setCommandLine(event.target.value);
                    invalidate();
                  }}
                  placeholder="npx -y @modelcontextprotocol/server-filesystem ~/notes"
                  className={`${FIELD_CONTROL} font-mono text-meta`}
                />
              )}
            </Field>
          ) : (
            <>
              <Field label="server url">
                {(props) => (
                  <input
                    {...props}
                    value={url}
                    onChange={(event) => {
                      setUrl(event.target.value);
                      invalidate();
                    }}
                    placeholder="https://example.com/mcp"
                    className={`${FIELD_CONTROL} font-mono text-meta`}
                  />
                )}
              </Field>
              <Field
                label="bearer token"
                hint="Optional. Stored in your operating system's keyring, never in a file."
              >
                {(props) => (
                  <input
                    {...props}
                    type="password"
                    value={token}
                    onChange={(event) => {
                      setToken(event.target.value);
                      invalidate();
                    }}
                    placeholder="leave empty if the server is open"
                    className={FIELD_CONTROL}
                  />
                )}
              </Field>
            </>
          )}

          <div>
            <Button
              size="md"
              variant={result?.ok ? "secondary" : "primary"}
              onClick={runTest}
              disabled={!canTest || testing}
            >
              {testing ? "CONNECTING…" : result?.ok ? "TEST AGAIN" : "TEST CONNECTION"}
            </Button>
          </div>

          {result && (
            <div
              role="status"
              className={`border px-3 py-2 ${
                result.ok
                  ? "border-ok bg-[rgba(52,211,153,0.06)]"
                  : "border-danger bg-[rgba(251,113,133,0.06)]"
              }`}
            >
              <p className={`text-label leading-relaxed ${result.ok ? "text-ok" : "text-danger"}`}>
                <span aria-hidden className="mr-1 font-mono">
                  {result.ok ? "✓" : "✗"}
                </span>
                {result.detail}
                {result.ok && result.latency_ms > 0 && (
                  <span className="text-ink-faint"> · {result.latency_ms}ms</span>
                )}
              </p>
              {result.tools.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {result.tools.map((tool) => (
                    <span
                      key={tool}
                      className="border border-line px-1.5 py-0.5 font-mono text-micro text-ink-muted"
                    >
                      {tool}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}

          <Field label="name" hint="What this server is called in the list.">
            {(props) => (
              <input
                {...props}
                value={name}
                onChange={(event) => {
                  setName(event.target.value);
                  setSaveError(null);
                }}
                placeholder="e.g. filesystem"
                className={FIELD_CONTROL}
              />
            )}
          </Field>
        </div>

        {saveError && (
          <p role="alert" className="mt-3 border border-danger px-3 py-2 text-label text-danger">
            {saveError}
          </p>
        )}

        <div className="mt-5 flex items-center gap-2 border-t border-line pt-4">
          <Button size="md" onClick={onClose}>
            CANCEL
          </Button>
          <div className="ml-auto flex items-center gap-3">
            {!canSave && (
              <p className="text-right text-meta text-ink-faint">
                {result?.ok ? "Give it a name to save." : "Pass the connection test, then name it."}
              </p>
            )}
            <Button type="submit" size="md" variant="primary" disabled={!canSave || saving}>
              {saving ? "SAVING…" : "SAVE SERVER"}
            </Button>
          </div>
        </div>
      </form>
    </Dialog>
  );
}
