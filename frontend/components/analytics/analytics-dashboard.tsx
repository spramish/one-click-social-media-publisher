"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Activity,
  BarChart3,
  Eye,
  Heart,
  LineChart,
  MessageCircle,
  RefreshCw,
  Share2,
  TrendingUp,
  Users,
} from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { PlatformIcon } from "@/components/platform-icon";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { toast } from "@/hooks/use-toast";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatDateTime, formatRelative } from "@/lib/format";
import { PLATFORMS, type SocialPlatform } from "@/lib/platforms";
import type {
  AccountPerformance,
  AnalyticsCapability,
  AnalyticsOverview,
  HistoryPoint,
  MetricKey,
  PlatformPerformance,
  TargetPerformance,
} from "@/lib/types";
import { cn } from "@/lib/utils";

/** The metrics a chart can be drawn for, in a stable display order. */
const METRIC_ORDER: { key: MetricKey; label: string; icon: LucideIcon }[] = [
  { key: "views", label: "Views", icon: Eye },
  { key: "likes", label: "Likes", icon: Heart },
  { key: "comments", label: "Comments", icon: MessageCircle },
  { key: "shares", label: "Shares", icon: Share2 },
  { key: "reach", label: "Reach", icon: Users },
  { key: "impressions", label: "Impressions", icon: BarChart3 },
  { key: "engagement", label: "Engagement", icon: Activity },
];

const WINDOW_OPTIONS = [7, 30, 90, 180];

/** "18 Nov 2026", parsing `YYYY-MM-DD` as a local day rather than UTC midnight. */
function formatDayLabel(day: string): string {
  const date = new Date(`${day}T00:00:00`);
  if (Number.isNaN(date.getTime())) return day;
  return date.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
}

/** Short axis label: "1.2k". */
function formatAxis(value: number): string {
  if (value >= 1000) {
    const thousands = value / 1000;
    return `${thousands >= 10 ? thousands.toFixed(0) : thousands.toFixed(1)}k`;
  }
  return String(Math.round(value));
}

/**
 * A figure that keeps "unavailable" distinct from "zero".
 *
 * `null` means the platform never reported the metric, so it is rendered as a
 * muted `N/A` — never as `0`, which would read as a real measurement.
 */
function MetricValue({ value, className }: { value: number | null; className?: string }) {
  if (value === null) {
    return (
      <span
        className={cn("text-muted-foreground", className)}
        title="This platform does not provide this metric"
      >
        N/A
      </span>
    );
  }
  return <span className={className}>{value.toLocaleString()}</span>;
}

/**
 * The analytics dashboard.
 *
 * Every `GET` behind this page reads snapshots already stored, so opening it
 * never calls a platform. The one button that does reach out is **Refresh**,
 * which stores a new snapshot and then re-reads the page. Metrics a platform
 * does not expose are shown as "N/A" rather than as a fabricated number.
 */
export function AnalyticsDashboard() {
  const { activeWorkspace, hasPermission } = useAuth();
  const workspaceId = activeWorkspace?.id ?? null;

  const canRead = hasPermission("analytics:read");
  const canRefresh = hasPermission("analytics:refresh");

  const [capability, setCapability] = useState<AnalyticsCapability | null>(null);
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [platforms, setPlatforms] = useState<PlatformPerformance[]>([]);
  const [accounts, setAccounts] = useState<AccountPerformance[]>([]);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [targets, setTargets] = useState<TargetPerformance[]>([]);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const [windowDays, setWindowDays] = useState(30);
  const [historyMetric, setHistoryMetric] = useState<MetricKey>("views");
  const [historyPlatform, setHistoryPlatform] = useState<SocialPlatform | "">("");

  const loadBase = useCallback(async () => {
    if (!workspaceId || !canRead) return;
    setLoadError(null);
    try {
      const [caps, head, byPlatform, byAccount, published] = await Promise.all([
        api.analytics.capability(workspaceId),
        api.analytics.overview(workspaceId),
        api.analytics.platforms(workspaceId),
        api.analytics.accounts(workspaceId),
        api.analytics.targets(workspaceId, { limit: 25 }),
      ]);
      setCapability(caps);
      setOverview(head);
      setPlatforms(byPlatform);
      setAccounts(byAccount);
      setTargets(published);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Could not load analytics.");
    } finally {
      setLoading(false);
    }
  }, [workspaceId, canRead]);

  // The series is read separately so changing the window or platform re-draws
  // the chart without re-fetching the rest of the page.
  const loadHistory = useCallback(async () => {
    if (!workspaceId || !canRead) return;
    try {
      const series = await api.analytics.history(workspaceId, {
        days: windowDays,
        platform: historyPlatform || undefined,
      });
      setHistory(series);
    } catch {
      /* the base load already surfaces a load error */
    }
  }, [workspaceId, canRead, windowDays, historyPlatform]);

  useEffect(() => {
    void loadBase();
  }, [loadBase]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  async function refresh() {
    if (!workspaceId) return;
    setRefreshing(true);
    try {
      const result = await api.analytics.refresh(workspaceId);
      const failed = result.errors.length;
      toast({
        title: "Metrics refreshed",
        description:
          failed === 0
            ? `${result.snapshots_written} snapshot(s) stored.`
            : `${result.snapshots_written} snapshot(s) stored; ${failed} destination(s) could not be read.`,
        variant: failed === 0 ? "success" : "warning",
      });
      await Promise.all([loadBase(), loadHistory()]);
    } catch (err) {
      toast({
        title: "Could not refresh metrics",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setRefreshing(false);
    }
  }

  const unreadablePlatforms = useMemo(
    () => (capability?.platforms ?? []).filter((entry) => !entry.metrics_available),
    [capability],
  );

  if (!activeWorkspace) {
    return (
      <Alert variant="info">
        <AlertDescription>Select a workspace to see its analytics.</AlertDescription>
      </Alert>
    );
  }

  if (!canRead) {
    return (
      <Alert variant="warning">
        <AlertDescription>
          Your role in {activeWorkspace.name} does not include access to analytics.
        </AlertDescription>
      </Alert>
    );
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, index) => (
            <Skeleton key={index} className="h-24 w-full" />
          ))}
        </div>
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }

  if (loadError) {
    return (
      <Alert variant="destructive">
        <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
          <span>{loadError}</span>
          <Button size="sm" variant="outline" onClick={() => void loadBase()}>
            <RefreshCw className="h-4 w-4" />
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  if (!overview) return null;

  const nothingYet =
    overview.published_target_count === 0 && accounts.length === 0 && targets.length === 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {overview.last_refreshed_at
            ? `Metrics last collected ${formatRelative(overview.last_refreshed_at)}.`
            : "No metrics have been collected yet."}
        </p>
        {canRefresh ? (
          <Button onClick={() => void refresh()} disabled={refreshing}>
            {refreshing ? <Spinner /> : <RefreshCw className="h-4 w-4" />}
            Refresh metrics
          </Button>
        ) : null}
      </div>

      {unreadablePlatforms.length > 0 ? (
        <Alert variant="info">
          <AlertDescription>
            {unreadablePlatforms.map((entry) => entry.display_name).join(", ")}{" "}
            {unreadablePlatforms.length === 1 ? "does" : "do"} not expose public performance
            metrics through its official API, so no figures are reported for{" "}
            {unreadablePlatforms.length === 1 ? "it" : "them"}. Nothing is estimated or invented.
          </AlertDescription>
        </Alert>
      ) : null}

      {nothingYet ? (
        <EmptyState
          icon={TrendingUp}
          title="No performance data yet"
          description="Publish a video, then choose Refresh metrics to collect the counts each platform reports."
        />
      ) : (
        <>
          <OverviewCards overview={overview} />

          <ComparisonCard rows={platforms} />

          <HistoryCard
            points={history}
            metric={historyMetric}
            onMetricChange={setHistoryMetric}
            windowDays={windowDays}
            onWindowChange={setWindowDays}
            platform={historyPlatform}
            onPlatformChange={setHistoryPlatform}
          />

          <AudienceCard accounts={accounts} />

          <TargetsCard targets={targets} />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------
/** The double-clickable headline numbers. */
function OverviewCards({ overview }: { overview: AnalyticsOverview }) {
  const { totals, audience } = overview;
  const cards: { label: string; icon: LucideIcon; value: number | null; hint?: string }[] = [
    {
      label: "Published posts",
      icon: TrendingUp,
      value: overview.published_post_count,
      hint: `${overview.published_target_count} published video${overview.published_target_count === 1 ? "" : "s"}`,
    },
    { label: "Views", icon: Eye, value: totals.views },
    { label: "Likes", icon: Heart, value: totals.likes },
    { label: "Comments", icon: MessageCircle, value: totals.comments },
    { label: "Shares", icon: Share2, value: totals.shares },
    { label: "Engagement", icon: Activity, value: totals.engagement },
    { label: "Followers", icon: Users, value: audience.followers },
    { label: "Impressions", icon: BarChart3, value: totals.impressions },
  ];

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {cards.map((card) => (
        <Card key={card.label}>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              {card.label}
            </CardTitle>
            <card.icon className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold tabular-nums">
              <MetricValue value={card.value} />
            </div>
            {card.hint ? (
              <p className="mt-1 text-xs text-muted-foreground">{card.hint}</p>
            ) : null}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Platform comparison
// ---------------------------------------------------------------------------
/** A per-platform table plus a bar chart, so destinations can be compared. */
function ComparisonCard({ rows }: { rows: PlatformPerformance[] }) {
  const bars = rows.map((row) => ({
    platform: row.platform,
    name: row.display_name,
    value: row.totals.views,
  }));
  const maxValue = Math.max(1, ...bars.map((bar) => bar.value ?? 0));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Platform comparison</CardTitle>
        <CardDescription>
          Totals summed from each destination&apos;s most recent snapshot. A blank bar means the
          platform did not report that metric.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {rows.length === 0 ? (
          <EmptyState
            icon={BarChart3}
            title="Nothing to compare yet"
            description="Once a video has been published and refreshed, each platform appears here."
          />
        ) : (
          <>
            <div className="space-y-4">
              {bars.map((bar) => (
                <div key={bar.platform} className="flex items-center gap-3">
                  <div className="flex w-32 shrink-0 items-center gap-2">
                    <PlatformIcon platform={bar.platform} />
                    <span className="truncate text-sm">{bar.name}</span>
                  </div>
                  <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-primary"
                      style={{
                        width: `${bar.value === null ? 0 : (bar.value / maxValue) * 100}%`,
                      }}
                    />
                  </div>
                  <div className="w-20 shrink-0 text-right text-sm tabular-nums">
                    <MetricValue value={bar.value} />
                  </div>
                </div>
              ))}
              <p className="text-xs text-muted-foreground">Bars show views.</p>
            </div>

            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Platform</TableHead>
                    <TableHead className="text-right">Published</TableHead>
                    {METRIC_ORDER.map((metric) => (
                      <TableHead key={metric.key} className="text-right">
                        {metric.label}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.platform}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <PlatformIcon platform={row.platform} />
                          <span className="font-medium">{row.display_name}</span>
                          {!row.metrics_available ? (
                            <Badge variant="secondary">No metrics API</Badge>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {row.published_target_count}
                      </TableCell>
                      {METRIC_ORDER.map((metric) => (
                        <TableCell key={metric.key} className="text-right tabular-nums">
                          <MetricValue value={row.totals[metric.key]} />
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------
/** The historical performance chart, with its metric and window selectors. */
function HistoryCard({
  points,
  metric,
  onMetricChange,
  windowDays,
  onWindowChange,
  platform,
  onPlatformChange,
}: {
  points: HistoryPoint[];
  metric: MetricKey;
  onMetricChange: (metric: MetricKey) => void;
  windowDays: number;
  onWindowChange: (days: number) => void;
  platform: SocialPlatform | "";
  onPlatformChange: (platform: SocialPlatform | "") => void;
}) {
  const label = METRIC_ORDER.find((entry) => entry.key === metric)?.label ?? metric;
  const samples = points.reduce((total, point) => total + point.samples, 0);

  return (
    <Card>
      <CardHeader className="gap-4 sm:flex-row sm:items-start sm:justify-between sm:space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="text-base">Performance over time</CardTitle>
          <CardDescription>
            Each day shows that day&apos;s {label.toLowerCase()}, taken from the newest snapshot of
            each destination. Oldest day first.
          </CardDescription>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Select
            aria-label="Metric"
            className="h-9 w-36"
            value={metric}
            onChange={(event) => onMetricChange(event.target.value as MetricKey)}
          >
            {METRIC_ORDER.map((entry) => (
              <option key={entry.key} value={entry.key}>
                {entry.label}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Platform"
            className="h-9 w-36"
            value={platform}
            onChange={(event) => onPlatformChange(event.target.value as SocialPlatform | "")}
          >
            <option value="">All platforms</option>
            {PLATFORMS.map((entry) => (
              <option key={entry.key} value={entry.key}>
                {entry.name}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Range"
            className="h-9 w-28"
            value={String(windowDays)}
            onChange={(event) => onWindowChange(Number(event.target.value))}
          >
            {WINDOW_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option} days
              </option>
            ))}
          </Select>
        </div>
      </CardHeader>
      <CardContent>
        {points.length === 0 ? (
          <EmptyState
            icon={LineChart}
            title="No measurements in this range"
            description="Widen the range, or choose Refresh metrics to collect the latest numbers."
          />
        ) : (
          <>
            <Sparkline points={points} metric={metric} label={label} />
            <p className="mt-3 text-xs text-muted-foreground">
              {samples} snapshot{samples === 1 ? "" : "s"} across {points.length} day
              {points.length === 1 ? "" : "s"}.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

/** A dependency-free SVG line chart; a gap is drawn where a day has no value. */
function Sparkline({
  points,
  metric,
  label,
}: {
  points: HistoryPoint[];
  metric: MetricKey;
  label: string;
}) {
  const W = 800;
  const H = 240;
  const padL = 52;
  const padR = 16;
  const padT = 16;
  const padB = 32;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const n = points.length;
  const values = points.map((point) => point.metrics[metric]);
  const present = values.filter((value): value is number => value !== null);

  const xAt = (index: number) =>
    n <= 1 ? padL + innerW / 2 : padL + (index / (n - 1)) * innerW;
  const maxValue = present.length ? Math.max(...present) : 0;
  const yMax = maxValue > 0 ? maxValue : 1;
  const yAt = (value: number) => padT + innerH - (value / yMax) * innerH;

  if (present.length === 0) {
    return (
      <EmptyState
        icon={LineChart}
        title={`${label} is not available`}
        description="No platform reported this metric on any day in the selected range."
      />
    );
  }

  // Split into contiguous runs so a missing day leaves a visible gap.
  const segments: { index: number; value: number }[][] = [];
  let run: { index: number; value: number }[] = [];
  values.forEach((value, index) => {
    if (value === null) {
      if (run.length) {
        segments.push(run);
        run = [];
      }
    } else {
      run.push({ index, value });
    }
  });
  if (run.length) segments.push(run);

  const gridLines = [0, 0.25, 0.5, 0.75, 1].map((ratio) => ({
    ratio,
    y: padT + innerH - ratio * innerH,
    value: yMax * ratio,
  }));

  const labelStep = Math.max(1, Math.ceil(n / 5));
  const xLabels = points
    .map((point, index) => ({ point, index }))
    .filter(({ index }) => index % labelStep === 0 || index === n - 1);

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full text-primary"
      role="img"
      aria-label={`${label} over time`}
    >
      {gridLines.map((line) => (
        <g key={line.ratio}>
          <line
            x1={padL}
            x2={W - padR}
            y1={line.y}
            y2={line.y}
            className="stroke-border"
            strokeWidth={1}
            strokeDasharray={line.ratio === 0 ? undefined : "4 4"}
          />
          <text
            x={padL - 8}
            y={line.y + 3}
            textAnchor="end"
            className="fill-muted-foreground text-[10px]"
          >
            {formatAxis(line.value)}
          </text>
        </g>
      ))}

      {segments.map((segment, index) => (
        <polyline
          key={index}
          points={segment.map((pt) => `${xAt(pt.index)},${yAt(pt.value)}`).join(" ")}
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      ))}

      {segments.flatMap((segment) =>
        segment.map((pt) => (
          <circle
            key={`${pt.index}`}
            cx={xAt(pt.index)}
            cy={yAt(pt.value)}
            r={3}
            fill="currentColor"
          >
            <title>
              {formatDayLabel(points[pt.index].day)}: {pt.value.toLocaleString()}
            </title>
          </circle>
        )),
      )}

      {xLabels.map(({ point, index }) => (
        <text
          key={point.day}
          x={xAt(index)}
          y={H - 10}
          textAnchor="middle"
          className="fill-muted-foreground text-[10px]"
        >
          {formatDayLabel(point.day)}
        </text>
      ))}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Audience
// ---------------------------------------------------------------------------
/** Account-level numbers: followers, follows, and post counts per account. */
function AudienceCard({ accounts }: { accounts: AccountPerformance[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Audience</CardTitle>
        <CardDescription>
          Account totals reported by each platform. Numbers a platform does not disclose are shown
          as N/A.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {accounts.length === 0 ? (
          <EmptyState
            icon={Users}
            title="No connected accounts"
            description="Connect an account to see its audience figures here."
          />
        ) : (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Account</TableHead>
                  <TableHead className="text-right">Followers</TableHead>
                  <TableHead className="text-right">Following</TableHead>
                  <TableHead className="text-right">Posts</TableHead>
                  <TableHead className="text-right">Published</TableHead>
                  <TableHead>Retrieved</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {accounts.map((account) => (
                  <TableRow key={account.social_account_id}>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <PlatformIcon platform={account.platform} />
                        <div className="min-w-0">
                          <div className="truncate font-medium">{account.display_name}</div>
                          {account.username ? (
                            <div className="truncate text-xs text-muted-foreground">
                              @{account.username}
                            </div>
                          ) : null}
                        </div>
                        {account.status !== "connected" ? (
                          <Badge variant="warning">Reconnect</Badge>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      <MetricValue value={account.audience.followers} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      <MetricValue value={account.audience.follows} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      <MetricValue value={account.audience.posts_count} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {account.published_target_count}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {account.audience_retrieved_at
                        ? formatRelative(account.audience_retrieved_at)
                        : "Never"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Per-target metrics
// ---------------------------------------------------------------------------
/** The most recent published destinations and their latest stored metrics. */
function TargetsCard({ targets }: { targets: TargetPerformance[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Recent videos</CardTitle>
        <CardDescription>
          The newest published destinations and the last metrics collected for each.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {targets.length === 0 ? (
          <EmptyState
            icon={TrendingUp}
            title="No published videos"
            description="Publish a video to start collecting its performance here."
          />
        ) : (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Published</TableHead>
                  <TableHead>Video</TableHead>
                  <TableHead className="text-right">Views</TableHead>
                  <TableHead className="text-right">Likes</TableHead>
                  <TableHead className="text-right">Comments</TableHead>
                  <TableHead className="text-right">Shares</TableHead>
                  <TableHead>Retrieved</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {targets.map((target) => (
                  <TableRow key={target.post_target_id}>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {target.published_at ? formatDateTime(target.published_at) : "—"}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <PlatformIcon platform={target.platform} />
                        {target.remote_url ? (
                          <a
                            href={target.remote_url}
                            target="_blank"
                            rel="noreferrer"
                            className="truncate text-sm underline-offset-4 hover:underline"
                          >
                            {target.remote_post_id ?? "View post"}
                          </a>
                        ) : (
                          <span className="text-sm text-muted-foreground">
                            {target.remote_post_id ?? "—"}
                          </span>
                        )}
                      </div>
                    </TableCell>
                    {(["views", "likes", "comments", "shares"] as MetricKey[]).map((key) => (
                      <TableCell key={key} className="text-right tabular-nums">
                        <MetricValue value={target.snapshot?.metrics[key] ?? null} />
                      </TableCell>
                    ))}
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {target.snapshot ? formatRelative(target.snapshot.retrieved_at) : "Not read"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
