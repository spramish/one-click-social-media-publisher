"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { PasswordInput } from "@/components/auth/password-input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Spinner } from "@/components/ui/spinner";
import { toast } from "@/hooks/use-toast";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { PASSWORD_MIN_LENGTH } from "@/lib/config";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type Score = { value: number; label: string; tone: string };

/** Cheap client-side strength signal — the backend enforces the real policy. */
function scorePassword(password: string): Score {
  if (!password) return { value: 0, label: "Empty", tone: "bg-muted" };
  let score = 0;
  if (password.length >= PASSWORD_MIN_LENGTH) score += 1;
  if (password.length >= 16) score += 1;
  if (/[a-z]/.test(password) && /[A-Z]/.test(password)) score += 1;
  if (/\d/.test(password)) score += 1;
  if (/[^A-Za-z0-9]/.test(password)) score += 1;
  const value = Math.min(100, (score / 5) * 100);
  if (value >= 80) return { value, label: "Strong", tone: "bg-success" };
  if (value >= 60) return { value, label: "Good", tone: "bg-info" };
  if (value >= 40) return { value, label: "Fair", tone: "bg-warning" };
  return { value, label: "Weak", tone: "bg-destructive" };
}

export function RegisterForm() {
  const router = useRouter();
  const { register } = useAuth();

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const strength = useMemo(() => scorePassword(password), [password]);

  function validate(): boolean {
    const next: Record<string, string> = {};
    if (!email.trim()) next.email = "Email is required.";
    else if (!EMAIL_PATTERN.test(email.trim())) next.email = "Enter a valid email address.";
    if (!password) next.password = "Password is required.";
    else if (password.length < PASSWORD_MIN_LENGTH)
      next.password = `Use at least ${PASSWORD_MIN_LENGTH} characters.`;
    if (confirm !== password) next.confirm = "Passwords do not match.";
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setFormError(null);
    if (!validate()) return;

    setSubmitting(true);
    try {
      await register({
        email: email.trim(),
        password,
        full_name: fullName.trim() || undefined,
        workspace_name: workspaceName.trim() || undefined,
      });
      toast({
        title: "Account created",
        description: "We sent a verification link to your email.",
        variant: "success",
      });
      router.replace("/dashboard");
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.status === 409
            ? "An account with that email already exists."
            : error.message
          : "Something went wrong. Please try again.";
      setFormError(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-5" noValidate>
      {formError ? (
        <Alert variant="destructive">
          <AlertDescription>{formError}</AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-5 sm:grid-cols-2">
        <Field label="Full name" htmlFor="full_name" error={errors.full_name}>
          <Input
            id="full_name"
            name="full_name"
            autoComplete="name"
            placeholder="Ada Lovelace"
            value={fullName}
            onChange={(event) => setFullName(event.target.value)}
            disabled={submitting}
          />
        </Field>
        <Field label="Workspace name" htmlFor="workspace_name" error={errors.workspace_name}>
          <Input
            id="workspace_name"
            name="workspace_name"
            placeholder="Ada's Studio"
            value={workspaceName}
            onChange={(event) => setWorkspaceName(event.target.value)}
            disabled={submitting}
          />
        </Field>
      </div>

      <Field label="Email" htmlFor="email" error={errors.email} required>
        <Input
          id="email"
          name="email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          aria-invalid={Boolean(errors.email)}
          aria-describedby={errors.email ? "email-error" : undefined}
          disabled={submitting}
        />
      </Field>

      <Field
        label="Password"
        htmlFor="password"
        error={errors.password}
        hint={`At least ${PASSWORD_MIN_LENGTH} characters.`}
        required
      >
        <PasswordInput
          id="password"
          name="password"
          autoComplete="new-password"
          placeholder="Create a strong password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          aria-invalid={Boolean(errors.password)}
          aria-describedby={errors.password ? "password-error" : undefined}
          disabled={submitting}
        />
        {password ? (
          <div className="flex items-center gap-3 pt-1">
            <Progress value={strength.value} label="Password strength" className="h-1.5" />
            <span className="w-12 shrink-0 text-right text-xs font-medium text-muted-foreground">
              {strength.label}
            </span>
          </div>
        ) : null}
      </Field>

      <Field label="Confirm password" htmlFor="confirm" error={errors.confirm} required>
        <PasswordInput
          id="confirm"
          name="confirm"
          autoComplete="new-password"
          placeholder="Repeat your password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
          aria-invalid={Boolean(errors.confirm)}
          aria-describedby={errors.confirm ? "confirm-error" : undefined}
          disabled={submitting}
        />
      </Field>

      <Button type="submit" className="w-full" disabled={submitting}>
        {submitting ? <Spinner /> : null}
        {submitting ? "Creating account…" : "Create account"}
      </Button>

      <p className="text-center text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link href="/login" className="font-medium text-primary underline-offset-4 hover:underline">
          Sign in
        </Link>
      </p>
    </form>
  );
}
