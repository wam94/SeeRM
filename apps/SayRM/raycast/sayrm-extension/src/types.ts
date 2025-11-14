export interface ExternalBrief {
  summary_id: number;
  callsign: string;
  company_name?: string | null;
  product: string;
  news: string[];
  announcements: string[];
  raw_text: string;
  created_at: string;
}

export interface InternalBrief {
  summary_id: number;
  callsign: string;
  notes: string;
  status: string;
  created_at: string;
}

export interface TemplateInfo {
  id: string;
  title: string;
  description: string;
  body: string;
  tags: string[];
}

export interface DraftPreview {
  id: number;
  callsign: string;
  template_id?: string | null;
  body: string;
  created_at: string;
}

export interface ComposeDraftResponse {
  draft_id: number;
  callsign: string;
  template_id?: string | null;
  body: string;
  created_at: string;
}

export interface InternalUsageSnapshot {
  status: string;
  owners: string[];
  products: string[];
  notes?: string | null;
  raw?: Record<string, unknown> | null;
}

export interface ExternalContextCard {
  brief: ExternalBrief;
  context: Record<string, unknown>;
}

export interface InternalContextCard {
  brief: InternalBrief;
  snapshot: InternalUsageSnapshot;
}

export interface CompanyContextResponse {
  callsign: string;
  external?: ExternalContextCard;
  internal?: InternalContextCard;
  templates: TemplateInfo[];
}
