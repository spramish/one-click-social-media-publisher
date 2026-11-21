"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  CalendarClock,
  CheckCircle2,
  Clapperboard,
  ExternalLink,
  Film,
  Info,
  Link2,
  RefreshCw,
  Rocket,
  TriangleAlert,
  WandSparkles,
} from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { PlatformIcon } from "@/components/platform-icon";
import {
  ScheduleTimeField,
  browserTimezone,
} from "@/components/schedule/schedule-time-field";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/hooks/use-toast";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatBytes, formatDateTimeInZone, formatDuration, formatRelative } from "@/lib/format";
import type { SocialPlatform } from "@/lib/platforms";
import type {
  AICapability,
  MediaFile,
  Post,
  PostStatus,
  PostTarget,
  PostTargetStatus,
  PublisherCapability,
  SocialAccount,
  TargetSelectionPayload,
} from "@/lib/types";

/** How often an in-flight publish is re-checked, in milliseconds. */
const POLL_INTERVAL_MS = 3000;

/** How many ready videos the picker asks for in one request. */
const VIDEO_PAGE_SIZE = 100;

const PLATFORM_NAME: Record<SocialPlatform, string> = {
  instagram: "Instagram",
  facebook: "Facebook",
  youtube: "YouTube",
  tiktok: "TikTok",
};

type BadgeVariant = "default" | "secondary" | "destructive" | "success" | "warning";

const POST_STATUS: Record<PostStatus, { label: string; variant: BadgeVariant }> = {
  draft: { label: "Draft", variant: "secondary" },
  scheduled: { label: "Scheduled", variant: "secondary" },
  publishing: { label: "Publishing", variant: "warning" },
  published: { label: "Published", variant: "success" },
  partially_published: { label: "Partially published", variant: "warning" },
  failed: { label: "Failed", variant: "destructive" },
  cancelled: { label: "Cancelled", variant: "secondary" },
};

const TARGET_STATUS: Record<PostTargetStatus, { label: string; variant: BadgeVariant }> = {
  pending: { label: "Pending", variant: "secondary" },
  queued: { label: "Queued", variant: "secondary" },
  publishing: { label: "Publishing", variant: "warning" },
  published: { label: "Published", variant: "success" },
  failed: { label: "Failed", variant: "destructive" },
  cancelled: { label: "Cancelled", variant: "secondary" },
  skipped: { label: "Skipped", variant: "warning" },
};

/** A destination is still in flight while it holds one of these. */
const LIVE_TARGET_STATUSES: readonly PostTargetStatus[] = ["pending", "queued", "publishing"];

/** Per-destination composer state, keyed by connected-account id. */
type TargetDraft = {
  /** Whether this account is one of the destinations. */
  enabled: boolean;
  /** Whether the caption/title below override the post's base text. */
  custom: boolean;
  caption: string;
  title: string;
};

const EMPTY_DRAFT: TargetDraft = { enabled: false, custom: false, caption: "", title: "" };

/** True while at least one destination has not reached a terminal state. */
function isPostInFlight(post: Post): boolean {
  return post.targets.some((target) => LIVE_TARGET_STATUSES.includes(target.status));
}

/** Why this video cannot go to this platform, or null when it can. */
function mediaIssue(capability: PublisherCapability, media: MediaFile): string | null {
  if (!capability.allowed_mime_types.includes(media.mime_type)) {
    return `${capability.display_name} does not accept ${media.mime_type}.`;
  }
  if (media.size_bytes > capability.max_size_bytes) {
    return `Larger than ${capability.display_name}'s ${formatBytes(capability.max_size_bytes)} limit.`;
  }
  if (
    capability.max_duration_seconds !== null &&
    media.duration_seconds !== null &&
    media.duration_seconds > capability.max_duration_seconds
  ) {
    return `Longer than ${capability.display_name}'s ${formatDuration(capability.max_duration_seconds)} limit.`;
  }
  if (
    media.width !== null &&
    media.height !== null &&
    (media.width < capability.min_width || media.height < capability.min_height)
  ) {
    return `Smaller than ${capability.display_name}'s ${capability.min_width}×${capability.min_height} minimum.`;
  }
  return null;
}

export function PublisherComposer() {
  const { activeWorkspace, hasPermission } = useAuth();
  const workspaceId = activeWorkspace?.id ?? null;

  const canRead = hasPermission("post:read");
  const canPublish = hasPermission("post:publish");
  const canSchedule = hasPermission("schedule:create");

  const [capabilities, setCapabilities] = useState<PublisherCapability[] | null>(null);
  const [videos, setVideos] = useState<MediaFile[] | null>(null);
  const [accounts, setAccounts] = useState<SocialAccount[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [mediaId, setMediaId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [caption, setCaption] = useState("");
  const [drafts, setDrafts] = useState<Record<string, TargetDraft>>({});

  const [post, setPost] = useState<Post | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // "Schedule for later": a future instant plus the IANA zone it is read in.
  const [scheduling, setScheduling] = useState(false);
  const [scheduleAt, setScheduleAt] = useState("");
  const [scheduleTimezone, setScheduleTimezone] = useState("");

  // Default the schedule's zone to the viewer's own, once on the client (the
  // server render has no reliable zone, so it starts empty to avoid a mismatch).
  useEffect(() => {
    setScheduleTimezone((current) => current || browserTimezone());
  }, []);

  // AI caption drafting. The capability is fetched lazily the first time the
  // panel is opened, so the composer costs nothing when the assistant is unused.
  const canGenerate = hasPermission("ai:generate");
  const [aiOpen, setAiOpen] = useState(false);
  const [aiCapability, setAiCapability] = useState<AICapability | null>(null);
  const [aiCapabilityError, setAiCapabilityError] = useState<string | null>(null);
  const [aiBrief, setAiBrief] = useState("");
  const [aiTone, setAiTone] = useState("");
  const [aiStyle, setAiStyle] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!workspaceId || !canRead) return;
    setLoadError(null);
    setCapabilities(null);
    setVideos(null);
    setAccounts(null);
    try {
      const [caps, media, connected] = await Promise.all([
        api.posts.capabilities(workspaceId),
        api.media.list(workspaceId, { status: "ready", limit: VIDEO_PAGE_SIZE }),
        api.accounts.list(workspaceId, { status: "connected" }),
      ]);
      setCapabilities(caps);
      setVideos(media);
      setAccounts(connected);
    } catch (err) {
      setVideos([]);
      setAccounts([]);
      setLoadError(err instanceof ApiError ? err.message : "Could not load the composer.");
    }
  }, [workspaceId, canRead]);

  useEffect(() => {
    void load();
  }, [load]);

  // Keep a draft per connected account without discarding what was typed.
  useEffect(() => {
    if (!accounts) return;
    setDrafts((prev) => {
      const next: Record<string, TargetDraft> = {};
      for (const account of accounts) next[account.id] = prev[account.id] ?? EMPTY_DRAFT;
      return next;
    });
  }, [accounts]);

  // Self-terminating poll: each refresh re-runs this effect, and it stops once
  // every destination holds a terminal status.
  useEffect(() => {
    if (!workspaceId || !post || !isPostInFlight(post)) return;
    const timer = setTimeout(async () => {
      try {
        setPost(await api.posts.get(workspaceId, post.id));
      } catch (err) {
        // A vanished post will never progress; stop hammering the API.
        if (err instanceof ApiError && err.status === 404) setPost(null);
      }
    }, POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [workspaceId, post]);

  const capabilityByPlatform = useMemo(() => {
    const map = new Map<SocialPlatform, PublisherCapability>();
    for (const capability of capabilities ?? []) map.set(capability.platform, capability);
    return map;
  }, [capabilities]);

  const selectedMedia = useMemo(
    () => videos?.find((video) => video.id === mediaId) ?? null,
    [videos, mediaId],
  );

  const selectedAccounts = useMemo(
    () => (accounts ?? []).filter((account) => drafts[account.id]?.enabled),
    [accounts, drafts],
  );

  /** The text this destination will actually publish. */
  const captionFor = useCallback(
    (accountId: string): string => {
      const draft = drafts[accountId];
      return draft?.custom && draft.caption.trim() ? draft.caption.trim() : caption.trim();
    },
    [drafts, caption],
  );

  const titleFor = useCallback(
    (accountId: string): string => {
      const draft = drafts[accountId];
      return draft?.custom && draft.title.trim() ? draft.title.trim() : title.trim();
    },
    [drafts, title],
  );

  function updateDraft(accountId: string, patch: Partial<TargetDraft>) {
    setDrafts((prev) => ({
      ...prev,
      [accountId]: { ...(prev[accountId] ?? EMPTY_DRAFT), ...patch },
    }));
  }

  function reset() {
    setPost(null);
    setMediaId(null);
    setTitle("");
    setCaption("");
    setFormError(null);
    setScheduling(false);
    setScheduleAt("");
    setDrafts((prev) => {
      const next: Record<string, TargetDraft> = {};
      for (const key of Object.keys(prev)) next[key] = EMPTY_DRAFT;
      return next;
    });
  }

  /**
   * Validate the form and build the destination list — shared by "publish now"
   * and "schedule for later", so both paths apply identical rules.
   */
  function collectTargets():
    | { mediaId: string; targets: TargetSelectionPayload[] }
    | { error: string } {
    if (!mediaId) return { error: "Choose a video to publish." };
    if (selectedAccounts.length === 0) return { error: "Select at least one destination." };

    // A hard consequence is worth catching here rather than as a 409 later.
    const missingTitle = selectedAccounts.find((account) => {
      const capability = capabilityByPlatform.get(account.platform);
      return capability?.requires_title && !titleFor(account.id);
    });
    if (missingTitle) {
      return { error: `${PLATFORM_NAME[missingTitle.platform]} needs a title before it can publish.` };
    }
    const tooLong = selectedAccounts.find((account) => {
      const capability = capabilityByPlatform.get(account.platform);
      return capability !== undefined && captionFor(account.id).length > capability.max_caption_length;
    });
    if (tooLong) {
      const capability = capabilityByPlatform.get(tooLong.platform);
      return {
        error: `The caption for ${PLATFORM_NAME[tooLong.platform]} is longer than its ${capability?.max_caption_length} character limit.`,
      };
    }

    const targets: TargetSelectionPayload[] = selectedAccounts.map((account) => {
      const draft = drafts[account.id];
      const payload: TargetSelectionPayload = { social_account_id: account.id };
      if (draft?.custom) {
        payload.caption = draft.caption.trim() || null;
        payload.title = draft.title.trim() || null;
      }
      return payload;
    });
    return { mediaId, targets };
  }

  /** Toggle the AI panel, loading what the model can do the first time. */
  async function toggleAI() {
    const next = !aiOpen;
    setAiOpen(next);
    setAiError(null);
    if (!next || aiCapability !== null || !workspaceId) return;
    setAiCapabilityError(null);
    try {
      setAiCapability(await api.ai.capability(workspaceId));
    } catch (err) {
      setAiCapabilityError(
        err instanceof ApiError ? err.message : "Could not reach the assistant.",
      );
    }
  }

  /** Draft a caption with the assistant and place it in the base caption field. */
  async function draftCaptionWithAI() {
    if (!workspaceId) return;
    if (!aiBrief.trim()) {
      setAiError("Describe the video so the assistant has something to work from.");
      return;
    }
    setAiBusy(true);
    setAiError(null);
    try {
      const content = await api.ai.generate(workspaceId, {
        kind: "caption",
        brief: aiBrief.trim(),
        tone: aiTone || null,
        style: aiStyle || null,
      });
      setCaption(content.output);
      setAiOpen(false);
      toast({
        title: "Caption drafted",
        description: "Review and edit it before publishing. Nothing has been published.",
        variant: "success",
      });
    } catch (err) {
      setAiError(err instanceof ApiError ? err.message : "The assistant could not respond.");
    } finally {
      setAiBusy(false);
    }
  }

  async function publishEverywhere() {
    if (!workspaceId) return;
    setFormError(null);

    const collected = collectTargets();
    if ("error" in collected) {
      setFormError(collected.error);
      return;
    }

    setSubmitting(true);
    try {
      const created = await api.posts.create(workspaceId, {
        media_id: collected.mediaId,
        title: title.trim() || null,
        base_caption: caption.trim() || null,
      });
      const dispatched = await api.posts.publish(workspaceId, created.id, {
        targets: collected.targets,
      });
      setPost(dispatched);
      toast({
        title: "Publishing everywhere",
        description: `${collected.targets.length} destination${collected.targets.length === 1 ? "" : "s"} queued. Each publishes independently.`,
        variant: "success",
      });
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Could not publish this post.");
    } finally {
      setSubmitting(false);
    }
  }

  async function scheduleForLater() {
    if (!workspaceId) return;
    setFormError(null);

    const collected = collectTargets();
    if ("error" in collected) {
      setFormError(collected.error);
      return;
    }
    if (!scheduleAt) {
      setFormError("Choose the date and time to publish.");
      return;
    }

    setSubmitting(true);
    try {
      const created = await api.posts.create(workspaceId, {
        media_id: collected.mediaId,
        title: title.trim() || null,
        base_caption: caption.trim() || null,
      });
      const schedule = await api.schedules.create(workspaceId, {
        post_id: created.id,
        scheduled_for: scheduleAt,
        timezone: scheduleTimezone.trim() || null,
        targets: collected.targets,
      });
      toast({
        title: "Post scheduled",
        description: `Queued for ${formatDateTimeInZone(schedule.scheduled_for, schedule.timezone)} (${schedule.timezone}).`,
        variant: "success",
      });
      reset();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Could not schedule this post.");
    } finally {
      setSubmitting(false);
    }
  }

  async function refreshPost(postId: string) {
    if (!workspaceId) return;
    try {
      setPost(await api.posts.get(workspaceId, postId));
    } catch (err) {
      toast({
        title: "Could not refresh the publishing status",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "destructive",
      });
    }
  }

  if (!activeWorkspace) {
    return (
      <Alert variant="info">
        <AlertDescription>Select a workspace to compose a post.</AlertDescription>
      </Alert>
    );
  }

  if (!canRead) {
    return (
      <Alert variant="warning">
        <AlertDescription>
          Your role in {activeWorkspace.name} does not include access to posts.
        </AlertDescription>
      </Alert>
    );
  }

  const loading = capabilities === null || videos === null || accounts === null;

  return (
    <div className="space-y-6">
      {loadError ? (
        <Alert variant="destructive">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      {post ? <ProgressPanel post={post} onRefresh={() => void refreshPost(post.id)} /> : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">1 · Choose a video</CardTitle>
          <CardDescription>
            Only videos that finished processing can be published. Each platform&apos;s limits are
            checked before anything is uploaded.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {[0, 1, 2, 3].map((index) => (
                <Skeleton key={index} className="h-40 w-full" />
              ))}
            </div>
          ) : videos.length === 0 ? (
            <EmptyState
              icon={Film}
              title="No videos are ready to publish"
              description="Upload a video and wait for it to finish processing."
              action={
                <Button asChild variant="outline">
                  <Link href="/upload">Go to upload</Link>
                </Button>
              }
            />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {videos.map((video) => (
                <VideoCard
                  key={video.id}
                  video={video}
                  selected={video.id === mediaId}
                  onSelect={() => setMediaId(video.id)}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="gap-4 sm:flex-row sm:items-start sm:justify-between sm:space-y-0">
          <div className="space-y-1.5">
            <CardTitle className="text-base">2 · Write the post</CardTitle>
            <CardDescription>
              This is the caption every destination starts from; override it per platform below.
            </CardDescription>
          </div>
          {canGenerate ? (
            <Button
              variant="outline"
              size="sm"
              className="shrink-0"
              onClick={() => void toggleAI()}
            >
              <WandSparkles className="h-4 w-4" />
              {aiOpen ? "Hide assistant" : "Draft with AI"}
            </Button>
          ) : null}
        </CardHeader>
        <CardContent className="space-y-4">
          {aiOpen ? (
            <div className="space-y-4 rounded-lg border bg-muted/30 p-4">
              {aiCapabilityError ? (
                <Alert variant="destructive">
                  <AlertDescription>{aiCapabilityError}</AlertDescription>
                </Alert>
              ) : aiCapability === null ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Spinner />
                  Checking the assistant…
                </div>
              ) : !aiCapability.configured ? (
                <Alert variant="warning">
                  <AlertDescription>
                    The assistant is not configured on this server, so it cannot draft a caption.
                    You can still write one yourself.
                  </AlertDescription>
                </Alert>
              ) : (
                <>
                  <Field
                    label="What is the video about?"
                    htmlFor="composer-ai-brief"
                    hint={`Up to ${aiCapability.max_brief_chars.toLocaleString()} characters.`}
                  >
                    <Textarea
                      id="composer-ai-brief"
                      value={aiBrief}
                      maxLength={aiCapability.max_brief_chars}
                      onChange={(event) => setAiBrief(event.target.value)}
                      placeholder="e.g. A 30-second clip of a sunrise hike, ending with the summit view."
                    />
                  </Field>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <Field label="Tone" htmlFor="composer-ai-tone">
                      <Select
                        id="composer-ai-tone"
                        value={aiTone}
                        onChange={(event) => setAiTone(event.target.value)}
                      >
                        <option value="">Default</option>
                        {aiCapability.tones.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    <Field label="Style" htmlFor="composer-ai-style">
                      <Select
                        id="composer-ai-style"
                        value={aiStyle}
                        onChange={(event) => setAiStyle(event.target.value)}
                      >
                        <option value="">Default</option>
                        {aiCapability.styles.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </Select>
                    </Field>
                  </div>
                  {aiError ? (
                    <Alert variant="destructive">
                      <AlertDescription>{aiError}</AlertDescription>
                    </Alert>
                  ) : null}
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <p className="text-xs text-muted-foreground">
                      The draft replaces the caption below. Nothing is published.
                    </p>
                    <Button size="sm" onClick={() => void draftCaptionWithAI()} disabled={aiBusy}>
                      {aiBusy ? <Spinner /> : <WandSparkles className="h-4 w-4" />}
                      Draft caption
                    </Button>
                  </div>
                </>
              )}
            </div>
          ) : null}

          <Field label="Title" htmlFor="post-title" hint="Used by YouTube. Other platforms ignore it.">
            <Input
              id="post-title"
              value={title}
              maxLength={200}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="A short, descriptive title"
            />
          </Field>
          <Field label="Caption" htmlFor="post-caption">
            <Textarea
              id="post-caption"
              value={caption}
              onChange={(event) => setCaption(event.target.value)}
              placeholder="What should this video say?"
              className="min-h-[120px]"
            />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">3 · Pick the destinations</CardTitle>
          <CardDescription>
            One independent job is created per destination, so a failure on one platform never
            cancels the others.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <Skeleton className="h-32 w-full" />
          ) : accounts.length === 0 ? (
            <EmptyState
              icon={Link2}
              title="No accounts are connected"
              description="Connect Instagram, Facebook, YouTube, or TikTok to choose a destination."
              action={
                <Button asChild variant="outline">
                  <Link href="/accounts">Connect an account</Link>
                </Button>
              }
            />
          ) : (
            <ul className="divide-y">
              {accounts.map((account) => (
                <DestinationRow
                  key={account.id}
                  account={account}
                  capability={capabilityByPlatform.get(account.platform) ?? null}
                  draft={drafts[account.id] ?? EMPTY_DRAFT}
                  issue={selectedMedia ? mediaIssueSafe(capabilityByPlatform.get(account.platform), selectedMedia) : null}
                  baseCaption={caption}
                  baseTitle={title}
                  onChange={(patch) => updateDraft(account.id, patch)}
                />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <div className="flex flex-col gap-3">
        {formError ? (
          <Alert variant="destructive">
            <AlertDescription>{formError}</AlertDescription>
          </Alert>
        ) : null}

        {scheduling ? (
          <div className="space-y-3 rounded-lg border bg-card p-4">
            <ScheduleTimeField
              id="composer-schedule"
              value={scheduleAt}
              timezone={scheduleTimezone}
              disabled={submitting}
              onChange={(patch) => {
                if (patch.value !== undefined) setScheduleAt(patch.value);
                if (patch.timezone !== undefined) setScheduleTimezone(patch.timezone);
              }}
            />
            <p className="text-xs text-muted-foreground">
              Nothing is published now. The post is queued and fires automatically at this time, in
              this time zone.
            </p>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-4">
          <p className="text-sm text-muted-foreground">
            {selectedAccounts.length === 0
              ? "No destinations selected."
              : `${scheduling ? "Scheduling" : "Publishing"} to ${selectedAccounts.length} destination${selectedAccounts.length === 1 ? "" : "s"}.`}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={reset} disabled={submitting}>
              Clear
            </Button>
            <Button
              variant="outline"
              disabled={submitting || !canSchedule || loading || selectedAccounts.length === 0}
              title={canSchedule ? undefined : "Your role cannot schedule posts."}
              onClick={() => {
                setFormError(null);
                setScheduling((open) => !open);
              }}
            >
              <CalendarClock className="h-4 w-4" />
              {scheduling ? "Cancel schedule" : "Schedule"}
            </Button>
            {scheduling ? (
              <Button
                onClick={() => void scheduleForLater()}
                disabled={submitting || loading || selectedAccounts.length === 0}
              >
                {submitting ? <Spinner /> : <CalendarClock className="h-4 w-4" />}
                Queue it
              </Button>
            ) : (
              <Button
                onClick={() => void publishEverywhere()}
                disabled={submitting || !canPublish || loading || selectedAccounts.length === 0}
                title={canPublish ? undefined : "Your role cannot publish posts."}
              >
                {submitting ? <Spinner /> : <Rocket className="h-4 w-4" />}
                Publish everywhere
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** A video tile that toggles the current selection. */
function VideoCard({
  video,
  selected,
  onSelect,
}: {
  video: MediaFile;
  selected: boolean;
  onSelect: () => void;
}) {
  const resolution =
    video.width && video.height ? `${video.width}×${video.height}` : "resolution pending";

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={`group overflow-hidden rounded-lg border text-left transition-colors ${
        selected ? "border-primary ring-2 ring-primary/40" : "hover:border-primary/50"
      }`}
    >
      <span className="relative block aspect-video w-full bg-muted">
        {video.thumbnail_url ? (
          // Signed URLs point at the local API host, which `next/image` cannot
          // optimise without a remote-pattern allowlist.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={video.thumbnail_url} alt="" className="h-full w-full object-cover" />
        ) : (
          <span className="flex h-full w-full items-center justify-center text-muted-foreground">
            <Clapperboard className="h-6 w-6" />
          </span>
        )}
        {selected ? (
          <span className="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-full bg-primary text-primary-foreground">
            <CheckCircle2 className="h-4 w-4" />
          </span>
        ) : null}
      </span>
      <span className="block space-y-0.5 p-3">
        <span className="block truncate text-sm font-medium" title={video.original_filename}>
          {video.original_filename}
        </span>
        <span className="block text-xs text-muted-foreground">
          {formatBytes(video.size_bytes)} · {resolution} · {formatDuration(video.duration_seconds)}
        </span>
      </span>
    </button>
  );
}

function mediaIssueSafe(
  capability: PublisherCapability | undefined,
  media: MediaFile,
): string | null {
  return capability ? mediaIssue(capability, media) : null;
}

/** One connected account as a selectable publishing destination. */
function DestinationRow({
  account,
  capability,
  draft,
  issue,
  baseCaption,
  baseTitle,
  onChange,
}: {
  account: SocialAccount;
  capability: PublisherCapability | null;
  draft: TargetDraft;
  issue: string | null;
  baseCaption: string;
  baseTitle: string;
  onChange: (patch: Partial<TargetDraft>) => void;
}) {
  const caption = draft.custom && draft.caption.trim() ? draft.caption.trim() : baseCaption.trim();
  const overLimit =
    capability !== null && caption.length > capability.max_caption_length;

  return (
    <li className="py-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted">
            <PlatformIcon platform={account.platform} className="h-4 w-4" />
          </span>
          <div className="min-w-0 space-y-1">
            <p className="truncate text-sm font-medium" title={account.display_name}>
              {account.display_name}
            </p>
            <p className="truncate text-xs text-muted-foreground">
              {PLATFORM_NAME[account.platform]}
              {account.username ? ` · @${account.username}` : ""}
            </p>
            {issue ? (
              <p className="flex items-center gap-1.5 text-xs text-warning">
                <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
                {issue} It will be skipped.
              </p>
            ) : capability?.notes ? (
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Info className="h-3.5 w-3.5 shrink-0" />
                {capability.notes}
              </p>
            ) : null}
          </div>
        </div>
        <Switch
          checked={draft.enabled}
          onCheckedChange={(enabled) => onChange({ enabled })}
          aria-label={`Publish to ${account.display_name}`}
        />
      </div>

      {draft.enabled ? (
        <div className="mt-3 space-y-3 rounded-lg border bg-muted/30 p-3 sm:ml-12">
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs font-medium">Customise for {PLATFORM_NAME[account.platform]}</span>
            <Switch
              checked={draft.custom}
              onCheckedChange={(custom) => onChange({ custom })}
              aria-label="Override the caption for this platform"
            />
          </div>

          {draft.custom ? (
            <div className="space-y-3">
              {capability?.supports_title ? (
                <Field
                  label="Title"
                  htmlFor={`title-${account.id}`}
                  required={capability.requires_title}
                  hint={capability.requires_title ? "This platform requires a title." : undefined}
                >
                  <Input
                    id={`title-${account.id}`}
                    value={draft.title}
                    maxLength={200}
                    onChange={(event) => onChange({ title: event.target.value })}
                    placeholder={baseTitle || "A title for this platform"}
                  />
                </Field>
              ) : null}
              <Field
                label="Caption"
                htmlFor={`caption-${account.id}`}
                error={overLimit ? `Too long by ${caption.length - (capability?.max_caption_length ?? 0)} characters.` : null}
                hint={
                  capability
                    ? `${caption.length} / ${capability.max_caption_length} characters`
                    : undefined
                }
              >
                <Textarea
                  id={`caption-${account.id}`}
                  value={draft.caption}
                  onChange={(event) => onChange({ caption: event.target.value })}
                  placeholder={baseCaption || "A caption for this platform"}
                  aria-invalid={overLimit}
                />
              </Field>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              Uses the post caption{capability?.supports_title ? " and title" : ""} from step 2.
            </p>
          )}
        </div>
      ) : null}
    </li>
  );
}

/** The per-destination progress board for a dispatched post. */
function ProgressPanel({ post, onRefresh }: { post: Post; onRefresh: () => void }) {
  const status = POST_STATUS[post.status] ?? POST_STATUS.draft;
  const inFlight = isPostInFlight(post);
  const published = post.targets.filter((target) => target.status === "published").length;

  return (
    <Card>
      <CardHeader className="gap-4 sm:flex-row sm:items-start sm:justify-between sm:space-y-0">
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <CardTitle className="text-base">Publishing status</CardTitle>
            <Badge variant={status.variant}>{status.label}</Badge>
          </div>
          <CardDescription>
            {inFlight
              ? "A destination is published only once the platform confirms it. This refreshes automatically."
              : `Finished: ${published} of ${post.targets.length} confirmed.`}
          </CardDescription>
        </div>
        <Button variant="outline" size="sm" onClick={onRefresh}>
          {inFlight ? <Spinner /> : <RefreshCw className="h-4 w-4" />}
          Refresh
        </Button>
      </CardHeader>
      <CardContent>
        <ul className="divide-y">
          {post.targets.map((target) => (
            <TargetProgress key={target.id} target={target} />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function TargetProgress({ target }: { target: PostTarget }) {
  const status = TARGET_STATUS[target.status] ?? TARGET_STATUS.pending;
  const job = target.job;
  const spinning = LIVE_TARGET_STATUSES.includes(target.status);

  return (
    <li className="flex items-start justify-between gap-4 py-4">
      <div className="flex min-w-0 items-start gap-3">
        <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted">
          <PlatformIcon platform={target.platform} className="h-4 w-4" />
        </span>
        <div className="min-w-0 space-y-1">
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium">{PLATFORM_NAME[target.platform]}</p>
            <Badge variant={status.variant}>
              {spinning ? <Spinner className="mr-1 h-3 w-3" /> : null}
              {status.label}
            </Badge>
          </div>

          {job ? (
            <p className="text-xs text-muted-foreground">
              Attempt {job.attempt_count} of {job.max_attempts}
              {job.next_retry_at ? ` · next retry ${formatRelative(job.next_retry_at)}` : ""}
            </p>
          ) : null}

          {target.status === "published" && target.published_at ? (
            <p className="text-xs text-muted-foreground">
              Confirmed {formatRelative(target.published_at)}.
            </p>
          ) : null}

          {target.error_message ? (
            <p className="text-xs text-destructive">{target.error_message}</p>
          ) : null}
        </div>
      </div>

      {target.remote_url ? (
        <Button asChild size="sm" variant="outline" className="shrink-0">
          <a href={target.remote_url} target="_blank" rel="noreferrer">
            <ExternalLink className="h-4 w-4" />
            View
          </a>
        </Button>
      ) : null}
    </li>
  );
}
