export interface UIDesignRead {
  id: string;
  project_id: string;
  content: string;
  html_preview: string | null;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface UIDesignCreate {
  project_id: string;
  content?: string;
  html_preview?: string | null;
}

export interface UIDesignUpdate {
  content?: string;
  html_preview?: string | null;
  approved_by?: string | null;
  approved_at?: string | null;
}
