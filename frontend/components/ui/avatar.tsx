import * as React from "react";

import { cn } from "@/lib/utils";

function Avatar({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "relative flex h-9 w-9 shrink-0 overflow-hidden rounded-full border border-border bg-muted",
        className,
      )}
      {...props}
    />
  );
}

function AvatarFallback({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "flex h-full w-full items-center justify-center rounded-full text-xs font-semibold uppercase text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

/** Two-letter initials from a name, falling back to the email. */
export function initialsOf(value: string | null | undefined): string {
  const source = (value ?? "").trim();
  if (!source) return "?";
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return `${parts[0][0]}${parts[1][0]}`;
  }
  return source.slice(0, 2);
}

export { Avatar, AvatarFallback };
