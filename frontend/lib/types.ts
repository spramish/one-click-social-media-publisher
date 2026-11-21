/** Types mirroring the FastAPI response models in `backend/app/schemas`. */

import type { SocialPlatform } from "@/lib/platforms";

export type User = {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  is_verified: boolean;
  is_superuser: boolean;
  created_at: string;
  last_login_at: string | null;
};

export type AuthResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
};

export type Workspace = {
  id: string;
  name: string;
  slug: string;
  created_at: string;
  is_owner: boolean;
  role: string;
  permissions: string[];
};

export type WorkspaceMember = {
  id: string;
  user_id: string;
  email: string;
  full_name: string | null;
  role: string;
  status: "invited" | "active" | "suspended";
  joined_at: string | null;
};

export type Session = {
  id: string;
  user_agent: string | null;
  ip_address: string | null;
  created_at: string;
  last_used_at: string | null;
  expires_at: string;
  current: boolean;
};

export type MessageResponse = { message: string };

export type HealthStatus = {
  status: "ok" | "error";
  [key: string]: unknown;
};

export type RegisterPayload = {
  email: string;
  password: string;
  full_name?: string;
  workspace_name?: string;
};

export type LoginPayload = { email: string; password: string };

export type ChangePasswordPayload = {
  current_password: string;
  new_password: string;
};

// ---------------------------------------------------------------------------
// Media (Phase 5) — mirrors `backend/app/schemas/media.py`
// ---------------------------------------------------------------------------

export type MediaStatus = "pending" | "processing" | "ready" | "failed" | "deleted";

/**
 * A stored video. It carries **signed, expiring URLs** rather than a path:
 * `video_url` and `thumbnail_url` stop working at `urls_expire_at`, so a long
 * lived page must re-fetch the library to renew them.
 */
export type MediaFile = {
  id: string;
  workspace_id: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  status: MediaStatus;
  storage_backend: string;
  checksum_sha256: string | null;
  uploaded_by_id: string | null;
  created_at: string;
  updated_at: string;
  video_url: string;
  thumbnail_url: string | null;
  urls_expire_at: string;
};

/** Declares an upload before any bytes are sent. */
export type MediaUploadRequest = {
  filename: string;
  mime_type: string;
  size_bytes: number;
  replace_media_id?: string | null;
};

/** State of an in-flight resumable upload. */
export type UploadSession = {
  id: string;
  workspace_id: string;
  original_filename: string;
  mime_type: string;
  declared_size_bytes: number;
  /** Bytes durably written so far. */
  received_bytes: number;
  /** Offset the next chunk must start at (equals `received_bytes`). */
  offset: number;
  chunk_size_bytes: number;
  replace_media_id: string | null;
  created_at: string;
  expires_at: string;
};

export type MediaStats = {
  file_count: number;
  total_bytes: number;
  ready_count: number;
  failed_count: number;
};

export type MediaCapabilities = {
  ffmpeg_available: boolean;
  max_upload_bytes: number;
  chunk_size_bytes: number;
  allowed_mime_types: string[];
  signed_url_ttl_seconds: number;
  thumbnail_max_width: number;
};

// ---------------------------------------------------------------------------
// Connected accounts (Phase 6) — mirrors `backend/app/schemas/account.py`
// ---------------------------------------------------------------------------

export type SocialAccountStatus = "connected" | "expired" | "revoked" | "error";

/**
 * A connected destination account as the dashboard sees it. The shape never
 * carries a token — only the *metadata* of the credential (expiry, scopes) so a
 * health indicator can be drawn without the secret leaving the server.
 */
export type SocialAccount = {
  id: string;
  workspace_id: string;
  platform: SocialPlatform;
  platform_account_id: string;
  display_name: string;
  username: string | null;
  avatar_url: string | null;
  status: SocialAccountStatus;
  /** When the access token stops working, if the platform tells us. */
  token_expires_at: string | null;
  /** Seconds until expiry (negative once past it); null when unknown. */
  token_expires_in_seconds: number | null;
  /** True when the connection cannot publish until the user reconnects. */
  needs_reconnect: boolean;
  scopes: string[];
  connected_by_id: string | null;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
};

/** Whether one platform can be connected on this server, and how many are. */
export type PlatformCapability = {
  platform: SocialPlatform;
  display_name: string;
  /** False when the server has no client id/secret, so the UI can say so. */
  configured: boolean;
  connected_count: number;
};

export type AccountCapabilities = { platforms: PlatformCapability[] };

/** Where to send the browser to grant access on a platform. */
export type AuthorizationStart = {
  platform: SocialPlatform;
  authorization_url: string;
  /** When the `state` this URL carries stops being accepted. */
  expires_at: string;
  state_ttl_seconds: number;
};

export type AccountDisconnectResult = {
  message: string;
  /** True when the platform confirmed the grant was revoked upstream. */
  revoked_upstream: boolean;
};

// ---------------------------------------------------------------------------
// Posts & publishing (Phase 7) — mirrors `backend/app/schemas/post.py`
// ---------------------------------------------------------------------------

/** Aggregate status of a post across every one of its destinations. */
export type PostStatus =
  | "draft"
  | "scheduled"
  | "publishing"
  | "published"
  | "partially_published"
  | "failed"
  | "cancelled";

/** Per-destination status. One failure never cancels the other destinations. */
export type PostTargetStatus =
  | "pending"
  | "queued"
  | "publishing"
  | "published"
  | "failed"
  | "cancelled"
  | "skipped";

/** Execution state of the Celery job behind one destination. */
export type JobStatus =
  | "pending"
  | "queued"
  | "running"
  | "retrying"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "dead_letter";

export type LogLevel = "debug" | "info" | "warning" | "error" | "critical";

/**
 * One platform's publishing limits, straight from the backend publishers.
 * The composer renders guidance from this rather than hard-coding a ceiling.
 */
export type PublisherCapability = {
  platform: SocialPlatform;
  display_name: string;
  max_size_bytes: number;
  max_duration_seconds: number | null;
  min_width: number;
  min_height: number;
  max_caption_length: number;
  requires_title: boolean;
  supports_title: boolean;
  allowed_mime_types: string[];
  notes: string;
};

/** One append-only log line from a publishing job. */
export type PublishingLog = {
  id: string;
  level: LogLevel;
  message: string;
  context: Record<string, unknown> | null;
  response_payload: Record<string, unknown> | null;
  created_at: string;
};

/** Execution state of one (destination, attempt-sequence) publishing job. */
export type PublishingJob = {
  id: string;
  status: JobStatus;
  attempt_count: number;
  max_attempts: number;
  celery_task_id: string | null;
  scheduled_for: string | null;
  started_at: string | null;
  finished_at: string | null;
  next_retry_at: string | null;
  error_class: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

/** A job plus its full, redacted log trail — used for job monitoring. */
export type PublishingJobDetail = PublishingJob & {
  workspace_id: string;
  post_target_id: string;
  platform: SocialPlatform;
  logs: PublishingLog[];
};

/** One destination: the text for it, its outcome, and its job. */
export type PostTarget = {
  id: string;
  post_id: string;
  social_account_id: string;
  platform: SocialPlatform;
  status: PostTargetStatus;
  caption: string | null;
  title: string | null;
  remote_post_id: string | null;
  remote_url: string | null;
  published_at: string | null;
  error_message: string | null;
  job: PublishingJob | null;
  created_at: string;
  updated_at: string;
};

/** A composed post and every destination it targets. */
export type Post = {
  id: string;
  workspace_id: string;
  media_file_id: string;
  created_by_id: string | null;
  title: string | null;
  base_caption: string | null;
  status: PostStatus;
  published_at: string | null;
  targets: PostTarget[];
  created_at: string;
  updated_at: string;
};

/** Compose a draft: one uploaded video plus the user's base caption. */
export type PostCreatePayload = {
  media_id: string;
  title?: string | null;
  base_caption?: string | null;
};

/** One destination ticked in the composer. */
export type TargetSelectionPayload = {
  social_account_id: string;
  /** Per-platform caption, overriding the post's base caption when set. */
  caption?: string | null;
  /** Per-platform title (YouTube), falling back to the post's title. */
  title?: string | null;
};

export type PublishEverywherePayload = { targets: TargetSelectionPayload[] };

// ---------------------------------------------------------------------------
// Content scheduling (Phase 8) — mirrors `backend/app/schemas/schedule.py`
// ---------------------------------------------------------------------------

/** Lifecycle of a scheduled (future-dated) post. */
export type ScheduleStatus = "pending" | "queued" | "dispatched" | "cancelled" | "missed";

/** Which slice of the calendar to read. */
export type ScheduleWindow = "upcoming" | "past" | "all";

/**
 * One calendar entry: a post and the moment it will be published.
 *
 * `scheduled_for` is the stored UTC instant; `local_time` is that same instant
 * rendered in `timezone`, so the UI can show the time the user picked without
 * re-implementing time-zone maths. The destinations are not repeated here — they
 * live on the post's targets, which the schedule shares with a manual publish.
 */
export type Schedule = {
  id: string;
  workspace_id: string;
  post_id: string;
  scheduled_for: string;
  timezone: string;
  local_time: string;
  status: ScheduleStatus;
  dispatched_at: string | null;
  cancelled_at: string | null;
  cancelled_by_id: string | null;
  post_status: PostStatus;
  post_title: string | null;
  media_file_id: string;
  created_at: string;
  updated_at: string;
};

/** Queue a composed post for a future time. */
export type ScheduleCreatePayload = {
  post_id: string;
  /** A naive value is read in `timezone`; an aware one is converted to UTC. */
  scheduled_for: string;
  timezone?: string | null;
  targets: TargetSelectionPayload[];
};

/** Move a queued post to a different time. */
export type ScheduleUpdatePayload = {
  scheduled_for: string;
  timezone?: string | null;
};

// ---------------------------------------------------------------------------
// AI content assistant (Phase 9) — mirrors `backend/app/schemas/ai.py`
// ---------------------------------------------------------------------------

/** The kinds of copy the assistant can produce. */
export type AIContentKind =
  | "caption"
  | "hashtags"
  | "description"
  | "rewrite"
  | "suggestion";

/** What the assistant can do on this server, and whether it is configured. */
export type AICapability = {
  provider: string;
  model: string;
  /** False when the server has no model credentials, so the UI can say so. */
  configured: boolean;
  kinds: AIContentKind[];
  tones: string[];
  styles: string[];
  max_brief_chars: number;
  max_output_tokens: number;
};

/**
 * One stored suggestion. It is never published on its own: the user reviews it
 * and carries the text into the composer, which is a separate explicit action.
 */
export type AIContent = {
  id: string;
  workspace_id: string;
  post_id: string | null;
  created_by_id: string | null;
  kind: AIContentKind;
  model: string;
  prompt: string;
  output: string;
  tone: string | null;
  is_accepted: boolean;
  created_at: string;
  updated_at: string;
};

/** Ask the assistant for one piece of copy. */
export type AIGeneratePayload = {
  kind: AIContentKind;
  brief?: string;
  tone?: string | null;
  style?: string | null;
  /** Tailor the output to one platform when the user has one in mind. */
  platform?: SocialPlatform | null;
  /** Optionally record which post this suggestion was generated for. */
  post_id?: string | null;
  /** The text to rewrite — required when `kind` is `"rewrite"`. */
  draft?: string | null;
};

// ---------------------------------------------------------------------------
// Analytics dashboard (Phase 10) — mirrors `backend/app/schemas/analytics.py`
// ---------------------------------------------------------------------------

/** The performance figures a snapshot may carry. */
export type MetricKey =
  | "views"
  | "likes"
  | "comments"
  | "shares"
  | "reach"
  | "impressions"
  | "engagement";

/**
 * One set of performance figures.
 *
 * Every figure is `number | null`, and the two cases are **not** the same: a
 * genuine `0` is a measurement, while `null` means the platform did not provide
 * that metric at all. `available` counts how many of the seven fields are
 * present, so `available === 0` reads as "no metrics", never "no engagement".
 */
export type MetricSet = {
  views: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  reach: number | null;
  impressions: number | null;
  engagement: number | null;
  available: number;
};

/** Audience figures for an account, each `null` when the platform omits it. */
export type AudienceTotals = {
  followers: number | null;
  follows: number | null;
  posts_count: number | null;
  available: number;
};

/** One destination's latest stored metrics and when they were retrieved. */
export type MetricSnapshot = {
  metrics: MetricSet;
  retrieved_at: string;
};

/** The dashboard's headline numbers. */
export type AnalyticsOverview = {
  published_post_count: number;
  published_target_count: number;
  tracked_target_count: number;
  totals: MetricSet;
  audience: AudienceTotals;
  last_refreshed_at: string | null;
};

/** One platform's published count and summed latest metrics. */
export type PlatformPerformance = {
  platform: SocialPlatform;
  display_name: string;
  /** False when this platform exposes no public metrics API. */
  metrics_available: boolean;
  published_target_count: number;
  tracked_target_count: number;
  totals: MetricSet;
  last_refreshed_at: string | null;
};

/** One connected account's audience and the performance of its targets. */
export type AccountPerformance = {
  social_account_id: string;
  platform: SocialPlatform;
  display_name: string;
  username: string | null;
  status: SocialAccountStatus;
  published_target_count: number;
  audience: AudienceTotals;
  audience_retrieved_at: string | null;
  totals: MetricSet;
};

/** One published target's latest stored metrics (`snapshot` is `null` if never read). */
export type TargetPerformance = {
  post_target_id: string;
  post_id: string;
  platform: SocialPlatform;
  remote_post_id: string | null;
  remote_url: string | null;
  published_at: string | null;
  snapshot: MetricSnapshot | null;
};

/** One day of the historical performance chart. */
export type HistoryPoint = {
  /** ISO date, `YYYY-MM-DD`. */
  day: string;
  /** How many snapshots were taken that day. */
  samples: number;
  metrics: MetricSet;
};

/** Whether one platform's metrics can be read, and how many accounts it has. */
export type AnalyticsPlatformCapability = {
  platform: SocialPlatform;
  display_name: string;
  metrics_available: boolean;
  connected_accounts: number;
};

/** What this server can report on. */
export type AnalyticsCapability = {
  platforms: AnalyticsPlatformCapability[];
  default_window_days: number;
  max_history_points: number;
  max_refresh_targets: number;
};

/** The tallies from one refresh sweep. `errors` is informational, per destination. */
export type RefreshResult = {
  post_targets_refreshed: number;
  post_targets_skipped: number;
  accounts_refreshed: number;
  accounts_skipped: number;
  snapshots_written: number;
  errors: string[];
};

// ---------------------------------------------------------------------------
// Notifications (Phase 11) — mirrors `backend/app/schemas/notification.py`
// ---------------------------------------------------------------------------

/** The category of an in-app notification. */
export type NotificationType =
  | "publish_success"
  | "publish_failure"
  | "schedule_reminder"
  | "token_expiring"
  | "model_fallback"
  | "subscription"
  | "system";

/**
 * One notification addressed to the signed-in user.
 *
 * `is_read` is derived server-side from `read_at`; `payload` carries the ids a
 * click-through needs (a post, a schedule, a connected account).
 */
export type Notification = {
  id: string;
  workspace_id: string | null;
  type: NotificationType;
  title: string;
  body: string | null;
  payload: Record<string, unknown> | null;
  is_read: boolean;
  read_at: string | null;
  created_at: string;
};

/** A page of notifications plus the badge count. */
export type NotificationList = {
  items: Notification[];
  total: number;
  unread_count: number;
  limit: number;
  offset: number;
};

/**
 * The user's email opt-outs. Each flag governs the **email copy only**: the
 * in-app notification is always recorded, so turning one off never hides
 * anything from the bell.
 */
export type NotificationPreference = {
  email_publish_success: boolean;
  email_publish_failure: boolean;
  email_schedule_reminder: boolean;
  email_token_expiring: boolean;
  updated_at: string | null;
};

// ---------------------------------------------------------------------------
// AI model registry and automatic failover (Phase 12) — mirrors
// `backend/app/schemas/ai_model.py`
// ---------------------------------------------------------------------------

/** The transport family used to reach a model. */
export type AIModelKind = "openai_compatible" | "ollama";

/** Last availability the router observed for a model. */
export type AIModelStatus = "unknown" | "healthy" | "unhealthy";

/** Outcome of a single model attempt made while routing one task. */
export type AITaskStatus = "succeeded" | "failed";

/**
 * One model in the registry.
 *
 * It names the **environment variable** that holds its key (`credential_env`) —
 * never the key itself — and `credential_ready` says whether that variable
 * resolves on the server right now. `priority` is the failover order: lower is
 * tried first.
 */
export type AIModel = {
  id: string;
  name: string;
  display_name: string;
  kind: AIModelKind;
  model: string;
  base_url: string;
  credential_env: string | null;
  credential_ready: boolean;
  priority: number;
  enabled: boolean;
  supports: AIContentKind[] | null;
  timeout_seconds: number;
  max_output_tokens: number;
  temperature: number;
  status: AIModelStatus;
  last_checked_at: string | null;
  last_ok_at: string | null;
  consecutive_failures: number;
  last_error: string | null;
  created_at: string;
  updated_at: string;
};

/** Register one model. A blank `base_url` takes the kind's server default. */
export type AIModelCreatePayload = {
  name: string;
  display_name: string;
  kind: AIModelKind;
  model: string;
  base_url?: string;
  credential_env?: string | null;
  priority?: number;
  enabled?: boolean;
  supports?: AIContentKind[] | null;
  timeout_seconds?: number;
  max_output_tokens?: number;
  temperature?: number;
};

/** A partial update: only the fields present are applied. `credential_env: ""` clears it. */
export type AIModelUpdatePayload = Partial<AIModelCreatePayload>;

/** What one availability sweep probed and found. */
export type AIHealthSweepResult = {
  checked: number;
  healthy: number;
  unhealthy: number;
};

/** Aggregated success rate and latency for one model, from the attempt log. */
export type AIModelPerformance = {
  model_name: string;
  total: number;
  succeeded: number;
  failed: number;
  success_rate: number;
  avg_latency_ms: number | null;
};

/** One routed task, collapsed from its attempts. */
export type AITask = {
  task_id: string;
  kind: AIContentKind;
  status: AITaskStatus;
  attempts: number;
  model_name: string;
  created_at: string;
  finished_at: string;
};

/** One model attempt: the usage log, the execution trail and the performance sample. */
export type AITaskAttempt = {
  id: string;
  task_id: string;
  model_id: string | null;
  model_name: string;
  kind: AIContentKind;
  status: AITaskStatus;
  attempt: number;
  latency_ms: number | null;
  error_class: string | null;
  error: string | null;
  created_at: string;
};

/** A page of routed tasks. */
export type AITaskList = {
  items: AITask[];
  total: number;
  limit: number;
  offset: number;
};

/** A page of raw attempts. */
export type AITaskAttemptList = {
  items: AITaskAttempt[];
  total: number;
  limit: number;
  offset: number;
};
