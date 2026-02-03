import {
  Action,
  ActionPanel,
  Detail,
  Form,
  Toast,
  showHUD,
  showToast,
  useNavigation,
} from "@raycast/api";
import { useState } from "react";

import { SayRMApi } from "./api";
import { CompanyContextResponse, TemplateInfo } from "./types";
import { splitLines } from "./utils";

export default function CompanyContextCommand() {
  const { push } = useNavigation();
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(values: { callsign: string; manualHighlights?: string }) {
    try {
      setIsSubmitting(true);
      await showToast({ style: Toast.Style.Animated, title: "Compiling context…" });
      const highlights = splitLines(values.manualHighlights || "");
      const context = await SayRMApi.buildContext(values.callsign, highlights);
      await showHUD("Context ready");
      push(<ContextDetail context={context} />);
    } catch (error) {
      await showToast({
        style: Toast.Style.Failure,
        title: "Failed to build context",
        message: String(error),
      });
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <Form
      isLoading={isSubmitting}
      actions={
        <ActionPanel>
          <Action.SubmitForm title="Build Context" onSubmit={handleSubmit} />
        </ActionPanel>
      }
    >
      <Form.TextField id="callsign" title="Callsign" placeholder="acme" autoFocus />
      <Form.TextArea
        id="manualHighlights"
        title="Manual Highlights"
        info="Optional bullets to pass to the external brief."
      />
    </Form>
  );
}

function ContextDetail({ context }: { context: CompanyContextResponse }) {
  const external = context.external;
  const internal = context.internal;
  const sections: string[] = [];

  if (external?.brief) {
    sections.push(
      `## External Summary\n\n${external.brief.raw_text}\n`,
      `**Context owners:** ${(external.context.owners as string[] | undefined)?.join(", ") || "Unknown"}`
    );
  }

  if (internal?.brief) {
    sections.push(`## Internal Snapshot\n\n${internal.brief.notes}`);
  }

  if (context.templates.length) {
    const templateList = context.templates
      .map((template) => `- **${template.title}** — ${template.description}`)
      .join("\n");
    sections.push(`## Templates\n\n${templateList}`);
  }

  const markdown = `# Context for ${context.callsign}\n\n${sections.join("\n\n---\n\n") || "_No context available._"}`;

  return (
    <Detail
      markdown={markdown}
      metadata={
        <Detail.Metadata>
          <Detail.Metadata.Label title="Callsign" text={context.callsign} />
          <Detail.Metadata.Label title="Templates" text={String(context.templates.length)} />
        </Detail.Metadata>
      }
      actions={
        <ActionPanel>
          <Action.CopyToClipboard title="Copy Markdown" content={markdown} />
          {context.templates.map((template: TemplateInfo) => (
            <Action.CopyToClipboard
              key={template.id}
              title={`Copy Template: ${template.title}`}
              content={template.body}
            />
          ))}
        </ActionPanel>
      }
    />
  );
}
