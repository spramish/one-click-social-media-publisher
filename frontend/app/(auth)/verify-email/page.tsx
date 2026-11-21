import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { AuthCard } from "@/components/auth/auth-layout";
import { VerifyEmailPanel } from "@/components/auth/verify-email-panel";
import { LoadingState } from "@/components/ui/spinner";

export const metadata: Metadata = { title: "Verify email" };

export default function VerifyEmailPage() {
  return (
    <AuthCard
      title="Email verification"
      subtitle="Confirming the address on your account."
      footer={
        <Link href="/login" className="font-medium text-primary underline-offset-4 hover:underline">
          Back to sign in
        </Link>
      }
    >
      <Suspense fallback={<LoadingState label="Verifying…" />}>
        <VerifyEmailPanel />
      </Suspense>
    </AuthCard>
  );
}
