/**
 * Typed API client for the FastAPI backend.
 *
 * Token strategy (see docs/PROJECT_ARCHITECTURE.md §8):
 *   - the **access token** lives in memory only (never localStorage), so a XSS
 *     payload cannot exfiltrate a long-lived credential;
 *   - the **refresh token** lives in an HttpOnly cookie the browser attaches to
 *     `/api/v1/auth/*` requests — JavaScript can never read it.
 *
 * Every request sends `credentials: "include"` so that cookie is present, and a
 * single automatic refresh-and-retry happens on the first `401`.
 */

import { API_BASE_URL as DEFAULT_BASE_URL } from "@/lib/config";
import type { SocialPlatform } from "@/lib/platforms";
import type {
  AccountCapabilities,
  AccountDisconnectResult,
  AccountPerformance,
  AICapability,
  AIContent,
  AIContentKind,
  AIGeneratePayload,
  AIHealthSweepResult,
  AIModel,
  AIModelCreatePayload,
  AIModelPerformance,
  AIModelUpdatePayload,
  AITaskAttemptList,
  AITaskList,
  AnalyticsCapability,
  AnalyticsOverview,
  AuthResponse,
  AuthorizationStart,
  ChangePasswordPayload,
  HealthStatus,
  HistoryPoint,
  LoginPayload,
  MediaCapabilities,
  MediaFile,
  MediaStats,
  MediaStatus,
  MediaUploadRequest,
  MessageResponse,
  NotificationList,
  NotificationPreference,
  NotificationType,
  PlatformPerformance,
  Post,
  PostCreatePayload,
  PostStatus,
  PublishEverywherePayload,
  PublisherCapability,
  PublishingJobDetail,
  RefreshResult,
  RegisterPayload,
  Schedule,
  ScheduleCreatePayload,
  ScheduleStatus,
  ScheduleUpdatePayload,
  ScheduleWindow,
  Session,
  SocialAccount,
  SocialAccountStatus,
  TargetPerformance,
  UploadSession,
  User,
  Workspace,
  WorkspaceMember,
} from "@/lib/types";

export const API_BASE_URL = DEFAULT_BASE_URL;
const V1 = "/api/v1";

// ---------------------------------------------------------------------------
// Access-token storage
// ---------------------------------------------------------------------------
let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

// A registered callback lets the auth context drop its state when refresh fails.
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------
/** An error carrying the HTTP status and the parsed FastAPI `detail`. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown, message?: string) {
    super(message ?? formatDetail(detail, status));
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** Turn a FastAPI error body into a single human-readable line. */
export function formatDetail(detail: unknown, status: number): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          const record = item as { msg?: unknown; loc?: unknown[] };
          const field = Array.isArray(record.loc) ? record.loc.slice(1).join(".") : "";
          return field ? `${field}: ${String(record.msg)}` : String(record.msg);
        }
        return null;
      })
      .filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  return `Request failed (${status}).`;
}

// ---------------------------------------------------------------------------
// Core request
// ---------------------------------------------------------------------------
type RequestOptions = {
  method?: string;
  body?: unknown;
  /** Send the Authorization header. Defaults to true. */
  auth?: boolean;
  /** Skip the automatic refresh-and-retry (used by the refresh call itself). */
  skipRefresh?: boolean;
  timeoutMs?: number;
  signal?: AbortSignal;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, auth = true, skipRefresh = false, timeoutMs = 20000 } = options;

  const headers = new Headers({ Accept: "application/json" });
  const isFormData = typeof FormData !== "undefined" && body instanceof FormData;
  // A `Blob` body is sent verbatim: the resumable upload protocol takes raw
  // bytes, and setting a JSON content type on it would corrupt the request.
  const isRawBody = typeof Blob !== "undefined" && body instanceof Blob;
  if (body !== undefined && !isFormData && !isRawBody) headers.set("Content-Type", "application/json");
  if (auth && accessToken) headers.set("Authorization", `Bearer ${accessToken}`);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const abortByCaller = () => controller.abort();
  options.signal?.addEventListener("abort", abortByCaller);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body:
        body === undefined
          ? undefined
          : isFormData
            ? (body as FormData)
            : isRawBody
              ? (body as Blob)
              : JSON.stringify(body),
      credentials: "include",
      cache: "no-store",
      signal: controller.signal,
    });
  } catch (error) {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", abortByCaller);
    if (options.signal?.aborted) {
      throw new ApiError(0, "aborted", "The request was cancelled.");
    }
    throw new ApiError(0, "network", "Cannot reach the API. Is the backend running?");
  }
  clearTimeout(timer);
  options.signal?.removeEventListener("abort", abortByCaller);

  if (response.status === 401 && auth && !skipRefresh) {
    try {
      await refreshAccessToken();
    } catch {
      onUnauthorized?.();
      throw new ApiError(401, "unauthenticated", "Your session has expired. Please sign in again.");
    }
    return request<T>(path, { ...options, skipRefresh: true });
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? safeJson(text) : null;

  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      ? (payload as { detail: unknown }).detail
      : payload;
    throw new ApiError(response.status, detail);
  }

  return payload as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

// ---------------------------------------------------------------------------
// Refresh (de-duplicated so parallel 401s trigger a single rotation)
// ---------------------------------------------------------------------------
let refreshInFlight: Promise<string> | null = null;

export function refreshAccessToken(): Promise<string> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      const data = await request<{ access_token: string }>(`${V1}/auth/refresh`, {
        method: "POST",
        body: {},
        auth: false,
        skipRefresh: true,
      });
      setAccessToken(data.access_token);
      return data.access_token;
    })().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------
export const api = {
  baseUrl: API_BASE_URL,

  health: () => request<HealthStatus>(`${V1}/health`, { auth: false, timeoutMs: 5000 }),
  healthDb: () => request<HealthStatus>(`${V1}/health/db`, { auth: false, timeoutMs: 5000 }),
  healthRedis: () => request<HealthStatus>(`${V1}/health/redis`, { auth: false, timeoutMs: 5000 }),

  auth: {
    register: (payload: RegisterPayload) =>
      request<AuthResponse>(`${V1}/auth/register`, { method: "POST", body: payload, auth: false }),
    login: (payload: LoginPayload) =>
      request<AuthResponse>(`${V1}/auth/login`, { method: "POST", body: payload, auth: false }),
    /** Uses the HttpOnly cookie — no token is passed in. */
    refresh: () =>
      request<AuthResponse>(`${V1}/auth/refresh`, {
        method: "POST",
        body: {},
        auth: false,
        skipRefresh: true,
      }),
    logout: () =>
      request<MessageResponse>(`${V1}/auth/logout`, { method: "POST", body: {}, auth: false }),
    logoutAll: () =>
      request<MessageResponse>(`${V1}/auth/logout-all`, { method: "POST", body: {} }),
    me: () => request<User>(`${V1}/auth/me`),
    sessions: () => request<Session[]>(`${V1}/auth/sessions`),
    revokeSession: (sessionId: string) =>
      request<MessageResponse>(`${V1}/auth/sessions/${sessionId}`, { method: "DELETE" }),
    changePassword: (payload: ChangePasswordPayload) =>
      request<MessageResponse>(`${V1}/auth/password/change`, { method: "POST", body: payload }),
    requestEmailVerification: () =>
      request<MessageResponse>(`${V1}/auth/email/verification`, { method: "POST", body: {} }),
    verifyEmail: (token: string) =>
      request<MessageResponse>(`${V1}/auth/email/verify`, {
        method: "POST",
        body: { token },
        auth: false,
      }),
    requestPasswordReset: (email: string) =>
      request<MessageResponse>(`${V1}/auth/password/reset`, {
        method: "POST",
        body: { email },
        auth: false,
      }),
    confirmPasswordReset: (token: string, newPassword: string) =>
      request<MessageResponse>(`${V1}/auth/password/reset/confirm`, {
        method: "POST",
        body: { token, new_password: newPassword },
        auth: false,
      }),
  },

  workspaces: {
    list: () => request<Workspace[]>(`${V1}/workspaces`),
    get: (workspaceId: string) => request<Workspace>(`${V1}/workspaces/${workspaceId}`),
    members: (workspaceId: string) =>
      request<WorkspaceMember[]>(`${V1}/workspaces/${workspaceId}/members`),
    removeMember: (workspaceId: string, memberId: string) =>
      request<MessageResponse>(`${V1}/workspaces/${workspaceId}/members/${memberId}`, {
        method: "DELETE",
      }),
  },

  media: {
    /** Limits and FFmpeg availability — read before offering an upload. */
    capabilities: (workspaceId: string) =>
      request<MediaCapabilities>(`${V1}/media/${workspaceId}/capabilities`),

    stats: (workspaceId: string) =>
      request<MediaStats>(`${V1}/media/${workspaceId}/stats`),

    list: (
      workspaceId: string,
      params: { status?: MediaStatus; search?: string; limit?: number; offset?: number } = {},
    ) => {
      const query = new URLSearchParams();
      if (params.status) query.set("status", params.status);
      if (params.search) query.set("search", params.search);
      if (params.limit !== undefined) query.set("limit", String(params.limit));
      if (params.offset !== undefined) query.set("offset", String(params.offset));
      const suffix = query.toString() ? `?${query.toString()}` : "";
      return request<MediaFile[]>(`${V1}/media/${workspaceId}${suffix}`);
    },

    get: (workspaceId: string, mediaId: string) =>
      request<MediaFile>(`${V1}/media/${workspaceId}/${mediaId}`),

    /** Stage 1 — declare the upload so an unacceptable file is refused up front. */
    createUpload: (workspaceId: string, payload: MediaUploadRequest) =>
      request<UploadSession>(`${V1}/media/${workspaceId}/uploads`, {
        method: "POST",
        body: payload,
      }),

    /** Stage 3 — how many bytes are stored, so an interrupted upload resumes. */
    getUpload: (workspaceId: string, uploadId: string) =>
      request<UploadSession>(`${V1}/media/${workspaceId}/uploads/${uploadId}`),

    /** Stage 2 — append one chunk of raw bytes at an explicit offset. */
    appendChunk: (
      workspaceId: string,
      uploadId: string,
      offset: number,
      chunk: Blob,
      signal?: AbortSignal,
    ) =>
      request<UploadSession>(`${V1}/media/${workspaceId}/uploads/${uploadId}?offset=${offset}`, {
        method: "PUT",
        body: chunk,
        // A 5 MiB chunk over a slow uplink can outlast the default budget.
        timeoutMs: 300000,
        signal,
      }),

    /** Stage 4 — verify, inspect with FFprobe, and store the video. */
    completeUpload: (workspaceId: string, uploadId: string) =>
      request<MediaFile>(`${V1}/media/${workspaceId}/uploads/${uploadId}/complete`, {
        method: "POST",
        body: {},
        // Probing and thumbnailing a long video can take a while.
        timeoutMs: 180000,
      }),

    /** Stage 5 — abandon an upload and discard its partial file. */
    abortUpload: (workspaceId: string, uploadId: string) =>
      request<MessageResponse>(`${V1}/media/${workspaceId}/uploads/${uploadId}`, {
        method: "DELETE",
      }),

    remove: (workspaceId: string, mediaId: string) =>
      request<MessageResponse>(`${V1}/media/${workspaceId}/${mediaId}`, { method: "DELETE" }),
  },

  accounts: {
    /** Per-platform configuration state and connection count. */
    capabilities: (workspaceId: string) =>
      request<AccountCapabilities>(`${V1}/accounts/${workspaceId}/capabilities`),

    list: (
      workspaceId: string,
      params: { platform?: SocialPlatform; status?: SocialAccountStatus } = {},
    ) => {
      const query = new URLSearchParams();
      if (params.platform) query.set("platform", params.platform);
      if (params.status) query.set("status", params.status);
      const suffix = query.toString() ? `?${query.toString()}` : "";
      return request<SocialAccount[]>(`${V1}/accounts/${workspaceId}${suffix}`);
    },

    get: (workspaceId: string, accountId: string) =>
      request<SocialAccount>(`${V1}/accounts/${workspaceId}/${accountId}`),

    /** Create the single-use state and return the platform's consent URL. */
    authorize: (workspaceId: string, platform: SocialPlatform) =>
      request<AuthorizationStart>(`${V1}/accounts/${workspaceId}/${platform}/authorize`, {
        method: "POST",
        body: {},
      }),

    /** Exchange the stored refresh token for a new access token. */
    refresh: (workspaceId: string, accountId: string) =>
      request<SocialAccount>(`${V1}/accounts/${workspaceId}/${accountId}/refresh`, {
        method: "POST",
        body: {},
      }),

    /** Revoke upstream where supported, then destroy the stored tokens. */
    disconnect: (workspaceId: string, accountId: string) =>
      request<AccountDisconnectResult>(`${V1}/accounts/${workspaceId}/${accountId}`, {
        method: "DELETE",
      }),
  },

  posts: {
    /** Every platform's publishing limits, so the composer can guide the user. */
    capabilities: (workspaceId: string) =>
      request<PublisherCapability[]>(`${V1}/posts/${workspaceId}/capabilities`),

    list: (
      workspaceId: string,
      params: { status?: PostStatus; limit?: number; offset?: number } = {},
    ) => {
      const query = new URLSearchParams();
      if (params.status) query.set("status", params.status);
      if (params.limit !== undefined) query.set("limit", String(params.limit));
      if (params.offset !== undefined) query.set("offset", String(params.offset));
      const suffix = query.toString() ? `?${query.toString()}` : "";
      return request<Post[]>(`${V1}/posts/${workspaceId}${suffix}`);
    },

    get: (workspaceId: string, postId: string) =>
      request<Post>(`${V1}/posts/${workspaceId}/${postId}`),

    /** Record a draft: one video plus the base caption. Publishes nothing yet. */
    create: (workspaceId: string, payload: PostCreatePayload) =>
      request<Post>(`${V1}/posts/${workspaceId}`, { method: "POST", body: payload }),

    /**
     * Dispatch one independent job per destination. Returns `202` with every
     * target `queued` — nothing is `published` until the worker confirms it.
     */
    publish: (workspaceId: string, postId: string, payload: PublishEverywherePayload) =>
      request<Post>(`${V1}/posts/${workspaceId}/${postId}/publish`, {
        method: "POST",
        body: payload,
      }),

    /** One job with its full, redacted log trail, for progress monitoring. */
    job: (workspaceId: string, jobId: string) =>
      request<PublishingJobDetail>(`${V1}/posts/${workspaceId}/jobs/${jobId}`),
  },

  schedules: {
    /** The calendar, split into still-to-fire (`upcoming`) and history (`past`). */
    list: (
      workspaceId: string,
      params: { window?: ScheduleWindow; status?: ScheduleStatus; limit?: number; offset?: number } = {},
    ) => {
      const query = new URLSearchParams();
      query.set("window", params.window ?? "upcoming");
      if (params.status) query.set("status", params.status);
      if (params.limit !== undefined) query.set("limit", String(params.limit));
      if (params.offset !== undefined) query.set("offset", String(params.offset));
      return request<Schedule[]>(`${V1}/posts/${workspaceId}/schedule?${query.toString()}`);
    },

    /** Records the entry and its pending destinations — dispatches nothing yet. */
    create: (workspaceId: string, payload: ScheduleCreatePayload) =>
      request<Schedule>(`${V1}/posts/${workspaceId}/schedule`, { method: "POST", body: payload }),

    /** Move an entry that has not fired to a new time. */
    reschedule: (workspaceId: string, postId: string, payload: ScheduleUpdatePayload) =>
      request<Schedule>(`${V1}/posts/${workspaceId}/schedule/${postId}`, {
        method: "PATCH",
        body: payload,
      }),

    /** Cancel an entry before it fires. */
    cancel: (workspaceId: string, postId: string) =>
      request<Schedule>(`${V1}/posts/${workspaceId}/schedule/${postId}`, { method: "DELETE" }),
  },

  ai: {
    /** Whether this server has a model configured, and what it can produce. */
    capability: (workspaceId: string) =>
      request<AICapability>(`${V1}/ai/${workspaceId}/capability`),

    /**
     * Produce one suggestion and store it for review. Nothing is published:
     * the text is carried into the composer by the user's own action.
     */
    generate: (workspaceId: string, payload: AIGeneratePayload) =>
      request<AIContent>(`${V1}/ai/${workspaceId}/generate`, {
        method: "POST",
        body: payload,
        // A model completion can outlast the default request budget.
        timeoutMs: 90000,
      }),

    /** Past suggestions, newest first, optionally filtered by kind. */
    history: (
      workspaceId: string,
      params: { kind?: AIContentKind; limit?: number; offset?: number } = {},
    ) => {
      const query = new URLSearchParams();
      if (params.kind) query.set("kind", params.kind);
      if (params.limit !== undefined) query.set("limit", String(params.limit));
      if (params.offset !== undefined) query.set("offset", String(params.offset));
      const suffix = query.toString() ? `?${query.toString()}` : "";
      return request<AIContent[]>(`${V1}/ai/${workspaceId}/history${suffix}`);
    },

    /** Record that the user accepted a suggestion. Still publishes nothing. */
    accept: (workspaceId: string, contentId: string) =>
      request<AIContent>(`${V1}/ai/${workspaceId}/history/${contentId}/accept`, {
        method: "POST",
        body: {},
      }),
  },

  aiModels: {
    /** The registry as the router walks it: lowest priority first. */
    list: (workspaceId: string, params: { enabledOnly?: boolean } = {}) => {
      const query = new URLSearchParams();
      if (params.enabledOnly) query.set("enabled_only", "true");
      const suffix = query.toString() ? `?${query.toString()}` : "";
      return request<AIModel[]>(`${V1}/ai/${workspaceId}/models${suffix}`);
    },

    /** Add a model. `kind` picks the transport; a blank `base_url` takes its default. */
    create: (workspaceId: string, payload: AIModelCreatePayload) =>
      request<AIModel>(`${V1}/ai/${workspaceId}/models`, { method: "POST", body: payload }),

    /**
     * Change only the fields present: reordering priority, toggling `enabled`,
     * or editing any setting. `credential_env: ""` clears it.
     */
    update: (workspaceId: string, modelId: string, payload: AIModelUpdatePayload) =>
      request<AIModel>(`${V1}/ai/${workspaceId}/models/${modelId}`, {
        method: "PATCH",
        body: payload,
      }),

    /** Remove a model. Its past attempts are kept and still name it. */
    remove: (workspaceId: string, modelId: string) =>
      request<void>(`${V1}/ai/${workspaceId}/models/${modelId}`, { method: "DELETE" }),

    /** Probe every enabled model now and refresh its status. */
    healthCheck: (workspaceId: string) =>
      request<AIHealthSweepResult>(`${V1}/ai/${workspaceId}/models/health-check`, {
        method: "POST",
        body: {},
        // A probe walks every enabled model in turn, so it can outlast the default.
        timeoutMs: 120000,
      }),

    /** Success rate and average latency per model, from the attempt log. */
    performance: (workspaceId: string, params: { windowDays?: number } = {}) => {
      const query = new URLSearchParams();
      if (params.windowDays !== undefined) query.set("window_days", String(params.windowDays));
      const suffix = query.toString() ? `?${query.toString()}` : "";
      return request<AIModelPerformance[]>(
        `${V1}/ai/${workspaceId}/models/performance${suffix}`,
      );
    },

    /** Recent tasks, newest first, each naming the model that answered. */
    tasks: (workspaceId: string, params: { limit?: number; offset?: number } = {}) => {
      const query = new URLSearchParams();
      if (params.limit !== undefined) query.set("limit", String(params.limit));
      if (params.offset !== undefined) query.set("offset", String(params.offset));
      const suffix = query.toString() ? `?${query.toString()}` : "";
      return request<AITaskList>(`${V1}/ai/${workspaceId}/tasks${suffix}`);
    },

    /** The failover chain for one task: which models were tried, in what order. */
    taskAttempts: (workspaceId: string, taskId: string) =>
      request<AITaskAttemptList>(`${V1}/ai/${workspaceId}/tasks/${taskId}`),
  },

  analytics: {
    /** Which platforms can be reported on, and how many accounts each has. */
    capability: (workspaceId: string) =>
      request<AnalyticsCapability>(`${V1}/analytics/${workspaceId}/capability`),

    /** Headline figures. Reads stored snapshots only — it contacts no platform. */
    overview: (workspaceId: string) =>
      request<AnalyticsOverview>(`${V1}/analytics/${workspaceId}/overview`),

    /** Per-platform breakdown, for comparing destinations. */
    platforms: (workspaceId: string) =>
      request<PlatformPerformance[]>(`${V1}/analytics/${workspaceId}/platforms`),

    /** Per-account audience and performance. */
    accounts: (workspaceId: string) =>
      request<AccountPerformance[]>(`${V1}/analytics/${workspaceId}/accounts`),

    /** Each published destination with its latest metrics (`snapshot` may be null). */
    targets: (
      workspaceId: string,
      params: { platform?: SocialPlatform; limit?: number; offset?: number } = {},
    ) => {
      const query = new URLSearchParams();
      if (params.platform) query.set("platform", params.platform);
      if (params.limit !== undefined) query.set("limit", String(params.limit));
      if (params.offset !== undefined) query.set("offset", String(params.offset));
      const suffix = query.toString() ? `?${query.toString()}` : "";
      return request<TargetPerformance[]>(`${V1}/analytics/${workspaceId}/targets${suffix}`);
    },

    /** A daily series, oldest day first. Reads stored snapshots only. */
    history: (
      workspaceId: string,
      params: { days?: number; platform?: SocialPlatform } = {},
    ) => {
      const query = new URLSearchParams();
      if (params.days !== undefined) query.set("days", String(params.days));
      if (params.platform) query.set("platform", params.platform);
      const suffix = query.toString() ? `?${query.toString()}` : "";
      return request<HistoryPoint[]>(`${V1}/analytics/${workspaceId}/history${suffix}`);
    },

    /**
     * Pull fresh metrics from each platform and store a new snapshot. This is the
     * only call that reaches out to a platform, and one destination's failure is
     * returned in `errors` rather than raised.
     */
    refresh: (workspaceId: string) =>
      request<RefreshResult>(`${V1}/analytics/${workspaceId}/refresh`, {
        method: "POST",
        body: {},
        // A sweep across every destination can outlast the default budget.
        timeoutMs: 120000,
      }),
  },

  notifications: {
    /**
     * The signed-in user's inbox, newest first. Addressed to the user rather
     * than a workspace, so no workspace id is passed.
     */
    list: (
      params: { unreadOnly?: boolean; type?: NotificationType; limit?: number; offset?: number } = {},
    ) => {
      const query = new URLSearchParams();
      if (params.unreadOnly) query.set("unread_only", "true");
      if (params.type) query.set("type", params.type);
      if (params.limit !== undefined) query.set("limit", String(params.limit));
      if (params.offset !== undefined) query.set("offset", String(params.offset));
      const suffix = query.toString() ? `?${query.toString()}` : "";
      return request<NotificationList>(`${V1}/notifications${suffix}`);
    },

    /** Just the badge number — cheaper than loading the inbox. */
    unreadCount: () =>
      request<{ count: number }>(`${V1}/notifications/unread-count`),

    markRead: (notificationId: string) =>
      request<Notification>(`${V1}/notifications/${notificationId}/read`, {
        method: "POST",
        body: {},
      }),

    markAllRead: () =>
      request<{ updated: number }>(`${V1}/notifications/read-all`, {
        method: "POST",
        body: {},
      }),

    dismiss: (notificationId: string) =>
      request<void>(`${V1}/notifications/${notificationId}`, { method: "DELETE" }),

    /** The user's email opt-outs. */
    preferences: () => request<NotificationPreference>(`${V1}/notifications/preferences`),

    /**
     * Change one or more email opt-outs. These gate the email copy only — the
     * in-app notification is always recorded.
     */
    updatePreferences: (payload: Partial<Omit<NotificationPreference, "updated_at">>) =>
      request<NotificationPreference>(`${V1}/notifications/preferences`, {
        method: "PUT",
        body: payload,
      }),
  },
};
