import Link from "next/link";
import type { ReactNode } from "react";

import { Brand } from "@/components/brand";

/** Split-screen auth layout: form on the left, product story on the right. */
export function AuthLayout({
  title,
  subtitle,
  children,
  footer,
  aside,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
  footer?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="flex flex-col justify-center px-6 py-10 sm:px-10">
        <div className="mx-auto w-full max-w-md">
          <Link href="/" aria-label="One-Click Publisher home">
            <Brand />
          </Link>
          <div className="mt-8 space-y-1">
            <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
            <p className="text-sm text-muted-foreground">{subtitle}</p>
          </div>
          <div className="mt-6">{children}</div>
          {footer ? <div className="mt-6 text-sm text-muted-foreground">{footer}</div> : null}
        </div>
      </div>

      <aside className="relative hidden border-l bg-muted/30 lg:flex lg:flex-col lg:justify-center lg:px-12">
        <div className="mx-auto w-full max-w-md">{aside}</div>
      </aside>
    </div>
  );
}

/** Centred single-column auth page (verification, password reset). */
export function AuthCard({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center px-6 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 flex justify-center">
          <Link href="/" aria-label="One-Click Publisher home">
            <Brand />
          </Link>
        </div>
        <div className="rounded-xl border bg-card p-6 shadow-sm sm:p-8">
          <div className="space-y-1">
            <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
            {subtitle ? <p className="text-sm text-muted-foreground">{subtitle}</p> : null}
          </div>
          <div className="mt-6">{children}</div>
        </div>
        {footer ? <div className="mt-6 text-center text-sm text-muted-foreground">{footer}</div> : null}
      </div>
    </div>
  );
}
