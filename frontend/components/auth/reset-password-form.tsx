"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { PasswordInput } from "@/components/auth/password-input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Spinner } from "@/components/ui/spinner";
import { toast } from "@/hooks/use-toast";
import { ApiError, api } from "@/lib/api";
import { PASSWORD_MIN_LENGTH } from "@/lib/config";

export function ResetPasswordForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<{ password?: string; confirm?: string }>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (!token) {
    return (
      <Alert variant="destructive">
        <AlertDescription>
          This reset link is missing its token. Request a new link from the{" "}
          <Link href="/forgot-password" className="font-medium underline">
            forgot-password page
          </Link>
          .
        </AlertDescription>
      </Alert>
    );
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setFormError(null);
    const next: { password?: string; confirm?: string } = {};
    if (password.length < PASSWORD_MIN_LENGTH)
      next.password = `Use at least ${PASSWORD_MIN_LENGTH} characters.`;
    if (confirm !== password) next.confirm = "Passwords do not match.";
    setErrors(next);
    if (Object.keys(next).length) return;

    setSubmitting(true);
    try {
      await api.auth.confirmPasswordReset(token, password);
      toast({ title: "Password updated", description: "Sign in with your new password.", variant: "success" });
      router.replace("/login");
    } catch (err) {
      setFormError(
        err instanceof ApiError ? err.message : "Something went wrong. Please try again.",
      );
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
      <Field
        label="New password"
        htmlFor="password"
        error={errors.password}
        hint={`At least ${PASSWORD_MIN_LENGTH} characters.`}
        required
      >
        <PasswordInput
          id="password"
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          aria-invalid={Boolean(errors.password)}
          disabled={submitting}
        />
      </Field>
      <Field label="Confirm new password" htmlFor="confirm" error={errors.confirm} required>
        <PasswordInput
          id="confirm"
          autoComplete="new-password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
          aria-invalid={Boolean(errors.confirm)}
          disabled={submitting}
        />
      </Field>
      <Button type="submit" className="w-full" disabled={submitting}>
        {submitting ? <Spinner /> : null}
        {submitting ? "Updating…" : "Set new password"}
      </Button>
    </form>
  );
}
