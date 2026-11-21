import type { Metadata } from "next";

import { AIAssistant } from "@/components/ai/ai-assistant";
import { PageHeader } from "@/components/page-header";

export const metadata: Metadata = { title: "AI assistant" };

export default function AssistantPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="AI content assistant"
        description="Draft captions, hashtags, and descriptions tuned to each platform. Review everything before it is used."
      />
      <AIAssistant />
    </div>
  );
}
