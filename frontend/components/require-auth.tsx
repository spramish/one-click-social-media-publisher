"use client";

import { usePathname, useRouter } from "next/navigation";
import * as React from "react";

import { Spinner } from "@/components/ui/spinner";
import { useAuth } from "@/lib/auth-context";

/**
 * Client-side route guard. Renders a loader while the session is resolved and
 * redirects anonymous visitors to `/login`, preserving the intended path.
 *
 * This is a UX guard only — every protected API route re-checks the token
 * server-side, so it is not a security boundary.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  React.useEffect(() => {
    if (status === "anonymous") {
      const next = encodeURIComponent(pathname || "/dashboard");
      router.replace(`/login?next=${next}`);
    }
  }, [status, router, pathname]);

  if (status !== "authenticated") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="flex flex-col items-center gap-3 text-sm text-muted-foreground">
          <Spinner className="h-5 w-5" />
          <span>
            {status === "loading" ? "Checking your session…" : "Redirecting to sign in…"}
          </span>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
