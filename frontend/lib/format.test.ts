import { describe, expect, it } from "vitest";

import {
  formatBytes,
  formatDate,
  formatDateTime,
  formatDateTimeInZone,
  formatDuration,
  formatRelative,
} from "@/lib/format";

const ISO = "2026-03-12T14:05:00Z";

describe("formatBytes", () => {
  it("reports sub-kibibyte sizes as whole bytes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1023)).toBe("1023 B");
  });

  it("scales by 1024 and keeps one decimal below ten units", () => {
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("drops the decimal from ten units upward", () => {
    expect(formatBytes(12 * 1024 * 1024)).toBe("12 MB");
  });

  it("climbs to the largest fitting unit", () => {
    expect(formatBytes(3 * 1024 ** 3)).toBe("3.0 GB");
    expect(formatBytes(2 * 1024 ** 4)).toBe("2.0 TB");
  });

  it("renders an em dash for missing or non-finite input", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(undefined)).toBe("—");
    expect(formatBytes(Number.NaN)).toBe("—");
    expect(formatBytes(Number.POSITIVE_INFINITY)).toBe("—");
  });
});

describe("formatDuration", () => {
  it("uses mm:ss below an hour", () => {
    expect(formatDuration(0)).toBe("0:00");
    expect(formatDuration(7)).toBe("0:07");
    expect(formatDuration(67)).toBe("1:07");
  });

  it("adds hours when the clip is long enough", () => {
    expect(formatDuration(3723)).toBe("1:02:03");
  });

  it("rounds to the nearest second", () => {
    expect(formatDuration(59.6)).toBe("1:00");
  });

  it("renders an em dash for missing or negative input", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(undefined)).toBe("—");
    expect(formatDuration(-1)).toBe("—");
    expect(formatDuration(Number.NaN)).toBe("—");
  });
});

describe("formatDateTime / formatDate", () => {
  it("renders the year for a valid instant", () => {
    expect(formatDateTime(ISO)).toContain("2026");
    expect(formatDate(ISO)).toContain("2026");
  });

  it("renders an em dash for missing or unparseable input", () => {
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime(undefined)).toBe("—");
    expect(formatDate("not-a-date")).toBe("—");
  });
});

describe("formatDateTimeInZone", () => {
  it("renders the given instant in the requested IANA zone", () => {
    expect(formatDateTimeInZone(ISO, "Asia/Tokyo")).toContain("2026");
  });

  it("falls back to the viewer's zone when the zone is unknown", () => {
    expect(formatDateTimeInZone(ISO, "Not/AZone")).toBe(formatDateTime(ISO));
  });

  it("renders an em dash for missing input", () => {
    expect(formatDateTimeInZone(null, "UTC")).toBe("—");
  });
});

describe("formatRelative", () => {
  it("reports a missing instant as an em dash", () => {
    expect(formatRelative(null)).toBe("—");
    expect(formatRelative("nonsense")).toBe("—");
  });

  it("describes a future instant with its largest unit", () => {
    const inTwoDays = new Date(Date.now() + 2 * 24 * 60 * 60 * 1000).toISOString();
    const rendered = formatRelative(inTwoDays);
    expect(rendered).toContain("2");
    expect(rendered).toContain("day");
  });

  it("describes a past instant", () => {
    const threeHoursAgo = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString();
    const rendered = formatRelative(threeHoursAgo);
    expect(rendered).toContain("3");
    expect(rendered).toContain("hour");
  });
});
