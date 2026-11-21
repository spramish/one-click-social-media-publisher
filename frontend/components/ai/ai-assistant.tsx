"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Check, ClipboardCopy, Pencil, Sparkles, WandSparkles } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/field";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/hooks/use-toast";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatRelative } from "@/lib/format";
import { PLATFORMS, type SocialPlatform } from "@/lib/platforms";
import type { AICapability, AIContent, AIContentKind } from "@/lib/types";

type BadgeVariant = "default" | "secondary" | "destructive" | "success" | "warning";

/** What each kind of copy is for, and whether it needs source text. */
const KIND_META: Record<
  AIContentKind,
  { label: string; description: string; needsDraft?: boolean }
> = {
  caption: {
    label: "Caption",
    description: "A post caption for the video.",
  },
  hashtags: {
    label: "Hashtags",
    description: "A focused set of hashtags to append to a caption.",
  },
  description: {
    label: "Description",
    description: "A longer description, e.g. for a YouTube video page.",
  },
  rewrite: {
    label: "Rewrite",
    description: "Rewrite text you already have, keeping its meaning.",
    needsDraft: true,
  },
  suggestion: {
    label: "Ideas",
    description: "Content angles and hooks for this video.",
  },
};

/** A stable order for the kind tabs, matching the backend enum. */
const KIND_ORDER: AIContentKind[] = ["caption", "hashtags", "description", "rewrite", "suggestion"];

/**
 * The AI content assistant.
 *
 * Nothing here publishes: a generated suggestion is stored by the backend, shown
 * for review, and copied into the composer by the user's own action. When the
 * server has no model configured the page says so rather than failing.
 */
export function AIAssistant() {
  const { activeWorkspace, hasPermission } = useAuth();
  const workspaceId = activeWorkspace?.id ?? null;

  const canGenerate = hasPermission("ai:generate");

  const [capability, setCapability] = useState<AICapability | null>(null);
  const [history, setHistory] = useState<AIContent[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [kind, setKind] = useState<AIContentKind>("caption");
  const [brief, setBrief] = useState("");
  const [tone, setTone] = useState("");
  const [style, setStyle] = useState("");
  const [platform, setPlatform] = useState<SocialPlatform | "">("");
  const [draft, setDraft] = useState("");

  const [generating, setGenerating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [result, setResult] = useState<AIContent | null>(null);
  const [output, setOutput] = useState("");
  const [accepting, setAccepting] = useState(false);

  const load = useCallback(async () => {
    if (!workspaceId || !canGenerate) return;
    setLoadError(null);
    setCapability(null);
    setHistory(null);
    try {
      const [caps, past] = await Promise.all([
        api.ai.capability(workspaceId),
        api.ai.history(workspaceId, { limit: 20 }),
      ]);
      setCapability(caps);
      setHistory(past);
    } catch (err) {
      setHistory([]);
      setLoadError(err instanceof ApiError ? err.message : "Could not load the assistant.");
    }
  }, [workspaceId, canGenerate]);

  useEffect(() => {
    void load();
  }, [load]);

  async function generate() {
    if (!workspaceId) return;
    setFormError(null);

    const meta = KIND_META[kind];
    if (!brief.trim()) {
      setFormError("Describe the video so the assistant has something to work from.");
      return;
    }
    if (meta.needsDraft && !draft.trim()) {
      setFormError("Paste the text you want rewritten.");
      return;
    }

    setGenerating(true);
    try {
      const content = await api.ai.generate(workspaceId, {
        kind,
        brief: brief.trim(),
        tone: tone || null,
        style: style || null,
        platform: platform || null,
        draft: meta.needsDraft ? draft.trim() : null,
      });
      setResult(content);
      setOutput(content.output);
      setHistory((prev) => [content, ...(prev ?? [])]);
      toast({
        title: "Suggestion ready",
        description: "Review it below, then copy it into the composer.",
        variant: "success",
      });
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "The assistant could not respond.");
    } finally {
      setGenerating(false);
    }
  }

  async function accept(content: AIContent) {
    if (!workspaceId) return;
    setAccepting(true);
    try {
      const updated = await api.ai.accept(workspaceId, content.id);
      setHistory((prev) =>
        (prev ?? []).map((item) => (item.id === updated.id ? updated : item)),
      );
      setResult((prev) => (prev && prev.id === updated.id ? updated : prev));
      toast({ title: "Suggestion accepted", description: "Kept in your history. Nothing was published." });
    } catch (err) {
      toast({
        title: "Could not accept the suggestion",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setAccepting(false);
    }
  }

  if (!activeWorkspace) {
    return (
      <Alert variant="info">
        <AlertDescription>Select a workspace to use the assistant.</AlertDescription>
      </Alert>
    );
  }

  if (!canGenerate) {
    return (
      <Alert variant="warning">
        <AlertDescription>
          Your role in {activeWorkspace.name} does not include access to the AI assistant.
        </AlertDescription>
      </Alert>
    );
  }

  const loading = capability === null || history === null;
  const configured = capability?.configured ?? false;
  const maxBrief = capability?.max_brief_chars ?? 2000;
  const needsDraft = KIND_META[kind].needsDraft ?? false;

  return (
    <div className="space-y-6">
      {loadError ? (
        <Alert variant="destructive">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      {loading ? (
        <Skeleton className="h-40 w-full" />
      ) : !configured ? (
        <Alert variant="warning">
          <AlertTitle>The assistant is not configured</AlertTitle>
          <AlertDescription>
            This server has no AI model credentials, so nothing can be generated. Set{" "}
            <code className="font-mono text-xs">OPENAI_API_KEY</code> on the backend and restart it.
            Everything else in the app keeps working without it.
          </AlertDescription>
        </Alert>
      ) : (
        <>
          <Card>
            <CardHeader className="gap-4 sm:flex-row sm:items-start sm:justify-between sm:space-y-0">
              <div className="space-y-1.5">
                <CardTitle className="text-base">Write with the assistant</CardTitle>
                <CardDescription>
                  Describe the video, choose a tone, and generate copy. Nothing is published — you
                  review and reuse it yourself.
                </CardDescription>
              </div>
              <Badge variant="secondary" className="shrink-0">
                {capability?.provider} · {capability?.model}
              </Badge>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="flex flex-wrap gap-2">
                {KIND_ORDER.filter((option) => (capability?.kinds ?? []).includes(option)).map(
                  (option) => (
                    <Button
                      key={option}
                      type="button"
                      size="sm"
                      variant={option === kind ? "default" : "outline"}
                      onClick={() => {
                        setKind(option);
                        setFormError(null);
                      }}
                    >
                      {KIND_META[option].label}
                    </Button>
                  ),
                )}
              </div>

              <Field
                label={needsDraft ? "What is the video about?" : "Brief"}
                htmlFor="ai-brief"
                hint={`${KIND_META[kind].description} Up to ${maxBrief.toLocaleString()} characters.`}
              >
                <Textarea
                  id="ai-brief"
                  value={brief}
                  maxLength={maxBrief}
                  onChange={(event) => setBrief(event.target.value)}
                  placeholder="e.g. A 30-second clip of a sunrise hike, ending with the summit view."
                  className="min-h-[100px]"
                />
              </Field>

              {needsDraft ? (
                <Field label="Text to rewrite" htmlFor="ai-draft" required>
                  <Textarea
                    id="ai-draft"
                    value={draft}
                    maxLength={10000}
                    onChange={(event) => setDraft(event.target.value)}
                    placeholder="Paste the caption or description you want rewritten."
                    className="min-h-[100px]"
                  />
                </Field>
              ) : null}

              <div className="grid gap-4 sm:grid-cols-3">
                <Field label="Tone" htmlFor="ai-tone">
                  <Select
                    id="ai-tone"
                    value={tone}
                    onChange={(event) => setTone(event.target.value)}
                  >
                    <option value="">Default</option>
                    {(capability?.tones ?? []).map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Style" htmlFor="ai-style">
                  <Select
                    id="ai-style"
                    value={style}
                    onChange={(event) => setStyle(event.target.value)}
                  >
                    <option value="">Default</option>
                    {(capability?.styles ?? []).map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Platform" htmlFor="ai-platform" hint="Optional">
                  <Select
                    id="ai-platform"
                    value={platform}
                    onChange={(event) => setPlatform(event.target.value as SocialPlatform | "")}
                  >
                    <option value="">Any platform</option>
                    {PLATFORMS.map((option) => (
                      <option key={option.key} value={option.key}>
                        {option.name}
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>

              {formError ? (
                <Alert variant="destructive">
                  <AlertDescription>{formError}</AlertDescription>
                </Alert>
              ) : null}

              <div className="flex justify-end">
                <Button onClick={() => void generate()} disabled={generating}>
                  {generating ? <Spinner /> : <WandSparkles className="h-4 w-4" />}
                  Generate
                </Button>
              </div>
            </CardContent>
          </Card>

          {result ? (
            <ResultCard
              content={result}
              output={output}
              onOutputChange={setOutput}
              onAccept={() => void accept(result)}
              accepting={accepting}
            />
          ) : null}

          <HistoryCard entries={history} onAccept={accept} accepting={accepting} />
        </>
      )}
    </div>
  );
}

/** The editable result of the most recent generation. */
function ResultCard({
  content,
  output,
  onOutputChange,
  onAccept,
  accepting,
}: {
  content: AIContent;
  output: string;
  onOutputChange: (value: string) => void;
  onAccept: () => void;
  accepting: boolean;
}) {
  return (
    <Card>
      <CardHeader className="gap-4 sm:flex-row sm:items-start sm:justify-between sm:space-y-0">
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle className="text-base">Suggestion</CardTitle>
            <Badge variant="secondary">{KIND_META[content.kind].label}</Badge>
            {content.tone ? <Badge variant="outline">{content.tone}</Badge> : null}
            {content.is_accepted ? <Badge variant="success">Accepted</Badge> : null}
          </div>
          <CardDescription>
            Edit freely — this is a draft. Copy it into the composer to use it.
          </CardDescription>
        </div>
        <CopyButton value={output} />
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label="Generated text" htmlFor="ai-output">
          <Textarea
            id="ai-output"
            value={output}
            rows={8}
            onChange={(event) => onOutputChange(event.target.value)}
            className="min-h-[160px] font-mono text-sm"
          />
        </Field>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Button asChild variant="outline">
            <Link href="/compose">
              <Pencil className="h-4 w-4" />
              Open the composer
            </Link>
          </Button>
          <Button onClick={onAccept} disabled={accepting || content.is_accepted}>
            {accepting ? <Spinner /> : <Check className="h-4 w-4" />}
            {content.is_accepted ? "Accepted" : "Accept"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/** Past generations, newest first, so an idea is never lost. */
function HistoryCard({
  entries,
  onAccept,
  accepting,
}: {
  entries: AIContent[];
  onAccept: (content: AIContent) => void;
  accepting: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">History</CardTitle>
        <CardDescription>Every suggestion is kept here for later reuse.</CardDescription>
      </CardHeader>
      <CardContent>
        {entries.length === 0 ? (
          <EmptyState
            icon={Sparkles}
            title="No suggestions yet"
            description="Generate a caption or hashtag set above and it will appear here."
          />
        ) : (
          <ul className="divide-y">
            {entries.map((entry) => (
              <li key={entry.id} className="flex items-start justify-between gap-4 py-4">
                <div className="min-w-0 space-y-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="secondary">{KIND_META[entry.kind].label}</Badge>
                    {entry.tone ? <Badge variant="outline">{entry.tone}</Badge> : null}
                    {entry.is_accepted ? <Badge variant="success">Accepted</Badge> : null}
                    <span className="text-xs text-muted-foreground">
                      {formatRelative(entry.created_at)}
                    </span>
                  </div>
                  <p className="line-clamp-2 whitespace-pre-wrap break-words text-sm text-muted-foreground">
                    {entry.output}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <CopyButton value={entry.output} iconOnly />
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => onAccept(entry)}
                    disabled={accepting || entry.is_accepted}
                  >
                    {entry.is_accepted ? "Accepted" : "Accept"}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/** Copy-to-clipboard with a brief confirmation, so the user knows it worked. */
function CopyButton({ value, iconOnly = false }: { value: string; iconOnly?: boolean }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      toast({
        title: "Could not copy",
        description: "Your browser blocked clipboard access.",
        variant: "destructive",
      });
    }
  }

  if (iconOnly) {
    return (
      <Button
        size="icon"
        variant="ghost"
        onClick={() => void copy()}
        aria-label="Copy to clipboard"
      >
        {copied ? <Check className="h-4 w-4" /> : <ClipboardCopy className="h-4 w-4" />}
      </Button>
    );
  }

  return (
    <Button variant="outline" size="sm" onClick={() => void copy()} className="shrink-0">
      {copied ? <Check className="h-4 w-4" /> : <ClipboardCopy className="h-4 w-4" />}
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}
