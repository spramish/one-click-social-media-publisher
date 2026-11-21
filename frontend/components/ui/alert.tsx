import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { AlertCircle, CheckCircle2, Info, TriangleAlert } from "lucide-react";

import { cn } from "@/lib/utils";

const alertVariants = cva(
  "relative w-full rounded-lg border px-4 py-3 text-sm [&>svg]:absolute [&>svg]:left-4 [&>svg]:top-4 [&>svg]:h-4 [&>svg]:w-4 [&>svg~*]:pl-7",
  {
    variants: {
      variant: {
        default: "bg-card text-card-foreground",
        info: "border-info/40 bg-info/10 text-foreground [&>svg]:text-info",
        success: "border-success/40 bg-success/10 text-foreground [&>svg]:text-success",
        warning: "border-warning/40 bg-warning/10 text-foreground [&>svg]:text-warning",
        destructive:
          "border-destructive/40 bg-destructive/10 text-foreground [&>svg]:text-destructive",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

const ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  info: Info,
  success: CheckCircle2,
  warning: TriangleAlert,
  destructive: AlertCircle,
  default: Info,
};

export interface AlertProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof alertVariants> {}

function Alert({ className, variant = "default", children, ...props }: AlertProps) {
  const Icon = ICONS[variant ?? "default"] ?? Info;
  return (
    <div role="alert" className={cn(alertVariants({ variant }), className)} {...props}>
      <Icon />
      {children}
    </div>
  );
}

function AlertTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h5 className={cn("mb-1 font-medium leading-none tracking-tight", className)} {...props} />;
}

function AlertDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <div className={cn("text-sm text-muted-foreground", className)} {...props} />;
}

export { Alert, AlertTitle, AlertDescription, alertVariants };
