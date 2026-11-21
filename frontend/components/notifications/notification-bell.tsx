"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Bell, CheckCheck, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { toast } from "@/hooks/use-toast";
import { ApiError, api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Notification } from "@/lib/types";

/** Where a notice's click-through should land, from the ids it carries. */
const TYPE_ROUTE: Partial<Record<Notification["type"], string>> = {
  publish_success: "/history",
  publish_failure: "/history",
  schedule_reminder: "/schedule",
  token_expiring: "/accounts",
  // A fallback notice points at the registry, so the user can see which model
  // answered and reorder the failover list.
  model_fallback: "/ai-models",
};

/** How often the badge re-checks while the dashboard is open. */
const POLL_INTERVAL_MS = 60_000;

export function NotificationBell() {
  const router = useRouter();
  const [count, setCount] = useState(0);
  const [items, setItems] = useState<Notification[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [markingAll, setMarkingAll] = useState(false);

  const refreshCount = useCallback(async () => {
    try {
      const { count: unread } = await api.notifications.unreadCount();
      setCount(unread);
    } catch {
      // A transient failure keeps the last known count rather than showing an error.
    }
  }, []);

  useEffect(() => {
    void refreshCount();
    const timer = window.setInterval(() => void refreshCount(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [refreshCount]);

  const loadInbox = useCallback(async () => {
    setLoading(true);
    try {
      const listing = await api.notifications.list({ limit: 20 });
      setItems(listing.items);
      setCount(listing.unread_count);
    } catch (error) {
      toast({
        title: "Could not load notifications",
        description: error instanceof ApiError ? error.message : undefined,
        variant: "destructive",
      });
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  async function open(notification: Notification) {
    const route = TYPE_ROUTE[notification.type];
    if (!notification.is_read) {
      try {
        await api.notifications.markRead(notification.id);
        setItems((current) =>
          current
            ? current.map((item) =>
                item.id === notification.id ? { ...item, is_read: true } : item,
              )
            : current,
        );
        setCount((value) => Math.max(0, value - 1));
      } catch {
        // Reading is best-effort; navigation should not be blocked by it.
      }
    }
    if (route) router.push(route);
  }

  async function dismiss(notification: Notification) {
    setBusy(notification.id);
    try {
      await api.notifications.dismiss(notification.id);
      setItems((current) => (current ? current.filter((item) => item.id !== notification.id) : current));
      if (!notification.is_read) setCount((value) => Math.max(0, value - 1));
    } catch (error) {
      toast({
        title: "Could not dismiss",
        description: error instanceof ApiError ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setBusy(null);
    }
  }

  async function markAllRead() {
    setMarkingAll(true);
    try {
      await api.notifications.markAllRead();
      setItems((current) => (current ? current.map((item) => ({ ...item, is_read: true })) : current));
      setCount(0);
    } catch (error) {
      toast({
        title: "Could not mark all read",
        description: error instanceof ApiError ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setMarkingAll(false);
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="relative rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
        aria-label={count ? `Notifications, ${count} unread` : "Notifications"}
        onFocus={() => {
          if (items === null) void loadInbox();
        }}
        onClick={() => void loadInbox()}
      >
        <Bell className="h-4 w-4" />
        {count > 0 ? (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold leading-none text-destructive-foreground">
            {count > 99 ? "99+" : count}
          </span>
        ) : null}
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-80 p-0">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-sm font-semibold">Notifications</span>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs"
            disabled={markingAll || count === 0}
            onClick={() => void markAllRead()}
          >
            {markingAll ? <Spinner /> : <CheckCheck className="h-3.5 w-3.5" />}
            Mark all read
          </Button>
        </div>

        <div className="max-h-96 overflow-y-auto">
          {loading && items === null ? (
            <div className="space-y-2 p-3">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : items && items.length > 0 ? (
            items.map((item) => (
              <div
                key={item.id}
                className={cn(
                  "group flex items-start gap-2 border-b px-3 py-2.5 last:border-b-0",
                  !item.is_read && "bg-primary/5",
                )}
              >
                <button
                  type="button"
                  onClick={() => void open(item)}
                  className="flex flex-1 items-start gap-2 text-left"
                >
                  <span
                    className={cn(
                      "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
                      item.is_read ? "bg-transparent" : "bg-primary",
                    )}
                    aria-hidden
                  />
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium">{item.title}</span>
                    {item.body ? (
                      <span className="mt-0.5 block line-clamp-2 text-xs text-muted-foreground">
                        {item.body}
                      </span>
                    ) : null}
                    <span className="mt-1 block text-[11px] text-muted-foreground">
                      {formatRelative(item.created_at)}
                    </span>
                  </span>
                </button>
                <button
                  type="button"
                  aria-label="Dismiss notification"
                  disabled={busy === item.id}
                  onClick={() => void dismiss(item)}
                  className="rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-accent hover:text-foreground group-hover:opacity-100 focus-visible:opacity-100"
                >
                  {busy === item.id ? <Spinner /> : <X className="h-3.5 w-3.5" />}
                </button>
              </div>
            ))
          ) : (
            <p className="px-3 py-6 text-center text-sm text-muted-foreground">
              You are all caught up.
            </p>
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
