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
import { useEffect, useState } from "react";

import { SayRMApi } from "../api";
import { ComposeDraftResponse, TemplateInfo } from "../types";
import { formatTimestamp, splitLines } from "../utils";

export default function DraftHelperCommand() {
  const { push } = useNavigation();
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [isLoadingTemplates, setIsLoadingTemplates] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const data = await SayRMApi.listTemplates();
        setTemplates(data);
      } catch (error) {
        await showToast({
          style: Toast.Style.Failure,
          title: "Failed to load templates",
          message: String(error),
        });
      } finally {
        setIsLoadingTemplates(false);
      }
    }
    load();
  }, []);

  async function handleSubmit(values: {
    callsign: string;
    templateId?: string;
    instructions?: string;
    manualSnippets?: string;
    externalSummary?: string;
    internalSummary?: string;
  }) {
    try {
      setIsSubmitting(true);
      await showToast({ style: Toast.Style.Animated, title: "Composing draft…" });
      const result = await SayRMApi.composeDraft({
        callsign: values.callsign,
        template_id: values.templateId || undefined,
        instructions: values.instructions,
        manual_snippets: splitLines(values.manualSnippets || ""),
        external_summary: values.externalSummary || undefined,
        internal_summary: values.internalSummary || undefined,
      });
      await showHUD("Draft ready");
      push(<DraftDetail draft={result} />);
    } catch (error) {
      await showToast({
        style: Toast.Style.Failure,
        title: "Failed to compose draft",
        message: String(error),
      });
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <Form
      isLoading={isLoadingTemplates || isSubmitting}
      actions={
        <ActionPanel>
          <Action.SubmitForm title="Compose Draft" onSubmit={handleSubmit} />
        </ActionPanel>
      }
    >
      <Form.TextField id="callsign" title="Callsign" placeholder="acme" autoFocus />
      <Form.Dropdown id="templateId" title="Template">
        <Form.Dropdown.Item value="" title="No template" />
        {templates.map((template) => (
          <Form.Dropdown.Item key={template.id} value={template.id} title={template.title} />
        ))}
      </Form.Dropdown>
      <Form.TextArea id="instructions" title="Instructions" placeholder="What tone, CTA, etc." />
      <Form.TextArea
        id="manualSnippets"
        title="Manual Snippets"
        placeholder="One snippet per line; these will be inserted verbatim."
      />
      <Form.TextArea id="externalSummary" title="External Summary Override" />
      <Form.TextArea id="internalSummary" title="Internal Summary Override" />
    </Form>
  );
}

function DraftDetail({ draft }: { draft: ComposeDraftResponse }) {
  return (
    <Detail
      markdown={`# Draft for ${draft.callsign}\n\n${draft.body}`}
      metadata={
        <Detail.Metadata>
          <Detail.Metadata.Label title="Draft ID" text={String(draft.draft_id)} />
          <Detail.Metadata.Label title="Template" text={draft.template_id || "N/A"} />
          <Detail.Metadata.Label title="Created" text={formatTimestamp(draft.created_at)} />
        </Detail.Metadata>
      }
      actions={
        <ActionPanel>
          <Action.CopyToClipboard title="Copy Draft" content={draft.body} />
          <Action.Paste content={draft.body} />
        </ActionPanel>
      }
    />
  );
}

