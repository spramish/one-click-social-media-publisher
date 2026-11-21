import type { Metadata } from "next";

import { AccountsManager } from "@/components/accounts/accounts-manager";
import { PageHeader } from "@/components/page-header";

export const metadata: Metadata = { title: "Social accounts" };

export default function AccountsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Connected social accounts"
        description="Link the Instagram, Facebook, YouTube, and TikTok accounts you publish to."
      />
      <AccountsManager />
    </div>
  );
}
