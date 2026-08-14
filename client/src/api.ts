import axios, { type AxiosResponse } from "axios";

import type {
  AiApplyRequest,
  AiApplyResponse,
  AiChatRequest,
  AiChatResponse,
  DocumentCreate,
  DocumentDetail,
  DocumentPage,
  DocumentRename,
  VersionCreate,
  VersionPage,
  VersionRead,
  VersionRename,
  VersionUpdate,
} from "./types";

/**
 * The only place that knows the base URL, the timeouts, and how to turn an
 * unknown throwable into a sentence a user can read. No React and no store
 * imports, so a caller can mock this module with a single `vi.mock`.
 */
export const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const http = axios.create({ baseURL: BASE_URL, timeout: 15_000 });
// PLAN §3.4: the server's own budget is 75 s (ai_request_timeout_seconds). The client
// must wait longer than that, or it reports a timeout for a request that succeeded.
// 90_000 is UNCHANGED from Task 1 — only the reason is new. The old comment said
// "> the server's 60 s", which stopped being true the moment the graph landed.
const aiHttp = axios.create({ baseURL: BASE_URL, timeout: 90_000 });

/**
 * Carries the HTTP status alongside the message, because callers need to tell
 * "AI is not configured" (503) from any other failure, and `toMessage` erases
 * that distinction.
 */
export class ApiError extends Error {
  constructor(
    readonly status: number | null,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Rules in order — this is what makes "never fail silently" true. */
export function toMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (error.code === "ECONNABORTED") return "The request timed out.";
    if (!error.response) return `Cannot reach the server. Is it running on ${BASE_URL}?`;

    const data: unknown = error.response.data;
    const detail =
      typeof data === "object" && data !== null
        ? (data as Record<string, unknown>).detail
        : undefined;

    // FastAPI's HTTPException detail: already a readable sentence.
    if (typeof detail === "string" && detail) return detail;

    // A 422 body: a list of validation errors, each with a `msg`.
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => {
          const msg =
            typeof item === "object" && item !== null
              ? (item as Record<string, unknown>).msg
              : undefined;
          return typeof msg === "string" ? msg : "";
        })
        .filter(Boolean);
      if (messages.length) return `Invalid request: ${messages.join("; ")}`;
    }

    return `Request failed (${error.response.status}).`;
  }

  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong.";
}

/** One catch for every helper, so no helper can leak a raw axios error. */
async function request<T>(send: () => Promise<AxiosResponse<T>>): Promise<T> {
  try {
    return (await send()).data;
  } catch (error) {
    const status = axios.isAxiosError(error) ? (error.response?.status ?? null) : null;
    throw new ApiError(status, toMessage(error));
  }
}

/** Pagination is a query string, never part of the path — see `params` below. */
export function listDocuments(limit: number, offset: number): Promise<DocumentPage> {
  return request<DocumentPage>(() => http.get("/api/documents", { params: { limit, offset } }));
}

export function createDocument(title: string, content: string | null): Promise<DocumentDetail> {
  const body: DocumentCreate = { title, content };
  return request<DocumentDetail>(() => http.post("/api/documents", body));
}

export function getDocument(id: number): Promise<DocumentDetail> {
  return request<DocumentDetail>(() => http.get(`/api/documents/${id}`));
}

export function renameDocument(id: number, title: string): Promise<DocumentDetail> {
  const body: DocumentRename = { title };
  return request<DocumentDetail>(() => http.patch(`/api/documents/${id}`, body));
}

export function listVersions(id: number, limit: number, offset: number): Promise<VersionPage> {
  return request<VersionPage>(() =>
    http.get(`/api/documents/${id}/versions`, { params: { limit, offset } }),
  );
}

export function getVersion(id: number, versionNumber: number): Promise<VersionRead> {
  return request<VersionRead>(() => http.get(`/api/documents/${id}/versions/${versionNumber}`));
}

export function createVersion(
  id: number,
  content: string,
  name: string | null = null,
): Promise<VersionRead> {
  const body: VersionCreate = { content, name };
  return request<VersionRead>(() => http.post(`/api/documents/${id}/versions`, body));
}

export function updateVersion(
  id: number,
  versionNumber: number,
  content: string,
): Promise<VersionRead> {
  const body: VersionUpdate = { content };
  return request<VersionRead>(() => http.put(`/api/documents/${id}/versions/${versionNumber}`, body));
}

/** PATCH renames only: it never sends content, so it cannot overwrite a draft. */
export function renameVersion(
  id: number,
  versionNumber: number,
  name: string,
): Promise<VersionRead> {
  const body: VersionRename = { name };
  return request<VersionRead>(() =>
    http.patch(`/api/documents/${id}/versions/${versionNumber}`, body),
  );
}

/**
 * Run 1. Returns one of six outcomes; `status: "error"` is a 200 with a message,
 * not an exception, so this throws only on transport or HTTP failures.
 *
 * `body.html` MUST be `editor.getHTML()`, never a version's stored `content`:
 * the server hashes exactly these bytes into `proposal.base_sha256`, and
 * `aiApply` re-reads `getHTML()`. Hashing two different normalisations of the
 * same document makes every proposal 409.
 */
export function aiChat(body: AiChatRequest): Promise<AiChatResponse> {
  return request<AiChatResponse>(() => aiHttp.post("/api/ai/chat", body));
}

/**
 * Run 2. Deterministic and offline: it never reaches OpenAI, so it works with no
 * API key configured. A 409 means the document drifted since the proposal was
 * written — discard the proposal and ask again, never retry this call.
 *
 * Uses aiHttp (90 s) rather than http (15 s) for one reason only: consistency of
 * the error text the user sees for the two halves of one interaction. /apply is
 * CPU-bound and returns in milliseconds. Say so, or the next reader "fixes" it.
 */
export function aiApply(body: AiApplyRequest): Promise<AiApplyResponse> {
  return request<AiApplyResponse>(() => aiHttp.post("/api/ai/apply", body));
}

/** Transient upstream failures: the same bytes are worth sending again unchanged.
 *  NOT 503 (configuration — a retry cannot help) and NOT 409 (the document moved —
 *  the proposal must be re-asked, not retried). */
export const RETRYABLE_STATUSES: ReadonlySet<number> = new Set([429, 502, 504]);
