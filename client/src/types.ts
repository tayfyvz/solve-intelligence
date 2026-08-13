/**
 * The wire format, snake_case, mirroring the FastAPI schemas field for field.
 *
 * There is deliberately no camelCase mapping layer: it would be pure translation
 * whose only failure mode is silent drift, and it turns every "is this field
 * named right?" question into a two-file lookup.
 *
 * `server/tests/test_client_contract.py` reads the `export interface X {` blocks
 * in this file and asserts each one names a server schema with the same fields,
 * so every interface below must be a real wire shape. `Page<T>` is generic and
 * therefore invisible to that regex — which is what we want, since FastAPI names
 * the envelope per item type.
 */

/** The list envelope shared by both paginated endpoints. */
export interface Page<T> {
  items: T[];
  /** Unfiltered row count, so the client can render page numbers. */
  total: number;
  limit: number;
  offset: number;
}

export type DocumentPage = Page<DocumentSummary>;
export type VersionPage = Page<VersionSummary>;

export interface DocumentSummary {
  id: number;
  title: string;
  version_count: number;
  /** The most recent version's `updated_at` — "last touched". */
  updated_at: string;
}

export interface DocumentDetail {
  id: number;
  title: string;
  version_count: number;
  latest_version_number: number;
  created_at: string;
  updated_at: string;
}

export interface VersionSummary {
  version_number: number;
  name: string;
  created_at: string;
  updated_at: string;
}

export interface VersionRead {
  document_id: number;
  version_number: number;
  name: string;
  /** May legitimately be "" — a cleared draft. Never test truthiness. */
  content: string;
  created_at: string;
  updated_at: string;
}

export interface DocumentCreate {
  title: string;
  /** null means "start with an empty first version". */
  content: string | null;
}

export interface DocumentRename {
  title: string;
}

export interface VersionCreate {
  content: string;
  /** null means "let the server name it `Version {n}`". */
  name: string | null;
}

/** PUT: content only. Renaming is a different verb, so a save can never rename. */
export interface VersionUpdate {
  content: string;
}

export interface VersionRename {
  name: string;
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
