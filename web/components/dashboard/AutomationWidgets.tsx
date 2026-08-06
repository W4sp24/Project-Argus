"use client";

import WidgetShell from "@/components/automations/WidgetShell";
import { useConfirm } from "@/components/ui/useConfirm";
import { useToast } from "@/components/Toast";
import {
  deleteAutomationWidget,
  useAutomationInstances,
  useAutomationWidgets,
} from "@/lib/api";

/**
 * Automation-fed panels on the dashboard.
 *
 * Auto-place, then take control (Home Assistant's model): a new widget
 * appends automatically, so an automation is useful the moment it first
 * pushes — no configuration step between installing and benefiting. Removing
 * one is what tells the backend the arrangement has become the user's.
 *
 * Renders nothing at all when there are no widgets. An empty panel announcing
 * that you have no automations is worse than the space it occupies, and every
 * existing install starts in exactly that state.
 */
export default function AutomationWidgets() {
  const { data: widgets, mutate } = useAutomationWidgets();
  const { data: instances } = useAutomationInstances();
  const { show } = useToast();
  const { confirm, confirmDialog } = useConfirm();

  // Origin is only information once there is a second instance to tell apart.
  // With one registered, every chip would read the same and say nothing, so
  // the name is withheld and OriginChip renders null.
  const showOrigin = (instances?.length ?? 0) > 1;
  const byId = new Map((instances ?? []).map((i) => [i.id, i]));

  if (!widgets || widgets.length === 0) return null;

  async function remove(slug: string, instanceId: string, title: string) {
    // useConfirm resolves null on cancel and "" on a plain confirm.
    const answer = await confirm({
      label: "Remove widget",
      message: `Remove ${title} from the dashboard?`,
      detail:
        "The automation keeps running in n8n. Its next push will bring the widget back.",
      confirmLabel: "Remove",
      tone: "danger",
    });
    if (answer === null) return;
    try {
      await deleteAutomationWidget(slug, instanceId);
      show(`automations :: removed ${slug}`);
      void mutate();
    } catch (error) {
      show(
        `automations :: could not remove ${slug} — ${
          error instanceof Error ? error.message : "unknown error"
        }`,
        { tone: "error" },
      );
    }
  }

  return (
    <>
      {widgets.map((widget) => {
        const instance = byId.get(widget.instance_id);
        return (
          <WidgetShell
            key={`${widget.instance_id}:${widget.slug}`}
            widget={widget}
            instanceName={showOrigin ? instance?.name : null}
            instanceBaseUrl={instance?.base_url}
            menuItems={[
              {
                label: "REMOVE",
                run: () =>
                  void remove(widget.slug, widget.instance_id, widget.title || widget.slug),
              },
            ]}
          />
        );
      })}
      {confirmDialog}
    </>
  );
}
