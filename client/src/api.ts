import axios, { type AxiosResponse } from "axios";

import type {
  AiEditRequest,
  AiEditResponse,
  DocumentDetail,
  DocumentSummary,
  VersionRead,
  VersionWrite,
} from "./types";

/**
 * The only place that knows the base URL, the timeouts, and how to turn an
 * unknown throwable into a sentence a user can read. No React and no store
 * imports, so a caller can mock this module with a single `vi.mock`.
 */
export const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const http = axios.create({ baseURL: BASE_URL, timeout: 15_000 });
// The AI route waits on OpenAI; the server gives up at 60 s, so the client must
// wait longer than that or it reports a timeout for a request that succeeded.
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

export function listDocuments(): Promise<DocumentSummary[]> {
  return request<DocumentSummary[]>(() => http.get("/api/documents"));
}

export function getDocument(id: number): Promise<DocumentDetail> {
  return request<DocumentDetail>(() => http.get(`/api/documents/${id}`));
}

export function getVersion(id: number, versionNumber: number): Promise<VersionRead> {
  return request<VersionRead>(() => http.get(`/api/documents/${id}/versions/${versionNumber}`));
}

export function createVersion(id: number, content: string): Promise<VersionRead> {
  const body: VersionWrite = { content };
  return request<VersionRead>(() => http.post(`/api/documents/${id}/versions`, body));
}

export function updateVersion(
  id: number,
  versionNumber: number,
  content: string,
): Promise<VersionRead> {
  const body: VersionWrite = { content };
  return request<VersionRead>(() => http.put(`/api/documents/${id}/versions/${versionNumber}`, body));
}

/**
 * `status: "error"` is a 200 with a message, not an exception — this throws only
 * on transport or HTTP failures.
 */
export function aiEdit(body: AiEditRequest): Promise<AiEditResponse> {
  return request<AiEditResponse>(() => aiHttp.post("/api/ai/edit", body));
}
