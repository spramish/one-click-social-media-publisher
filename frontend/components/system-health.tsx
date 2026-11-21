"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, Database, RefreshCw, Server } from "lucide-react";

import { api } from "@/lib/api";
import type { HealthStatus } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

type State = "loading" | "ok" | "error";

type Probe = {
  key: string;
  label: string;
  icon: React.ReactNode;
  description: string;
  run: () => Promise<HealthStatus>;
};

const PROBES: Probe[] = [
  {
    key: "api",
    label: "FastAPI backend",
    icon: <Server className="h-4 w-4" />,
    description: "Liveness probe of the API process",
    run: api.health,
  },
  {
    key: "db",
    label: "PostgreSQL",
    icon: <Database className="h-4 w-4" />,
    description: "Database readiness probe",
    run: api.healthDb,
  },
  {
    key: "redis",
    label: "Redis",
    icon: <Activity className="h-4 w-4" />,
    description: "Broker / cache readiness probe",
    run: api.healthRedis,
  },
];

export function SystemHealth() {
  const [states, setStates] = useState<Record<string, State>>({
    api: "loading",
    db: "loading",
    redis: "loading",
  });
  const [details, setDetails] = useState<Record<string, string>>({});
  const [checking, setChecking] = useState(false);

  const check = useCallback(async () => {
    setChecking(true);
    setStates({ api: "loading", db: "loading", redis: "loading" });
    const results = await Promise.all(
      PROBES.map(async (probe) => {
        try {
          const body = await probe.run();
          return { key: probe.key, status: body.status, detail: JSON.stringify(body) } as const;
        } catch (error) {
          return {
            key: probe.key,
            status: "error" as const,
            detail: error instanceof Error ? error.message : "Unknown error",
          } as const;
        }
      }),
    );
    setStates(Object.fromEntries(results.map((r) => [r.key, r.status as State])));
    setDetails(Object.fromEntries(results.map((r) => [r.key, r.detail])));
    setChecking(false);
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle>System health</CardTitle>
          <CardDescription>
            Verifies the frontend can reach the backend, database, and Redis.
          </CardDescription>
        </div>
        <Button variant="outline" size="sm" onClick={() => void check()} disabled={checking}>
          <RefreshCw className={cn("h-4 w-4", checking && "animate-spin")} />
          Re-check
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        {PROBES.map((probe) => {
          const state = states[probe.key];
          return (
            <div
              key={probe.key}
              className="flex items-center justify-between rounded-md border bg-secondary/30 px-4 py-3"
            >
              <div className="flex items-center gap-3">
                <span className="text-muted-foreground">{probe.icon}</span>
                <div>
                  <p className="text-sm font-medium">{probe.label}</p>
                  <p className="text-xs text-muted-foreground">{probe.description}</p>
                </div>
              </div>
              {state === "loading" ? (
                <Badge variant="secondary">Checking…</Badge>
              ) : state === "ok" ? (
                <Badge variant="success">Operational</Badge>
              ) : (
                <Badge variant="destructive" title={details[probe.key]}>
                  Unreachable
                </Badge>
              )}
            </div>
          );
        })}
        <p className="text-xs text-muted-foreground">
          API base URL: <code className="font-mono">{api.baseUrl}</code>
        </p>
      </CardContent>
    </Card>
  );
}
