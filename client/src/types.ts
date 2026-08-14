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

/**
 * Read-only context. Deliberately carries no ProseMirror positions: nothing on the
 * wire can address a range, so no operation can target one.
 */
export interface AiSelection {
  text: string;
  claim_numbers: number[];
  whole_claims: boolean;
  truncated: boolean;
}

/** One planner operation. Claims are addressed by NUMBER and regions by NAME. */
export interface AiOperation {
  kind:
    | "format_claim"
    | "delete_claim"
    | "insert_claim"
    | "replace_claim"
    | "insert_section"
    | "replace_text";
  claim_number: number | null;
  after_claim_number: number | null;
  mark: "bold" | "italic" | "strike" | null;
  enabled: boolean | null;
  text: string | null;
  heading: string | null;
  paragraphs: string[] | null;
  position: "before_claims" | "after_claims" | null;
  find: string | null;
  replace: string | null;
}

export interface AiVerifyReport {
  ok: boolean;
  /** Non-empty means the edit was blocked. */
  errors: string[];
  warnings: string[];
}

/**
 * Round-trips through this client between the two AI calls. The server keeps no copy,
 * so there is nothing to lose on a restart — and nothing to garbage-collect either.
 * It carries no HTML of any kind: the preview is `summary` plus the operations' own
 * text, rendered by us.
 */
export interface AiProposal {
  proposal_id: string;
  document_id: number;
  version_number: number;
  /** sha256 of the exact html the plan was written against. */
  base_sha256: string;
  created_at: string;
  message: string;
  /** One human-readable line per operation. */
  summary: string[];
  /** True when the plan writes NEW PROSE rather than only rearranging existing text. */
  authors_new_text: boolean;
  operations: AiOperation[];
}

export interface AiChatRequest {
  document_id: number;
  version_number: number;
  /** MUST be `editor.getHTML()`, never a version's stored `content` — see `aiChat`. */
  html: string;
  instruction: string;
  context_text: string | null;
  /** The uploaded file's NAME only. Its contents travel in `context_text`. */
  context_name: string | null;
  selection: AiSelection | null;
  history: ChatTurn[];
  /** True once the user has approved AI editing of this document AND this version. */
  consented: boolean;
  pending_question: string | null;
  clarify_count: number;
}

export interface AiChatResponse {
  status: "applied" | "proposal" | "answer" | "no_change" | "needs_clarification" | "error";
  message: string;
  /** Non-null if and only if `status === "applied"`. The server nulls it on every
   *  other outcome, so `if (res.html)` is the whole of invariant 3 on this side.
   *  From /chat it additionally implies the request carried `consented: true`. */
  html: string | null;
  /** Non-null if and only if `status === "proposal"`. */
  proposal: AiProposal | null;
  verification: AiVerifyReport | null;
  warnings: string[];
  /** Claim numbers whose quotes the SERVER verified. Only ever populated on `answer`. */
  citations: number[];
  /** Complete prefilled instructions. Clicking one SENDS it verbatim as the next
   *  instruction — never an id, because an id would need server state between two
   *  HTTP calls. Only ever populated on `needs_clarification`. */
  options: string[];
}

export interface AiApplyRequest {
  /** The LIVE editor html at the moment Apply was clicked, not what was sent to /chat. */
  html: string;
  proposal: AiProposal;
}

export interface AiApplyResponse {
  status: "applied" | "no_change" | "error";
  message: string;
  html: string | null;
  verification: AiVerifyReport | null;
  warnings: string[];
}

// ------------------------------------------------------------------- the import surface

export interface TextImportRequest {
  /** The decoded file content. Non-UTF-8, NUL bytes and the wrong extension are already
   *  rejected client-side by `textFile.ts`. */
  text: string;
  /** Used only to suggest a title when the file's own first line is not one. */
  filename: string | null;
}

export interface TextImportResult {
  title: string;
  /** Exactly what a save will store: the server sanitises and canonicalises before
   *  returning, so the preview is not an approximation of the result. */
  content: string;
  claim_count: number;
  /** Everything the importer was unsure about, in sentences a user can read. Empty when
   *  the file converted cleanly. Never silently dropped content. */
  notes: string[];
}
