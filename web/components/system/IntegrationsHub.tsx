"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import AddMcpServerDialog from "@/components/system/AddMcpServerDialog";
import ConnectDialog from "@/components/system/ConnectDialog";
import Button from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/useConfirm";
import {
  deleteMcpServer,
  disconnectIntegration,
  useIntegrations,
  useJournalSessions,
  useMcpSnippets,
  type ConnectorInfo,
  type McpServerInfo,
} from "@/lib/api";
import { formatRelativeTime } from "@/lib/relativeTime";

/**
 * INTEGRATIONS (§12) — one hub replacing the two static panels that used to
 * sit here.
 *
 * What was wrong with those: every row was hardcoded, MCP.SERVERS advertised
 * `mcp-obsidian:27124` and `mcp-gmail:27125` (neither of which existed
 * anywhere in the backend), and every CONNECT button fired a toast naming a
 * CLI command — one of which, `argus mcp add`, was never implemented. The
 * panel reported a system that wasn't there.
 *
 * Now: connectors and MCP servers come from `GET /api/integrations` and every
 * CONNECT opens a flow that finishes in the app. The two rows that genuinely
 * are informational — Claude Code hooks, email capture — say so plainly and
 * keep their existing client-side sources rather than being faked into the
 * same shape.
 */

const STATUS_STYLE: Record<string, string> = {
  WIRED: "border-ok text-ok",
  "NOT CONNECTED": "border-ink-faint text-ink-faint",
  "NEEDS SETUP": "border-amber-400 text-amber-400",
  MANUAL: "border-amber-400 text-amber-400",
  LOADING: "border-ink-faint text-ink-faint",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`shrink-0 border px-1.5 py-0.5 font-mono text-micro uppercase tracking-[0.1em] ${
        STATUS_STYLE[status] ?? STATUS_STYLE["NOT CONNECTED"]
      }`}
    >
      {status}
    </span>
  );
}

function Card({
  status,
  title,
  detail,
  chips,
  actions,
}: {
  status: string;
  title: string;
  detail: string;
  chips?: string[];
  actions?: React.ReactNode;
}) {
  const live = status === "WIRED";
  return (
    <div className="flex flex-col border border-line p-3 transition-colors hover:border-lineHi">
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${live ? "bg-ok" : "bg-ink-faint"}`}
        />
        <p className="min-w-0 flex-1 truncate font-mono text-label text-ink">{title}</p>
        <StatusBadge status={status} />
      </div>

      <p className="mt-2 text-label leading-relaxed text-ink-muted">{detail}</p>

      {chips && chips.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {chips.map((chip) => (
            <span
              key={chip}
              className="border border-line px-1.5 py-0.5 font-mono text-micro text-ink-faint"
            >
              {chip}
            </span>
          ))}
        </div>
      )}

      {actions && <div className="mt-3 flex items-center gap-2 pt-1">{actions}</div>}
    </div>
  );
}

const CONNECTOR_STATUS: Record<string, string> = {
  wired: "WIRED",
  "not-connected": "NOT CONNECTED",
  "needs-credentials": "NEEDS SETUP",
};

export default function IntegrationsHub() {
  const { data, isLoading, mutate } = useIntegrations();
  const { data: sessions } = useJournalSessions();
  const { data: snippets } = useMcpSnippets();
  const { show } = useToast();
  const { confirm, confirmDialog } = useConfirm();

  const [connecting, setConnecting] = useState<ConnectorInfo | null>(null);
  const [addingServer, setAddingServer] = useState(false);
  const [showSnippets, setShowSnippets] = useState(false);

  const lastSession = sessions?.[0];
  const connectors = data?.connectors ?? [];
  const servers = data?.mcp_servers ?? [];

  const gcalNeedsCredentials =
    connectors.find((c) => c.id === "gcal")?.status === "needs-credentials";

  async function disconnect(connector: ConnectorInfo) {
    const answer = await confirm({
      label: `Disconnect ${connector.name}`,
      message: `Forget the stored credential for ${connector.name}?`,
      detail: "Argus stops reading it. You can reconnect at any time.",
      confirmLabel: "DISCONNECT",
      tone: "danger",
    });
    if (answer === null) return;
    try {
      await disconnectIntegration(connector.id);
      show(`${connector.id} :: disconnected`);
      void mutate();
    } catch (error) {
      show(error instanceof Error ? error.message : "could not disconnect");
    }
  }

  async function removeServer(server: McpServerInfo) {
    const answer = await confirm({
      label: `Remove ${server.name}`,
      message: `Remove the MCP server "${server.name}"?`,
      detail: server.key_state === "absent" ? undefined : "Its stored token is deleted with it.",
      confirmLabel: "REMOVE",
      tone: "danger",
    });
    if (answer === null) return;
    try {
      await deleteMcpServer(server.name);
      show(`mcp :: ${server.name} removed`);
      void mutate();
    } catch (error) {
      show(error instanceof Error ? error.message : "could not remove that server");
    }
  }

  return (
    <>
      <Panel
        label="INTEGRATIONS"
        headerRight={
          <Button variant="quiet" onClick={() => setAddingServer(true)}>
            + ADD MCP SERVER
          </Button>
        }
      >
        {isLoading && !data ? (
          <p className="text-label text-ink-faint">loading integrations…</p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {connectors.map((connector) => {
              const status = CONNECTOR_STATUS[connector.status] ?? "NOT CONNECTED";
              return (
                <Card
                  key={connector.id}
                  status={status}
                  title={connector.name}
                  detail={connector.detail}
                  actions={
                    status === "WIRED" ? (
                      <Button variant="quiet" onClick={() => void disconnect(connector)}>
                        DISCONNECT
                      </Button>
                    ) : (
                      <Button variant="primary" onClick={() => setConnecting(connector)}>
                        {status === "NEEDS SETUP" ? "SET UP" : "CONNECT"}
                      </Button>
                    )
                  }
                />
              );
            })}

            {servers.map((server) => (
              <Card
                key={server.name}
                status="WIRED"
                title={server.name}
                detail={
                  server.transport === "stdio"
                    ? [server.command, ...server.args].filter(Boolean).join(" ")
                    : (server.url ?? "")
                }
                chips={[
                  ...server.tools,
                  // "unknown" earns its own chip: a silent absence reads as
                  // "your token is gone" when the keyring merely would not open.
                  ...(server.key_state === "present" ? ["token in keyring"] : []),
                  ...(server.key_state === "unknown" ? ["keyring unreadable"] : []),
                ]}
                actions={
                  <Button variant="quiet" onClick={() => void removeServer(server)}>
                    REMOVE
                  </Button>
                }
              />
            ))}

            {/* Informational rows. Real state, but nothing to connect — kept
                distinct rather than dressed up as connectable. */}
            <Card
              status={sessions ? (lastSession ? "WIRED" : "NOT CONNECTED") : "LOADING"}
              title="claude code hooks"
              detail={
                lastSession
                  ? `last session ${formatRelativeTime(lastSession.date)} · ${lastSession.project}`
                  : "no session stamps yet — run claude-integration/setup.ps1"
              }
            />
            <Card
              status="MANUAL"
              title="email capture"
              detail="paste-only by design — no inbox access, ever (§11)"
            />
          </div>
        )}

        {servers.length === 0 && !isLoading && (
          <p className="mt-3 border-t border-line pt-3 text-label leading-relaxed text-ink-faint">
            No MCP servers registered. Adding one starts it, completes the handshake, and lists the
            tools it offers.{" "}
            <span className="text-ink-muted">
              Registered tools are not callable from Argus chat yet — that needs adapter work
              across all three model providers.
            </span>
          </p>
        )}

        <div className="mt-3 border-t border-line pt-3">
          <button
            type="button"
            onClick={() => setShowSnippets((open) => !open)}
            className="font-mono text-meta uppercase tracking-[0.1em] text-ink-faint transition-colors hover:text-ink"
          >
            {showSnippets ? "▾" : "▸"} expose argus to your coding agents
          </button>
          {showSnippets && (
            <div className="mt-2 flex flex-col gap-2">
              {Object.entries(snippets ?? {}).map(([agent, snippet]) => (
                <div key={agent}>
                  <p className="mb-1 font-mono text-micro uppercase tracking-[0.12em] text-ink-faint">
                    {agent}
                  </p>
                  <pre className="overflow-x-auto border border-line bg-sunken px-2.5 py-2 font-mono text-micro leading-relaxed text-ink-muted">
                    {snippet}
                  </pre>
                </div>
              ))}
              {!snippets && <p className="text-meta text-ink-faint">loading…</p>}
            </div>
          )}
        </div>
      </Panel>

      {connecting && (
        <ConnectDialog
          connectorId={connecting.id}
          hasCredentials={connecting.id === "gcal" ? !gcalNeedsCredentials : true}
          onClose={() => setConnecting(null)}
          onDone={() => void mutate()}
        />
      )}
      {addingServer && (
        <AddMcpServerDialog
          onClose={() => setAddingServer(false)}
          onAdded={() => void mutate()}
        />
      )}
      {confirmDialog}
    </>
  );
}
