import { AxiosError, AxiosHeaders, type AxiosResponse } from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";

// `axios.create` runs at api.ts module scope, so the stubs have to exist before
// the module is imported — that is what `vi.hoisted` buys. Two stubs, told apart
// by the timeout, so "aiEdit uses the long-timeout instance" is testable.
const { stub, aiStub, configs } = vi.hoisted(() => ({
  stub: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
  aiStub: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
  configs: [] as { baseURL?: string; timeout?: number }[],
}));

vi.mock("axios", async (importOriginal) => {
  const actual = await importOriginal<typeof import("axios")>();
  const create = (config: { baseURL?: string; timeout?: number }) => {
    configs.push(config);
    return (config.timeout ?? 0) > 60_000 ? aiStub : stub;
  };
  return {
    ...actual,
    default: Object.assign(Object.create(null), actual.default, { create }),
  };
});

const {
  ApiError,
  BASE_URL,
  aiEdit,
  createVersion,
  getDocument,
  getVersion,
  listDocuments,
  toMessage,
  updateVersion,
} = await import("../api");

/** A real AxiosError, so `axios.isAxiosError` and the `.response` shape are genuine. */
function axiosError(
  code: string | undefined,
  response?: { status: number; data: unknown },
): AxiosError {
  const config = { headers: new AxiosHeaders() };
  const full = response
    ? ({
        status: response.status,
        statusText: "",
        data: response.data,
        headers: {},
        config,
      } as AxiosResponse)
    : undefined;
  return new AxiosError("request failed", code, config, {}, full);
}

describe("toMessage", () => {
  const cases: { name: string; error: unknown; expected: string }[] = [
    {
      name: "a timeout",
      error: axiosError("ECONNABORTED"),
      expected: "The request timed out.",
    },
    {
      name: "no response at all",
      error: axiosError("ERR_NETWORK"),
      expected: `Cannot reach the server. Is it running on ${BASE_URL}?`,
    },
    {
      name: "a string detail (HTTPException)",
      error: axiosError(undefined, { status: 404, data: { detail: "Document 9 not found." } }),
      expected: "Document 9 not found.",
    },
    {
      name: "an array detail (422)",
      error: axiosError(undefined, {
        status: 422,
        data: { detail: [{ msg: "Field required" }, { msg: "Input should be a valid string" }] },
      }),
      expected: "Invalid request: Field required; Input should be a valid string",
    },
    {
      name: "a bare status",
      error: axiosError(undefined, { status: 500, data: "Internal Server Error" }),
      expected: "Request failed (500).",
    },
    {
      name: "a non-axios throwable",
      error: "boom",
      expected: "Something went wrong.",
    },
  ];

  it.each(cases)("renders $name", ({ error, expected }) => {
    expect(toMessage(error)).toBe(expected);
  });

  it("prefers a plain Error's own message", () => {
    expect(toMessage(new Error("editor exploded"))).toBe("editor exploded");
  });
});

describe("helpers", () => {
  beforeEach(() => {
    for (const fn of [stub.get, stub.post, stub.put, aiStub.get, aiStub.post, aiStub.put]) {
      fn.mockReset();
    }
  });

  // The verb and the path are the whole contract with FastAPI, and a put/post
  // slip in updateVersion would turn every save into a new version (invariant 9).
  it("calls the documented route and verb for each helper", async () => {
    stub.get.mockResolvedValue({ data: null });
    stub.post.mockResolvedValue({ data: null });
    stub.put.mockResolvedValue({ data: null });

    await listDocuments();
    expect(stub.get).toHaveBeenCalledWith("/api/documents");

    await getDocument(7);
    expect(stub.get).toHaveBeenCalledWith("/api/documents/7");

    await getVersion(7, 3);
    expect(stub.get).toHaveBeenCalledWith("/api/documents/7/versions/3");

    await createVersion(7, "<p>new</p>");
    expect(stub.post).toHaveBeenCalledWith("/api/documents/7/versions", { content: "<p>new</p>" });

    await updateVersion(7, 3, "<p>edited</p>");
    expect(stub.put).toHaveBeenCalledWith("/api/documents/7/versions/3", {
      content: "<p>edited</p>",
    });
    expect(stub.post).toHaveBeenCalledTimes(1); // update never creates
  });

  it("gives the AI route a timeout longer than the server's 60 s", () => {
    expect(configs).toEqual([
      { baseURL: BASE_URL, timeout: 15_000 },
      { baseURL: BASE_URL, timeout: 90_000 },
    ]);
  });

  it("rethrows an axios failure as an ApiError that keeps the status", async () => {
    stub.get.mockRejectedValue(
      axiosError(undefined, { status: 503, data: { detail: "AI editing is unavailable." } }),
    );

    const error = await getVersion(1, 1).then(
      () => null,
      (e: unknown) => e,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect((error as InstanceType<typeof ApiError>).status).toBe(503);
    expect((error as Error).message).toBe("AI editing is unavailable.");
  });

  it("reports a null status when the throwable is not an axios error", async () => {
    stub.get.mockRejectedValue(new Error("something odd"));

    const error = await getVersion(1, 1).then(
      () => null,
      (e: unknown) => e,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect((error as InstanceType<typeof ApiError>).status).toBeNull();
    expect((error as Error).message).toBe("something odd");
  });

  it("returns empty content as-is rather than treating it as a failure", async () => {
    stub.get.mockResolvedValue({
      data: { document_id: 1, version_number: 2, content: "", updated_at: "2026-01-01T00:00:00" },
    });

    await expect(getVersion(1, 2)).resolves.toMatchObject({ content: "" });
  });

  it('does not throw when the AI responds with status "error"', async () => {
    aiStub.post.mockResolvedValue({
      data: { status: "error", html: null, message: "I could not apply that.", warnings: [] },
    });

    await expect(
      aiEdit({ html: "<p>a</p>", instruction: "do it", context_text: null, history: [] }),
    ).resolves.toEqual({
      status: "error",
      html: null,
      message: "I could not apply that.",
      warnings: [],
    });
  });
});
