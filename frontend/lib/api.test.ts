import { describe, expect, it } from "vitest";

import { ApiError, formatDetail } from "@/lib/api";

describe("formatDetail", () => {
  it("returns a plain string detail unchanged", () => {
    expect(formatDetail("Email already registered.", 409)).toBe("Email already registered.");
  });

  it("ignores a blank string detail", () => {
    expect(formatDetail("   ", 500)).toBe("Request failed (500).");
  });

  it("names the offending field for a FastAPI validation array", () => {
    const detail = [
      { loc: ["body", "email"], msg: "value is not a valid email address" },
    ];
    expect(formatDetail(detail, 422)).toBe("email: value is not a valid email address");
  });

  it("joins several validation errors with a semicolon", () => {
    const detail = [
      { loc: ["body", "email"], msg: "invalid email" },
      { loc: ["body", "password"], msg: "too short" },
    ];
    expect(formatDetail(detail, 422)).toBe("email: invalid email; password: too short");
  });

  it("falls back to a status line for an unrecognised body", () => {
    expect(formatDetail(null, 502)).toBe("Request failed (502).");
    expect(formatDetail({ unexpected: true }, 500)).toBe("Request failed (500).");
  });
});

describe("ApiError", () => {
  it("carries the status and the raw detail", () => {
    const error = new ApiError(404, "Workspace not found");
    expect(error).toBeInstanceOf(Error);
    expect(error.status).toBe(404);
    expect(error.detail).toBe("Workspace not found");
    expect(error.name).toBe("ApiError");
    expect(error.message).toBe("Workspace not found");
  });

  it("derives its message from the detail when none is given", () => {
    const error = new ApiError(422, [{ loc: ["body", "name"], msg: "required" }]);
    expect(error.message).toBe("name: required");
  });

  it("accepts an explicit message", () => {
    const error = new ApiError(500, "boom", "Something went wrong");
    expect(error.message).toBe("Something went wrong");
    expect(error.detail).toBe("boom");
  });
});
