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

import { SayRMApi } from "../api";
import { ExternalBrief } from "../types";
import { formatTimestamp, splitLines } from "../utils";

export default function ClientBriefCommand() {
  const { push } = useNavigation();
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(values: { callsign: string; manualHighlights?: string }) {
    try {
      setIsSubmitting(true);
      await showToast({ style: Toast.Style.Animated, title: "Fetching brief…" });
      const highlights = splitLines(values.manualHighlights || "");
      const brief = await SayRMApi.createExternalBrief(values.callsign, highlights);
      await showHUD("External brief ready");
      push(<BriefDetail brief={brief} />);
    } catch (error) {
      await showToast({
        style: Toast.Style.Failure,
        title: "Failed to fetch brief",
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
          <Action.SubmitForm title="Fetch Brief" onSubmit={handleSubmit} />
        </ActionPanel>
      }
    >
      <Form.TextField id="callsign" title="Callsign" placeholder="acme" autoFocus />
      <Form.TextArea
        id="manualHighlights"
        title="Manual Highlights"
        placeholder="Recent funding round\nNew product launch"
      />
    </Form>
  );
}

function BriefDetail({ brief }: { brief: ExternalBrief }) {
  const newsBlock = brief.news.length ? brief.news.map((n) => `- ${n}`).join("\n") : "_No recent news._";
  const annBlock = brief.announcements.length
    ? brief.announcements.map((n) => `- ${n}`).join("\n")
    : "_No announcements logged._";
  const markdown = `# ${brief.company_name || brief.callsign}

**What they do**

${brief.product}

**Latest news**

${newsBlock}

**Announcements (<= 6 months)**

${annBlock}

`;
  return (
    <Detail
      markdown={markdown}
      metadata={
        <Detail.Metadata>
          <Detail.Metadata.Label title="Callsign" text={brief.callsign} />
          <Detail.Metadata.Label title="Created" text={formatTimestamp(brief.created_at)} />
          <Detail.Metadata.Label title="Summary ID" text={String(brief.summary_id)} />
        </Detail.Metadata>
      }
      actions={
        <ActionPanel>
          <Action.CopyToClipboard title="Copy Full Brief" content={markdown} />
          <Action.CopyToClipboard title="Copy Product Summary" content={brief.product} />
          <Action.CopyToClipboard title="Copy Raw Text" content={brief.raw_text} />
        </ActionPanel>
      }
    />
  );
}
