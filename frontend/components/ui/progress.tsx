import * as React from "react";

import { cn } from "@/lib/utils";

/** Accessible progress bar. `value` is clamped to 0–100. */
function Progress({
  value = 0,
  className,
  label,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { value?: number; label?: string }) {
  const clamped = Math.min(100, Math.max(0, value));
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped}
      aria-label={label}
      className={cn("h-2 w-full overflow-hidden rounded-full bg-muted", className)}
      {...props}
    >
      <div
        className="h-full rounded-full bg-primary transition-all"
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

export { Progress };
