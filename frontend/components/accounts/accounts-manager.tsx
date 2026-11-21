"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Link2, Plug, RefreshCw, RotateCw, Unplug } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { PlatformIcon } from "@/components/platform-icon";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Avatar, AvatarFallback, initialsOf } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { toast } from "@/hooks/use-toast";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatDateTime, formatRelative } from "@/lib/format";
import type { SocialPlatform } from "@/lib/platforms";
import type {
  AccountCapabilities,
  PlatformCapability,
  SocialAccount,
  SocialAccountStatus,
} from "@/lib/types";

const PLATFORM_NAME: Record<SocialPlatform, string> = {
  instagram: "Instagram",
  facebook: "Facebook",
  youtube: "YouTube",
  tiktok: "TikTok",
};

const STATUS: Record<
  SocialAccountStatus,
  { label: string; variant: "success" | "warning" | "destructive" | "secondary" }
> = {
  connected: { label: "Connected", variant: "success" },
  expired: { label: "Expired", variant: "warning" },
  revoked: { label: "Revoked", variant: "destructive" },
  error: { label: "Error", variant: "destructive" },
};

/** One-line health summary for a connection. */
function tokenSummary(account: SocialAccount): string {
  if (account.needs_reconnect) return "Reconnect this account before you can publish to it.";
  if (account.token_expires_at) {
    return `Access expires ${formatRelative(account.token_expires_at)}.`;
  }
  return "The platform issued a token with no known expiry.";
}

export function AccountsManager() {
  const { activeWorkspace, hasPermission } = useAuth();
  const workspaceId = activeWorkspace?.id ?? null;

  const canRead = hasPermission("account:read");
  const canConnect = hasPermission("account:connect");
  const canDisconnect = hasPermission("account:disconnect");

  const [capabilities, setCapabilities] = useState<AccountCapabilities | null>(null);
  const [accounts, setAccounts] = useState<SocialAccount[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState<SocialPlatform | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // The callback redirect lands here once; report its outcome a single time.
  const reported = useRef(false);

  const load = useCallback(
    async (initial = false) => {
      if (!workspaceId || !canRead) return;
      if (initial) {
        setCapabilities(null);
        setAccounts(null);
      }
      setError(null);
      try {
        const [caps, list] = await Promise.all([
          api.accounts.capabilities(workspaceId),
          api.accounts.list(workspaceId),
        ]);
        setCapabilities(caps);
        setAccounts(list);
      } catch (err) {
        setAccounts([]);
        setError(err instanceof ApiError ? err.message : "Could not load your connected accounts.");
      }
    },
    [workspaceId, canRead],
  );

  useEffect(() => {
    void load(true);
  }, [load]);

  // The platform sends the browser back to this page with the result in the
  // query string, so surface it and then scrub it so a refresh stays quiet.
  useEffect(() => {
    if (!workspaceId || reported.current) return;
    const params = new URLSearchParams(window.location.search);
    const connected = params.get("connected");
    const failed = params.get("error");
    if (!connected && !failed) return;
    reported.current = true;

    if (connected) {
      const count = params.get("accounts");
      toast({
        title: `${PLATFORM_NAME[connected as SocialPlatform] ?? "Account"} connected`,
        description: count ? `${count} account(s) are now linked.` : undefined,
        variant: "success",
      });
    } else if (failed) {
      toast({ title: "Could not connect the account", description: failed, variant: "destructive" });
    }

    const url = new URL(window.location.href);
    for (const key of ["connected", "accounts", "error", "platform", "code", "state"]) {
      url.searchParams.delete(key);
    }
    window.history.replaceState({}, "", url.toString());
    void load();
  }, [workspaceId, load]);

  async function connect(platform: SocialPlatform) {
    if (!workspaceId) return;
    setConnecting(platform);
    try {
      const start = await api.accounts.authorize(workspaceId, platform);
      // Hand the browser to the platform's own consent screen.
      window.location.href = start.authorization_url;
    } catch (err) {
      setConnecting(null);
      toast({
        title: `Could not start the ${PLATFORM_NAME[platform]} connection`,
        description: err instanceof ApiError ? err.message : undefined,
        variant: "destructive",
      });
    }
  }

  async function refresh(account: SocialAccount) {
    if (!workspaceId) return;
    setBusyId(account.id);
    try {
      const updated = await api.accounts.refresh(workspaceId, account.id);
      setAccounts((prev) => prev?.map((item) => (item.id === updated.id ? updated : item)) ?? prev);
      toast({
        title: "Connection refreshed",
        description: `${updated.display_name} is ready to publish again.`,
        variant: "success",
      });
    } catch (err) {
      toast({
        title: "Could not refresh this connection",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setBusyId(null);
    }
  }

  async function disconnect(account: SocialAccount) {
    if (!workspaceId) return;
    if (
      !window.confirm(
        `Disconnect “${account.display_name}”? Stored tokens are deleted and publishing to it stops.`,
      )
    ) {
      return;
    }
    setBusyId(account.id);
    try {
      const result = await api.accounts.disconnect(workspaceId, account.id);
      setAccounts((prev) => prev?.filter((item) => item.id !== account.id) ?? prev);
      toast({ title: "Account disconnected", description: result.message, variant: "success" });
      void load();
    } catch (err) {
      toast({
        title: "Could not disconnect this account",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setBusyId(null);
    }
  }

  if (!activeWorkspace) {
    return (
      <Alert variant="info">
        <AlertDescription>Select a workspace to manage its connected accounts.</AlertDescription>
      </Alert>
    );
  }

  if (!canRead) {
    return (
      <Alert variant="warning">
        <AlertDescription>
          Your role in {activeWorkspace.name} does not include access to connected accounts.
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

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Connect a platform</CardTitle>
          <CardDescription>
            You are sent to the platform to approve access. The tokens it issues are encrypted
            before they are stored, and are never sent back to this page.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {capabilities === null ? (
            <div className="grid gap-4 sm:grid-cols-2">
              {[0, 1, 2, 3].map((index) => (
                <Skeleton key={index} className="h-24 w-full" />
              ))}
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              {capabilities.platforms.map((capability) => (
                <PlatformRow
                  key={capability.platform}
                  capability={capability}
                  canConnect={canConnect}
                  connecting={connecting}
                  onConnect={connect}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Connected accounts</CardTitle>
          <CardDescription>
            Every account the workspace can publish to. A connection that has expired or been
            revoked must be reconnected before it can be used.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {accounts === null ? (
            <Skeleton className="h-40 w-full" />
          ) : accounts.length === 0 ? (
            <EmptyState
              icon={Link2}
              title="No accounts connected yet"
              description="Connect Instagram, Facebook, YouTube, or TikTok above to start publishing. You can link more than one account per platform."
            />
          ) : (
            <ul className="divide-y">
              {accounts.map((account) => (
                <AccountRow
                  key={account.id}
                  account={account}
                  canConnect={canConnect}
                  canDisconnect={canDisconnect}
                  busy={busyId === account.id}
                  connecting={connecting === account.platform}
                  onReconnect={connect}
                  onRefresh={refresh}
                  onDisconnect={disconnect}
                />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function PlatformRow({
  capability,
  canConnect,
  connecting,
  onConnect,
}: {
  capability: PlatformCapability;
  canConnect: boolean;
  connecting: SocialPlatform | null;
  onConnect: (platform: SocialPlatform) => void;
}) {
  const { platform, display_name, configured, connected_count } = capability;
  const pending = connecting === platform;

  return (
    <div className="flex items-center justify-between gap-4 rounded-lg border p-4">
      <div className="flex min-w-0 items-center gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-muted">
          <PlatformIcon platform={platform} className="h-5 w-5" />
        </span>
        <div className="min-w-0 space-y-0.5">
          <p className="truncate text-sm font-medium">{display_name}</p>
          <p className="truncate text-xs text-muted-foreground">
            {!configured
              ? "Not configured on this server"
              : connected_count > 0
                ? `${connected_count} account${connected_count === 1 ? "" : "s"} connected`
                : "No accounts connected"}
          </p>
        </div>
      </div>
      <Button
        size="sm"
        variant={configured ? "default" : "outline"}
        disabled={!configured || !canConnect || connecting !== null}
        title={
          !configured
            ? "This server has no client credentials for this platform yet."
            : !canConnect
              ? "Your role cannot connect accounts."
              : undefined
        }
        onClick={() => onConnect(platform)}
      >
        {pending ? <Spinner /> : <Plug className="h-4 w-4" />}
        {connected_count > 0 ? "Add account" : "Connect"}
      </Button>
    </div>
  );
}

function AccountRow({
  account,
  canConnect,
  canDisconnect,
  busy,
  connecting,
  onReconnect,
  onRefresh,
  onDisconnect,
}: {
  account: SocialAccount;
  canConnect: boolean;
  canDisconnect: boolean;
  busy: boolean;
  connecting: boolean;
  onReconnect: (platform: SocialPlatform) => void;
  onRefresh: (account: SocialAccount) => void;
  onDisconnect: (account: SocialAccount) => void;
}) {
  const status = STATUS[account.status] ?? STATUS.error;
  const disabled = busy || connecting;

  return (
    <li className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 items-center gap-3">
        <span className="relative shrink-0">
          <Avatar className="h-10 w-10">
            {account.avatar_url ? (
              // Platform CDN host is outside `next/image`'s allowlist.
              // eslint-disable-next-line @next/next/no-img-element
              <img src={account.avatar_url} alt="" className="h-full w-full object-cover" />
            ) : (
              <AvatarFallback>{initialsOf(account.display_name)}</AvatarFallback>
            )}
          </Avatar>
          <span className="absolute -bottom-0.5 -right-0.5 flex h-5 w-5 items-center justify-center rounded-full border border-border bg-background">
            <PlatformIcon platform={account.platform} className="h-3 w-3" />
          </span>
        </span>

        <div className="min-w-0 space-y-0.5">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-medium" title={account.display_name}>
              {account.display_name}
            </p>
            <Badge variant={status.variant}>{status.label}</Badge>
          </div>
          <p className="truncate text-xs text-muted-foreground">
            {account.username ? `@${account.username} · ` : ""}
            {PLATFORM_NAME[account.platform]}
            {account.scopes.length ? ` · ${account.scopes.length} permission${account.scopes.length === 1 ? "" : "s"} granted` : ""}
          </p>
          <p
            className={account.needs_reconnect ? "text-xs text-warning" : "text-xs text-muted-foreground"}
            title={account.token_expires_at ? formatDateTime(account.token_expires_at) : undefined}
          >
            {tokenSummary(account)}
          </p>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2 sm:justify-end">
        {canConnect ? (
          account.needs_reconnect ? (
            <Button size="sm" disabled={disabled} onClick={() => onReconnect(account.platform)}>
              {connecting ? <Spinner /> : <RotateCw className="h-4 w-4" />}
              Reconnect
            </Button>
          ) : (
            <Button size="sm" variant="outline" disabled={disabled} onClick={() => onRefresh(account)}>
              {busy ? <Spinner /> : <RefreshCw className="h-4 w-4" />}
              Refresh
            </Button>
          )
        ) : null}
        {canDisconnect ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={disabled}
            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
            onClick={() => onDisconnect(account)}
          >
            <Unplug className="h-4 w-4" />
            Disconnect
          </Button>
        ) : null}
      </div>
    </li>
  );
}
