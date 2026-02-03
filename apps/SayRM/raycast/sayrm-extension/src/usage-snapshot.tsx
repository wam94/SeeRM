import { Action, ActionPanel, Detail, Form, Toast, showHUD, showToast, useNavigation } from "@raycast/api";
import { useState } from "react";

import { SayRMApi } from "./api";
import { InternalBrief } from "./types";
import { formatTimestamp } from "./utils";

export default function UsageSnapshotCommand() {
  const { push } = useNavigation();
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(values: { callsign: string }) {
    try {
      setIsSubmitting(true);
      await showToast({ style: Toast.Style.Animated, title: "Pulling usage snapshot…" });
      const brief = await SayRMApi.createInternalBrief(values.callsign);
      await showHUD("Usage snapshot ready");
      push(<UsageDetail brief={brief} />);
    } catch (error) {
      await showToast({
        style: Toast.Style.Failure,
        title: "Failed to pull snapshot",
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
          <Action.SubmitForm title="Fetch Usage" onSubmit={handleSubmit} />
        </ActionPanel>
      }
    >
      <Form.TextField id="callsign" title="Callsign" placeholder="acme" autoFocus />
    </Form>
  );
}

function UsageDetail({ brief }: { brief: InternalBrief }) {
  const markdown = `# Usage Snapshot for ${brief.callsign}

${brief.notes}
`;
  return (
    <Detail
      markdown={markdown}
      metadata={
        <Detail.Metadata>
          <Detail.Metadata.Label title="Status" text={brief.status} />
          <Detail.Metadata.Label title="Created" text={formatTimestamp(brief.created_at)} />
          <Detail.Metadata.Label title="Summary ID" text={String(brief.summary_id)} />
        </Detail.Metadata>
      }
      actions={
        <ActionPanel>
          <Action.CopyToClipboard title="Copy Notes" content={brief.notes} />
          <Action.CopyToClipboard title="Copy Markdown" content={markdown} />
        </ActionPanel>
      }
    />
  );
}
