import { describe, expect, it } from "vitest";

import { cn } from "@/lib/utils";

describe("cn", () => {
  it("joins class names", () => {
    expect(cn("a", "b")).toBe("a b");
  });

  it("ignores falsy values", () => {
    expect(cn("a", false, undefined, null, "c")).toBe("a c");
  });

  it("lets a later Tailwind utility win over an earlier conflict", () => {
    expect(cn("p-2", "p-4")).toBe("p-4");
    expect(cn("text-sm", "text-lg")).toBe("text-lg");
  });

  it("keeps utilities that do not conflict", () => {
    expect(cn("p-4", "text-lg")).toBe("p-4 text-lg");
  });
});
