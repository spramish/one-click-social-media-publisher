"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  CalendarClock,
  CheckCircle2,
  History,
  Pencil,
  RefreshCw,
  Rocket,
  XCircle,
} from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { ScheduleTimeField, instantToLocalInput } from "@/components/schedule/schedule-time-field";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "@/hooks/use-toast";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatDateTimeInZone, formatRelative } from "@/lib/format";
import type { Schedule, ScheduleStatus, ScheduleWindow } from "@/lib/types";

type BadgeVariant = "default" | "secondary" | "destructive" | "success" | "warning";

const STATUS: Record<ScheduleStatus, { label: string; variant: BadgeVariant }> = {
  pending: { label: "Pending", variant: "secondary" },
  queued: { label: "Queued", variant: "secondary" },
  dispatched: { label: "Dispatched", variant: "success" },
  cancelled: { label: "Cancelled", variant: "secondary" },
  missed: { label: "Missed", variant: "warning" },
};

/** An entry that has not fired yet can still be moved or dropped. */
const EDITABLE_STATUSES: readonly ScheduleStatus[] = ["pending", "queued"];

function isEditable(schedule: Schedule): boolean {
  return EDITABLE_STATUSES.includes(schedule.status);
}

/** The wall-clock date (in the entry's own zone) an entry falls on. */
function localDateLabel(schedule: Schedule): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: schedule.timezone,
      weekday: "long",
      day: "numeric",
      month: "long",
      year: "numeric",
    }).format(new Date(schedule.scheduled_for));
  } catch {
    return "Scheduled";
  }
}

type Draft = { time: string; zone: string; error: string | null };

export function SchedulingCalendar() {
  const { activeWorkspace, hasPermission } = useAuth();
  const workspaceId = activeWorkspace?.id ?? null;

  const canRead = hasPermission("schedule:read");
  const canReschedule = hasPermission("schedule:create");
  const canCancel = hasPermission("schedule:cancel");

  const [tab, setTab] = useState<ScheduleWindow>("upcoming");
  const [entries, setEntries] = useState<Schedule[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyPostId, setBusyPostId] = useState<string | null>(null);
  const [editingPostId, setEditingPostId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>({ time: "", zone: "", error: null });

  const load = useCallback(
    async (target: ScheduleWindow, initial = false) => {
      if (!workspaceId || !canRead) return;
      if (initial) setEntries(null);
      setError(null);
      try {
        setEntries(await api.schedules.list(workspaceId, { window: target }));
      } catch (err) {
        setEntries([]);
        setError(err instanceof ApiError ? err.message : "Could not load the schedule.");
      }
    },
    [workspaceId, canRead],
  );

  useEffect(() => {
    void load(tab, true);
  }, [load, tab]);

  function changeTab(next: ScheduleWindow) {
    setTab(next);
    // Clear the previous window's rows immediately so the new tab never flashes
    // the other one's entries while its request is in flight.
    setEntries(null);
    setEditingPostId(null);
  }

  function startEditing(schedule: Schedule) {
    setEditingPostId(schedule.post_id);
    setDraft({
      time: instantToLocalInput(schedule.scheduled_for, schedule.timezone),
      zone: schedule.timezone,
      error: null,
    });
  }

  function stopEditing() {
    setEditingPostId(null);
    setDraft({ time: "", zone: "", error: null });
  }

  async function saveReschedule(schedule: Schedule) {
    if (!workspaceId) return;
    if (!draft.time) {
      setDraft((prev) => ({ ...prev, error: "Choose the new date and time." }));
      return;
    }
    setBusyPostId(schedule.post_id);
    setDraft((prev) => ({ ...prev, error: null }));
    try {
      const updated = await api.schedules.reschedule(workspaceId, schedule.post_id, {
        scheduled_for: draft.time,
        timezone: draft.zone.trim() || null,
      });
      setEntries(
        (prev) => prev?.map((item) => (item.post_id === updated.post_id ? updated : item)) ?? prev,
      );
      stopEditing();
      toast({
        title: "Schedule updated",
        description: `It will publish at ${formatDateTimeInZone(updated.scheduled_for, updated.timezone)} (${updated.timezone}).`,
        variant: "success",
      });
    } catch (err) {
      setDraft((prev) => ({
        ...prev,
        error: err instanceof ApiError ? err.message : "Could not reschedule this post.",
      }));
    } finally {
      setBusyPostId(null);
    }
  }

  async function cancelSchedule(schedule: Schedule) {
    if (!workspaceId) return;
    if (
      !window.confirm(
        "Cancel this scheduled post? Nothing will be published and its destinations are dropped.",
      )
    ) {
      return;
    }
    setBusyPostId(schedule.post_id);
    try {
      await api.schedules.cancel(workspaceId, schedule.post_id);
      // A cancelled entry no longer belongs to the "upcoming" queue.
      setEntries((prev) => prev?.filter((item) => item.post_id !== schedule.post_id) ?? prev);
      toast({
        title: "Schedule cancelled",
        description: "Nothing will be published for this post.",
        variant: "success",
      });
    } catch (err) {
      toast({
        title: "Could not cancel this schedule",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setBusyPostId(null);
    }
  }

  // Entries arrive soonest-first (upcoming) or newest-first (past); grouping by
  // the zone-local day turns that into a small calendar without reordering.
  const groups = useMemo(() => {
    const result: Array<{ label: string; items: Schedule[] }> = [];
    for (const entry of entries ?? []) {
      const label = localDateLabel(entry);
      const last = result[result.length - 1];
      if (last && last.label === label) last.items.push(entry);
      else result.push({ label, items: [entry] });
    }
    return result;
  }, [entries]);

  if (!activeWorkspace) {
    return (
      <Alert variant="info">
        <AlertDescription>Select a workspace to see its scheduled posts.</AlertDescription>
      </Alert>
    );
  }

  if (!canRead) {
    return (
      <Alert variant="warning">
        <AlertDescription>
          Your role in {activeWorkspace.name} does not include access to the schedule.
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="space-y-6">
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      <Tabs value={tab} defaultValue="upcoming" onValueChange={(v) => changeTab(v as ScheduleWindow)}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <TabsList>
            <TabsTrigger value="upcoming">
              <CalendarClock className="h-4 w-4" />
              Upcoming
            </TabsTrigger>
            <TabsTrigger value="past">
              <History className="h-4 w-4" />
              Past
            </TabsTrigger>
          </TabsList>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => void load(tab, true)}>
              <RefreshCw className="h-4 w-4" />
              Refresh
            </Button>
            <Button asChild size="sm">
              <Link href="/compose">
                <Rocket className="h-4 w-4" />
                Schedule a post
              </Link>
            </Button>
          </div>
        </div>

        <TabsContent value="upcoming">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Queue</CardTitle>
            </CardHeader>
            <CardContent>
              {entries === null ? (
                <Skeleton className="h-40 w-full" />
              ) : entries.length === 0 ? (
                <EmptyState
                  icon={CalendarClock}
                  title="Nothing is scheduled"
                  description="Compose a post, choose a future date and time, and it will appear here until it is published."
                  action={
                    <Button asChild variant="outline">
                      <Link href="/compose">Schedule a post</Link>
                    </Button>
                  }
                />
              ) : (
                <ScheduleGroups
                  groups={groups}
                  canReschedule={canReschedule}
                  canCancel={canCancel}
                  busyPostId={busyPostId}
                  editingPostId={editingPostId}
                  draft={draft}
                  onEdit={startEditing}
                  onStopEdit={stopEditing}
                  onDraftChange={(patch) =>
                    setDraft((prev) => ({ ...prev, ...patch, error: null }))
                  }
                  onSave={saveReschedule}
                  onCancel={cancelSchedule}
                />
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="past">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">History</CardTitle>
            </CardHeader>
            <CardContent>
              {entries === null ? (
                <Skeleton className="h-40 w-full" />
              ) : entries.length === 0 ? (
                <EmptyState
                  icon={History}
                  title="No scheduled history yet"
                  description="Posts that were published, cancelled, or missed their window are listed here."
                />
              ) : (
                <ScheduleGroups
                  groups={groups}
                  canReschedule={canReschedule}
                  canCancel={canCancel}
                  busyPostId={busyPostId}
                  editingPostId={editingPostId}
                  draft={draft}
                  onEdit={startEditing}
                  onStopEdit={stopEditing}
                  onDraftChange={(patch) =>
                    setDraft((prev) => ({ ...prev, ...patch, error: null }))
                  }
                  onSave={saveReschedule}
                  onCancel={cancelSchedule}
                />
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

function ScheduleGroups({
  groups,
  canReschedule,
  canCancel,
  busyPostId,
  editingPostId,
  draft,
  onEdit,
  onStopEdit,
  onDraftChange,
  onSave,
  onCancel,
}: {
  groups: Array<{ label: string; items: Schedule[] }>;
  canReschedule: boolean;
  canCancel: boolean;
  busyPostId: string | null;
  editingPostId: string | null;
  draft: Draft;
  onEdit: (schedule: Schedule) => void;
  onStopEdit: () => void;
  onDraftChange: (patch: { value?: string; timezone?: string }) => void;
  onSave: (schedule: Schedule) => void;
  onCancel: (schedule: Schedule) => void;
}) {
  return (
    <div className="space-y-6">
      {groups.map((group) => (
        <div key={group.label} className="space-y-1">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {group.label}
          </p>
          <ul className="divide-y">
            {group.items.map((entry) => (
              <ScheduleRow
                key={entry.id}
                entry={entry}
                canReschedule={canReschedule}
                canCancel={canCancel}
                busy={busyPostId === entry.post_id}
                editing={editingPostId === entry.post_id}
                draft={draft}
                onEdit={() => onEdit(entry)}
                onStopEdit={onStopEdit}
                onDraftChange={onDraftChange}
                onSave={() => onSave(entry)}
                onCancel={() => onCancel(entry)}
              />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function ScheduleRow({
  entry,
  canReschedule,
  canCancel,
  busy,
  editing,
  draft,
  onEdit,
  onStopEdit,
  onDraftChange,
  onSave,
  onCancel,
}: {
  entry: Schedule;
  canReschedule: boolean;
  canCancel: boolean;
  busy: boolean;
  editing: boolean;
  draft: Draft;
  onEdit: () => void;
  onStopEdit: () => void;
  onDraftChange: (patch: { value?: string; timezone?: string }) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const status = STATUS[entry.status] ?? STATUS.pending;
  const editable = isEditable(entry);

  return (
    <li className="py-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted">
            <CalendarClock className="h-4 w-4" />
          </span>
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm font-medium" title={entry.post_title ?? undefined}>
                {entry.post_title?.trim() || "Untitled post"}
              </p>
              <Badge variant={status.variant}>{status.label}</Badge>
            </div>
            <p className="text-xs text-muted-foreground">
              {formatDateTimeInZone(entry.scheduled_for, entry.timezone)} · {entry.timezone} ·{" "}
              {formatRelative(entry.scheduled_for)}
            </p>
            {entry.status === "queued" ? (
              <p className="text-xs text-muted-foreground">
                Handed to the publisher; waiting for the platform to confirm.
              </p>
            ) : null}
            {entry.status === "missed" ? (
              <p className="text-xs text-warning">
                The worker was unavailable at this time, so nothing was published. Re-queue it from
                the composer.
              </p>
            ) : null}
            {entry.cancelled_at ? (
              <p className="text-xs text-muted-foreground">
                Cancelled {formatRelative(entry.cancelled_at)}.
              </p>
            ) : null}
          </div>
        </div>

        {editable ? (
          <div className="flex shrink-0 items-center gap-2">
            {canReschedule ? (
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => (editing ? onStopEdit() : onEdit())}
              >
                <Pencil className="h-4 w-4" />
                {editing ? "Close" : "Reschedule"}
              </Button>
            ) : null}
            {canCancel ? (
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                onClick={onCancel}
              >
                <XCircle className="h-4 w-4" />
                Cancel
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>

      {editing && editable ? (
        <div className="mt-3 space-y-3 rounded-lg border bg-muted/30 p-3 sm:ml-12">
          <ScheduleTimeField
            id={`reschedule-${entry.id}`}
            value={draft.time}
            timezone={draft.zone}
            onChange={onDraftChange}
            error={draft.error}
            disabled={busy}
          />
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="outline" disabled={busy} onClick={onStopEdit}>
              Cancel
            </Button>
            <Button size="sm" disabled={busy} onClick={onSave}>
              {busy ? <Spinner /> : <CheckCircle2 className="h-4 w-4" />}
              Save time
            </Button>
          </div>
        </div>
      ) : null}
    </li>
  );
}
