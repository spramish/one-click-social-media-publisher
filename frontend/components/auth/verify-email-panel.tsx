"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { CheckCircle2, XCircle } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { ApiError, api } from "@/lib/api";

type State = "verifying" | "success" | "error";

export function VerifyEmailPanel() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [state, setState] = useState<State>(token ? "verifying" : "error");
  const [message, setMessage] = useState<string>(
    token ? "" : "This verification link is missing its token.",
  );

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const result = await api.auth.verifyEmail(token);
        if (cancelled) return;
        setMessage(result.message);
        setState("success");
      } catch (error) {
        if (cancelled) return;
        setMessage(
          error instanceof ApiError ? error.message : "We could not verify this link.",
        );
        setState("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (state === "verifying") {
    return (
      <div className="flex flex-col items-center gap-3 py-4 text-sm text-muted-foreground">
        <Spinner className="h-5 w-5" />
        <span>Verifying your email…</span>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <Alert variant={state === "success" ? "success" : "destructive"}>
        {state === "success" ? <CheckCircle2 /> : <XCircle />}
        <AlertDescription>{message}</AlertDescription>
      </Alert>
      <Button asChild className="w-full">
        <Link href={state === "success" ? "/dashboard" : "/login"}>
          {state === "success" ? "Continue to dashboard" : "Back to sign in"}
        </Link>
      </Button>
    </div>
  );
}
