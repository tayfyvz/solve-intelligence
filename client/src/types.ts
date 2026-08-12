/**
 * The wire format, snake_case, mirroring the FastAPI schemas field for field.
 *
 * There is deliberately no camelCase mapping layer: it would be pure translation
 * whose only failure mode is silent drift, and it turns every "is this field
 * named right?" question into a two-file lookup.
 */

export interface DocumentSummary {
  id: number;
  title: string;
}

export interface VersionSummary {
  version_number: number;
  updated_at: string;
}

export interface DocumentDetail {
  id: number;
  title: string;
  versions: VersionSummary[];
}

export interface VersionRead {
  document_id: number;
  version_number: number;
  /** May legitimately be "" — a cleared draft. Never test truthiness. */
  content: string;
  updated_at: string;
}

export interface VersionWrite {
  content: string;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface AiEditRequest {
  html: string;
  instruction: string;
  context_text: string | null;
  history: ChatTurn[];
}

export interface AiEditResponse {
  status: "ok" | "needs_clarification" | "error";
  /** Non-null only when the document actually changed. */
  html: string | null;
  message: string;
  warnings: string[];
}
