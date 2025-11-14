import { Action, ActionPanel, List, Toast, showToast } from "@raycast/api";
import { useEffect, useState } from "react";

import { SayRMApi } from "../api";
import { TemplateInfo } from "../types";

export default function TemplatePickerCommand() {
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        setIsLoading(true);
        const data = await SayRMApi.listTemplates();
        setTemplates(data);
      } catch (error) {
        await showToast({
          style: Toast.Style.Failure,
          title: "Failed to load templates",
          message: String(error),
        });
      } finally {
        setIsLoading(false);
      }
    }
    load();
  }, []);

  return (
    <List isLoading={isLoading} searchBarPlaceholder="Search templates…">
      {templates.map((template) => (
        <List.Item
          key={template.id}
          title={template.title}
          subtitle={template.description}
          accessories={template.tags.map((tag) => ({ tag }))}
          actions={
            <ActionPanel>
              <Action.CopyToClipboard title="Copy Template Body" content={template.body} />
              <Action.Paste content={template.body} />
            </ActionPanel>
          }
        />
      ))}
    </List>
  );
}

