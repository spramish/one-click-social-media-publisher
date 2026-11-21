"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Cpu,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "@/hooks/use-toast";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatRelative } from "@/lib/format";
import type {
  AIContentKind,
  AIModel,
  AIModelCreatePayload,
  AIModelKind,
  AIModelPerformance,
  AIModelStatus,
  AITask,
  AITaskAttempt,
} from "@/lib/types";

type BadgeVariant = "default" | "secondary" | "destructive" | "success" | "warning";

/** How the priority list is spaced when it is renumbered. */
const PRIORITY_STEP = 10;

const KIND_LABEL: Record<AIModelKind, string> = {
  openai_compatible: "OpenAI-compatible",
  ollama: "Ollama (local)",
};

const STATUS_META: Record<AIModelStatus, { label: string; variant: BadgeVariant }> = {
  healthy: { label: "Healthy", variant: "success" },
  unhealthy: { label: "Unhealthy", variant: "destructive" },
  unknown: { label: "Not checked", variant: "secondary" },
};

const CONTENT_LABEL: Record<AIContentKind, string> = {
  caption: "Caption",
  hashtags: "Hashtags",
  description: "Description",
  rewrite: "Rewrite",
  suggestion: "Ideas",
};

const EMPTY_FORM: AIModelCreatePayload = {
  name: "",
  display_name: "",
  kind: "openai_compatible",
  model: "",
  base_url: "",
  credential_env: "",
  priority: 100,
};

/**
 * The AI model registry.
 *
 * The list order **is** the failover order: the router tries the lowest
 * priority first and moves down the list when a model is unavailable. Two rules
 * are shown rather than hidden: a model names the *environment variable* holding
 * its key (the key itself is never sent to or stored by the browser), and a
 * missing key is reported as "key missing" rather than silently failing at
 * generation time.
 */
export function ModelRegistry() {
  const { activeWorkspace, hasPermission } = useAuth();
  const workspaceId = activeWorkspace?.id ?? null;
  const canManage = hasPermission("ai:manage");

  const [models, setModels] = useState<AIModel[] | null>(null);
  const [performance, setPerformance] = useState<AIModelPerformance[] | null>(null);
  const [tasks, setTasks] = useState<AITask[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  const [reordering, setReordering] = useState(false);

  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<AIModelCreatePayload>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [openTask, setOpenTask] = useState<string | null>(null);
  const [attempts, setAttempts] = useState<Record<string, AITaskAttempt[]>>({});
  const [loadingAttempts, setLoadingAttempts] = useState(false);

  const load = useCallback(async () => {
    if (!workspaceId || !canManage) return;
    setLoadError(null);
    try {
      const [registry, stats, history] = await Promise.all([
        api.aiModels.list(workspaceId),
        api.aiModels.performance(workspaceId),
        api.aiModels.tasks(workspaceId, { limit: 20 }),
      ]);
      setModels(registry);
      setPerformance(stats);
      setTasks(history.items);
    } catch (err) {
      setModels([]);
      setPerformance([]);
      setTasks([]);
      setLoadError(err instanceof ApiError ? err.message : "Could not load the AI models.");
    }
  }, [workspaceId, canManage]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Replace one row in place, keeping the list order the router uses. */
  function replaceModel(updated: AIModel) {
    setModels((prev) => (prev ? prev.map((row) => (row.id === updated.id ? updated : row)) : prev));
  }

  /**
   * Write a new order to the server.
   *
   * Priorities are renumbered evenly so a swap always produces a strict order,
   * even when two rows currently share a priority.
   */
  async function persistOrder(next: AIModel[]) {
    if (!workspaceId) return;
    const renumbered = next.map((row, index) => ({ ...row, priority: (index + 1) * PRIORITY_STEP }));
    await Promise.all(
      renumbered
        .filter((row, index) => row.priority !== next[index].priority)
        .map((row) => api.aiModels.update(workspaceId, row.id, { priority: row.priority })),
    );
    setModels(renumbered);
  }

  async function move(index: number, delta: number) {
    if (!models) return;
    const target = index + delta;
    if (target < 0 || target >= models.length) return;
    const next = [...models];
    [next[index], next[target]] = [next[target], next[index]];
    setReordering(true);
    try {
      await persistOrder(next);
    } catch (err) {
      toast({
        title: "Could not reorder",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setReordering(false);
    }
  }

  async function toggleEnabled(model: AIModel, enabled: boolean) {
    if (!workspaceId) return;
    setBusyId(model.id);
    try {
      replaceModel(await api.aiModels.update(workspaceId, model.id, { enabled }));
    } catch (err) {
      toast({
        title: "Could not update the model",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setBusyId(null);
    }
  }

  async function remove(model: AIModel) {
    if (!workspaceId) return;
    if (!window.confirm(`Remove ${model.display_name} from the registry?`)) return;
    setBusyId(model.id);
    try {
      await api.aiModels.remove(workspaceId, model.id);
      setModels((prev) => (prev ? prev.filter((row) => row.id !== model.id) : prev));
      toast({ title: "Model removed", description: "Its past attempts are kept in the history." });
    } catch (err) {
      toast({
        title: "Could not remove the model",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setBusyId(null);
    }
  }

  async function runHealthCheck() {
    if (!workspaceId) return;
    setProbing(true);
    try {
      const result = await api.aiModels.healthCheck(workspaceId);
      await load();
      toast({
        title: "Availability checked",
        description: `${result.healthy} of ${result.checked} models responded.`,
        variant: result.unhealthy > 0 ? "warning" : "success",
      });
    } catch (err) {
      toast({
        title: "Could not check availability",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setProbing(false);
    }
  }

  async function createModel() {
    if (!workspaceId) return;
    const name = (form.name ?? "").trim();
    const displayName = (form.display_name ?? "").trim();
    const modelName = (form.model ?? "").trim();
    if (!name || !displayName || !modelName) {
      setFormError("A name, a display name and a model id are all required.");
      return;
    }

    const payload: AIModelCreatePayload = {
      name,
      display_name: displayName,
      kind: form.kind,
      model: modelName,
      priority: form.priority,
    };
    const baseUrl = (form.base_url ?? "").trim();
    if (baseUrl) payload.base_url = baseUrl;
    const credentialEnv = (form.credential_env ?? "").trim();
    if (credentialEnv) payload.credential_env = credentialEnv;

    setSaving(true);
    setFormError(null);
    try {
      const created = await api.aiModels.create(workspaceId, payload);
      setModels((prev) => (prev ? [...prev, created].sort((a, b) => a.priority - b.priority) : prev));
      setForm(EMPTY_FORM);
      setShowForm(false);
      toast({ title: "Model added", description: `${created.display_name} joined the failover list.` });
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Could not add the model.");
    } finally {
      setSaving(false);
    }
  }

  async function toggleTask(task: AITask) {
    if (!workspaceId) return;
    if (openTask === task.task_id) {
      setOpenTask(null);
      return;
    }
    setOpenTask(task.task_id);
    if (attempts[task.task_id]) return;
    setLoadingAttempts(true);
    try {
      const listing = await api.aiModels.taskAttempts(workspaceId, task.task_id);
      setAttempts((prev) => ({ ...prev, [task.task_id]: listing.items }));
    } catch (err) {
      toast({
        title: "Could not load the attempt chain",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "destructive",
      });
      setOpenTask(null);
    } finally {
      setLoadingAttempts(false);
    }
  }

  if (!activeWorkspace) {
    return (
      <Alert variant="info">
        <AlertDescription>Select a workspace to manage the AI models.</AlertDescription>
      </Alert>
    );
  }

  if (!canManage) {
    return (
      <Alert variant="warning">
        <AlertDescription>
          Your role in {activeWorkspace.name} does not include managing the AI models.
        </AlertDescription>
      </Alert>
    );
  }

  const loading = models === null;

  return (
    <div className="space-y-6">
      {loadError ? (
        <Alert variant="destructive">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      <Tabs defaultValue="models">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <TabsList>
            <TabsTrigger value="models">Models</TabsTrigger>
            <TabsTrigger value="performance">Performance</TabsTrigger>
            <TabsTrigger value="history">Task history</TabsTrigger>
          </TabsList>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => void runHealthCheck()} disabled={probing}>
              {probing ? <Spinner /> : <RefreshCw className="h-4 w-4" />}
              Check availability
            </Button>
            <Button
              onClick={() => {
                setShowForm((open) => !open);
                setFormError(null);
              }}
            >
              <Plus className="h-4 w-4" />
              Add model
            </Button>
          </div>
        </div>

        <TabsContent value="models" className="space-y-4">
          {showForm ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Add a model</CardTitle>
                <CardDescription>
                  A blank base URL takes the default for the kind. Name the environment variable that
                  holds the key — the key itself is never stored.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Name" htmlFor="model-name" hint="Stable slug, e.g. groq-llama" required>
                    <Input
                      id="model-name"
                      value={form.name ?? ""}
                      onChange={(event) => setForm({ ...form, name: event.target.value })}
                      placeholder="groq-llama"
                    />
                  </Field>
                  <Field label="Display name" htmlFor="model-display" required>
                    <Input
                      id="model-display"
                      value={form.display_name ?? ""}
                      onChange={(event) => setForm({ ...form, display_name: event.target.value })}
                      placeholder="Groq Llama 3.1"
                    />
                  </Field>
                  <Field label="Kind" htmlFor="model-kind">
                    <Select
                      id="model-kind"
                      value={form.kind}
                      onChange={(event) =>
                        setForm({ ...form, kind: event.target.value as AIModelKind })
                      }
                    >
                      <option value="openai_compatible">OpenAI-compatible</option>
                      <option value="ollama">Ollama (local)</option>
                    </Select>
                  </Field>
                  <Field label="Model id" htmlFor="model-id" hint="The upstream model name" required>
                    <Input
                      id="model-id"
                      value={form.model ?? ""}
                      onChange={(event) => setForm({ ...form, model: event.target.value })}
                      placeholder="llama-3.1-70b-versatile"
                    />
                  </Field>
                  <Field label="Base URL" htmlFor="model-base-url" hint="Optional">
                    <Input
                      id="model-base-url"
                      value={form.base_url ?? ""}
                      onChange={(event) => setForm({ ...form, base_url: event.target.value })}
                      placeholder="https://api.groq.com/openai/v1"
                    />
                  </Field>
                  <Field
                    label="Credential env var"
                    htmlFor="model-credential"
                    hint="Optional — omit for a local model"
                  >
                    <Input
                      id="model-credential"
                      value={form.credential_env ?? ""}
                      onChange={(event) => setForm({ ...form, credential_env: event.target.value })}
                      placeholder="GROQ_API_KEY"
                    />
                  </Field>
                  <Field label="Priority" htmlFor="model-priority" hint="Lower is tried first">
                    <Input
                      id="model-priority"
                      type="number"
                      min={0}
                      value={form.priority ?? 100}
                      onChange={(event) =>
                        setForm({ ...form, priority: Number(event.target.value) || 0 })
                      }
                    />
                  </Field>
                </div>

                {formError ? (
                  <Alert variant="destructive">
                    <AlertDescription>{formError}</AlertDescription>
                  </Alert>
                ) : null}

                <div className="flex justify-end gap-2">
                  <Button
                    variant="outline"
                    onClick={() => {
                      setShowForm(false);
                      setFormError(null);
                    }}
                  >
                    Cancel
                  </Button>
                  <Button onClick={() => void createModel()} disabled={saving}>
                    {saving ? <Spinner /> : <Plus className="h-4 w-4" />}
                    Add model
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : null}

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Failover order</CardTitle>
              <CardDescription>
                The router tries these from the top. Each row shows the health it last observed.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {loading ? (
                <div className="space-y-3">
                  <Skeleton className="h-16 w-full" />
                  <Skeleton className="h-16 w-full" />
                </div>
              ) : models.length === 0 ? (
                <EmptyState
                  icon={Cpu}
                  title="No models registered"
                  description="The assistant falls back to the server's own environment settings. Add a model here to control the failover order, or to run fully locally with Ollama."
                />
              ) : (
                <ul className="divide-y">
                  {models.map((model, index) => (
                    <ModelRow
                      key={model.id}
                      model={model}
                      index={index}
                      count={models.length}
                      busy={busyId === model.id || reordering}
                      onMove={(delta) => void move(index, delta)}
                      onToggle={(enabled) => void toggleEnabled(model, enabled)}
                      onRemove={() => void remove(model)}
                    />
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="performance">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Performance</CardTitle>
              <CardDescription>
                Success rate and average latency from the last 30 days of attempts.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {performance === null ? (
                <Skeleton className="h-32 w-full" />
              ) : performance.length === 0 ? (
                <EmptyState
                  icon={Activity}
                  title="No attempts recorded yet"
                  description="Once the assistant writes something, each model's record appears here."
                />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Model</TableHead>
                      <TableHead>Attempts</TableHead>
                      <TableHead>Succeeded</TableHead>
                      <TableHead>Failed</TableHead>
                      <TableHead>Success rate</TableHead>
                      <TableHead>Avg latency</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {performance.map((row) => (
                      <TableRow key={row.model_name}>
                        <TableCell className="font-medium">{row.model_name}</TableCell>
                        <TableCell>{row.total}</TableCell>
                        <TableCell>{row.succeeded}</TableCell>
                        <TableCell>{row.failed}</TableCell>
                        <TableCell>{Math.round(row.success_rate * 100)}%</TableCell>
                        <TableCell>
                          {row.avg_latency_ms === null ? "—" : `${row.avg_latency_ms} ms`}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="history">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Task history</CardTitle>
              <CardDescription>
                Every routed task and the model that answered it. Expand one to see the full attempt
                chain.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {tasks === null ? (
                <Skeleton className="h-32 w-full" />
              ) : tasks.length === 0 ? (
                <EmptyState
                  icon={Activity}
                  title="Nothing routed yet"
                  description="Generate something with the assistant and the routing history will appear here."
                />
              ) : (
                <ul className="divide-y">
                  {tasks.map((task) => (
                    <TaskRow
                      key={task.task_id}
                      task={task}
                      open={openTask === task.task_id}
                      loading={loadingAttempts && openTask === task.task_id}
                      attempts={attempts[task.task_id] ?? null}
                      onToggle={() => void toggleTask(task)}
                    />
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

/** One registered model, with its controls and last observed health. */
function ModelRow({
  model,
  index,
  count,
  busy,
  onMove,
  onToggle,
  onRemove,
}: {
  model: AIModel;
  index: number;
  count: number;
  busy: boolean;
  onMove: (delta: number) => void;
  onToggle: (enabled: boolean) => void;
  onRemove: () => void;
}) {
  const status = STATUS_META[model.status];

  return (
    <li className="flex flex-wrap items-start justify-between gap-4 py-4">
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">#{index + 1}</Badge>
          <span className="font-medium">{model.display_name}</span>
          <Badge variant="secondary">{KIND_LABEL[model.kind]}</Badge>
          <Badge variant={status.variant}>{status.label}</Badge>
          {!model.enabled ? <Badge variant="warning">Disabled</Badge> : null}
          {model.credential_env ? (
            <Badge variant={model.credential_ready ? "success" : "destructive"}>
              {model.credential_ready ? "Key set" : "Key missing"}
            </Badge>
          ) : (
            <Badge variant="outline">No key needed</Badge>
          )}
        </div>
        <p className="truncate text-xs text-muted-foreground">
          {model.name} · {model.model}
          {model.credential_env ? ` · ${model.credential_env}` : ""}
        </p>
        {model.last_error ? (
          <p className="line-clamp-2 break-words text-xs text-muted-foreground">
            Last error: {model.last_error}
          </p>
        ) : model.last_ok_at ? (
          <p className="text-xs text-muted-foreground">Last responded {formatRelative(model.last_ok_at)}</p>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <Button
          size="icon"
          variant="ghost"
          aria-label="Move up"
          disabled={busy || index === 0}
          onClick={() => onMove(-1)}
        >
          <ArrowUp className="h-4 w-4" />
        </Button>
        <Button
          size="icon"
          variant="ghost"
          aria-label="Move down"
          disabled={busy || index === count - 1}
          onClick={() => onMove(1)}
        >
          <ArrowDown className="h-4 w-4" />
        </Button>
        <Switch
          checked={model.enabled}
          disabled={busy}
          aria-label={`Enable ${model.display_name}`}
          onCheckedChange={onToggle}
        />
        <Button
          size="icon"
          variant="ghost"
          aria-label="Remove model"
          disabled={busy}
          onClick={onRemove}
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>
    </li>
  );
}

/** One routed task; expanding it reveals the models that were tried. */
function TaskRow({
  task,
  open,
  loading,
  attempts,
  onToggle,
}: {
  task: AITask;
  open: boolean;
  loading: boolean;
  attempts: AITaskAttempt[] | null;
  onToggle: () => void;
}) {
  return (
    <li className="py-3">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-start justify-between gap-4 text-left"
      >
        <span className="min-w-0 space-y-1">
          <span className="flex flex-wrap items-center gap-2">
            <Badge variant={task.status === "succeeded" ? "success" : "destructive"}>
              {task.status === "succeeded" ? "Succeeded" : "Failed"}
            </Badge>
            <Badge variant="secondary">{CONTENT_LABEL[task.kind]}</Badge>
            <span className="text-sm font-medium">{task.model_name}</span>
            {task.attempts > 1 ? (
              <Badge variant="warning">{task.attempts} attempts</Badge>
            ) : null}
          </span>
          <span className="block text-xs text-muted-foreground">
            {formatRelative(task.created_at)}
          </span>
        </span>
        {open ? (
          <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        )}
      </button>

      {open ? (
        <div className="mt-3 pl-1">
          {loading ? (
            <Skeleton className="h-12 w-full" />
          ) : attempts && attempts.length > 0 ? (
            <ol className="space-y-2">
              {attempts.map((attempt) => (
                <li
                  key={attempt.id}
                  className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-xs"
                >
                  <Badge variant="outline">#{attempt.attempt}</Badge>
                  <span className="font-medium">{attempt.model_name}</span>
                  <Badge variant={attempt.status === "succeeded" ? "success" : "destructive"}>
                    {attempt.status}
                  </Badge>
                  {attempt.latency_ms !== null ? (
                    <span className="text-muted-foreground">{attempt.latency_ms} ms</span>
                  ) : null}
                  {attempt.error ? (
                    <span className="min-w-0 flex-1 truncate text-muted-foreground">
                      {attempt.error_class ? `${attempt.error_class}: ` : ""}
                      {attempt.error}
                    </span>
                  ) : null}
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-xs text-muted-foreground">No attempts recorded for this task.</p>
          )}
        </div>
      ) : null}
    </li>
  );
}
