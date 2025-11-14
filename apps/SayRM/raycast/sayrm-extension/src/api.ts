import { getPreferenceValues } from "@raycast/api";

import {
  CompanyContextResponse,
  ComposeDraftResponse,
  DraftPreview,
  ExternalBrief,
  InternalBrief,
  TemplateInfo,
} from "./types";

type Prefs = {
  baseUrl?: string;
};

const prefs = getPreferenceValues<Prefs>();
const BASE_URL = (prefs.baseUrl || "http://127.0.0.1:8070").replace(/\/$/, "");

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers || {}),
    },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Request failed (${response.status}): ${detail}`);
  }
  return (await response.json()) as T;
}

export const SayRMApi = {
  async createExternalBrief(callsign: string, manualHighlights: string[]): Promise<ExternalBrief> {
    return request<ExternalBrief>(`/companies/${encodeURIComponent(callsign)}/briefs/external`, {
      method: "POST",
      body: JSON.stringify({ manual_highlights: manualHighlights }),
    });
  },

  async createInternalBrief(callsign: string): Promise<InternalBrief> {
    return request<InternalBrief>(`/companies/${encodeURIComponent(callsign)}/briefs/internal`, {
      method: "POST",
    });
  },

  async listTemplates(): Promise<TemplateInfo[]> {
    return request<TemplateInfo[]>("/templates");
  },

  async composeDraft(payload: {
    callsign: string;
    template_id?: string;
    instructions?: string;
    manual_snippets?: string[];
    external_summary?: string;
    internal_summary?: string;
  }): Promise<ComposeDraftResponse> {
    return request<ComposeDraftResponse>("/drafts/compose", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  async listDrafts(callsign?: string, limit = 5): Promise<DraftPreview[]> {
    const params = new URLSearchParams();
    if (callsign) params.set("callsign", callsign);
    params.set("limit", String(limit));
    const data = await request<{ drafts: DraftPreview[] }>(`/drafts/recent?${params.toString()}`);
    return data.drafts;
  },

  async labelDraft(draftId: number, labels: Record<string, string>, createdBy?: string): Promise<void> {
    await request("/drafts/labels", {
      method: "POST",
      body: JSON.stringify({ draft_id: draftId, labels, created_by: createdBy }),
    });
  },

  async buildContext(callsign: string, manualHighlights: string[] = []): Promise<CompanyContextResponse> {
    return request<CompanyContextResponse>(`/companies/${encodeURIComponent(callsign)}/context`, {
      method: "POST",
      body: JSON.stringify({ manual_highlights: manualHighlights }),
    });
  },
};
