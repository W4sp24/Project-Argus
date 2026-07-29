import AgentUsage from "@/components/AgentUsage";
import ModeHeader from "@/components/ModeHeader";
import TokenUsage from "@/components/TokenUsage";
import DisplayPanel from "@/components/system/DisplayPanel";
import DoctorPanel from "@/components/system/DoctorPanel";
import IntegrationsHub from "@/components/system/IntegrationsHub";
import LocalModelBrowser from "@/components/system/LocalModelBrowser";
import ModelsPanel from "@/components/system/ModelsPanel";
import SetupGuide from "@/components/system/SetupGuide";
import UpdatesPanel from "@/components/system/UpdatesPanel";

/** System mode (§12) — setup guide, health, integrations, usage, models.
 *
 * `IntegrationsHub` replaces the former `Integrations` + `McpServers` pair: one
 * panel over one real API, instead of two panels of hardcoded rows. It is
 * full-width now because MCP server cards carry tool chips and no longer fit
 * the left column.
 */
export default function SystemPage() {
  return (
    <>
      <ModeHeader mode="system" greeting="System diagnostics online." />

      <div className="flex flex-col gap-4">
        <SetupGuide />
        <DoctorPanel />
        <IntegrationsHub />

        <AgentUsage size="large" />

        <div className="grid gap-4 lg:grid-cols-shell">
          <div className="flex min-w-0 flex-col gap-4">
            <ModelsPanel />
            <LocalModelBrowser />
          </div>
          <div className="flex min-w-0 flex-col gap-4">
            {/* Renders nothing in the browser build (no installer to update). */}
            <UpdatesPanel />
            <DisplayPanel />
            <TokenUsage />
          </div>
        </div>
      </div>
    </>
  );
}
