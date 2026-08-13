import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(cleanup);

// The zustand store is a module singleton, so state written by one test is still
// there in the next one unless it is reset explicitly. The import is deliberately
// lazy: a static one here runs before a test file's `vi.mock("../api")` is
// registered, which would pin the store to the real, unmocked api module.
afterEach(async () => {
  const { __resetStoreForTests } = await import("../store");
  __resetStoreForTests();
});
