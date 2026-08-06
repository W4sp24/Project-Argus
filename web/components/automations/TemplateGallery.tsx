"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import {
  installAutomationTemplate,
  useAutomationTemplates,
  type AutomationTemplate,
} from "@/lib/api";
import { openExternalUrl } from "@/lib/quickLinks";
import { KindChip } from "./chips";

/**
 * The shipped workflow gallery.
 *
 * Installing pushes the bundled definition into the user's own n8n with the
 * `argus` tag, the callback URL and the bearer token already substituted —
 * and then **stops**, handing off to n8n so the user can grant the
 * credential. That last step genuinely cannot be automated: OAuth needs a
 * real browser round trip, and doing the equivalent for an API-key type would
 * mean handing Argus the third-party secret again, which is the thing this
 * whole arrangement exists to avoid. The UI therefore treats "installed" as
 * "created and activated", not as "working", and says so.
 */

function TemplateCard({
  template,
  onInstalled,
}: {
  template: AutomationTemplate;
  onInstalled: () => void;
}) {
  const { show } = useToast();
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function install() {
    setInstalling(true);
    setError(null);
    try {
      const result = await installAutomationTemplate(template.id);
      show(`automations :: installed ${template.name}`);
      onInstalled();
      // Send them straight to the credential step — the card cannot do it,
      // and a template sitting in n8n without its credential is the single
      // most likely reason a widget never leaves WAITING.
      if (result.open_in_n8n) openExternalUrl(result.open_in_n8n);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "could not install this template");
    } finally {
      setInstalling(false);
    }
  }

  return (
    <li className="flex flex-col gap-2.5 border border-line p-3.5 transition-colors hover:border-lineHi">
      <div className="flex flex-wrap items-center gap-2">
        <KindChip kind={template.kind} />
        <p className="min-w-0 flex-1 truncate font-mono text-label text-ink-bright">
          {template.name}
        </p>
        {template.installed && (
          <span className="border border-ok px-1 py-px font-mono text-micro uppercase tracking-[0.14em] text-ok">
            INSTALLED
          </span>
        )}
      </div>

      <p className="text-label leading-relaxed text-ink-muted">{template.description}</p>

      {template.chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {template.chips.map((chip) => (
            <span
              key={chip}
              className="border border-line px-1.5 py-0.5 font-mono text-micro uppercase tracking-[0.1em] text-ink-faint"
            >
              {chip}
            </span>
          ))}
        </div>
      )}

      {template.requires.length > 0 && (
        <p className="text-meta text-ink-faint">
          Needs a {template.requires.join(" and ")} credential, granted in n8n.
        </p>
      )}
      {template.replaces && (
        <p className="text-meta text-ink-faint">
          Replaces <span className="font-mono">{template.replaces}</span>.
        </p>
      )}
      {template.widget_slug && (
        <p className="text-meta text-ink-faint">
          Pushes to <span className="font-mono">{template.widget_slug}</span>.
        </p>
      )}

      {error && (
        <p role="alert" className="border border-danger px-3 py-2 text-label text-danger">
          {error}
        </p>
      )}

      <Button className="mt-1 w-full" onClick={() => void install()} disabled={installing}>
        {installing ? "INSTALLING…" : template.installed ? "INSTALL AGAIN" : "INSTALL"}
      </Button>
    </li>
  );
}

export default function TemplateGallery({ onInstalled }: { onInstalled: () => void }) {
  const { data: templates, error, mutate } = useAutomationTemplates();

  if (error) {
    return (
      <Panel label="GALLERY">
        <p className="text-label text-ink-faint">
          The template catalog could not be loaded — is the backend running?
        </p>
      </Panel>
    );
  }

  if (!templates) {
    return (
      <Panel label="GALLERY">
        <p className="text-label text-ink-faint">Loading templates…</p>
      </Panel>
    );
  }

  return (
    <Panel label="GALLERY">
      <p className="mb-3 text-label leading-relaxed text-ink-muted">
        Installing pushes the workflow into your n8n and opens it there, so you can grant its
        credential — the one step that has to happen in n8n rather than here.
      </p>
      <ul className="grid gap-3 sm:grid-cols-2">
        {templates.map((template) => (
          <TemplateCard
            key={template.id}
            template={template}
            onInstalled={() => {
              void mutate();
              onInstalled();
            }}
          />
        ))}
      </ul>
    </Panel>
  );
}
