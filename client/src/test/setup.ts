import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(cleanup);

// jsdom has no layout engine and no scrollIntoView. The property is *absent*, so
// `element.scrollIntoView({…})` is a TypeError thrown out of a click handler — a run
// in which every assertion passed still fails. Nothing asserted anywhere depends on
// scrolling actually happening, so a no-op is the honest stub, and the citation test
// in chatPanel.test.tsx spies on it.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}

// The zustand store is a module singleton, so state written by one test is still
// there in the next one unless it is reset explicitly. The import is deliberately
// lazy: a static one here runs before a test file's `vi.mock("../services/api")` is
// registered, which would pin the store to the real, unmocked api module.
afterEach(async () => {
  const { __resetStoreForTests } = await import("../store");
  __resetStoreForTests();
});
