import { describe, expect, it } from "vitest";

import {
  acceptAttribute,
  acceptedLabel,
  resolveMimeType,
  validateFile,
} from "@/lib/media-upload";
import type { MediaCapabilities } from "@/lib/types";

const CAPABILITIES: MediaCapabilities = {
  ffmpeg_available: true,
  max_upload_bytes: 100 * 1024 * 1024,
  chunk_size_bytes: 5 * 1024 * 1024,
  allowed_mime_types: ["video/mp4", "video/quicktime", "video/webm"],
  signed_url_ttl_seconds: 900,
  thumbnail_max_width: 640,
};

function file(name: string, type: string, size = 1024): File {
  // ``Uint8Array`` lets us fake the reported size via a real byte count.
  return new File([new Uint8Array(size).fill(1)], name, { type });
}

describe("acceptedLabel", () => {
  it("maps known MIME types to container names", () => {
    expect(acceptedLabel(["video/mp4", "video/quicktime", "video/webm"])).toBe("MP4, MOV, WebM");
  });

  it("falls back to the raw MIME type for an unknown one", () => {
    expect(acceptedLabel(["video/x-custom"])).toBe("video/x-custom");
  });
});

describe("acceptAttribute", () => {
  it("pairs each MIME type with the extensions that map to it", () => {
    expect(acceptAttribute(["video/mp4"])).toBe("video/mp4,.mp4,.m4v");
  });

  it("does not repeat a shared type", () => {
    const value = acceptAttribute(["video/mp4", "video/mp4"]);
    expect(value).toBe("video/mp4,.mp4,.m4v");
  });
});

describe("resolveMimeType", () => {
  it("prefers the browser-reported type", () => {
    expect(resolveMimeType(file("clip.MP4", "video/MP4"))).toBe("video/mp4");
  });

  it("guesses from the extension when the browser reports none", () => {
    expect(resolveMimeType(file("clip.mkv", ""))).toBe("video/x-matroska");
    expect(resolveMimeType(file("clip.AVI", ""))).toBe("video/x-msvideo");
  });

  it("returns an empty string when the type is unknowable", () => {
    expect(resolveMimeType(file("clip", ""))).toBe("");
  });
});

describe("validateFile", () => {
  it("accepts a supported file", () => {
    expect(validateFile(file("clip.mp4", "video/mp4"), CAPABILITIES)).toBeNull();
  });

  it("rejects an empty file", () => {
    expect(validateFile(file("clip.mp4", "video/mp4", 0), CAPABILITIES)).toBe(
      "That file is empty.",
    );
  });

  it("rejects a file over the size limit, naming both sizes", () => {
    const message = validateFile(
      file("clip.mp4", "video/mp4", CAPABILITIES.max_upload_bytes + 1),
      CAPABILITIES,
    );
    expect(message).toContain("the limit is");
    expect(message).toContain("100 MB");
  });

  it("rejects a file whose type cannot be determined", () => {
    expect(validateFile(file("clip", ""), CAPABILITIES)).toContain(
      "Could not determine the file type",
    );
  });

  it("rejects a type that is not on the allow-list", () => {
    const message = validateFile(file("clip.avi", "video/x-msvideo"), CAPABILITIES);
    expect(message).toContain("is not an accepted video type");
    expect(message).toContain("MP4");
  });
});
