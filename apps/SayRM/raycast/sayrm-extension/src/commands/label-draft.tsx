import {
  Action,
  ActionPanel,
  Form,
  Toast,
  showHUD,
  showToast,
} from "@raycast/api";
import { useEffect, useMemo, useState } from "react";

import { SayRMApi } from "../api";
import { DraftPreview } from "../types";

export default function LabelDraftCommand() {
  const [drafts, setDrafts] = useState<DraftPreview[]>([]);
  const [callsignFilter, setCallsignFilter] = useState("");
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        setIsLoading(true);
        const data = await SayRMApi.listDrafts(callsignFilter || undefined, 10);
        setDrafts(data);
      } catch (error) {
        await showToast({
          style: Toast.Style.Failure,
          title: "Failed to load drafts",
          message: String(error),
        });
      } finally {
        setIsLoading(false);
      }
    }
    load();
  }, [callsignFilter]);

  async function handleSubmit(values: {
    draftId: string;
    quality?: string;
    tone?: string;
    nextStep?: string;
    notes?: string;
    createdBy?: string;
  }) {
    try {
      if (!values.draftId) {
        await showToast({
          style: Toast.Style.Failure,
          title: "Select a draft",
          message: "Choose a draft before saving labels.",
        });
        return;
      }
      await showToast({ style: Toast.Style.Animated, title: "Saving labels…" });
      const labels: Record<string, string> = {};
      if (values.quality) labels["quality"] = values.quality;
      if (values.tone) labels["tone"] = values.tone;
      if (values.nextStep) labels["next_step"] = values.nextStep;
      if (values.notes) labels["notes"] = values.notes;
      await SayRMApi.labelDraft(Number(values.draftId), labels, values.createdBy);
      await showHUD("Labels saved");
    } catch (error) {
      await showToast({
        style: Toast.Style.Failure,
        title: "Failed to save labels",
        message: String(error),
      });
    }
  }

  const draftOptions = useMemo(
    () =>
      drafts.map((draft) => ({
        value: String(draft.id),
        title: `${draft.callsign} – ${draft.body.slice(0, 40)}…`,
      })),
    [drafts]
  );

  return (
    <Form
      isLoading={isLoading}
      actions={
        <ActionPanel>
          <Action.SubmitForm title="Save Labels" onSubmit={handleSubmit} />
        </ActionPanel>
      }
    >
      <Form.TextField
        id="callsignFilter"
        title="Filter by Callsign"
        placeholder="acme (optional)"
        value={callsignFilter}
        onChange={setCallsignFilter}
      />
      <Form.Dropdown id="draftId" title="Draft" storeValue>
        {draftOptions.length === 0 ? (
          <Form.Dropdown.Item value="" title="No drafts available" />
        ) : (
          draftOptions.map((option) => <Form.Dropdown.Item key={option.value} {...option} />)
        )}
      </Form.Dropdown>
      <Form.Dropdown id="quality" title="Quality" storeValue>
        <Form.Dropdown.Item value="" title="Unspecified" />
        <Form.Dropdown.Item value="great" title="Great" />
        <Form.Dropdown.Item value="ok" title="Needs Light Edits" />
        <Form.Dropdown.Item value="rewrite" title="Needs Rewrite" />
      </Form.Dropdown>
      <Form.Dropdown id="tone" title="Tone" storeValue>
        <Form.Dropdown.Item value="" title="Unspecified" />
        <Form.Dropdown.Item value="on_voice" title="On Voice" />
        <Form.Dropdown.Item value="too_formal" title="Too Formal" />
        <Form.Dropdown.Item value="too_salesy" title="Too Salesy" />
      </Form.Dropdown>
      <Form.Dropdown id="nextStep" title="Next Step" storeValue>
        <Form.Dropdown.Item value="" title="None" />
        <Form.Dropdown.Item value="send" title="Ready to Send" />
        <Form.Dropdown.Item value="tweak" title="Tweak Needed" />
        <Form.Dropdown.Item value="discard" title="Discard" />
      </Form.Dropdown>
      <Form.TextArea id="notes" title="Notes" />
      <Form.TextField id="createdBy" title="Your Initials" placeholder="WM" />
    </Form>
  );
}
