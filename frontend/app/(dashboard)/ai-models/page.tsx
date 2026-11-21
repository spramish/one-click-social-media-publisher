import type { Metadata } from "next";

import { ModelRegistry } from "@/components/ai/model-registry";
import { PageHeader } from "@/components/page-header";

export const metadata: Metadata = { title: "AI models" };

export default function AIModelsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="AI models"
        description="The free models the assistant may call, in failover order. When one is unavailable, rate-limited, or times out, the next is tried automatically — and a local Ollama model can be the last resort."
      />
      <ModelRegistry />
    </div>
  );
}
