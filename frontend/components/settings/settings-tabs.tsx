"use client";

import { useCallback, useEffect, useState } from "react";
import { Bell, Laptop, ShieldCheck, Trash2 } from "lucide-react";

import { PasswordInput } from "@/components/auth/password-input";
import { SystemHealth } from "@/components/system-health";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/field";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "@/hooks/use-toast";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { PASSWORD_MIN_LENGTH } from "@/lib/config";
import { formatDateTime, formatRelative } from "@/lib/format";
import type { NotificationPreference, Session } from "@/lib/types";

export function SettingsTabs() {
  return (
    <Tabs defaultValue="account" className="space-y-0">
      <TabsList className="w-full justify-start overflow-x-auto sm:w-auto">
        <TabsTrigger value="account">Account</TabsTrigger>
        <TabsTrigger value="notifications">Notifications</TabsTrigger>
        <TabsTrigger value="password">Password</TabsTrigger>
        <TabsTrigger value="sessions">Sessions</TabsTrigger>
        <TabsTrigger value="system">System</TabsTrigger>
      </TabsList>
      <TabsContent value="account">
        <AccountTab />
      </TabsContent>
      <TabsContent value="notifications">
        <NotificationsTab />
      </TabsContent>
      <TabsContent value="password">
        <PasswordTab />
      </TabsContent>
      <TabsContent value="sessions">
        <SessionsTab />
      </TabsContent>
      <TabsContent value="system">
        <SystemTab />
      </TabsContent>
    </Tabs>
  );
}

function AccountTab() {
  const { user } = useAuth();
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);

  async function resend() {
    setSending(true);
    try {
      await api.auth.requestEmailVerification();
      setSent(true);
      toast({ title: "Verification email sent", variant: "success" });
    } catch (error) {
      toast({
        title: "Could not send the email",
        description: error instanceof ApiError ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSending(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Account details</CardTitle>
        <CardDescription>
          Your profile is read-only in this phase; editing arrives with team management.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!user ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <>
            <dl className="grid gap-4 sm:grid-cols-2">
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">Full name</dt>
                <dd className="mt-1 text-sm">{user.full_name ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">Email</dt>
                <dd className="mt-1 text-sm">{user.email}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">Status</dt>
                <dd className="mt-1 flex flex-wrap items-center gap-2 text-sm">
                  <Badge variant={user.is_active ? "success" : "destructive"}>
                    {user.is_active ? "Active" : "Disabled"}
                  </Badge>
                  <Badge variant={user.is_verified ? "success" : "warning"}>
                    {user.is_verified ? "Email verified" : "Email not verified"}
                  </Badge>
                  {user.is_superuser ? <Badge variant="secondary">Superuser</Badge> : null}
                </dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                  Member since
                </dt>
                <dd className="mt-1 text-sm">{formatDateTime(user.created_at)}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">Last sign-in</dt>
                <dd className="mt-1 text-sm">{formatDateTime(user.last_login_at)}</dd>
              </div>
            </dl>

            {!user.is_verified ? (
              <>
                <Separator />
                {sent ? (
                  <Alert variant="success">
                    <AlertDescription>
                      Sent. In development the verification link is printed to the backend console.
                    </AlertDescription>
                  </Alert>
                ) : (
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <p className="text-sm text-muted-foreground">
                      Verify your email to secure the account and enable notifications.
                    </p>
                    <Button onClick={() => void resend()} disabled={sending}>
                      {sending ? <Spinner /> : null}
                      Resend verification email
                    </Button>
                  </div>
                )}
              </>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}

/** An email flag and the label the settings page shows for it. */
type EmailFlag = Exclude<keyof NotificationPreference, "updated_at">;

const EMAIL_PREFERENCES: Array<{ key: EmailFlag; label: string; description: string }> = [
  {
    key: "email_publish_success",
    label: "Successful publishes",
    description: "Email me when a video goes live on a destination.",
  },
  {
    key: "email_publish_failure",
    label: "Failed publishes",
    description: "Email me when a destination could not be published to.",
  },
  {
    key: "email_schedule_reminder",
    label: "Scheduled-post reminders",
    description: "Email me shortly before a scheduled post is due to publish.",
  },
  {
    key: "email_token_expiring",
    label: "Expiring connections",
    description: "Email me when a connected account is about to need reconnecting.",
  },
];

function NotificationsTab() {
  const [preferences, setPreferences] = useState<NotificationPreference | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<EmailFlag | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setPreferences(await api.notifications.preferences());
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not load your notification preferences.",
      );
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function toggle(key: EmailFlag, value: boolean) {
    if (!preferences) return;
    const previous = preferences;
    // Optimistic: the switch responds immediately and is reverted on failure.
    setSaving(key);
    setPreferences({ ...preferences, [key]: value });
    const payload: Partial<Omit<NotificationPreference, "updated_at">> = {};
    payload[key] = value;
    try {
      setPreferences(await api.notifications.updatePreferences(payload));
    } catch (err) {
      setPreferences(previous);
      toast({
        title: "Could not save your preference",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSaving(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bell className="h-4 w-4" />
          Email notifications
        </CardTitle>
        <CardDescription>
          Notifications always appear in the bell. These settings only control whether an email copy
          is also sent, so switching one off never hides anything.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-1">
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : preferences === null ? (
          <div className="space-y-3">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </div>
        ) : (
          EMAIL_PREFERENCES.map((flag) => (
            <div
              key={flag.key}
              className="flex items-center justify-between gap-4 border-b py-4 last:border-b-0"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium">{flag.label}</p>
                <p className="text-xs text-muted-foreground">{flag.description}</p>
              </div>
              <div className="flex items-center gap-2">
                {saving === flag.key ? <Spinner /> : null}
                <Switch
                  checked={preferences[flag.key]}
                  aria-label={flag.label}
                  disabled={saving !== null}
                  onCheckedChange={(checked) => void toggle(flag.key, checked)}
                />
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

function PasswordTab() {
  const { logout } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<{ current?: string; next?: string; confirm?: string }>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setFormError(null);
    const problems: typeof errors = {};
    if (!current) problems.current = "Enter your current password.";
    if (next.length < PASSWORD_MIN_LENGTH)
      problems.next = `Use at least ${PASSWORD_MIN_LENGTH} characters.`;
    if (confirm !== next) problems.confirm = "Passwords do not match.";
    setErrors(problems);
    if (Object.keys(problems).length) return;

    setSubmitting(true);
    try {
      await api.auth.changePassword({ current_password: current, new_password: next });
      toast({
        title: "Password changed",
        description: "Other sessions were revoked. Please sign in again.",
        variant: "success",
      });
      // The backend revokes all other sessions; re-authenticate with the new password.
      await logout();
      window.location.assign("/login");
    } catch (error) {
      setFormError(
        error instanceof ApiError ? error.message : "Something went wrong. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Change password</CardTitle>
        <CardDescription>
          Changing your password signs out every other device immediately.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="max-w-md space-y-5" noValidate>
          {formError ? (
            <Alert variant="destructive">
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          ) : null}
          <Field label="Current password" htmlFor="current" error={errors.current} required>
            <PasswordInput
              id="current"
              autoComplete="current-password"
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
              disabled={submitting}
            />
          </Field>
          <Field
            label="New password"
            htmlFor="next"
            error={errors.next}
            hint={`At least ${PASSWORD_MIN_LENGTH} characters.`}
            required
          >
            <PasswordInput
              id="next"
              autoComplete="new-password"
              value={next}
              onChange={(event) => setNext(event.target.value)}
              disabled={submitting}
            />
          </Field>
          <Field label="Confirm new password" htmlFor="confirm" error={errors.confirm} required>
            <PasswordInput
              id="confirm"
              autoComplete="new-password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              disabled={submitting}
            />
          </Field>
          <Button type="submit" disabled={submitting}>
            {submitting ? <Spinner /> : null}
            {submitting ? "Updating…" : "Change password"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function SessionsTab() {
  const { logoutAll } = useAuth();
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setSessions(await api.auth.sessions());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load your sessions.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function revoke(session: Session) {
    setRevoking(session.id);
    try {
      await api.auth.revokeSession(session.id);
      toast({ title: "Session revoked", variant: "success" });
      await load();
    } catch (err) {
      toast({
        title: "Could not revoke the session",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setRevoking(null);
    }
  }

  async function signOutEverywhere() {
    await logoutAll();
    window.location.assign("/login");
  }

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle>Active sessions</CardTitle>
          <CardDescription>Each sign-in has its own rotating refresh token.</CardDescription>
        </div>
        <Button variant="outline" size="sm" onClick={() => void signOutEverywhere()}>
          <ShieldCheck className="h-4 w-4" />
          Sign out everywhere
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : sessions === null ? (
          <div className="space-y-3">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : (
          sessions.map((session) => (
            <div
              key={session.id}
              className="flex flex-col gap-3 rounded-md border bg-secondary/20 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="flex items-start gap-3">
                <Laptop className="mt-0.5 h-4 w-4 text-muted-foreground" />
                <div>
                  <p className="text-sm font-medium">
                    {session.user_agent ?? "Unknown device"}
                    {session.current ? (
                      <Badge variant="success" className="ml-2">
                        This device
                      </Badge>
                    ) : null}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {session.ip_address ?? "unknown IP"} · last used{" "}
                    {formatRelative(session.last_used_at ?? session.created_at)}
                  </p>
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="text-destructive hover:text-destructive"
                disabled={revoking === session.id}
                onClick={() => void revoke(session)}
              >
                {revoking === session.id ? <Spinner /> : <Trash2 className="h-4 w-4" />}
                Revoke
              </Button>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

function SystemTab() {
  return (
    <div className="space-y-5">
      <SystemHealth />
      <Card>
        <CardHeader>
          <CardTitle>Security model</CardTitle>
          <CardDescription>How this dashboard talks to the API.</CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2 text-sm text-muted-foreground">
            <li>Access tokens live in memory only and are never written to local storage.</li>
            <li>
              Refresh tokens travel in an HttpOnly cookie scoped to <code>/api/v1/auth</code>.
            </li>
            <li>Expired access tokens are refreshed and the request retried exactly once.</li>
            <li>Every request is re-authorized on the server; the UI guard is UX only.</li>
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
