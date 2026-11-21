import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CheckCircle2 } from "lucide-react";

/**
 * Honest placeholder for a module whose backend arrives in a later phase.
 *
 * It lists exactly what will be built and which phase delivers it — no mock
 * data or fake charts are shown, so the UI never implies working functionality
 * that does not exist yet.
 */
export function ModulePlaceholder({
  phase,
  title,
  description,
  capabilities,
}: {
  phase: number;
  title: string;
  description: string;
  capabilities: string[];
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CardTitle>{title}</CardTitle>
          <Badge variant="secondary">Phase {phase}</Badge>
        </div>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Delivered in this phase
        </p>
        <ul className="grid gap-2 sm:grid-cols-2">
          {capabilities.map((capability) => (
            <li key={capability} className="flex items-start gap-2 text-sm text-muted-foreground">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/70" />
              <span>{capability}</span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
