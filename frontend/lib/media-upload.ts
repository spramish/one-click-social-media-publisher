/**
 * Client side of the resumable upload protocol in
 * `backend/app/api/v1/endpoints/media.py`.
 *
 * Five stages, in order:
 *
 *   1. `POST   /media/{ws}/uploads`            declare filename, MIME, size
 *   2. `PUT    /media/{ws}/uploads/{id}?offset=` append one chunk of raw bytes
 *   3. `GET    /media/{ws}/uploads/{id}`       ask how many bytes landed
 *   4. `POST   /media/{ws}/uploads/{id}/complete` verify, FFprobe, store
 *   5. `DELETE /media/{ws}/uploads/{id}`       abandon
 *
 * Stage 3 is what makes the transfer recoverable: a chunk that arrives out of
 * order is refused with `409` and the server's real offset is re-read, so a
 * dropped connection costs a retry rather than the whole file.
 */

import { ApiError, api } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import type { MediaCapabilities, MediaFile } from "@/lib/types";

/** Chunk attempts before an upload gives up (the session stays resumable). */
const MAX_ATTEMPTS_PER_CHUNK = 3;

/** Fallback used only if the server omits `chunk_size_bytes`. */
const DEFAULT_CHUNK_BYTES = 5 * 1024 * 1024;

export type UploadProgress = {
  /** Bytes durably stored on the server, not bytes merely sent. */
  uploadedBytes: number;
  totalBytes: number;
  /** Rounded 0–100, for display. */
  percent: number;
};

export type UploadVideoOptions = {
  workspaceId: string;
  file: File;
  /** When set, the finished upload replaces this media file's bytes in place. */
  replaceMediaId?: string | null;
  onProgress?: (progress: UploadProgress) => void;
  signal?: AbortSignal;
};

/** Container labels for the MIME types the API accepts. */
const MIME_LABELS: Record<string, string> = {
  "video/mp4": "MP4",
  "video/quicktime": "MOV",
  "video/webm": "WebM",
  "video/x-matroska": "MKV",
  "video/x-msvideo": "AVI",
};

/** File extensions used to guess a type when the browser reports none. */
const EXTENSION_MIME_TYPES: Record<string, string> = {
  ".mp4": "video/mp4",
  ".m4v": "video/mp4",
  ".mov": "video/quicktime",
  ".webm": "video/webm",
  ".mkv": "video/x-matroska",
  ".avi": "video/x-msvideo",
};

/** Human list of accepted containers, e.g. "MP4, MOV, WebM". */
export function acceptedLabel(mimeTypes: string[]): string {
  return mimeTypes.map((mime) => MIME_LABELS[mime] ?? mime).join(", ");
}

/** `accept` attribute value for a file input, pairing each type with extensions. */
export function acceptAttribute(mimeTypes: string[]): string {
  const parts = new Set<string>();
  for (const mime of mimeTypes) {
    parts.add(mime);
    for (const [extension, type] of Object.entries(EXTENSION_MIME_TYPES)) {
      if (type === mime) parts.add(extension);
    }
  }
  return [...parts].join(",");
}

/**
 * The browser's MIME guess, falling back to the extension.
 *
 * Some platforms report an empty type for Matroska or AVI. The server validates
 * the declared type, so guessing from the extension here keeps a legitimate file
 * from being rejected before it is even offered.
 */
export function resolveMimeType(file: File): string {
  if (file.type) return file.type.toLowerCase();
  const dot = file.name.lastIndexOf(".");
  if (dot < 0) return "";
  return EXTENSION_MIME_TYPES[file.name.slice(dot).toLowerCase()] ?? "";
}

/** Returns a message when the file cannot be accepted, else `null`. */
export function validateFile(file: File, capabilities: MediaCapabilities): string | null {
  if (file.size === 0) return "That file is empty.";
  if (file.size > capabilities.max_upload_bytes) {
    return `That file is ${formatBytes(file.size)}; the limit is ${formatBytes(
      capabilities.max_upload_bytes,
    )}.`;
  }
  const mimeType = resolveMimeType(file);
  if (!mimeType) {
    return `Could not determine the file type. Accepted: ${acceptedLabel(
      capabilities.allowed_mime_types,
    )}.`;
  }
  if (!capabilities.allowed_mime_types.includes(mimeType)) {
    return `“${mimeType}” is not an accepted video type. Accepted: ${acceptedLabel(
      capabilities.allowed_mime_types,
    )}.`;
  }
  return null;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function abortQuietly(workspaceId: string, uploadId: string): Promise<void> {
  try {
    await api.media.abortUpload(workspaceId, uploadId);
  } catch {
    /* Best effort: the session expires on its own after the server TTL. */
  }
}

/**
 * Declare, transfer, and finalize one video.
 *
 * Resolves with the stored `MediaFile`. Rejects with the underlying
 * {@link ApiError} when the server refuses the file (`415`, `413`, `422`,
 * `503`) or the transfer cannot be completed.
 */
export async function uploadVideo({
  workspaceId,
  file,
  replaceMediaId = null,
  onProgress,
  signal,
}: UploadVideoOptions): Promise<MediaFile> {
  const session = await api.media.createUpload(workspaceId, {
    filename: file.name,
    mime_type: resolveMimeType(file),
    size_bytes: file.size,
    replace_media_id: replaceMediaId,
  });

  const chunkSize = session.chunk_size_bytes || DEFAULT_CHUNK_BYTES;
  let offset = session.offset;

  const report = () =>
    onProgress?.({
      uploadedBytes: offset,
      totalBytes: file.size,
      percent: file.size ? Math.round((offset / file.size) * 100) : 0,
    });
  report();

  try {
    while (offset < file.size) {
      const chunk = file.slice(offset, Math.min(offset + chunkSize, file.size));
      let attempts = 0;
      for (;;) {
        try {
          const updated = await api.media.appendChunk(workspaceId, session.id, offset, chunk, signal);
          offset = updated.offset;
          report();
          break;
        } catch (error) {
          if (signal?.aborted) throw error;
          if (error instanceof ApiError && error.status === 409) {
            // Out of order: re-read the server's offset and re-slice from there.
            const latest = await api.media.getUpload(workspaceId, session.id);
            offset = latest.offset;
            report();
            break;
          }
          attempts += 1;
          if (attempts >= MAX_ATTEMPTS_PER_CHUNK) throw error;
          await delay(attempts * 500);
        }
      }
    }
  } catch (error) {
    if (signal?.aborted) await abortQuietly(workspaceId, session.id);
    // Otherwise the session stays on disk and can be resumed inside its TTL.
    throw error;
  }

  try {
    return await api.media.completeUpload(workspaceId, session.id);
  } catch (error) {
    // The bytes were refused, so there is nothing worth resuming.
    if (error instanceof ApiError && [413, 415, 422, 503].includes(error.status)) {
      await abortQuietly(workspaceId, session.id);
    }
    throw error;
  }
}
